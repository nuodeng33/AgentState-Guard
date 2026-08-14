"""Server-owned current-environment AI analysis API contract."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

import uvicorn

from agentguard.ai.provider import AIResult
from agentguard.api.server import create_app


class RecordingAnalysisProvider:
    model = "product-analysis-model"
    provider_name = "test-provider"

    def __init__(self) -> None:
        self.contexts: list[dict] = []

    def analyze(self, context: dict) -> AIResult:
        self.contexts.append(context)
        return AIResult(
            status="warn",
            severity="medium",
            summary="Authoritative state needs review.",
            possible_causes=["Uncertainty remains"],
            recommended_checks=["Review evidence"],
            evidence=["Server projection"],
            model=self.model,
            provider=self.provider_name,
            analyzed_at="2026-08-14T01:02:03Z",
        )


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _request(base_url, path, token=None, body=None):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=payload,
        method="POST" if body is not None else "GET",
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    if token:
        request.add_header("X-Session-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@contextmanager
def _running_api(tmp_path, provider):
    port = _free_port()
    app = create_app(
        state_db_path=tmp_path / "state.db",
        config={"base_dir": str(tmp_path)},
        assessment_provider=provider,
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
    token = _request(base_url, "/api/session")[1]["token"]
    try:
        yield base_url, token
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_ai_analyze_rejects_caller_authority_context(tmp_path):
    provider = RecordingAnalysisProvider()
    with _running_api(tmp_path, provider) as (base_url, token):
        status, body = _request(
            base_url,
            "/api/ai/analyze",
            token,
            {"context": {"runtime": {"status": "FORGED"}}},
        )

    assert status == 422
    assert body["reason_code"] == "AI_ANALYZE_REQUEST_INVALID"
    assert provider.contexts == []


def test_ai_analyze_uses_server_projections_and_returns_stable_advisory(tmp_path):
    provider = RecordingAnalysisProvider()
    with _running_api(tmp_path, provider) as (base_url, token):
        status, body = _request(base_url, "/api/ai/analyze", token, {})

    assert status == 200
    assert len(provider.contexts) == 1
    context = provider.contexts[0]
    assert set(context) == {"runtime", "agents", "supervision", "recovery", "changes"}
    assert context["runtime"]["view"] == "runtime"
    assert body == {
        "schema_version": "product-ai-advisory-1",
        "status": "AVAILABLE",
        "reason_code": "AI_ADVISORY_AVAILABLE",
        "severity": "MEDIUM",
        "summary": "Authoritative state needs review.",
        "uncertainties": ["Uncertainty remains"],
        "recommended_checks": ["Review evidence"],
        "evidence_refs": [],
        "provider": "test-provider",
        "model": "product-analysis-model",
        "analyzed_at": "2026-08-14T01:02:03Z",
    }
