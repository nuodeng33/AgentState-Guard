"""Tests for schema migration system."""

import sqlite3
import tempfile
from pathlib import Path

from agentguard.storage.migrations import Migration, MigrationEngine


class TestMigrations:
    def test_engine_creates_schema_migrations(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT, checksum TEXT, duration_ms INTEGER)")
            conn.commit()
            engine = MigrationEngine(db_path)
            assert engine.current_version(conn) == 0
            assert len(engine.pending(conn)) == 8  # v1 through v8
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_migrate_forward(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT, checksum TEXT, duration_ms INTEGER)")
            conn.commit()
            engine = MigrationEngine(db_path)
            result = engine.migrate(conn)
            assert len(result["applied"]) == 8
            assert result["errors"] == []
            assert engine.current_version(conn) == 8
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_migrate_idempotent(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT, checksum TEXT, duration_ms INTEGER)")
            conn.commit()
            engine = MigrationEngine(db_path)
            engine.migrate(conn)
            result = engine.migrate(conn)
            assert result["applied"] == []
            assert engine.current_version(conn) == 8
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_migrate_creates_tables(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT, checksum TEXT, duration_ms INTEGER)")
            conn.commit()
            engine = MigrationEngine(db_path)
            engine.migrate(conn)
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            table_names = [t[0] for t in tables]
            assert "checkpoints" in table_names
            assert "blobs" in table_names
            assert "audit_events" in table_names
            assert "transactions" in table_names
            assert "evidence_ledger_events" in table_names
            assert "recovery_drills" in table_names
            assert "recovery_drill_approvals" in table_names
            assert "trusted_baselines" in table_names
            assert "trusted_baseline_candidates" in table_names
            assert "trusted_baseline_approvals" in table_names
            assert "recovery_drill_bindings" in table_names
            assert "trusted_baseline_candidate_bindings" in table_names
            assert "recovery_authorizations" in table_names
            assert "device_link_bindings" in table_names
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_v5_rollback_does_not_own_v7_tables(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT, checksum TEXT, duration_ms INTEGER)")
            conn.commit()
            engine = MigrationEngine(db_path)
            engine.migrate(conn)
            executed: list[str] = []
            original = engine._execute_script

            def record(connection, script):
                executed.append(script)
                original(connection, script)

            engine._execute_script = record
            result = engine.rollback(conn, 4)

            assert result["errors"] == []
            assert engine.current_version(conn) == 4
            assert "device_link_bindings" in executed[0]
            assert "recovery_authorizations" in executed[1]
            assert "trusted_baseline_candidates" in executed[2]
            assert "trusted_baseline_approvals" in executed[2]
            assert "trusted_baseline_candidates" not in executed[3]
            assert "trusted_baseline_approvals" not in executed[3]
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_rollback(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT, checksum TEXT, duration_ms INTEGER)")
            conn.commit()
            engine = MigrationEngine(db_path)
            engine.migrate(conn)
            assert engine.current_version(conn) == 8
            result = engine.rollback(conn, 0)
            assert len(result["rolled_back"]) == 8
            assert engine.current_version(conn) == 0
            # Re-migrate
            engine.migrate(conn)
            assert engine.current_version(conn) == 8
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_custom_migration(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT, checksum TEXT, duration_ms INTEGER)")
            conn.commit()
            engine = MigrationEngine(db_path)
            engine.register(Migration(4, "Custom test", "CREATE TABLE custom_test (id INTEGER)", "DROP TABLE custom_test"))
            engine.migrate(conn)
            assert engine.current_version(conn) == 8
            tables = conn.execute("SELECT name FROM sqlite_master WHERE name='custom_test'").fetchall()
            assert len(tables) == 1
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)

    def test_migration_rejects_bad_sql(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT, checksum TEXT, duration_ms INTEGER)")
            conn.commit()
            engine = MigrationEngine(db_path)
            engine.register(Migration(99, "Bad SQL", "CREATE TABLE invalid_sql(...)", None))
            result = engine.migrate(conn)
            assert len(result["errors"]) > 0
        finally:
            conn.close()
            db_path.unlink(missing_ok=True)
