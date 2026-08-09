"""P8 authenticated, caller-untrusted supervision action contract."""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentguard.evidence.ledger import EvidenceLedger, verify_ledger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.policy.models import Decision, PolicyDecision
from agentguard.recovery.contracts import RecoveryOperation, RecoveryRequest
from agentguard.storage.db import StateDB
from agentguard.supervision.service import SupervisionActionError, SupervisionService
from tests.test_api_r4_contract import _get, _running_api
from tests.test_recovery_drill import _service as _recovery_service


def _decision(decision: Decision, *, checkpoint: bool = False) -> PolicyDecision:
    return PolicyDecision(
        decision=decision,
        severity="MEDIUM",
        matched_rule_ids=("p8-action-test",),
        summary_code="P8_ACTION_TEST",
        evidence_refs=("evidence-p8-action",),
        uncertainties=(),
        required_checks=(),
        requires_checkpoint=checkpoint,
        requires_manual_approval=decision is Decision.REVIEW,
    )


def _create_session(
    db_path: Path,
    decision: Decision = Decision.REVIEW,
    *,
    checkpoint: bool = False,
) -> str:
    database = StateDB(db_path)
    database.connect()
    try:
        return SupervisionService(database).create(
            "bounded-p8-action",
            _decision(decision, checkpoint=checkpoint),
        ).supervision_session_id
    finally:
        database.close()


def _post(base_url: str, path: str, token: str | None, payload: object):
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if token is not None:
        request.add_header("X-Session-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _action_ref(base_url: str, token: str, session_id: str) -> str:
    status, body = _get(base_url, "/api/v1/supervision", token)
    assert status == 200
    item = next(
        item for item in body["items"]
        if item["supervision_session_id"] == session_id
    )
    action_ref = item["action_ref"]
    assert isinstance(action_ref, str) and len(action_ref) == 64
    return action_ref


def _event_count(db_path: Path, session_id: str, event_type: str) -> int:
    database = StateDB(db_path)
    database.connect()
    try:
        return database._conn.execute(
            """SELECT COUNT(*) FROM evidence_ledger_events
               WHERE supervision_session_id = ? AND event_type = ?""",
            (session_id, event_type),
        ).fetchone()[0]
    finally:
        database.close()


@pytest.mark.parametrize("action", ["approve-once", "reject"])
def test_action_requires_session_token(tmp_path, action):
    db_path = tmp_path / "state.db"
    session_id = _create_session(db_path)
    with _running_api(tmp_path, db_path) as (base_url, _token, _root):
        status, body = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/{action}",
            None,
            {"action_ref": "0" * 64},
        )
    assert status == 401
    assert body == {"error": "Unauthorized"}


def test_review_projection_issues_opaque_action_ref_only_while_waiting(tmp_path):
    db_path = tmp_path / "state.db"
    review_id = _create_session(db_path)
    allow_id = _create_session(db_path, Decision.ALLOW)
    with _running_api(tmp_path, db_path) as (base_url, token, _root):
        body = _get(base_url, "/api/v1/supervision", token)[1]
    items = {item["supervision_session_id"]: item for item in body["items"]}
    assert len(items[review_id]["action_ref"]) == 64
    assert items[allow_id]["action_ref"] is None
    assert "declared_intent_digest" not in json.dumps(items)


def test_approve_once_transitions_one_review_session_and_ledgers_once(tmp_path):
    db_path = tmp_path / "state.db"
    session_id = _create_session(db_path)
    with _running_api(tmp_path, db_path) as (base_url, token, _root):
        action_ref = _action_ref(base_url, token, session_id)
        status, body = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            {"action_ref": action_ref},
        )
        projected = _get(base_url, "/api/v1/supervision", token)[1]
    assert status == 200
    assert body == {
        "schema_version": "r4-p8-action-1",
        "action": "APPROVE_ONCE",
        "supervision_session_id": session_id,
        "status": "APPROVED",
        "reason_code": "SUPERVISION_APPROVED_ONCE",
        "consumed": True,
        "evidence_refs": body["evidence_refs"],
    }
    assert len(body["evidence_refs"]) == 1
    item = next(
        item for item in projected["items"]
        if item["supervision_session_id"] == session_id
    )
    assert item["status"] == "APPROVED"
    assert item["manual_approval"] is True
    assert item["action_ref"] is None
    assert _event_count(db_path, session_id, "USER_APPROVED") == 1


