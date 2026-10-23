"""Deterministic loopback transport tests; no real credentials or model service."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentguard.ai.provider import (
    MAX_RESPONSE_BYTES,
    AIProviderError,
    AIResult,
    OpenAICompatibleProvider,
    ProviderConfig,
)
from tests.test_product_ai_api import RecordingAnalysisProvider, _request, _running_api


@contextmanager
def upstream():
    class State:
        status = 200
        raw = None
        block_path = ""
        status_line = None

        def __init__(self):
            self.models = {"data": [{"id": "fixture-model"}]}
            self.analysis = {"status": "warn", "severity": "medium", "summary": "Fixture advisory"}
            self.started = threading.Event()
            self.release = threading.Event()
            self.requests = []

    state = State()

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            if state.status_line is not None:
                self.wfile.write(state.status_line)
                self.wfile.flush()
                self.close_connection = True
                return
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")))) if self.command == "POST" else None
            state.requests.append((self.command, self.path, payload))
            if self.path == state.block_path:
                state.started.set()
                state.release.wait(4)
            data = state.models if self.command == "GET" else {"choices": [{"message": {"content": json.dumps(state.analysis)}}]}
            body = state.raw if state.raw is not None else json.dumps(data).encode()
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_GET = respond
        do_POST = respond

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def provider(url, model="fixture-model"):
    return OpenAICompatibleProvider(ProviderConfig(base_url=url, api_key="fixture-memory-token", model=model), timeout=2)


def test_transport_validates_model_without_generation_and_redacts_echoed_token():
    with upstream() as (state, url):
        client = provider(url)
        result = client.test_connection()
        assert result["ok"] is True
        assert result["models_available"] == 1
        assert "generation was not tested" in result["message"]
        assert [(method, path) for method, path, _ in state.requests] == [("GET", "/v1/models")]
        state.analysis = {**state.analysis, "summary": "Echo fixture-memory-token"}
        advisory = client.analyze({"runtime": {"status": "UNKNOWN"}})
        assert advisory.status == "warn"
        assert advisory.summary == "Echo [REDACTED]"
        assert state.requests[-1][2]["model"] == "fixture-model"
        assert state.requests[-1][2]["messages"][1]["content"] == '{"runtime": {"status": "UNKNOWN"}}'
        assert "fixture-memory-token" not in repr(client._config)


@pytest.mark.parametrize("models,code", [([], "AI_RESPONSE_INVALID"), ({}, "AI_MODELS_INVALID"),
    ({"data": [{"id": 4}]}, "AI_MODELS_INVALID"),
    ({"data": [{"id": "bad\nmodel"}]}, "AI_MODELS_INVALID"),
    ({"data": [{"id": str(i)} for i in range(1001)]}, "AI_MODELS_INVALID"),
    ({"data": []}, "AI_MODELS_EMPTY")])
def test_model_list_shape_and_count_are_bounded(models, code):
    with upstream() as (state, url):
        state.models = models
        assert provider(url).test_connection() == {"ok": False, "error": code}


@pytest.mark.parametrize("model,code", [("", "AI_MODEL_REQUIRED"), ("missing", "AI_MODEL_UNAVAILABLE")])
def test_empty_or_unlisted_selected_model_is_not_usable(model, code):
    with upstream() as (state, url):
        assert provider(url, model).test_connection() == {"ok": False, "error": code}
        assert all(method == "GET" for method, _, _ in state.requests)


def test_error_bodies_and_oversized_responses_never_escape_provider():
    with upstream() as (state, url):
        state.status = 401
        state.raw = b"PRIVATE-UPSTREAM-BODY fixture-memory-token"
        client = provider(url)
        assert client.test_connection() == {"ok": False, "error": "AI_HTTP_401"}
        with pytest.raises(AIProviderError, match="^AI_HTTP_401$"):
            client.list_models()
        error = client.analyze({})
        assert error.summary == error.raw_error == "AI_HTTP_401"
        state.status = 200
        state.raw = b"x" * (MAX_RESPONSE_BYTES + 1)
        assert client.test_connection() == {"ok": False, "error": "AI_RESPONSE_TOO_LARGE"}
        assert client.analyze({}).summary == "AI_RESPONSE_TOO_LARGE"


@pytest.mark.parametrize("invalid", [None, {"status": "allow", "severity": "low", "summary": "not authority"},
    {"status": "ok", "severity": "low", "summary": "x" * 201},
    {"status": "ok", "severity": "low", "summary": "ok", "evidence": ["x"] * 6},
    {"status": "ok", "severity": "low", "summary": "ok", "recommended_checks": [42]}])
def test_invalid_analysis_is_unavailable(invalid):
    with upstream() as (state, url):
        state.analysis = invalid
        result = provider(url).analyze({})
        assert result.status == "error"
        assert result.summary == result.raw_error == "AI_ANALYSIS_INVALID"


def test_invalid_or_oversized_context_is_not_sent():
    with upstream() as (state, url):
        client = provider(url)
        assert client.analyze({"x": object()}).summary == "AI_CONTEXT_INVALID"
        assert client.analyze({"x": "x" * 262_145}).summary == "AI_CONTEXT_TOO_LARGE"
        assert state.requests == []


@pytest.mark.parametrize("fields", [{"base_url": 4}, {"base_url": "file:///tmp/data"}, {"base_url": "http://bad host/v1"},
    {"base_url": "https://user:fixture@example.invalid/v1"}, {"api_key": "bad\nkey"},
    {"model": "x" * 257}, {"api_key": "x" * 8193}])
def test_routes_reject_invalid_fields_without_echo(tmp_path, fields):
    with _running_api(tmp_path, None) as (base, token):
        status, result = _request(base, "/api/ai/test", token,
            {"base_url": "http://127.0.0.1:1/v1", "model": "fixture-model", **fields})
    assert status == 200
    assert result == {"ok": False, "error": "AI_PROVIDER_CONFIG_INVALID"}


def test_models_error_is_explicit_and_failed_replacement_preserves_active_provider(tmp_path):
    active = RecordingAnalysisProvider()
    with upstream() as (state, url), _running_api(tmp_path, active) as (base, token):
        state.status = 401
        state.raw = b"PRIVATE-UPSTREAM-BODY fixture-memory-token"
        fields = {"base_url": url, "api_key": "fixture-memory-token", "model": "fixture-model"}
        assert _request(base, "/api/ai/models", token, fields)[1] == {"models": [], "error": "AI_HTTP_401"}
        assert _request(base, "/api/ai/test", token, fields)[1] == {"ok": False, "error": "AI_HTTP_401"}
        status, result = _request(base, "/api/ai/analyze", token, {})
        assert status == 200 and result["model"] == active.model
        assert len(active.contexts) == 1


@pytest.mark.parametrize("route,blocked", [("models", "/v1/models"), ("test", "/v1/models"), ("analyze", "/v1/chat/completions")])
def test_ai_network_io_does_not_block_health(tmp_path, route, blocked):
    with upstream() as (state, url), _running_api(tmp_path, provider(url)) as (base, token):
        state.block_path = blocked
        with ThreadPoolExecutor(max_workers=1) as executor:
            body = {} if route == "analyze" else {"base_url": url, "model": "fixture-model"}
            pending = executor.submit(_request, base, f"/api/ai/{route}", token, body)
            try:
                assert state.started.wait(2)
                start = time.monotonic()
                assert _request(base, "/api/health")[0] == 200
                assert time.monotonic() - start < 1
                assert not pending.done()
            finally:
                state.release.set()
            assert pending.result(timeout=3)[0] == 200


def test_inflight_analysis_keeps_original_provider_identity_when_replaced(tmp_path):
    started, release = threading.Event(), threading.Event()

    class BlockingProvider(RecordingAnalysisProvider):
        def analyze(self, context):
            started.set()
            release.wait(4)
            return AIResult(status="warn", severity="low", summary="Original provider advisory",
                            provider="spoofed-provider", model="spoofed-model")

    old = BlockingProvider()
    with upstream() as (_state, url), _running_api(tmp_path, old) as (base, token):
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(_request, base, "/api/ai/analyze", token, {})
            try:
                assert started.wait(2)
                assert _request(base, "/api/ai/test", token, {"base_url": url, "model": "fixture-model"})[1]["ok"] is True
            finally:
                release.set()
            status, advisory = pending.result(timeout=3)
        assert status == 200
        assert advisory["provider"] == old.provider_name
        assert advisory["model"] == old.model
        assert _request(base, "/api/ai/analyze", token, {})[1]["model"] == "fixture-model"


def test_malformed_status_and_json_return_safe_errors():
    with upstream() as (state, url):
        state.status_line = b"PRIVATE-UPSTREAM-BODY fixture-memory-token\r\n\r\n"
        assert provider(url).test_connection() == {"ok": False, "error": "AI_CONNECTION_FAILED"}
        state.status_line = None
        state.raw = b"{"
        assert provider(url).test_connection() == {"ok": False, "error": "AI_RESPONSE_INVALID"}


def test_network_timeout_is_explicit_without_raw_exception_text():
    with upstream() as (state, url):
        state.block_path = "/v1/models"
        client = OpenAICompatibleProvider(ProviderConfig(base_url=url, model="fixture-model"), timeout=.05)
        assert client.test_connection() == {"ok": False, "error": "AI_CONNECTION_FAILED"}
        assert state.started.is_set()
