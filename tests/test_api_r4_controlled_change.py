"""Production FastAPI wiring for the bounded R4 controlled configuration change."""

from __future__ import annotations

import json
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from agentguard import cli
from agentguard.ai.supervisor import AIAssessment
from agentguard.api.r4_controlled_change import ControlledChangeError
from agentguard.api.server import create_app
from agentguard.discovery import (
    CapabilityStatus,
    DiscoverySnapshot,
    ProbeEvidence,
    RuntimeDescriptor,
)
from agentguard.evidence.ledger import verify_ledger
from agentguard.storage.db import StateDB
from agentguard.supervision.service import SupervisionService, SupervisionSession
from tests.test_api_r4_actions import _action_ref
from tests.test_api_r4_contract import _get


class _ReviewProvider:
    model = "r4-review-test"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def assess(self, authority_package: dict) -> AIAssessment:
        self.calls.append(authority_package)
        return AIAssessment(
            decision="ALLOW",
            severity="LOW",
            summary="Unsafe upgrade attempt must be clamped.",
            evidence_refs=tuple(authority_package["evidence_refs"]),
            uncertainties=(),
            required_checks=(),
            requires_checkpoint=False,
            requires_manual_approval=False,
        )


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextmanager
def _running_change_api(
    root: Path,
    provider=None,
    *,
    create_target: bool = True,
    discovery_service=None,
):
    config_dir = root / "config"
    target = config_dir / "agentguard.toml"
    if create_target:
        config_dir.mkdir(parents=True, exist_ok=True)
        target.write_text("safe = false\n", encoding="utf-8")
    db_path = root / "state.db"
    port = _free_port()
    app = create_app(
        state_db_path=db_path,
        config={"base_dir": str(root)},
        assessment_provider=provider,
        discovery_service=discovery_service,
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    base_url = f"http://127.0.0.1:{port}"
    token = _get(base_url, "/api/session")[1]["token"]
    try:
        yield base_url, token, db_path, target
    finally:
        server.should_exit = True
        thread.join(timeout=5)


class _RuntimeOnlyDiscovery:
    def discover(self) -> DiscoverySnapshot:
        observed_at = datetime.now(UTC)
        evidence = ProbeEvidence(
            evidence_id=f"runtime-only-{observed_at.timestamp()}",
            collector="controlled-change-test",
            source="local",
            observed_at=observed_at,
            fact_type="runtime.metadata",
            value={"runtime_kind": "SELF_RUNTIME"},
            status=CapabilityStatus.AVAILABLE,
            sanitized=True,
        )
        return DiscoverySnapshot(
            snapshot_id=f"runtime-only-{observed_at.timestamp()}",
            observed_at=observed_at,
            runtimes=(
                RuntimeDescriptor(
                    runtime_id="self-runtime",
                    runtime_type="SELF_RUNTIME",
                    domain_id="windows-current",
                    status=CapabilityStatus.AVAILABLE,
                    evidence_ids=(evidence.evidence_id,),
                    confidence=0.95,
                ),
            ),
            evidence=(evidence,),
            status=CapabilityStatus.AVAILABLE,
        )


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
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _events(db_path: Path, session_id: str | None = None) -> list[str]:
    database = StateDB(db_path)
    database.connect()
    try:
        if session_id is None:
            rows = database._conn.execute(
                "SELECT event_type FROM evidence_ledger_events ORDER BY sequence"
            ).fetchall()
        else:
            rows = database._conn.execute(
                """SELECT event_type FROM evidence_ledger_events
                   WHERE supervision_session_id = ? ORDER BY sequence""",
                (session_id,),
            ).fetchall()
        assert verify_ledger(database._conn) == []
        return [row[0] for row in rows]
    finally:
        database.close()


def test_review_prepare_uses_ai_advisory_then_p8_approval_and_authoritative_apply(tmp_path):
    provider = _ReviewProvider()
    content = "safe = true\n"
    with _running_change_api(tmp_path, provider) as (base_url, token, db_path, target):
        status, prepared = _post(
            base_url,
            "/api/v1/supervision/changes",
            token,
            {"content": content},
        )

        assert status == 200
        session_id = prepared["supervision_session_id"]
        assert prepared == {
            "schema_version": "r4-p9-controlled-change-1",
            "supervision_session_id": session_id,
            "status": "AWAITING_APPROVAL",
            "decision": "REVIEW",
            "reason_code": "CONTROLLED_CHANGE_PREPARED",
            "requires_manual_approval": True,
            "requires_checkpoint": True,
            "ai_advisory": "REVIEW",
            "action_ref": prepared["action_ref"],
        }
        assert len(prepared["action_ref"]) == 64
        assert len(provider.calls) == 1
        assert provider.calls[0]["policy_decision"] == "REVIEW"
        assert target.read_text(encoding="utf-8") == "safe = false\n"
        assert {"RUNTIME_DETECTED", "CONTROLLED_TARGET_BOUND"}.issubset(
            _events(db_path)
        )
        assert "AI_ASSESSED" in _events(db_path, session_id)

        action_ref = _action_ref(base_url, token, session_id)
        approved_status, approved = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            {"action_ref": action_ref},
        )
        assert approved_status == 200
        assert approved["status"] == "APPROVED"
        assert target.read_text(encoding="utf-8") == "safe = false\n"

        applied_status, applied = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/apply",
            token,
            {"content": content},
        )

    assert applied_status == 200
    assert applied["status"] == "COMPLETED"
    assert applied["reason_code"] == "CONTROLLED_CHANGE_COMPLETED"
    assert applied["changed"] is True
    assert applied["verification"] == "PASS"
    assert "checkpoint_id" in applied
    assert str(tmp_path) not in json.dumps(applied)
    assert content not in json.dumps(applied)
    assert target.read_text(encoding="utf-8") == content
    session_events = _events(db_path, session_id)
    assert session_events == [
        "SESSION_CREATED",
        "POLICY_EVALUATED",
        "AI_ASSESSED",
        "USER_APPROVED",
        "CHECKPOINT_CREATED",
        "MANIFEST_VERIFIED",
        "SESSION_ACTIVATED",
        "OBSERVED_CHANGE",
        "SESSION_COMPLETED",
    ]


