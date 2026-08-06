"""P6 blob, ledger, and transaction fault-injection matrix."""

from __future__ import annotations

import sqlite3

import pytest

from agentguard.discovery.capabilities import CapabilityStatus
from agentguard.discovery.domains import SelfRuntimeAdapter
from agentguard.evidence.ledger import verify_ledger
from agentguard.recovery.contracts import RecoveryOperation, RecoveryRequest
from agentguard.recovery.coverage import RecoveryCoverageService, RecoveryCoverageStatus
from agentguard.recovery.policy import RestorePolicy
from agentguard.recovery.service import RecoveryService
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore


def _service(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    database = StateDB(tmp_path / "state.db")
    database.connect()
    snapshots = SnapshotStore(tmp_path / "snapshots")
    recovery = RecoveryService(
        database=database,
        snapshots=snapshots,
        adapters={
            "local-domain": SelfRuntimeAdapter(
                recovery_policy=RestorePolicy(
                    approved_paths={"local-domain": (target,)},
                    validators={"local-domain": "toml-parse"},
                )
            )
        },
    )
    return target, database, snapshots, recovery


def _snapshot(target, recovery):
    return recovery.snapshot(
        RecoveryRequest(
            operation=RecoveryOperation.SNAPSHOT,
            execution_domain_id="local-domain",
            target_path=target,
            user_approved=True,
        )
    )


def test_blob_write_failure_rolls_back_checkpoint_and_ledger(tmp_path, monkeypatch):
    target, database, snapshots, recovery = _service(tmp_path)
    monkeypatch.setattr(
        snapshots,
        "save_recovery_v3",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("raw-secret")),
    )
    try:
        result = _snapshot(target, recovery)

        assert result.status is CapabilityStatus.UNREACHABLE
        assert result.reason_code == "RECOVERY_DOMAIN_UNREACHABLE"
        assert "raw-secret" not in str(result)
        assert database._conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone() == (0,)
        assert database._conn.execute("SELECT COUNT(*) FROM evidence_ledger_events").fetchone() == (0,)
        assert list(snapshots.snapshot_dir.iterdir()) == []
        assert not database._conn.in_transaction
    finally:
        database.close()


def test_atomic_snapshot_replace_failure_removes_temporary_file(tmp_path, monkeypatch):
    snapshots = SnapshotStore(tmp_path / "snapshots")
    monkeypatch.setattr(
        "agentguard.storage.snapshots.os.replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("raw-secret")),
    )

    with pytest.raises(OSError, match="raw-secret"):
        snapshots.save(1, {"format_version": 2, "files": {}})

    assert list(snapshots.snapshot_dir.iterdir()) == []


def test_verify_ledger_failure_returns_stable_error_without_false_success(tmp_path, monkeypatch):
    target, database, _snapshots, recovery = _service(tmp_path)
    try:
        created = _snapshot(target, recovery)
        before_events = database._conn.execute(
            "SELECT COUNT(*) FROM evidence_ledger_events"
        ).fetchone()[0]
        monkeypatch.setattr(
            recovery._ledger,
            "append",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                sqlite3.IntegrityError("raw-secret")
            ),
        )

        result = recovery.verify(
            RecoveryRequest(
                operation=RecoveryOperation.VERIFY,
                execution_domain_id="local-domain",
                checkpoint_id=created.checkpoint_id,
            )
        )

        assert result.status is CapabilityStatus.ERROR
        assert result.reason_code == "RECOVERY_PERSISTENCE_FAILED"
        assert "raw-secret" not in str(result)
        assert database._conn.execute(
            "SELECT COUNT(*) FROM evidence_ledger_events"
        ).fetchone() == (before_events,)
        assert verify_ledger(database._conn) == []
        assert not database._conn.in_transaction
    finally:
        database.close()


def test_coverage_ledger_read_failure_degrades_to_stable_unknown(tmp_path, monkeypatch):
    target, database, snapshots, recovery = _service(tmp_path)
    try:
        created = _snapshot(target, recovery)
        monkeypatch.setattr(
            "agentguard.recovery.coverage.verify_ledger",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                sqlite3.DatabaseError("raw-secret")
            ),
        )

        facts = RecoveryCoverageService(database, snapshots).compute(
            checkpoint_id=created.checkpoint_id,
            target_refs=(str(target),),
            execution_domain_id="local-domain",
        )

        assert facts.status is RecoveryCoverageStatus.EVIDENCE_INSUFFICIENT
        assert facts.reason_code == "RECOVERY_FACTS_UNAVAILABLE"
        assert "raw-secret" not in str(facts)
    finally:
        database.close()


def test_tampered_ledger_cannot_authorize_recovery_coverage(tmp_path):
    target, database, snapshots, recovery = _service(tmp_path)
    try:
        created = _snapshot(target, recovery)
        database._conn.execute("DROP TRIGGER evidence_ledger_events_no_update")
        database._conn.execute(
            "UPDATE evidence_ledger_events SET payload_safe_json = '{}' WHERE sequence = 1"
        )
        database._conn.commit()

        facts = RecoveryCoverageService(database, snapshots).compute(
            checkpoint_id=created.checkpoint_id,
            target_refs=(str(target),),
            execution_domain_id="local-domain",
        )

        assert facts.status is RecoveryCoverageStatus.EVIDENCE_INSUFFICIENT
        assert facts.reason_code == "RECOVERY_LEDGER_INVALID"
        assert facts.authorized_snapshot_coverage == 0.0
        assert facts.test_restore_verified_targets is None
    finally:
        database.close()
