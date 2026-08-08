"""Durable Trusted Baseline lifecycle contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentguard.evidence.ledger import EvidenceLedger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.recovery.contracts import RecoveryOperation, RecoveryRequest
from agentguard.recovery.coverage import RecoveryCoverageService
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


def test_forged_trusted_baseline_row_never_projects_trusted(tmp_path):
    target, database, snapshots, service, checkpoint = _verified_r3(tmp_path)
    try:
        database._conn.execute(
            """INSERT INTO trusted_baselines
               (baseline_id, checkpoint_id, execution_domain_id, manifest_digest,
                target_refs_digest, recovery_evidence_digest, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "forged-baseline",
                checkpoint.checkpoint_id,
                "self-runtime",
                checkpoint.manifest_digest,
                "forged-target-refs",
                "forged-evidence",
                datetime.now(UTC).isoformat(),
            ),
        )
        database._conn.commit()

        facts = RecoveryCoverageService(database, snapshots).compute(
            checkpoint_id=checkpoint.checkpoint_id,
            target_refs=(str(target),),
            execution_domain_id="self-runtime",
        )

        assert facts.r3_verified is True
        assert facts.trusted_baseline_status == "NONE"
        assert facts.trusted_baseline_id is None
        assert service.show_trusted_baseline("forged-baseline") == {
            "status": "FAILED",
            "reason_code": "TRUSTED_BASELINE_AUTHORITY_INVALID",
        }
    finally:
        database.close()


def test_multiple_valid_r3_drills_preserve_authoritative_r3(tmp_path):
    target, database, snapshots, service, checkpoint = _verified_r3(tmp_path)
    try:
        second = service.prepare_drill(
            checkpoint_id=checkpoint.checkpoint_id,
            execution_domain_id="self-runtime",
        )
        assert service.approve_drill(second["drill_id"])["status"] == "APPROVED"
        assert service.run_drill(second["drill_id"])["status"] == "VERIFIED_R3"

        facts = RecoveryCoverageService(database, snapshots).compute(
            checkpoint_id=checkpoint.checkpoint_id,
            target_refs=(str(target),),
            execution_domain_id="self-runtime",
        )
        assert facts.r3_verified is True
    finally:
        database.close()


def test_inconsistent_duplicate_r3_event_invalidates_service_truth(tmp_path):
    _target, database, _snapshots, service, checkpoint = _verified_r3(tmp_path)
    try:
        row = database._conn.execute(
            """SELECT d.drill_id, d.binding_digest, b.supervision_session_id,
                      d.target_refs_digest
               FROM recovery_drills d
               JOIN recovery_drill_bindings b USING (drill_id)
               WHERE d.checkpoint_id = ?""",
            (checkpoint.checkpoint_id,),
        ).fetchone()
        EvidenceLedger().append(
            database._conn,
            EvidenceEvent(
                schema_version=1,
                event_id="conflicting-r3-completed",
                recorded_at=datetime.now(UTC),
                observed_at=None,
                event_family=EventFamily.RECOVERY,
                event_type=EventType.RECOVERY_DRILL_COMPLETED,
                source="test",
                result="FAILED",
                execution_domain_id="self-runtime",
                supervision_session_id=row[2],
                transaction_id=None,
                checkpoint_id=checkpoint.checkpoint_id,
                subject_ref=f"manifest:{checkpoint.manifest_digest}",
                evidence_refs=(),
                payload_safe={
                    "drill_id": row[0],
                    "binding_digest": row[1],
                    "manifest_digest": checkpoint.manifest_digest,
                    "target_refs_digest": row[3],
                },
            ),
        )
        database._conn.commit()

        assert service.create_trusted_baseline(
            checkpoint_id=checkpoint.checkpoint_id,
            execution_domain_id="self-runtime",
        ) == {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_R3_REQUIRED"}
        assert service.show_drill(row[0]) == {
            "status": "FAILED",
            "reason_code": "RECOVERY_DRILL_AUTHORITY_INVALID",
        }
    finally:
        database.close()


def test_confirmed_baseline_projects_trusted_after_restart(tmp_path):
    target, database, snapshots, service, checkpoint = _verified_r3(tmp_path)
    try:
        candidate = service.create_trusted_baseline(
            checkpoint_id=checkpoint.checkpoint_id,
            execution_domain_id="self-runtime",
        )
        authorization = service.approve_trusted_baseline(candidate["candidate_id"])
        baseline = service.confirm_trusted_baseline(
            candidate["candidate_id"], authorization["authorization_id"], authorization["nonce"]
        )
        assert baseline["status"] == "TRUSTED"

        facts = RecoveryCoverageService(database, snapshots).compute(
            checkpoint_id=checkpoint.checkpoint_id,
            target_refs=(str(target),),
            execution_domain_id="self-runtime",
        )
        assert facts.trusted_baseline_status == "TRUSTED"
        assert facts.trusted_baseline_id == baseline["baseline_id"]
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


def _approved_baseline(tmp_path):
    target, database, snapshots, service, checkpoint = _verified_r3(tmp_path)
    candidate = service.create_trusted_baseline(
        checkpoint_id=checkpoint.checkpoint_id,
        execution_domain_id="self-runtime",
    )
    authorization = service.approve_trusted_baseline(candidate["candidate_id"])
    return target, database, snapshots, service, checkpoint, candidate, authorization


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("subject_id", "other-candidate"),
        ("checkpoint_id", "other-checkpoint"),
        ("execution_domain_id", "other-domain"),
        ("manifest_digest", "other-manifest"),
        ("target_refs_digest", "other-targets"),
        ("drill_fingerprint", "other-fingerprint"),
        ("policy_version", "other-policy"),
        ("supervision_session_id", "other-session"),
        ("operation_kind", "SELF_RUNTIME_R3_DRILL"),
    ],
)
def test_authorization_context_mismatch_cannot_confirm(
    tmp_path, column, value,
):
    _target, database, _snapshots, service, _checkpoint, candidate, authorization = _approved_baseline(tmp_path)
    try:
        database._conn.execute(
            f"UPDATE recovery_authorizations SET {column} = ? WHERE authorization_id = ?",
            (value, authorization["authorization_id"]),
        )
        database._conn.commit()
        result = service.confirm_trusted_baseline(
            candidate["candidate_id"], authorization["authorization_id"], authorization["nonce"]
        )
        assert result["status"] == "FAILED"
        assert result["reason_code"] in {
            "TRUSTED_BASELINE_CONFIRMATION_INVALID",
            "TRUSTED_BASELINE_R3_REQUIRED",
        }
        assert database._conn.execute(
            "SELECT COUNT(*) FROM trusted_baselines"
        ).fetchone()[0] == 0
    finally:
        database.close()


