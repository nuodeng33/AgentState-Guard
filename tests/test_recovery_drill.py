"""R3 controlled SelfRuntime recovery-drill contracts."""

from __future__ import annotations

from pathlib import Path

from agentguard.discovery.capabilities import CapabilityStatus
from agentguard.discovery.domains import SelfRuntimeAdapter
from agentguard.recovery.contracts import (
    RecoveryOperation,
    RecoveryOperationResult,
    RecoveryRequest,
)
from agentguard.recovery.policy import RestorePolicy
from agentguard.recovery.service import RecoveryService
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore


def _service(tmp_path: Path):
    target = tmp_path / "config.toml"
    target.write_text("safe=true\n")
    database = StateDB(tmp_path / "state.db")
    database.connect()
    snapshots = SnapshotStore(tmp_path / "snapshots")
    service = RecoveryService(
        database=database,
        snapshots=snapshots,
        adapters={
            "self-runtime": SelfRuntimeAdapter(
                recovery_policy=RestorePolicy(
                    approved_paths={"self-runtime": (target,)},
                    validators={"self-runtime": "toml-parse"},
                )
            )
        },
    )
    checkpoint = service.snapshot(
        RecoveryRequest(
            operation=RecoveryOperation.SNAPSHOT,
            execution_domain_id="self-runtime",
            target_path=target,
            user_approved=True,
        )
    )
    return target, database, snapshots, service, checkpoint


def _r2_then_approved_drill(service: RecoveryService, checkpoint_id: str) -> dict[str, object]:
    r2 = service.test_restore(
        RecoveryRequest(
            operation=RecoveryOperation.TEST_RESTORE,
            execution_domain_id="self-runtime",
            checkpoint_id=checkpoint_id,
        )
    )
    assert r2.reason_code == "TEST_RESTORE_VERIFIED"
    prepared = service.prepare_drill(
        checkpoint_id=checkpoint_id,
        execution_domain_id="self-runtime",
    )
    assert prepared["status"] == "AWAITING_APPROVAL"
    assert service.approve_drill(prepared["drill_id"])["status"] == "APPROVED"
    return prepared


def _drill_events(database: StateDB, checkpoint_id: str) -> list[str]:
    return [
        row[0]
        for row in database._conn.execute(
            "SELECT event_type FROM evidence_ledger_events WHERE checkpoint_id = ? ORDER BY sequence",
            (checkpoint_id,),
        )
    ]


def test_r3_drill_requires_prior_authoritative_r2_and_bound_approval(tmp_path):
    _target, database, _snapshots, service, checkpoint = _service(tmp_path)
    try:
        r2 = service.test_restore(
            RecoveryRequest(
                operation=RecoveryOperation.TEST_RESTORE,
                execution_domain_id="self-runtime",
                checkpoint_id=checkpoint.checkpoint_id,
            )
        )
        assert r2.reason_code == "TEST_RESTORE_VERIFIED"
        prepared = service.prepare_drill(
            checkpoint_id=checkpoint.checkpoint_id,
            execution_domain_id="self-runtime",
        )
        assert prepared["status"] == "AWAITING_APPROVAL"
        assert service.run_drill(prepared["drill_id"])["reason_code"] == "DRILL_APPROVAL_MISSING_OR_CONSUMED"
        assert service.approve_drill(prepared["drill_id"])["binding_digest"] == prepared["binding_digest"]
        assert service.run_drill(prepared["drill_id"])["status"] == "VERIFIED_R3"
        assert service.run_drill(prepared["drill_id"])["reason_code"] == "DRILL_APPROVAL_MISSING_OR_CONSUMED"
    finally:
        database.close()


def test_r3_drill_restores_managed_target_without_calling_r2(tmp_path, monkeypatch):
    source, database, _snapshots, service, checkpoint = _service(tmp_path)
    try:
        prepared = _r2_then_approved_drill(service, checkpoint.checkpoint_id)
        monkeypatch.setattr(
            service,
            "test_restore",
            lambda _request: (_ for _ in ()).throw(AssertionError("R3 must not delegate to test_restore")),
        )
        result = service.run_drill(prepared["drill_id"])
        assert result["status"] == "VERIFIED_R3"
        assert result["reason_code"] == "DRILL_VERIFIED_R3"
        assert result["verified_targets"] == 1
        assert result["drift_established"] is True
        assert result["managed_target_cleaned"] is True
        assert source.read_text() == "safe=true\n"
        events = _drill_events(database, checkpoint.checkpoint_id)
        assert {"RECOVERY_DRILL_STARTED", "DRIFT_ESTABLISHED", "FILE_RESTORED", "VALIDATOR_PASSED", "RECOVERY_DRILL_VERIFIED"} <= set(events)
    finally:
        database.close()


def test_r3_drill_rejects_missing_r2(tmp_path):
    _target, database, _snapshots, service, checkpoint = _service(tmp_path)
    try:
        assert service.prepare_drill(
            checkpoint_id=checkpoint.checkpoint_id,
            execution_domain_id="self-runtime",
        ) == {"status": "FAILED", "reason_code": "DRILL_R2_EVIDENCE_REQUIRED"}
    finally:
        database.close()


def test_r3_cleanup_failure_never_produces_verified_evidence(tmp_path, monkeypatch):
    _target, database, _snapshots, service, checkpoint = _service(tmp_path)
    try:
        prepared = _r2_then_approved_drill(service, checkpoint.checkpoint_id)
        monkeypatch.setattr(service, "_cleanup_drill_root", lambda _root: False)
        assert service.run_drill(prepared["drill_id"]) == {"status": "FAILED", "reason_code": "DRILL_CLEANUP_FAILED"}
        assert "RECOVERY_DRILL_VERIFIED" not in _drill_events(database, checkpoint.checkpoint_id)
    finally:
        database.close()