def test_approve_once_does_not_bypass_checkpoint_or_activate(tmp_path):
    db_path = tmp_path / "state.db"
    session_id = _create_session(db_path, checkpoint=True)
    with _running_api(tmp_path, db_path) as (base_url, token, _root):
        action_ref = _action_ref(base_url, token, session_id)
        status, _body = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            {"action_ref": action_ref},
        )
    assert status == 200
    database = StateDB(db_path)
    database.connect()
    try:
        row = database._conn.execute(
            """SELECT status, requires_checkpoint FROM supervision_sessions
               WHERE supervision_session_id = ?""",
            (session_id,),
        ).fetchone()
        assert row == ("APPROVED", 1)
        assert SupervisionService(database).activate(session_id).status == "APPROVED"
    finally:
        database.close()


def test_reject_is_terminal_and_preserves_review_policy_decision(tmp_path):
    db_path = tmp_path / "state.db"
    session_id = _create_session(db_path)
    with _running_api(tmp_path, db_path) as (base_url, token, _root):
        action_ref = _action_ref(base_url, token, session_id)
        status, body = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/reject",
            token,
            {"action_ref": action_ref},
        )
    assert status == 200
    assert body["status"] == "REJECTED"
    assert body["reason_code"] == "SUPERVISION_REJECTED"
    assert body["consumed"] is True
    assert len(body["evidence_refs"]) == 1
    assert _event_count(db_path, session_id, "USER_REJECTED") == 1
    database = StateDB(db_path)
    database.connect()
    try:
        assert database._conn.execute(
            """SELECT decision FROM supervision_sessions
               WHERE supervision_session_id = ?""",
            (session_id,),
        ).fetchone() == ("REVIEW",)
    finally:
        database.close()


@pytest.mark.parametrize("decision", [Decision.BLOCK, Decision.UNKNOWN, Decision.ALLOW])
def test_non_review_policy_cannot_manufacture_user_approval(tmp_path, decision):
    db_path = tmp_path / "state.db"
    session_id = _create_session(db_path, decision)
    with _running_api(tmp_path, db_path) as (base_url, token, _root):
        status, body = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            {"action_ref": "0" * 64},
        )
    assert status == 409
    assert body["reason_code"] == "SUPERVISION_POLICY_NOT_APPROVABLE"
    assert _event_count(db_path, session_id, "USER_APPROVED") == 0


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("approve-once", "approve-once"),
        ("approve-once", "reject"),
        ("reject", "approve-once"),
        ("reject", "reject"),
    ],
)
def test_action_replay_and_reordering_are_conflicts(tmp_path, first, second):
    db_path = tmp_path / "state.db"
    session_id = _create_session(db_path)
    with _running_api(tmp_path, db_path) as (base_url, token, _root):
        action_ref = _action_ref(base_url, token, session_id)
        first_status, _ = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/{first}",
            token,
            {"action_ref": action_ref},
        )
        second_status, body = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/{second}",
            token,
            {"action_ref": action_ref},
        )
    assert first_status == 200
    assert second_status == 409
    assert body["reason_code"] == "SUPERVISION_ACTION_REPLAYED"
    assert _event_count(db_path, session_id, "USER_APPROVED") <= 1
    assert _event_count(db_path, session_id, "USER_REJECTED") <= 1


def test_cross_session_action_ref_replay_is_rejected(tmp_path):
    db_path = tmp_path / "state.db"
    first_id = _create_session(db_path)
    second_id = _create_session(db_path)
    with _running_api(tmp_path, db_path) as (base_url, token, _root):
        first_ref = _action_ref(base_url, token, first_id)
        status, body = _post(
            base_url,
            f"/api/v1/supervision/{second_id}/approve-once",
            token,
            {"action_ref": first_ref},
        )
    assert status == 409
    assert body["reason_code"] == "SUPERVISION_ACTION_STALE"
    assert _event_count(db_path, second_id, "USER_APPROVED") == 0