def test_expired_authorization_cannot_confirm_or_consume(tmp_path):
    _target, database, _snapshots, service, _checkpoint, candidate, authorization = _approved_baseline(tmp_path)
    try:
        database._conn.execute(
            "UPDATE recovery_authorizations SET expires_at = ? WHERE authorization_id = ?",
            ("2000-01-01T00:00:00+00:00", authorization["authorization_id"]),
        )
        database._conn.commit()
        result = service.confirm_trusted_baseline(
            candidate["candidate_id"], authorization["authorization_id"], authorization["nonce"]
        )
        assert result == {
            "status": "FAILED",
            "reason_code": "TRUSTED_BASELINE_CONFIRMATION_EXPIRED",
        }
        assert database._conn.execute(
            "SELECT status FROM trusted_baseline_candidates WHERE candidate_id = ?",
            (candidate["candidate_id"],),
        ).fetchone()[0] == "CANDIDATE"
    finally:
        database.close()


def test_authorization_replay_after_restart_is_rejected(tmp_path):
    _target, database, _snapshots, service, _checkpoint, candidate, authorization = _approved_baseline(tmp_path)
    first = service.confirm_trusted_baseline(
        candidate["candidate_id"], authorization["authorization_id"], authorization["nonce"]
    )
    assert first["status"] == "TRUSTED"
    database.close()
    restarted = _service(tmp_path)[3]
    try:
        replay = restarted.confirm_trusted_baseline(
            candidate["candidate_id"], authorization["authorization_id"], authorization["nonce"]
        )
        assert replay == {
            "status": "FAILED",
            "reason_code": "TRUSTED_BASELINE_CONFIRMATION_INVALID",
        }
    finally:
        restarted._database.close()


def test_retired_baseline_loses_active_projection_after_restart(tmp_path):
    target, database, snapshots, service, checkpoint, candidate, authorization = _approved_baseline(tmp_path)
    baseline = service.confirm_trusted_baseline(
        candidate["candidate_id"], authorization["authorization_id"], authorization["nonce"]
    )
    assert baseline["status"] == "TRUSTED"
    assert service.retire_trusted_baseline(baseline["baseline_id"], "OPERATOR_RETIRED")["status"] == "RETIRED"
    facts = RecoveryCoverageService(database, snapshots).compute(
        checkpoint_id=checkpoint.checkpoint_id,
        target_refs=(str(target),),
        execution_domain_id="self-runtime",
    )
    assert facts.trusted_baseline_status == "RETIRED"
    database.close()
    restarted = _service(tmp_path)[3]
    try:
        facts = RecoveryCoverageService(restarted._database, snapshots).compute(
            checkpoint_id=checkpoint.checkpoint_id,
            target_refs=(str(target),),
            execution_domain_id="self-runtime",
        )
        assert facts.trusted_baseline_status == "RETIRED"
    finally:
        restarted._database.close()