def test_fresh_install_controlled_change_uses_product_target_authority_without_fake_agent(
    tmp_path,
):
    initial_content = None
    requested_content = "[checks]\nport = 3002\n"
    with _running_change_api(
        tmp_path,
        create_target=False,
        discovery_service=_RuntimeOnlyDiscovery(),
    ) as (base_url, token, db_path, target):
        initial_content = target.read_text(encoding="utf-8")
        prepared_status, prepared = _post(
            base_url,
            "/api/v1/supervision/changes",
            token,
            {"content": requested_content},
        )
        assert prepared_status == 200
        session_id = prepared["supervision_session_id"]
        approved_status, approved = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            {"action_ref": prepared["action_ref"]},
        )
        applied_status, applied = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/apply",
            token,
            {"content": requested_content},
        )

    assert initial_content is not None
    assert "config_file = \"config/agentguard.toml\"" in initial_content
    assert approved_status == 200
    assert approved["status"] == "APPROVED"
    assert applied_status == 200
    assert applied["status"] == "COMPLETED"
    assert applied["verification"] == "PASS"
    assert target.read_text(encoding="utf-8") == requested_content
    events = _events(db_path)
    assert "CONTROLLED_TARGET_BOUND" in events
    assert "AGENT_DETECTED" not in events
    assert "WORKSPACE_LINKED" not in events


def test_ai_unavailable_keeps_review_and_does_not_add_inaccurate_event(tmp_path):
    with _running_change_api(tmp_path) as (base_url, token, db_path, _target):
        status, prepared = _post(
            base_url,
            "/api/v1/supervision/changes",
            token,
            {"content": "safe = true\n"},
        )

    assert status == 200
    assert prepared["decision"] == "REVIEW"
    assert prepared["status"] == "AWAITING_APPROVAL"
    assert prepared["requires_manual_approval"] is True
    assert prepared["requires_checkpoint"] is True
    assert prepared["ai_advisory"] == "UNAVAILABLE"
    assert "AI_ASSESSED" not in _events(
        db_path, prepared["supervision_session_id"]
    )


def test_approved_content_cannot_drift_and_caller_cannot_supply_target(tmp_path):
    approved_content = "safe = true\n"
    with _running_change_api(tmp_path) as (base_url, token, db_path, target):
        status, prepared = _post(
            base_url,
            "/api/v1/supervision/changes",
            token,
            {"content": approved_content},
        )
        assert status == 200
        session_id = prepared["supervision_session_id"]
        action_ref = _action_ref(base_url, token, session_id)
        assert _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            {"action_ref": action_ref},
        )[0] == 200

        drift_status, drift = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/apply",
            token,
            {"content": "safe = 'changed-after-approval'\n"},
        )
        target_status, target_error = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/apply",
            token,
            {"content": approved_content, "target_path": str(tmp_path / "other.toml")},
        )

    assert drift_status == 409
    assert drift["reason_code"] == "CONTROLLED_CHANGE_INTENT_MISMATCH"
    assert target_status == 422
    assert target_error["reason_code"] == "CONTROLLED_CHANGE_REQUEST_INVALID"
    assert target.read_text(encoding="utf-8") == "safe = false\n"
    assert "CHECKPOINT_CREATED" not in _events(db_path, session_id)


