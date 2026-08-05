"""Tests for transaction engine and coverage."""

import sqlite3
import tempfile
from pathlib import Path

from agentguard.core.whitelist import Whitelist
from agentguard.transactions.coverage import compute_coverage, coverage_summary


class TestCoverage:
    def test_empty_coverage(self):
        c = compute_coverage([], Whitelist([]), {})
        assert c["coverage_pct"] == 100.0

    def test_all_recoverable(self):
        wl = Whitelist(["/tmp/test"])
        entries = {"/tmp/test/file.txt": {"mode": "restorable", "content_gz": "abc"}}
        c = compute_coverage(["/tmp/test/file.txt"], wl, entries)
        assert c["coverage_label"] == "fully_recoverable"

    def test_not_in_whitelist(self):
        wl = Whitelist(["/safe"])
        c = compute_coverage(["/unsafe/file.txt"], wl, {})
        assert len(c["not_recoverable"]) == 1
        assert c["coverage_pct"] < 100

    def test_audit_only_not_recoverable(self):
        wl = Whitelist(["/tmp"])
        entries = {"/tmp/secret.txt": {"mode": "audit_only"}}
        c = compute_coverage(["/tmp/secret.txt"], wl, entries)
        assert len(c["not_recoverable"]) == 1

    def test_mixed_coverage(self):
        wl = Whitelist(["/tmp"])
        entries = {
            "/tmp/good.txt": {"mode": "restorable", "content_gz": "abc"},
            "/tmp/bad.txt": {"mode": "audit_only"},
        }
        c = compute_coverage(["/tmp/good.txt", "/tmp/bad.txt"], wl, entries)
        assert len(c["fully_recoverable"]) == 1
        assert len(c["not_recoverable"]) == 1
        assert c["coverage_pct"] == 50.0

    def test_coverage_summary(self):
        wl = Whitelist(["/tmp"])
        entries = {"/tmp/f.txt": {"mode": "restorable", "content_gz": "abc"}}
        c = compute_coverage(["/tmp/f.txt"], wl, entries)
        summary = coverage_summary(c)
        assert "fully_recoverable" in summary
        assert "100.0%" in summary

    def test_no_checkpoint_entry(self):
        wl = Whitelist(["/tmp"])
        c = compute_coverage(["/tmp/unknown.txt"], wl, {})
        assert len(c["not_recoverable"]) == 1


class TestTransactionEngine:
    def _setup(self):
        """Create a minimal DB with schema."""
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="txn-test-"))
        db_path = self.tmp_dir / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT, checksum TEXT, duration_ms INTEGER)")
        conn.commit()
        from agentguard.storage.migrations import MigrationEngine
        MigrationEngine(db_path).migrate(conn)
        conn.close()

        from agentguard.storage.db import StateDB
        from agentguard.storage.snapshots import SnapshotStore
        self.db = StateDB(db_path)
        self.db.connect()
        self.snapshots = SnapshotStore(self.tmp_dir / "snapshots")
        self.config = {"security": {"restore_whitelist": []}}

    def teardown_method(self):
        self.db.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_create_plan(self):
        self._setup()
        from agentguard.transactions.engine import TransactionEngine
        engine = TransactionEngine(self.db, self.snapshots, self.config)
        result = engine.create_plan("test plan", [])
        assert result["status"] == "planned"
        assert result["transaction_id"] >= 1

    def test_create_plan_records_warning_as_legacy_safe_detail(self):
        self._setup()
        from agentguard.storage.audit import get_events, verify_chain
        from agentguard.transactions.engine import TransactionEngine

        engine = TransactionEngine(self.db, self.snapshots, self.config)
        result = engine.create_plan("warning plan", ["/outside-whitelist"])

        assert result["status"] == "planned"
        event = get_events(self.db._conn, event_type="transaction.plan")[0]
        assert event["result"] == "success"
        details = self.db._conn.execute(
            "SELECT details_safe FROM audit_events WHERE event_type = 'transaction.plan'"
        ).fetchone()[0]
        assert "success_with_warnings" in details
        assert verify_chain(self.db._conn) == []

    def test_transition_valid(self):
        self._setup()
        from agentguard.transactions.engine import TransactionEngine
        engine = TransactionEngine(self.db, self.snapshots, self.config)
        plan = engine.create_plan("transition test", [])
        txn_id = plan["transaction_id"]
        r = engine.apply(txn_id)
        assert r["status"] == "ok"
        t = engine.get_transaction(txn_id)
        assert t["status"] == "applying"

    def test_transition_invalid(self):
        self._setup()
        from agentguard.transactions.engine import TransactionEngine
        engine = TransactionEngine(self.db, self.snapshots, self.config)
        plan = engine.create_plan("invalid test", [])
        txn_id = plan["transaction_id"]
        r = engine.verify(txn_id)
        assert r["status"] == "error"

    def test_list_transactions(self):
        self._setup()
        from agentguard.transactions.engine import TransactionEngine
        engine = TransactionEngine(self.db, self.snapshots, self.config)
        engine.create_plan("txn 1", [])
        engine.create_plan("txn 2", [])
        txns = engine.list_transactions()
        assert len(txns) == 2

    def test_get_transaction(self):
        self._setup()
        from agentguard.transactions.engine import TransactionEngine
        engine = TransactionEngine(self.db, self.snapshots, self.config)
        plan = engine.create_plan("get test", [])
        t = engine.get_transaction(plan["transaction_id"])
        assert t["label"] == "get test"
        assert t["status"] == "planned"
