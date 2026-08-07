"""R3 controlled SelfRuntime recovery-drill contracts."""

from __future__ import annotations

from pathlib import Path

from agentguard.discovery.domains import SelfRuntimeAdapter
from agentguard.recovery.contracts import RecoveryOperation, RecoveryRequest
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

        approval = service.approve_drill(prepared["drill_id"])
        assert approval["binding_digest"] == prepared["binding_digest"]

        result = service.run_drill(prepared["drill_id"])
        assert result["status"] == "VERIFIED_R3"
        assert result["verified_targets"] == 1
        assert service.run_drill(prepared["drill_id"])["reason_code"] == "DRILL_APPROVAL_MISSING_OR_CONSUMED"
    finally:
        database.close()