def test_action_request_rejects_caller_authority_fields(tmp_path):
    db_path = tmp_path / "state.db"
    session_id = _create_session(db_path)
    with _running_api(tmp_path, db_path) as (base_url, token, _root):
        action_ref = _action_ref(base_url, token, session_id)
        status, body = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            {
                "action_ref": action_ref,
                "decision": "ALLOW",
                "status": "APPROVED",
                "trusted": True,
                "recovery_level": "R3",
            },
        )
    assert status == 422
    assert body["reason_code"] == "SUPERVISION_ACTION_REQUEST_INVALID"
    assert _event_count(db_path, session_id, "USER_APPROVED") == 0


def test_same_session_evidence_drift_invalidates_old_action_ref(tmp_path):
    db_path = tmp_path / "state.db"
    database = StateDB(db_path)
    database.connect()
    service = SupervisionService(database)
    try:
        session = service.create("bounded-p8-action", _decision(Decision.REVIEW))
        session_id = session.supervision_session_id
        stale_ref = service.action_ref(session_id)
        assert stale_ref is not None
        with database.transaction() as connection:
            EvidenceLedger().append(
                connection,
                EvidenceEvent(
                    schema_version=1,
                    event_id="p8-evidence-drift",
                    recorded_at=datetime.now(UTC),
                    observed_at=None,
                    event_family=EventFamily.SUPERVISION,
                    event_type=EventType.AI_ASSESSED,
                    source="ai-supervisor",
                    result="REVIEW",
                    execution_domain_id=None,
                    supervision_session_id=session_id,
                    transaction_id=None,
                    checkpoint_id=None,
                    subject_ref=session_id,
                    evidence_refs=(session_id,),
                    payload_safe={"decision": "REVIEW", "severity": "LOW"},
                ),
            )

        with pytest.raises(SupervisionActionError) as exc_info:
            service.approve_once(session_id, stale_ref)
        assert exc_info.value.reason_code == "SUPERVISION_ACTION_STALE"
        assert service._read(session_id).status == "AWAITING_APPROVAL"
        assert _event_count(db_path, session_id, "USER_APPROVED") == 0
    finally:
        database.close()


def test_untrusted_web_origin_cannot_read_session_bootstrap_token(tmp_path):
    with _running_api(tmp_path) as (base_url, _token, _root):
        request = urllib.request.Request(
            f"{base_url}/api/session",
            headers={"Origin": "https://attacker.invalid"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 200
            assert response.headers.get("Access-Control-Allow-Origin") is None


def test_unknown_session_returns_stable_404(tmp_path):
    missing = "session-00000000-0000-0000-0000-000000000000"
    with _running_api(tmp_path) as (base_url, token, _root):
        status, body = _post(
            base_url,
            f"/api/v1/supervision/{missing}/approve-once",
            token,
            {"action_ref": "0" * 64},
        )
    assert status == 404
    assert body["reason_code"] == "SUPERVISION_SESSION_NOT_FOUND"
    assert body["status"] == "UNCHANGED"
    assert body["consumed"] is False
    assert body["evidence_refs"] == []


@pytest.mark.parametrize("payload", [{}, {"action_ref": "invalid"}, []])
def test_malformed_action_body_has_stable_422(tmp_path, payload):
    db_path = tmp_path / "state.db"
    session_id = _create_session(db_path)
    with _running_api(tmp_path, db_path) as (base_url, token, _root):
        status, body = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            payload,
        )
    assert status == 422
    assert body["reason_code"] == "SUPERVISION_ACTION_REQUEST_INVALID"
    assert body["status"] == "UNCHANGED"
    assert _event_count(db_path, session_id, "USER_APPROVED") == 0


@pytest.mark.parametrize("column", ["curr_hash", "prev_hash", "payload_digest"])
def test_tampered_ledger_authority_fails_closed(tmp_path, column):
    db_path = tmp_path / "state.db"
    session_id = _create_session(db_path)
    database = StateDB(db_path)
    database.connect()
    try:
        action_ref = SupervisionService(database).action_ref(session_id)
        assert action_ref is not None
        database._conn.execute("DROP TRIGGER evidence_ledger_events_no_update")
        if column == "curr_hash":
            database._conn.execute(
                """UPDATE evidence_ledger_events SET curr_hash = ?
                   WHERE supervision_session_id = ? AND event_type = 'POLICY_EVALUATED'""",
                ("f" * 64, session_id),
            )
        elif column == "prev_hash":
            database._conn.execute(
                """UPDATE evidence_ledger_events SET prev_hash = ?
                   WHERE supervision_session_id = ? AND event_type = 'POLICY_EVALUATED'""",
                ("f" * 64, session_id),
            )
        else:
            database._conn.execute(
                """UPDATE evidence_ledger_events SET payload_digest = ?
                   WHERE supervision_session_id = ? AND event_type = 'POLICY_EVALUATED'""",
                ("f" * 64, session_id),
            )
        database._conn.commit()
    finally:
        database.close()

    with _running_api(tmp_path, db_path) as (base_url, token, _root):
        status, body = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            {"action_ref": action_ref},
        )
    assert status == 503
    assert body["reason_code"] == "SUPERVISION_LEDGER_INVALID"
    assert _event_count(db_path, session_id, "USER_APPROVED") == 0


