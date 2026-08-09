"""P8 authoritative backend contract tests; no UI or caller trust injection."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
import uvicorn

from agentguard.api.server import create_app
from agentguard.discovery import CapabilityStatus, DiscoverySnapshot, ProbeEvidence
from agentguard.evidence.discovery_adapter import discovery_events
from agentguard.evidence.ledger import EvidenceLedger
from agentguard.storage.db import StateDB
from tests.test_recovery_trusted_baseline import _verified_r3


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _get(base_url, path, token=None):
    request = urllib.request.Request(f"{base_url}{path}")
    if token is not None:
        request.add_header("X-Session-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@contextmanager
def _running_api(tmp_path: Path, state_db_path: Path | None = None):
    port = _free_port()
    app = create_app(
        state_db_path=state_db_path or tmp_path / "state.db",
        config={"base_dir": str(tmp_path)},
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    base_url = f"http://127.0.0.1:{port}"
    token = _get(base_url, "/api/session")[1]["token"]
    try:
        yield base_url, token, tmp_path
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture
def api(tmp_path: Path):
    with _running_api(tmp_path) as running:
        yield running


@pytest.mark.parametrize("path", [
    "/api/v1/runtime", "/api/v1/agents", "/api/v1/supervision", "/api/v1/recovery",
])
def test_r4_read_contract_is_stable_and_safe_on_empty_state(api, path):
    base_url, token, tmp_path = api
    status, body = _get(base_url, path, token)
    assert status == 200
    assert body["schema_version"] == "r4-p8-1"
    assert body["status"] in {"EMPTY", "UNKNOWN", "DEGRADED", "AVAILABLE"}
    assert isinstance(body["reason_code"], str)
    assert isinstance(body["evidence_refs"], list)
    encoded = json.dumps(body)
    assert str(tmp_path) not in encoded
    assert "state.db" not in encoded
    assert "password" not in encoded.lower()
    assert "token" not in encoded.lower()


def test_r4_contract_does_not_accept_caller_authoritative_truth(api):
    base_url, token, _tmp_path = api
    for path in ("/api/v1/runtime", "/api/v1/agents", "/api/v1/supervision", "/api/v1/recovery"):
        status, body = _get(base_url, f"{path}?recovery_level=TRUSTED&r3_verified=true&trusted_baseline=TRUSTED&decision=ALLOW&confidence=1.0", token)
        assert status == 200
        assert body.get("trusted_baseline_status") != "TRUSTED"


def test_r4_contract_requires_session_token(api):
    base_url, _token, _tmp_path = api
    status, _body = _get(base_url, "/api/v1/recovery")
    assert status == 401


def test_runtime_readiness_is_authenticated_and_checks_database(api, tmp_path):
    base_url, token, _root = api
    assert _get(base_url, "/api/readiness")[0] == 401
    status, body = _get(base_url, "/api/readiness", token)
    assert status == 200
    assert body == {
        "status": "ready",
        "database": "available",
        "reason_code": "RUNTIME_READY",
    }

    blocked = tmp_path / "readiness-database-is-a-directory"
    blocked.mkdir()
    with _running_api(tmp_path, blocked) as (blocked_url, blocked_token, _root):
        status, body = _get(blocked_url, "/api/readiness", blocked_token)
    assert status == 503
    assert body == {
        "status": "degraded",
        "database": "unreachable",
        "reason_code": "RUNTIME_DATABASE_UNREACHABLE",
    }


def test_r4_runtime_projects_verified_fact_and_unbound_agent_degrades(tmp_path):
    database = StateDB(tmp_path / "state.db")
    database.connect()
    snapshot = DiscoverySnapshot(
        snapshot_id="api-projection-snapshot",
        observed_at=datetime(2026, 8, 9, tzinfo=UTC),
        evidence=(
            ProbeEvidence(
                evidence_id="runtime-safe-ref",
                collector="runtime-probe",
                source="local",
                observed_at=datetime(2026, 8, 9, tzinfo=UTC),
                fact_type="runtime.metadata",
                value={
                    "runtime_kind": "python",
                    "execution_domain_id": "self-runtime",
                    "secret_token": "do-not-project",
                },
                status=CapabilityStatus.AVAILABLE,
                sanitized=True,
            ),
            ProbeEvidence(
                evidence_id="agent-safe-ref",
                collector="agent-probe",
                source="local",
                observed_at=datetime(2026, 8, 9, tzinfo=UTC),
                fact_type="agent.metadata",
                value={"agent_kind": "cloudcli", "execution_domain_id": "self-runtime"},
                status=CapabilityStatus.AVAILABLE,
                sanitized=True,
            ),
        ),
    )
    with database.transaction() as connection:
        for event in discovery_events(snapshot, recorded_at=datetime(2026, 8, 9, tzinfo=UTC)):
            EvidenceLedger().append(connection, event)
    database.close()

    with _running_api(tmp_path) as (base_url, token, _root):
        runtime = _get(base_url, "/api/v1/runtime?availability=FORGED", token)[1]
        agents = _get(base_url, "/api/v1/agents?confidence=1.0", token)[1]
        assert _get(base_url, "/api/v1/runtime", token)[1] == runtime

    assert runtime["status"] == "AVAILABLE"
    assert runtime["items"][0]["runtime_type"] == "python"
    assert runtime["items"][0]["availability"] == "AVAILABLE"
    assert runtime["items"][0]["execution_domain_id"] == "self-runtime"
    assert agents["status"] == "DEGRADED"
    assert agents["reason_code"] == "R4_WORKSPACE_BINDING_INCOMPLETE"
    assert agents["items"][0]["detected_identity"] == "cloudcli"
    assert agents["items"][0]["lifecycle"] == "DETECTED"
    assert agents["items"][0]["execution_domain_id"] == "self-runtime"
    assert agents["items"][0]["uncertainty"] is True
    assert agents["items"][0]["workspace"] == {
        "status": "UNKNOWN",
        "workspace_id": None,
        "binding_ref": None,
        "reason_code": "WORKSPACE_BINDING_MISSING",
    }
    assert "do-not-project" not in json.dumps(runtime)


def test_r4_supervision_and_recovery_project_real_server_state(tmp_path):
    state_root = tmp_path / ".agentguard"
    state_root.mkdir()
    target, database, _snapshots, _service, checkpoint = _verified_r3(state_root)
    database.close()

    with _running_api(tmp_path, state_root / "state.db") as (base_url, token, _root):
        supervision = _get(base_url, "/api/v1/supervision?decision=ALLOW&approved=true", token)[1]
        recovery = _get(
            base_url,
            "/api/v1/recovery?recovery_level=TRUSTED&r3_verified=false&trusted_baseline_status=TRUSTED",
            token,
        )[1]

    assert supervision["status"] == "AVAILABLE"
    assert any(item["policy_decision"] == "REVIEW" for item in supervision["items"])
    assert all("declared_intent_digest" not in item for item in supervision["items"])
    assert recovery["recovery_level"] == "R3"
    assert recovery["r1_verified"] is True
    assert recovery["r2_verified"] is True
    assert recovery["r3_verified"] is True
    assert recovery["trusted_baseline_status"] == "NONE"
    assert recovery["trusted_baseline_id"] is None
    assert recovery["items"][0]["checkpoint_id"] == checkpoint.checkpoint_id
    assert str(target) not in json.dumps(recovery)


def test_r4_contract_fails_closed_when_database_or_ledger_is_unavailable(tmp_path):
    blocked = tmp_path / "database-is-a-directory"
    blocked.mkdir()
    with _running_api(tmp_path, blocked) as (base_url, token, _root):
        unavailable = _get(base_url, "/api/v1/recovery", token)[1]
    assert unavailable["status"] == "DEGRADED"
    assert unavailable["reason_code"] == "R4_DATABASE_UNREACHABLE"
    assert unavailable["recovery_level"] == "R0"
    assert unavailable["trusted_baseline_status"] == "NONE"

    database = StateDB(tmp_path / "state.db")
    database.connect()
    snapshot = DiscoverySnapshot(
        snapshot_id="ledger-corruption",
        observed_at=datetime(2026, 8, 9, tzinfo=UTC),
        evidence=(ProbeEvidence(
            evidence_id="runtime-ledger-ref",
            collector="runtime-probe",
            source="local",
            observed_at=datetime(2026, 8, 9, tzinfo=UTC),
            fact_type="runtime.metadata",
            value={"runtime_kind": "python"},
            status=CapabilityStatus.AVAILABLE,
            sanitized=True,
        ),),
    )
    with database.transaction() as connection:
        EvidenceLedger().append(
            connection,
            discovery_events(snapshot, recorded_at=datetime(2026, 8, 9, tzinfo=UTC))[0],
        )
    database._conn.execute("DROP TRIGGER evidence_ledger_events_no_update")
    database._conn.execute(
        "UPDATE evidence_ledger_events SET result = 'forged' WHERE event_type = 'RUNTIME_DETECTED'"
    )
    database._conn.commit()
    database.close()
    with _running_api(tmp_path) as (base_url, token, _root):
        invalid = _get(base_url, "/api/v1/runtime", token)[1]
    assert invalid == {
        "schema_version": "r4-p8-1", "view": "runtime", "status": "DEGRADED",
        "reason_code": "R4_LEDGER_INVALID", "evidence_refs": [], "items": [],
    }


def test_r4_recovery_snapshot_unavailable_never_preserves_r3(tmp_path):
    state_root = tmp_path / ".agentguard"
    state_root.mkdir()
    _target, database, snapshots, _service, checkpoint = _verified_r3(state_root)
    snapshots.delete(database.get_checkpoint(int(checkpoint.checkpoint_id))["snapshot_path"])
    database.close()
    with _running_api(tmp_path, state_root / "state.db") as (base_url, token, _root):
        recovery = _get(base_url, "/api/v1/recovery", token)[1]
    assert recovery["recovery_level"] == "R0"
    assert recovery["r3_verified"] is False
    assert recovery["trusted_baseline_status"] == "NONE"
    assert recovery["items"][0]["reason_code"] == "RECOVERY_ARTIFACT_NOT_FOUND"