def test_baseline_confirm_ledger_failure_rolls_back_all_consumption(tmp_path, monkeypatch):
    _target, database, _snapshots, service, _checkpoint, candidate, authorization = _approved_baseline(tmp_path)
    original_append = service._ledger.append

    def fail_append(*args, **kwargs):
        raise RuntimeError("injected ledger failure")

    monkeypatch.setattr(service._ledger, "append", fail_append)
    try:
        result = service.confirm_trusted_baseline(
            candidate["candidate_id"], authorization["authorization_id"], authorization["nonce"]
        )
        assert result == {"status": "FAILED", "reason_code": "RECOVERY_PERSISTENCE_FAILED"}
        assert database._conn.execute(
            "SELECT COUNT(*) FROM trusted_baselines"
        ).fetchone()[0] == 0
        assert database._conn.execute(
            "SELECT status FROM trusted_baseline_candidates WHERE candidate_id = ?",
            (candidate["candidate_id"],),
        ).fetchone()[0] == "CANDIDATE"
        assert database._conn.execute(
            "SELECT consumed_at FROM recovery_authorizations WHERE authorization_id = ?",
            (authorization["authorization_id"],),
        ).fetchone()[0] is None
    finally:
        monkeypatch.setattr(service._ledger, "append", original_append)
        database.close()


def test_baseline_unique_constraint_failure_rolls_back_consumption(tmp_path):
    _target, database, _snapshots, service, _checkpoint, candidate, authorization = _approved_baseline(tmp_path)
    row = database._conn.execute(
        """SELECT checkpoint_id, execution_domain_id, manifest_digest, target_refs_digest,
                  recovery_evidence_digest
           FROM trusted_baseline_candidates WHERE candidate_id = ?""",
        (candidate["candidate_id"],),
    ).fetchone()
    database._conn.execute(
        """INSERT INTO trusted_baselines
           (baseline_id, checkpoint_id, execution_domain_id, manifest_digest,
            target_refs_digest, recovery_evidence_digest, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("forged-conflict", *row, datetime.now(UTC).isoformat()),
    )
    database._conn.commit()
    try:
        result = service.confirm_trusted_baseline(
            candidate["candidate_id"], authorization["authorization_id"], authorization["nonce"]
        )
        assert result == {"status": "FAILED", "reason_code": "RECOVERY_PERSISTENCE_FAILED"}
        assert database._conn.execute(
            "SELECT status FROM trusted_baseline_candidates WHERE candidate_id = ?",
            (candidate["candidate_id"],),
        ).fetchone()[0] == "CANDIDATE"
        assert database._conn.execute(
            "SELECT consumed_at FROM recovery_authorizations WHERE authorization_id = ?",
            (authorization["authorization_id"],),
        ).fetchone()[0] is None
    finally:
        database.close()


def test_post_trust_adverse_evidence_removes_active_trust(tmp_path):
    target, database, snapshots, service, checkpoint, candidate, authorization = _approved_baseline(tmp_path)
    baseline = service.confirm_trusted_baseline(
        candidate["candidate_id"], authorization["authorization_id"], authorization["nonce"]
    )
    assert baseline["status"] == "TRUSTED"
    try:
        EvidenceLedger().append(
            database._conn,
            EvidenceEvent(
                schema_version=1,
                event_id="scope-drift-after-trust",
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
        facts = RecoveryCoverageService(database, snapshots).compute(
            checkpoint_id=checkpoint.checkpoint_id,
            target_refs=(str(target),),
            execution_domain_id="self-runtime",
        )
        assert facts.r3_verified is False
        assert facts.trusted_baseline_status != "TRUSTED"
        assert facts.trusted_baseline_id is None
    finally:
        database.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "baseline_supervision_decision",
        "authorization_binding",
        "candidate_binding",
    ],
)
def test_post_trust_authority_chain_drift_removes_active_trust(tmp_path, mutation):
    target, database, snapshots, service, checkpoint, candidate, authorization = _approved_baseline(tmp_path)
    baseline = service.confirm_trusted_baseline(
        candidate["candidate_id"], authorization["authorization_id"], authorization["nonce"]
    )
    assert baseline["status"] == "TRUSTED"
    try:
        if mutation == "baseline_supervision_decision":
            database._conn.execute(
                """UPDATE supervision_sessions SET decision = 'UNKNOWN'
                   WHERE supervision_session_id = (
                     SELECT supervision_session_id FROM recovery_authorizations
                     WHERE authorization_id = ?
                   )""",
                (authorization["authorization_id"],),
            )
        elif mutation == "authorization_binding":
            database._conn.execute(
                "UPDATE recovery_authorizations SET binding_digest = 'forged' WHERE authorization_id = ?",
                (authorization["authorization_id"],),
            )
        else:
            database._conn.execute(
                "UPDATE trusted_baseline_candidate_bindings SET binding_digest = 'forged' WHERE candidate_id = ?",
                (candidate["candidate_id"],),
            )
        database._conn.commit()

        facts = RecoveryCoverageService(database, snapshots).compute(
            checkpoint_id=checkpoint.checkpoint_id,
            target_refs=(str(target),),
            execution_domain_id="self-runtime",
        )
        assert facts.trusted_baseline_status == "NONE"
        assert facts.trusted_baseline_id is None
        assert service.show_trusted_baseline(baseline["baseline_id"]) == {
            "status": "FAILED",
            "reason_code": "TRUSTED_BASELINE_AUTHORITY_INVALID",
        }
    finally:
        database.close()