def test_manually_inserted_session_without_policy_evidence_cannot_act(tmp_path):
    db_path = tmp_path / "state.db"
    database = StateDB(db_path)
    database.connect()
    session_id = "session-11111111-1111-1111-1111-111111111111"
    try:
        now = datetime.now(UTC).isoformat()
        database._conn.execute(
            """INSERT INTO supervision_sessions (
                   supervision_session_id, status, declared_intent_digest, decision,
                   requires_checkpoint, requires_manual_approval, created_at, updated_at
               ) VALUES (?, 'AWAITING_APPROVAL', ?, 'REVIEW', 0, 1, ?, ?)""",
            (session_id, "0" * 64, now, now),
        )
        database._conn.commit()
    finally:
        database.close()

    with _running_api(tmp_path, db_path) as (base_url, token, _root):
        status, body = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            {"action_ref": "0" * 64},
        )
    assert status == 503
    assert body["reason_code"] == "SUPERVISION_POLICY_EVIDENCE_INVALID"
    assert _event_count(db_path, session_id, "USER_APPROVED") == 0


def test_conflicting_policy_evidence_fails_closed(tmp_path):
    db_path = tmp_path / "state.db"
    session_id = _create_session(db_path)
    database = StateDB(db_path)
    database.connect()
    try:
        row = database._conn.execute(
            """SELECT payload_safe_json FROM evidence_ledger_events
               WHERE supervision_session_id = ? AND event_type = 'POLICY_EVALUATED'""",
            (session_id,),
        ).fetchone()
        with database.transaction() as connection:
            EvidenceLedger().append(
                connection,
                EvidenceEvent(
                    schema_version=1,
                    event_id="p8-conflicting-policy",
                    recorded_at=datetime.now(UTC),
                    observed_at=None,
                    event_family=EventFamily.SUPERVISION,
                    event_type=EventType.POLICY_EVALUATED,
                    source="supervision-service",
                    result="REVIEW",
                    execution_domain_id=None,
                    supervision_session_id=session_id,
                    transaction_id=None,
                    checkpoint_id=None,
                    subject_ref=session_id,
                    evidence_refs=(session_id,),
                    payload_safe=json.loads(row[0]),
                ),
            )
    finally:
        database.close()

    with _running_api(tmp_path, db_path) as (base_url, token, _root):
        status, body = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            {"action_ref": "0" * 64},
        )
    assert status == 503
    assert body["reason_code"] == "SUPERVISION_POLICY_EVIDENCE_INVALID"
    assert _event_count(db_path, session_id, "USER_APPROVED") == 0


def test_authoritative_row_binding_drift_fails_closed(tmp_path):
    db_path = tmp_path / "state.db"
    session_id = _create_session(db_path, checkpoint=True)
    database = StateDB(db_path)
    database.connect()
    try:
        action_ref = SupervisionService(database).action_ref(session_id)
        database._conn.execute(
            """UPDATE supervision_sessions SET requires_checkpoint = 0
               WHERE supervision_session_id = ?""",
            (session_id,),
        )
        database._conn.commit()
    finally:
        database.close()
    with _running_api(tmp_path, db_path) as (base_url, token, _root):
        status, body = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            {"action_ref": action_ref},
        )
    assert status == 503
    assert body["reason_code"] == "SUPERVISION_POLICY_EVIDENCE_INVALID"
    assert _event_count(db_path, session_id, "USER_APPROVED") == 0