def test_r3_adapter_rejects_symlinked_managed_root(tmp_path):
    _source, database, snapshots, service, checkpoint = _service(tmp_path)
    try:
        artifact, _reason = snapshots.load_recovery_v3_with_status(
            database.get_checkpoint(int(checkpoint.checkpoint_id))["snapshot_path"]
        )
        managed_root = tmp_path / "managed"
        managed_root.mkdir()
        linked_root = tmp_path / "linked"
        linked_root.symlink_to(managed_root, target_is_directory=True)
        result = service._adapters["self-runtime"].drill_restore(
            RecoveryRequest(
                operation=RecoveryOperation.DRILL_RESTORE,
                execution_domain_id="self-runtime",
                checkpoint_id=checkpoint.checkpoint_id,
                artifact=artifact,
                drill_root=linked_root,
            )
        )
        assert result.status is CapabilityStatus.ERROR
        assert result.reason_code == "DRILL_TARGET_UNAVAILABLE"
        assert not list(managed_root.iterdir())
    finally:
        database.close()


def test_r3_manifest_tampering_cannot_create_verified_r3(tmp_path, monkeypatch):
    _target, database, _snapshots, service, checkpoint = _service(tmp_path)
    try:
        prepared = _r2_then_approved_drill(service, checkpoint.checkpoint_id)
        original_load = service._snapshots.load_recovery_v3_with_status

        def tampered_load(path):
            artifact, reason = original_load(path)
            if artifact is not None:
                artifact["blobs"] = {key: value + b"tampered" for key, value in artifact["blobs"].items()}
            return artifact, reason

        monkeypatch.setattr(service._snapshots, "load_recovery_v3_with_status", tampered_load)
        assert service.run_drill(prepared["drill_id"])["status"] == "FAILED"
        assert "RECOVERY_DRILL_VERIFIED" not in _drill_events(database, checkpoint.checkpoint_id)
    finally:
        database.close()


def test_r3_validator_failure_cannot_create_verified_r3(tmp_path, monkeypatch):
    _target, database, _snapshots, service, checkpoint = _service(tmp_path)
    try:
        prepared = _r2_then_approved_drill(service, checkpoint.checkpoint_id)
        adapter = service._adapters["self-runtime"]
        original = adapter.drill_restore

        def invalid_validator(request):
            outcome = original(request)
            return RecoveryOperationResult(
                operation=outcome.operation,
                status=CapabilityStatus.ERROR,
                reason_code="DRILL_RECOVERY_VALIDATION_FAILED",
                execution_domain_id=outcome.execution_domain_id,
                checkpoint_id=outcome.checkpoint_id,
                manifest_digest=outcome.manifest_digest,
                details=outcome.details,
            )

        monkeypatch.setattr(adapter, "drill_restore", invalid_validator)
        assert service.run_drill(prepared["drill_id"]) == {"status": "FAILED", "reason_code": "DRILL_RECOVERY_VALIDATION_FAILED"}
        assert "RECOVERY_DRILL_VERIFIED" not in _drill_events(database, checkpoint.checkpoint_id)
    finally:
        database.close()


def test_r3_adapter_failure_cleans_managed_target(tmp_path, monkeypatch):
    _target, database, _snapshots, service, checkpoint = _service(tmp_path)
    try:
        prepared = _r2_then_approved_drill(service, checkpoint.checkpoint_id)
        adapter = service._adapters["self-runtime"]

        def failed_drill(request):
            assert request.drill_root is not None
            (request.drill_root / "partial").write_text("partial")
            return RecoveryOperationResult(
                operation=request.operation,
                status=CapabilityStatus.ERROR,
                reason_code="DRILL_PARTIAL_WRITE_FAILED",
                execution_domain_id=request.execution_domain_id,
                checkpoint_id=request.checkpoint_id,
                manifest_digest=checkpoint.manifest_digest,
            )

        monkeypatch.setattr(adapter, "drill_restore", failed_drill)
        assert service.run_drill(prepared["drill_id"]) == {"status": "FAILED", "reason_code": "DRILL_PARTIAL_WRITE_FAILED"}
        assert list((database.db_path.parent / ".agentguard-r3-drills").iterdir()) == []
        assert "RECOVERY_DRILL_VERIFIED" not in _drill_events(database, checkpoint.checkpoint_id)
    finally:
        database.close()


def test_r3_final_state_transition_failure_never_returns_verified(tmp_path, monkeypatch):
    _target, database, _snapshots, service, checkpoint = _service(tmp_path)
    try:
        prepared = _r2_then_approved_drill(service, checkpoint.checkpoint_id)
        original = service._run_managed_drill

        def interfere_with_final_transition(context):
            outcome = original(context)
            database._conn.execute(
                "UPDATE recovery_drills SET status = 'FAILED' WHERE drill_id = ?",
                (prepared["drill_id"],),
            )
            database._conn.commit()
            return outcome

        monkeypatch.setattr(service, "_run_managed_drill", interfere_with_final_transition)
        assert service.run_drill(prepared["drill_id"]) == {
            "status": "FAILED",
            "reason_code": "RECOVERY_PERSISTENCE_FAILED",
        }
        assert service.show_drill(prepared["drill_id"])["status"] == "FAILED"
        assert database._conn.execute(
            """SELECT COUNT(*) FROM evidence_ledger_events
               WHERE checkpoint_id = ? AND event_type = 'RECOVERY_DRILL_VERIFIED'""",
            (checkpoint.checkpoint_id,),
        ).fetchone()[0] == 0
    finally:
        database.close()
