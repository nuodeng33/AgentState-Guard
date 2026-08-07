"""Durable Trusted Baseline lifecycle contracts."""

from __future__ import annotations

from datetime import UTC, datetime

from agentguard.evidence.ledger import EvidenceLedger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.recovery.contracts import RecoveryOperation, RecoveryRequest
from tests.test_recovery_drill import _r2_then_approved_drill, _service


def _verified_r3(tmp_path):
    target, database, snapshots, service, checkpoint = _service(tmp_path)
    _r2_then_approved_drill(service, checkpoint.checkpoint_id)
    drill = database._conn.execute(
        "SELECT drill_id FROM recovery_drills WHERE checkpoint_id = ?",
        (checkpoint.checkpoint_id,),
    ).fetchone()[0]
    result = service.run_drill(drill)
    assert result["status"] == "VERIFIED_R3"
    return target, database, snapshots, service, checkpoint


def test_trusted_baseline_requires_verified_r3_and_explicit_bound_confirmation(tmp_path):
    _target, database, _snapshots, service, checkpoint = _service(tmp_path)
    try:
        blocked = service.create_trusted_baseline(
            checkpoint_id=checkpoint.checkpoint_id,
            execution_domain_id="self-runtime",
        )
        assert blocked["reason_code"] == "TRUSTED_BASELINE_R3_REQUIRED"

        service.test_restore(
            RecoveryRequest(
                operation=RecoveryOperation.TEST_RESTORE,
                execution_domain_id="self-runtime",
                checkpoint_id=checkpoint.checkpoint_id,
            )
        )
        drill = service.prepare_drill(
            checkpoint_id=checkpoint.checkpoint_id,
            execution_domain_id="self-runtime",
        )
        service.approve_drill(drill["drill_id"])
        assert service.run_drill(drill["drill_id"])["status"] == "VERIFIED_R3"

        pending = service.create_trusted_baseline(
            checkpoint_id=checkpoint.checkpoint_id,
            execution_domain_id="self-runtime",
        )
        assert pending["status"] == "CANDIDATE"
        assert pending["reason_code"] == "TRUSTED_BASELINE_CONFIRMATION_REQUIRED"
        assert service.confirm_trusted_baseline(pending["candidate_id"])["reason_code"] == "TRUSTED_BASELINE_CONFIRMATION_INVALID"
        authorization = service.approve_trusted_baseline(pending["candidate_id"])
        assert authorization["status"] == "APPROVED"
        confirmed = service.confirm_trusted_baseline(
            pending["candidate_id"],
            authorization["authorization_id"],
            authorization["nonce"],
        )
        assert confirmed["status"] == "TRUSTED"
        assert service.retire_trusted_baseline(confirmed["baseline_id"], "OPERATOR_RETIRED")["status"] == "RETIRED"
    finally:
        database.close()


def test_baseline_candidate_survives_service_restart(tmp_path):
    _target, database, _snapshots, service, checkpoint = _verified_r3(tmp_path)
    pending = service.create_trusted_baseline(
        checkpoint_id=checkpoint.checkpoint_id,
        execution_domain_id="self-runtime",
    )
    candidate_id = pending["candidate_id"]
    database.close()
    restarted = _service(tmp_path)[3]
    try:
        shown = restarted.show_trusted_baseline(candidate_id)
        assert shown["status"] == "CANDIDATE"
        authorization = restarted.approve_trusted_baseline(candidate_id)
        confirmed = restarted.confirm_trusted_baseline(
            candidate_id,
            authorization["authorization_id"],
            authorization["nonce"],
        )
        assert confirmed["status"] == "TRUSTED"
    finally:
        restarted._database.close()


def test_baseline_confirmation_is_one_time_and_bound_to_candidate(tmp_path):
    _target, database, _snapshots, service, checkpoint = _verified_r3(tmp_path)
    try:
        pending = service.create_trusted_baseline(
            checkpoint_id=checkpoint.checkpoint_id,
            execution_domain_id="self-runtime",
        )
        authorization = service.approve_trusted_baseline(pending["candidate_id"])
        assert service.confirm_trusted_baseline(
            pending["candidate_id"], authorization["authorization_id"], authorization["nonce"]
        )["status"] == "TRUSTED"
        assert service.confirm_trusted_baseline(
            pending["candidate_id"], authorization["authorization_id"], authorization["nonce"]
        )["reason_code"] == "TRUSTED_BASELINE_CONFIRMATION_INVALID"
    finally:
        database.close()


def test_trusted_baseline_rejects_r3_with_non_approved_supervision_session(tmp_path):
    _target, database, _snapshots, service, checkpoint = _verified_r3(tmp_path)
    try:
        database._conn.execute(
            """UPDATE supervision_sessions SET decision = 'UNKNOWN'
               WHERE supervision_session_id = (
                 SELECT supervision_session_id FROM recovery_drill_bindings
                 WHERE drill_id = (SELECT drill_id FROM recovery_drills WHERE checkpoint_id = ?)
               )""",
            (checkpoint.checkpoint_id,),
        )
        database._conn.commit()
        assert service.create_trusted_baseline(
            checkpoint_id=checkpoint.checkpoint_id,
            execution_domain_id="self-runtime",
        ) == {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_R3_REQUIRED"}
    finally:
        database.close()


def test_trusted_baseline_rejects_scope_drift_for_r3_context(tmp_path):
    _target, database, _snapshots, service, checkpoint = _verified_r3(tmp_path)
    try:
        EvidenceLedger().append(
            database._conn,
            EvidenceEvent(
                schema_version=1,
                event_id="scope-drift-after-r3",
                recorded_at=datetime.now(UTC),
                observed_at=None,
                event_family=EventFamily.CHANGE,
                event_type=EventType.SCOPE_DRIFT,
                source="test",
                result="REVIEW",
                execution_domain_id="self-runtime",
                supervision_session_id=None,
                transaction_id=None,
                checkpoint_id=checkpoint.checkpoint_id,
                subject_ref=f"manifest:{checkpoint.manifest_digest}",
                evidence_refs=(),
                payload_safe={"reason_code": "SCOPE_DRIFT"},
            ),
        )
        database._conn.commit()
        assert service.create_trusted_baseline(
            checkpoint_id=checkpoint.checkpoint_id,
            execution_domain_id="self-runtime",
        ) == {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_R3_REQUIRED"}
    finally:
        database.close()
