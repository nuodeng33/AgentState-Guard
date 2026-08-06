"""P6 execution-domain recovery routing and evidence contracts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agentguard.discovery.capabilities import CapabilityStatus
from agentguard.discovery.domains import SelfRuntimeAdapter, WindowsAdapter
from agentguard.evidence.ledger import verify_ledger
from agentguard.recovery.contracts import (
    RecoveryOperation,
    RecoveryOperationResult,
    RecoveryRequest,
)
from agentguard.recovery.policy import RestorePolicy
from agentguard.recovery.service import RecoveryService
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore


def _service(tmp_path: Path, policy: RestorePolicy) -> tuple[RecoveryService, StateDB]:
    database = StateDB(tmp_path / "state.db")
    database.connect()
    service = RecoveryService(
        database=database,
        snapshots=SnapshotStore(tmp_path / "snapshots"),
        adapters={"local-domain": SelfRuntimeAdapter(recovery_policy=policy)},
    )
    return service, database


def test_snapshot_verify_and_restore_route_through_adapter_and_ledger(tmp_path):
    """A real restore call must not bypass the P6 adapter boundary."""
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    service, database = _service(
        tmp_path,
        RestorePolicy(
            approved_paths={"local-domain": (target,)},
            validators={"local-domain": "toml-parse"},
        ),
    )
    try:
        created = service.snapshot(
            RecoveryRequest(
                operation=RecoveryOperation.SNAPSHOT,
                execution_domain_id="local-domain",
                target_path=target,
                user_approved=True,
            )
        )

        assert created.status is CapabilityStatus.AVAILABLE
        assert created.checkpoint_id is not None
        assert created.manifest_digest is not None

        verified = service.verify(
            RecoveryRequest(
                operation=RecoveryOperation.VERIFY,
                execution_domain_id="local-domain",
                checkpoint_id=created.checkpoint_id,
            )
        )
        assert verified.status is CapabilityStatus.AVAILABLE
        assert verified.reason_code == "RECOVERY_MANIFEST_VERIFIED"

        before = target.read_bytes()
        restored = service.restore(
            RecoveryRequest(
                operation=RecoveryOperation.RESTORE,
                execution_domain_id="local-domain",
                checkpoint_id=created.checkpoint_id,
            )
        )
        assert restored.status is CapabilityStatus.UNSUPPORTED
        assert restored.reason_code == "REAL_RESTORE_OUT_OF_SCOPE_P6"
        assert target.read_bytes() == before

        event_types = [
            row[0]
            for row in database._conn.execute(
                "SELECT event_type FROM evidence_ledger_events ORDER BY sequence"
            )
        ]
        assert event_types == [
            "CHECKPOINT_CREATED",
            "MANIFEST_VERIFIED",
            "MANIFEST_VERIFIED",
            "RESTORE_FAILED",
        ]
        assert verify_ledger(database._conn) == []
    finally:
        database.close()


def test_recovery_returns_stable_result_for_missing_or_unreachable_domain(tmp_path):
    service, database = _service(tmp_path, RestorePolicy())
    try:
        missing = service.snapshot(
            RecoveryRequest(
                operation=RecoveryOperation.SNAPSHOT,
                execution_domain_id="missing-domain",
                target_path=tmp_path / "missing.toml",
            )
        )

        assert missing.status is CapabilityStatus.UNSUPPORTED
        assert missing.reason_code == "RECOVERY_OPERATION_UNSUPPORTED"

        unavailable = service.snapshot(
            RecoveryRequest(
                operation=RecoveryOperation.SNAPSHOT,
                execution_domain_id="local-domain",
                target_path=tmp_path / "missing.toml",
            )
        )
        assert unavailable.status is CapabilityStatus.NOT_PRESENT
        assert unavailable.reason_code == "RECOVERY_TARGET_NOT_PRESENT"
        assert "missing.toml" not in str(unavailable)
    finally:
        database.close()


def test_verify_legacy_snapshot_is_read_only_and_does_not_migrate(tmp_path):
    service, database = _service(tmp_path, RestorePolicy())
    try:
        relative = service._snapshots.save(
            1,
            {"format_version": 2, "files": {}, "label": "legacy"},
        )
        checkpoint_id = database.insert_checkpoint(
            "legacy",
            relative,
            "legacy-digest",
            0,
            {},
            None,
            None,
        )

        result = service.verify(
            RecoveryRequest(
                operation=RecoveryOperation.VERIFY,
                execution_domain_id="local-domain",
                checkpoint_id=str(checkpoint_id),
            )
        )

        assert result.status is CapabilityStatus.UNSUPPORTED
        assert result.reason_code == "LEGACY_SNAPSHOT_READ_ONLY"
        assert database.get_checkpoint(checkpoint_id)["snapshot_path"] == relative
    finally:
        database.close()


def test_windows_adapter_never_treats_wsl_as_a_restore_target():
    adapter = WindowsAdapter(os_name="posix", platform_system=lambda: "Linux")

    result = adapter.snapshot(
        RecoveryRequest(
            operation=RecoveryOperation.SNAPSHOT,
            execution_domain_id="windows-current",
            target_path=Path("/tmp/example"),
        )
    )

    assert result.status is CapabilityStatus.UNSUPPORTED
    assert result.reason_code == "WINDOWS_RECOVERY_OUT_OF_SCOPE_P6"


class _FixedAdapter:
    def __init__(self, *, snapshot_result=None, restore_result=None):
        self._snapshot_result = snapshot_result
        self._restore_result = restore_result

    def snapshot(self, request):
        return self._snapshot_result

    def restore(self, request):
        return self._restore_result

    def verify(self, request):
        return self._snapshot_result


def test_service_rejects_cross_domain_adapter_success(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    artifact = RestorePolicy(
        approved_paths={"local-domain": (target,)},
        validators={"local-domain": "toml-parse"},
    ).snapshot_v3(target, "local-domain", user_approved=True)
    result = RecoveryOperationResult(
        operation=RecoveryOperation.SNAPSHOT,
        status=CapabilityStatus.AVAILABLE,
        reason_code="RECOVERY_MANIFEST_VERIFIED",
        execution_domain_id="other-domain",
        manifest_digest="0" * 64,
        artifact=artifact,
    )
    database = StateDB(tmp_path / "state.db")
    database.connect()
    service = RecoveryService(
        database=database,
        snapshots=SnapshotStore(tmp_path / "snapshots"),
        adapters={"local-domain": _FixedAdapter(snapshot_result=result)},
    )
    try:
        outcome = service.snapshot(
            RecoveryRequest(
                operation=RecoveryOperation.SNAPSHOT,
                execution_domain_id="local-domain",
                target_path=target,
                user_approved=True,
            )
        )

        assert outcome.status is CapabilityStatus.ERROR
        assert outcome.reason_code == "RECOVERY_ADAPTER_RESULT_INVALID"
        assert database._conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone() == (0,)
    finally:
        database.close()


def test_service_rejects_adapter_manifest_digest_mismatch(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    artifact = RestorePolicy(
        approved_paths={"local-domain": (target,)},
        validators={"local-domain": "toml-parse"},
    ).snapshot_v3(target, "local-domain", user_approved=True)
    result = RecoveryOperationResult(
        operation=RecoveryOperation.SNAPSHOT,
        status=CapabilityStatus.AVAILABLE,
        reason_code="RECOVERY_MANIFEST_VERIFIED",
        execution_domain_id="local-domain",
        manifest_digest="0" * 64,
        artifact=artifact,
    )
    database = StateDB(tmp_path / "state.db")
    database.connect()
    service = RecoveryService(
        database=database,
        snapshots=SnapshotStore(tmp_path / "snapshots"),
        adapters={"local-domain": _FixedAdapter(snapshot_result=result)},
    )
    try:
        outcome = service.snapshot(
            RecoveryRequest(
                operation=RecoveryOperation.SNAPSHOT,
                execution_domain_id="local-domain",
                target_path=target,
                user_approved=True,
            )
        )

        assert outcome.status is CapabilityStatus.ERROR
        assert outcome.reason_code == "RECOVERY_ADAPTER_RESULT_INVALID"
        assert database._conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone() == (0,)
    finally:
        database.close()


def test_service_overrides_adapter_restore_success_in_p6(tmp_path):
    fake_success = RecoveryOperationResult(
        operation=RecoveryOperation.RESTORE,
        status=CapabilityStatus.AVAILABLE,
        reason_code="FAKE_SUCCESS",
        execution_domain_id="local-domain",
    )
    database = StateDB(tmp_path / "state.db")
    database.connect()
    service = RecoveryService(
        database=database,
        snapshots=SnapshotStore(tmp_path / "snapshots"),
        adapters={"local-domain": _FixedAdapter(restore_result=fake_success)},
    )
    try:
        outcome = service.restore(
            RecoveryRequest(
                operation=RecoveryOperation.RESTORE,
                execution_domain_id="local-domain",
                checkpoint_id="1",
            )
        )

        assert outcome.status is CapabilityStatus.UNSUPPORTED
        assert outcome.reason_code == "REAL_RESTORE_OUT_OF_SCOPE_P6"
    finally:
        database.close()


def test_corrupt_v3_blob_is_not_misclassified_as_legacy(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    service, database = _service(
        tmp_path,
        RestorePolicy(
            approved_paths={"local-domain": (target,)},
            validators={"local-domain": "toml-parse"},
        ),
    )
    try:
        created = service.snapshot(
            RecoveryRequest(
                operation=RecoveryOperation.SNAPSHOT,
                execution_domain_id="local-domain",
                target_path=target,
                user_approved=True,
            )
        )
        checkpoint = database.get_checkpoint(int(created.checkpoint_id))
        stored = service._snapshots.load(checkpoint["snapshot_path"])
        digest = next(iter(stored["blobs"]))
        stored["blobs"][digest] = "not-hex"
        service._snapshots.save(int(created.checkpoint_id), stored)

        outcome = service.verify(
            RecoveryRequest(
                operation=RecoveryOperation.VERIFY,
                execution_domain_id="local-domain",
                checkpoint_id=created.checkpoint_id,
            )
        )

        assert outcome.status is CapabilityStatus.ERROR
        assert outcome.reason_code == "RECOVERY_MANIFEST_INVALID"
    finally:
        database.close()


def test_ledger_failure_rolls_back_checkpoint_and_removes_artifact(tmp_path, monkeypatch):
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    service, database = _service(
        tmp_path,
        RestorePolicy(
            approved_paths={"local-domain": (target,)},
            validators={"local-domain": "toml-parse"},
        ),
    )
    monkeypatch.setattr(
        service._ledger,
        "append",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.IntegrityError("raw-secret")),
    )
    try:
        outcome = service.snapshot(
            RecoveryRequest(
                operation=RecoveryOperation.SNAPSHOT,
                execution_domain_id="local-domain",
                target_path=target,
                user_approved=True,
            )
        )

        assert outcome.status is CapabilityStatus.ERROR
        assert outcome.reason_code == "RECOVERY_PERSISTENCE_FAILED"
        assert database._conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone() == (0,)
        assert database._conn.execute("SELECT COUNT(*) FROM evidence_ledger_events").fetchone() == (0,)
        assert list((tmp_path / "snapshots").glob("snapshot-*.dat")) == []
        assert "raw-secret" not in str(outcome)
    finally:
        database.close()


def test_failure_ledger_fault_does_not_replace_stable_operation_result(tmp_path, monkeypatch):
    service, database = _service(tmp_path, RestorePolicy())
    monkeypatch.setattr(
        service._ledger,
        "append",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.IntegrityError("raw-secret")),
    )
    try:
        outcome = service.snapshot(
            RecoveryRequest(
                operation=RecoveryOperation.SNAPSHOT,
                execution_domain_id="missing-domain",
                target_path=tmp_path / "missing.toml",
            )
        )

        assert outcome.status is CapabilityStatus.UNSUPPORTED
        assert outcome.reason_code == "RECOVERY_OPERATION_UNSUPPORTED"
        assert "raw-secret" not in str(outcome)
    finally:
        database.close()


def test_snapshot_path_uses_actual_autoincrement_checkpoint_id(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    service, database = _service(
        tmp_path,
        RestorePolicy(
            approved_paths={"local-domain": (target,)},
            validators={"local-domain": "toml-parse"},
        ),
    )
    try:
        discarded = database.insert_checkpoint("discarded", "none", "0" * 64, 0, {}, None, None)
        database._conn.execute("DELETE FROM checkpoints WHERE id = ?", (discarded,))
        database._conn.commit()

        outcome = service.snapshot(
            RecoveryRequest(
                operation=RecoveryOperation.SNAPSHOT,
                execution_domain_id="local-domain",
                target_path=target,
                user_approved=True,
            )
        )

        assert outcome.status is CapabilityStatus.AVAILABLE
        checkpoint = database.get_checkpoint(int(outcome.checkpoint_id))
        assert checkpoint["snapshot_path"] == f"snapshots/snapshot-{int(outcome.checkpoint_id):06d}.dat"
    finally:
        database.close()