@pytest.mark.parametrize(
    ("method_name", "event_type"),
    [("approve_once", "USER_APPROVED"), ("reject_once", "USER_REJECTED")],
)
def test_ledger_append_failure_rolls_back_action(
    tmp_path,
    monkeypatch,
    method_name,
    event_type,
):
    db_path = tmp_path / "state.db"
    database = StateDB(db_path)
    database.connect()
    service = SupervisionService(database)
    try:
        session = service.create("bounded-p8-action", _decision(Decision.REVIEW))
        session_id = session.supervision_session_id
        action_ref = service.action_ref(session_id)

        def fail_append(*_args, **_kwargs):
            raise sqlite3.IntegrityError("synthetic private database path")

        monkeypatch.setattr(service._ledger, "append", fail_append)
        with pytest.raises(SupervisionActionError) as exc_info:
            getattr(service, method_name)(session_id, action_ref)
        assert exc_info.value.reason_code == "SUPERVISION_AUTHORITY_UNAVAILABLE"
        assert service._read(session_id).status == "AWAITING_APPROVAL"
        assert _event_count(db_path, session_id, event_type) == 0
        assert verify_ledger(database._conn) == []
    finally:
        database.close()


@pytest.mark.parametrize(
    ("method_name", "event_type"),
    [("approve_once", "USER_APPROVED"), ("reject_once", "USER_REJECTED")],
)
def test_commit_failure_rolls_back_action(tmp_path, method_name, event_type):
    class CommitFailConnection(sqlite3.Connection):
        fail_next_commit = False

        def commit(self):
            if self.fail_next_commit:
                self.fail_next_commit = False
                raise sqlite3.OperationalError("synthetic private database path")
            return super().commit()

    db_path = tmp_path / "state.db"
    database = StateDB(db_path)
    database.connect()
    service = SupervisionService(database)
    session = service.create("bounded-p8-action", _decision(Decision.REVIEW))
    session_id = session.supervision_session_id
    action_ref = service.action_ref(session_id)
    database._conn.close()
    database._conn = sqlite3.connect(str(db_path), factory=CommitFailConnection)
    try:
        database._conn.fail_next_commit = True
        with pytest.raises(SupervisionActionError) as exc_info:
            getattr(service, method_name)(session_id, action_ref)
        assert exc_info.value.reason_code == "SUPERVISION_AUTHORITY_UNAVAILABLE"
        assert service._read(session_id).status == "AWAITING_APPROVAL"
        assert _event_count(db_path, session_id, event_type) == 0
        assert verify_ledger(database._conn) == []
    finally:
        database.close()


def test_recovery_bound_session_is_not_downgraded_by_generic_action(tmp_path):
    _target, database, _snapshots, recovery, checkpoint = _recovery_service(tmp_path)
    try:
        restored = recovery.test_restore(
            RecoveryRequest(
                operation=RecoveryOperation.TEST_RESTORE,
                execution_domain_id="self-runtime",
                checkpoint_id=checkpoint.checkpoint_id,
            )
        )
        assert restored.reason_code == "TEST_RESTORE_VERIFIED"
        prepared = recovery.prepare_drill(
            checkpoint_id=checkpoint.checkpoint_id,
            execution_domain_id="self-runtime",
        )
        session_id = database._conn.execute(
            """SELECT supervision_session_id FROM recovery_drill_bindings
               WHERE drill_id = ?""",
            (prepared["drill_id"],),
        ).fetchone()[0]
        assert SupervisionService(database).action_ref(session_id) is None
    finally:
        database.close()

    with _running_api(tmp_path, tmp_path / "state.db") as (base_url, token, _root):
        status, body = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            {"action_ref": "0" * 64},
        )
    assert status == 409
    assert body["reason_code"] == "SUPERVISION_RECOVERY_AUTHORIZATION_REQUIRED"
    assert _event_count(tmp_path / "state.db", session_id, "USER_APPROVED") == 0


