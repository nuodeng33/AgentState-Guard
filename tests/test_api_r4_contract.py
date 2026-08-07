"""P8 authoritative backend contract tests; no UI or caller trust injection."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import uvicorn

from agentguard.api.server import create_app


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


@pytest.fixture
def api(tmp_path: Path):
    port = _free_port()
    app = create_app(state_db_path=tmp_path / "state.db", config={"base_dir": str(tmp_path)})
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
