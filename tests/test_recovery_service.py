"""P6 execution-domain recovery routing and evidence contracts."""

from __future__ import annotations

from pathlib import Path

from agentguard.discovery.capabilities import CapabilityStatus
from agentguard.discovery.domains import SelfRuntimeAdapter, WindowsAdapter
from agentguard.evidence.ledger import verify_ledger
from agentguard.recovery.contracts import RecoveryOperation, RecoveryRequest
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