def test_ai_allow_assessment_cannot_replace_local_review_decision(tmp_path):
    db_path = tmp_path / "state.db"
    database = StateDB(db_path)
    database.connect()
    service = SupervisionService(database)
    try:
        session = service.create("bounded-p8-action", _decision(Decision.REVIEW))
        session_id = session.supervision_session_id
        with database.transaction() as connection:
            EvidenceLedger().append(
                connection,
                EvidenceEvent(
                    schema_version=1,
                    event_id="p8-ai-cannot-authorize",
                    recorded_at=datetime.now(UTC),
                    observed_at=None,
                    event_family=EventFamily.SUPERVISION,
                    event_type=EventType.AI_ASSESSED,
                    source="ai-supervisor",
                    result="ALLOW",
                    execution_domain_id=None,
                    supervision_session_id=session_id,
                    transaction_id=None,
                    checkpoint_id=None,
                    subject_ref=session_id,
                    evidence_refs=(session_id,),
                    payload_safe={"decision": "ALLOW", "severity": "LOW"},
                ),
            )
        fresh_ref = service.action_ref(session_id)
        assert fresh_ref is not None
        assert service.approve_once(session_id, fresh_ref).status == "APPROVED"
        assert database._conn.execute(
            """SELECT decision FROM supervision_sessions
               WHERE supervision_session_id = ?""",
            (session_id,),
        ).fetchone() == ("REVIEW",)
    finally:
        database.close()


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("approve_once", "approve_once"),
        ("approve_once", "reject_once"),
        ("reject_once", "reject_once"),
    ],
)
def test_concurrent_actions_have_one_transition_and_one_event(tmp_path, first, second):
    db_path = tmp_path / "state.db"
    database = StateDB(db_path)
    database.connect()
    session = SupervisionService(database).create(
        "bounded-p8-action",
        _decision(Decision.REVIEW),
    )
    session_id = session.supervision_session_id
    action_ref = SupervisionService(database).action_ref(session_id)
    database.close()

    barrier = threading.Barrier(2)
    lock = threading.Lock()
    outcomes: list[tuple[str, str]] = []

    def invoke(method_name: str) -> None:
        worker_db = StateDB(db_path)
        worker_db.connect()
        try:
            barrier.wait(timeout=5)
            try:
                result = getattr(SupervisionService(worker_db), method_name)(
                    session_id,
                    action_ref,
                )
                outcome = ("success", result.status)
            except SupervisionActionError as exc:
                outcome = ("error", exc.reason_code)
            with lock:
                outcomes.append(outcome)
        finally:
            worker_db.close()

    threads = [threading.Thread(target=invoke, args=(name,)) for name in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert len([item for item in outcomes if item[0] == "success"]) == 1
    assert outcomes.count(("error", "SUPERVISION_ACTION_REPLAYED")) == 1
    database = StateDB(db_path)
    database.connect()
    try:
        row = database._conn.execute(
            """SELECT status FROM supervision_sessions
               WHERE supervision_session_id = ?""",
            (session_id,),
        ).fetchone()
        events = database._conn.execute(
            """SELECT event_type FROM evidence_ledger_events
               WHERE supervision_session_id = ?
                 AND event_type IN ('USER_APPROVED', 'USER_REJECTED')""",
            (session_id,),
        ).fetchall()
        assert row[0] in {"APPROVED", "REJECTED"}
        assert len(events) == 1
        assert verify_ledger(database._conn) == []
    finally:
        database.close()


def test_database_failure_response_does_not_leak_path_or_exception(tmp_path):
    blocked = tmp_path / "private-state.db"
    blocked.mkdir()
    session_id = "session-22222222-2222-2222-2222-222222222222"
    with _running_api(tmp_path, blocked) as (base_url, token, _root):
        status, body = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/reject",
            token,
            {"action_ref": "0" * 64},
        )
    encoded = json.dumps(body)
    assert status == 503
    assert body["reason_code"] == "SUPERVISION_AUTHORITY_UNAVAILABLE"
    assert str(tmp_path) not in encoded
    assert "private-state.db" not in encoded
    assert "sqlite" not in encoded.casefold()