def test_second_prepare_cannot_stale_an_outstanding_approved_change(tmp_path):
    content = "safe = true\n"
    with _running_change_api(tmp_path) as (base_url, token, _db_path, target):
        first_status, first = _post(
            base_url,
            "/api/v1/supervision/changes",
            token,
            {"content": content},
        )
        assert first_status == 200
        session_id = first["supervision_session_id"]
        action_ref = _action_ref(base_url, token, session_id)
        assert _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            {"action_ref": action_ref},
        )[0] == 200

        second_status, second = _post(
            base_url,
            "/api/v1/supervision/changes",
            token,
            {"content": "safe = 'second'\n"},
        )
        applied_status, applied = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/apply",
            token,
            {"content": content},
        )

    assert second_status == 200
    assert second["reason_code"] == "CONTROLLED_CHANGE_PREPARED"
    assert applied_status == 200
    assert applied["status"] == "COMPLETED"
    assert target.read_text(encoding="utf-8") == content


def test_apply_retry_reuses_the_first_bound_checkpoint(tmp_path, monkeypatch):
    content = "safe = true\n"
    original_activate = SupervisionService.activate
    calls = 0

    def interrupt_once(self, session_id, checkpoint_id=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SupervisionSession(session_id, "APPROVED")
        return original_activate(self, session_id, checkpoint_id)

    monkeypatch.setattr(SupervisionService, "activate", interrupt_once)
    with _running_change_api(tmp_path) as (base_url, token, db_path, target):
        prepared = _post(
            base_url,
            "/api/v1/supervision/changes",
            token,
            {"content": content},
        )[1]
        session_id = prepared["supervision_session_id"]
        action_ref = _action_ref(base_url, token, session_id)
        assert _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            {"action_ref": action_ref},
        )[0] == 200

        first_status, first = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/apply",
            token,
            {"content": content},
        )
        retry_status, retry = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/apply",
            token,
            {"content": content},
        )

    assert first_status == 409
    assert first["reason_code"] == "CONTROLLED_CHANGE_ACTIVATION_DENIED"
    assert retry_status == 200
    assert retry["status"] == "COMPLETED"
    assert target.read_text(encoding="utf-8") == content
    database = StateDB(db_path)
    database.connect()
    try:
        assert database._conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone() == (1,)
    finally:
        database.close()


def test_apply_retry_resumes_an_authoritatively_activated_session(tmp_path, monkeypatch):
    content = "safe = true\n"
    original_activate = SupervisionService.activate
    calls = 0

    def activate_then_interrupt(self, session_id, checkpoint_id=None):
        nonlocal calls
        activated = original_activate(self, session_id, checkpoint_id)
        calls += 1
        if calls == 1:
            raise ControlledChangeError("SIMULATED_POST_ACTIVATION_INTERRUPTION", 503)
        return activated

    monkeypatch.setattr(SupervisionService, "activate", activate_then_interrupt)
    with _running_change_api(tmp_path) as (base_url, token, db_path, target):
        prepared = _post(
            base_url,
            "/api/v1/supervision/changes",
            token,
            {"content": content},
        )[1]
        session_id = prepared["supervision_session_id"]
        action_ref = _action_ref(base_url, token, session_id)
        assert _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            {"action_ref": action_ref},
        )[0] == 200

        interrupted_status, interrupted = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/apply",
            token,
            {"content": content},
        )
        retry_status, retry = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/apply",
            token,
            {"content": content},
        )

    assert interrupted_status == 503
    assert interrupted["reason_code"] == "SIMULATED_POST_ACTIVATION_INTERRUPTION"
    assert retry_status == 200
    assert retry["status"] == "COMPLETED"
    assert target.read_text(encoding="utf-8") == content
    database = StateDB(db_path)
    database.connect()
    try:
        assert database._conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone() == (1,)
    finally:
        database.close()


def test_cli_serve_propagates_the_server_owned_base_directory(tmp_path, monkeypatch):
    captured = {}

    def fake_run_server(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("agentguard.api.server.run_server", fake_run_server)

    assert cli.main(["--directory", str(tmp_path), "serve"]) == 0
    assert captured["config"]["base_dir"] == str(tmp_path.resolve())


def test_corrupt_ledger_cannot_create_checkpoint_before_activation(tmp_path):
    content = "safe = true\n"
    with _running_change_api(tmp_path) as (base_url, token, db_path, target):
        prepared = _post(
            base_url,
            "/api/v1/supervision/changes",
            token,
            {"content": content},
        )[1]
        session_id = prepared["supervision_session_id"]
        action_ref = _action_ref(base_url, token, session_id)
        assert _post(
            base_url,
            f"/api/v1/supervision/{session_id}/approve-once",
            token,
            {"action_ref": action_ref},
        )[0] == 200
        with sqlite3.connect(db_path) as connection:
            connection.execute("DROP TRIGGER evidence_ledger_events_no_update")
            connection.execute(
                "UPDATE evidence_ledger_events SET result = 'forged' WHERE sequence = 1"
            )
            assert connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone() == (0,)

        status, result = _post(
            base_url,
            f"/api/v1/supervision/{session_id}/apply",
            token,
            {"content": content},
        )

    assert status == 503
    assert result["reason_code"] == "CONTROLLED_CHANGE_LEDGER_INVALID"
    assert target.read_text(encoding="utf-8") == "safe = false\n"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone() == (0,)
