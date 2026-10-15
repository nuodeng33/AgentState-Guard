"""P7 isolated SelfRuntime test-restore contracts."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from agentguard.discovery.capabilities import CapabilityStatus
from agentguard.discovery.domains import SelfRuntimeAdapter, WindowsAdapter
from agentguard.evidence.ledger import verify_ledger
from agentguard.recovery.contracts import RecoveryOperation, RecoveryRequest
from agentguard.recovery.manifest import manifest_digest
from agentguard.recovery.policy import RestorePolicy
from agentguard.recovery.service import RecoveryService
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore


def _setup(tmp_path: Path):
    target = tmp_path / "config.toml"
    target.write_text("safe=true\n")
    database = StateDB(tmp_path / "state.db")
    database.connect()
    snapshots = SnapshotStore(tmp_path / "snapshots")
    policy = RestorePolicy(
        approved_paths={"local-domain": (target,)},
        validators={"local-domain": "toml-parse"},
    )
    service = RecoveryService(
        database=database,
        snapshots=snapshots,
        adapters={"local-domain": SelfRuntimeAdapter(recovery_policy=policy)},
    )
    created = service.snapshot(
        RecoveryRequest(
            operation=RecoveryOperation.SNAPSHOT,
            execution_domain_id="local-domain",
            target_path=target,
            user_approved=True,
        )
    )
    return target, database, snapshots, service, created


def test_test_restore_writes_and_verifies_an_isolated_copy(tmp_path):
    target, database, _snapshots, service, created = _setup(tmp_path)
    try:
        result = service.test_restore(
            RecoveryRequest(
                operation=RecoveryOperation.TEST_RESTORE,
                execution_domain_id="local-domain",
                checkpoint_id=created.checkpoint_id,
            )
        )

        assert result.status is CapabilityStatus.AVAILABLE
        assert result.reason_code == "TEST_RESTORE_VERIFIED"
        sandbox = Path(result.details["sandbox_path"])
        restored_files = [path for path in sandbox.rglob("*") if path.is_file()]
        assert len(restored_files) == 1
        restored = restored_files[0]
        assert sandbox in restored.resolve().parents
        assert restored.read_text() == "safe=true\n"
        assert target.read_text() == "safe=true\n"
        assert result.details["verified_targets"] == 1
        event_types = [
            row[0]
            for row in database._conn.execute(
                "SELECT event_type FROM evidence_ledger_events ORDER BY sequence"
            )
        ]
        assert event_types[-3:] == [
            "TEST_RESTORE_STARTED",
            "FILE_RESTORED",
            "VALIDATOR_PASSED",
        ]
        assert verify_ledger(database._conn) == []
    finally:
        database.close()


def test_test_restore_rejects_v2_without_creating_a_sandbox(tmp_path):
    target, database, snapshots, service, _created = _setup(tmp_path)
    try:
        relative = snapshots.save(99, {"format_version": 2, "files": {str(target): {}}})
        checkpoint_id = database.insert_checkpoint("legacy", relative, "legacy", 1, {}, None, None)
        result = service.test_restore(
            RecoveryRequest(
                operation=RecoveryOperation.TEST_RESTORE,
                execution_domain_id="local-domain",
                checkpoint_id=str(checkpoint_id),
            )
        )
        assert result.status is CapabilityStatus.UNSUPPORTED
        assert result.reason_code == "LEGACY_SNAPSHOT_READ_ONLY"
        assert "sandbox_path" not in result.details
        assert not any(path.name.startswith("agentguard-test-restore-") for path in tmp_path.iterdir())
    finally:
        database.close()


def test_test_restore_fails_closed_for_tampered_blob_and_records_failure(tmp_path):
    _target, database, snapshots, service, created = _setup(tmp_path)
    try:
        checkpoint = database.get_checkpoint(int(created.checkpoint_id))
        artifact = snapshots.load(checkpoint["snapshot_path"])
        blob_digest = next(iter(artifact["blobs"]))
        artifact["blobs"][blob_digest] = "00"
        snapshots.save(int(created.checkpoint_id), artifact)

        result = service.test_restore(
            RecoveryRequest(
                operation=RecoveryOperation.TEST_RESTORE,
                execution_domain_id="local-domain",
                checkpoint_id=created.checkpoint_id,
            )
        )
        assert result.status is CapabilityStatus.ERROR
        assert result.reason_code == "RECOVERY_MANIFEST_INVALID"
        assert "sandbox_path" not in result.details
        assert database._conn.execute(
            "SELECT event_type FROM evidence_ledger_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone() == ("RESTORE_FAILED",)
    finally:
        database.close()


def test_test_restore_fails_closed_without_restorable_entries(tmp_path):
    target, database, snapshots, service, _created = _setup(tmp_path)
    try:
        policy = RestorePolicy()
        artifact = policy.snapshot_v3(target, "local-domain")
        relative = snapshots.save_recovery_v3(100, artifact)
        checkpoint_id = database.insert_checkpoint("audit", relative, manifest_digest(artifact), 1, {}, None, None)
        result = service.test_restore(
            RecoveryRequest(
                operation=RecoveryOperation.TEST_RESTORE,
                execution_domain_id="local-domain",
                checkpoint_id=str(checkpoint_id),
            )
        )
        assert result.status is CapabilityStatus.ERROR
        assert result.reason_code == "TEST_RESTORE_NO_RESTORABLE_ENTRIES"
        assert "sandbox_path" not in result.details
    finally:
        database.close()


def test_test_restore_ledger_failure_does_not_return_success_or_leave_root(tmp_path, monkeypatch):
    _target, database, _snapshots, service, created = _setup(tmp_path)
    staging_root = tmp_path / "staging-root"
    original_mkdtemp = tempfile.mkdtemp

    def _staging_mkdtemp(*, prefix, **kwargs):
        if prefix == "agentguard-test-restore-root-":
            staging_root.mkdir()
            return str(staging_root)
        return original_mkdtemp(prefix=prefix, **kwargs)

    monkeypatch.setattr("agentguard.recovery.service.tempfile.mkdtemp", _staging_mkdtemp)
    try:
        monkeypatch.setattr(
            service._ledger,
            "append",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.IntegrityError("secret")),
        )
        result = service.test_restore(
            RecoveryRequest(
                operation=RecoveryOperation.TEST_RESTORE,
                execution_domain_id="local-domain",
                checkpoint_id=created.checkpoint_id,
            )
        )
        assert result.status is CapabilityStatus.ERROR
        assert result.reason_code == "RECOVERY_PERSISTENCE_FAILED"
        assert "secret" not in str(result)
        assert not staging_root.exists()
        assert not list(tmp_path.glob("agentguard-test-restore-root-*"))
        assert not database._conn.in_transaction
    finally:
        database.close()


def test_test_restore_never_verifies_against_a_corrupt_ledger(tmp_path):
    _target, database, _snapshots, service, created = _setup(tmp_path)
    try:
        database._conn.execute("DROP TRIGGER evidence_ledger_events_no_update")
        database._conn.execute(
            "UPDATE evidence_ledger_events SET result = 'forged' WHERE sequence = 1"
        )
        database._conn.commit()
        assert verify_ledger(database._conn)

        result = service.test_restore(
            RecoveryRequest(
                operation=RecoveryOperation.TEST_RESTORE,
                execution_domain_id="local-domain",
                checkpoint_id=created.checkpoint_id,
            )
        )

        assert result.status is CapabilityStatus.ERROR
        assert result.reason_code == "RECOVERY_LEDGER_INVALID"
        assert "sandbox_path" not in result.details
    finally:
        database.close()


def test_production_restore_fails_closed_without_target_and_approval(tmp_path):
    _target, database, _snapshots, service, created = _setup(tmp_path)
    try:
        result = service.restore(
            RecoveryRequest(
                operation=RecoveryOperation.RESTORE,
                execution_domain_id="local-domain",
                checkpoint_id=created.checkpoint_id,
            )
        )
        assert result.status is CapabilityStatus.ERROR
        assert result.reason_code == "RECOVERY_TARGET_SCOPE_INVALID"
    finally:
        database.close()


    adapter = WindowsAdapter(os_name="posix", platform_system=lambda: "Linux")
    result = adapter.test_restore(
        RecoveryRequest(
            operation=RecoveryOperation.TEST_RESTORE,
            execution_domain_id="windows-current",
        )
    )
    assert result.status is CapabilityStatus.UNSUPPORTED
    assert result.reason_code == "WINDOWS_RECOVERY_OUT_OF_SCOPE_P7"
