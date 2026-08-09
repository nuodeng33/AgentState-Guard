"""P8 authenticated, caller-untrusted supervision action contract."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agentguard.policy.models import Decision, PolicyDecision
from agentguard.storage.db import StateDB
from agentguard.supervision.service import SupervisionService
from tests.test_api_r4_contract import _get, _running_api


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
