"""Authenticated catalog routes over isolated, real metadata files."""

from __future__ import annotations

import json
import logging
import runpy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from fastapi.testclient import TestClient

from agentguard.api.server import create_app
from agentguard.discovery import application_probe, session_metadata
from agentguard.storage.db import StateDB


class FakeDiscovery:
    def __init__(self) -> None:
        self.items = [self._source("local"), self._source("other")]

    @staticmethod
    def _source(source_id: str) -> dict:
        return {"source_id": source_id, "kind": "NATIVE", "label": source_id,
                "state": "OBSERVED", "enabled": True, "freshness": "FRESH",
                "connected": True}

    def sources(self) -> dict:
        return {"items": self.items}

    def change(self, source_id: str, **values: object) -> None:
        next(item for item in self.items if item["source_id"] == source_id).update(values)


class FakeDomains:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def discovery_query(self, source_id: str, request: dict) -> dict:
        action = request["action"]
        self.calls.append((source_id, action))
        if action == "application_entry":
            return application_probe.probe_entry(request["path"])
        if action == "inspect_session_store":
            return session_metadata.inspect_store(request["provider"], request["path"])
        if action == "read_session_store":
            return session_metadata.read_store(request["provider"], request["path"],
                                               request["limit"], request["offset"],
                                               known_revision=request.get("known_revision"))
        raise AssertionError(f"unexpected probe: {action}")


def _client(tmp_path: Path, discovery: FakeDiscovery, domains: FakeDomains) -> TestClient:
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    app = create_app(state_db_path=state / "state.db", config={"base_dir": str(state)},
                     discovery_service=discovery, protection_domains=domains)
    return TestClient(app)


def _authorize(client: TestClient) -> None:
    client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]


def _store(root: Path, session_id: str = "same-session", title: str = "Working") -> Path:
    directory = root / "sessions" / "2026" / "09" / "29"
    directory.mkdir(parents=True)
    (directory / "rollout-one.jsonl").write_text(json.dumps({
        "type": "session_meta", "payload": {"id": session_id, "cwd": "/work/project",
                                            "timestamp": "2026-09-29T10:00:00Z"},
    }) + "\n", encoding="utf-8")
    return root


def _register_store(client: TestClient, source_id: str, root: Path) -> str:
    response = client.post("/api/v1/discovery/session-stores", json={
        "source_id": source_id, "provider": "CODEX", "path": str(root),
    })
    assert response.status_code == 201, response.text
    return response.json()["store_id"]


def test_authenticated_registration_lists_and_reads_without_control(tmp_path: Path) -> None:
    discovery, domains = FakeDiscovery(), FakeDomains()
    client = _client(tmp_path, discovery, domains)
    program = tmp_path / "codex.exe"
    program.write_bytes(b"test executable fixture")
    root = _store(tmp_path / "codex-home")
    prefix = "/api/v1/discovery"
    assert client.get(prefix + "/applications").status_code == 401
    assert client.post(prefix + "/applications", json={"source_id": "local", "path": str(program)}).status_code == 401
    assert client.get(prefix + "/sources/local/session-stores").status_code == 401
    assert client.get(prefix + "/sources/local/sessions").status_code == 401
    _authorize(client)

    registered = client.post(prefix + "/applications", json={
        "source_id": "local", "path": str(program), "label": "Codex fixture",
    })
    assert registered.status_code == 201, registered.text
    assert registered.json()["control_capabilities"]["send"] is False
    application_id = registered.json()["application_id"]
    applications = client.get(prefix + "/applications", params={"source_id": "local"})
    assert applications.status_code == 200
    assert len(applications.json()["items"]) == 1
    item = applications.json()["items"][0]
    assert item["id"] == application_id and item["provider"] == "CODEX"
    assert item["registered"] is True and item["control_capabilities"]["create"] is False
    assert item["running"] is False

    store_id = _register_store(client, "local", root)
    listed = client.get(prefix + "/sources/local/session-stores")
    assert listed.status_code == 200
    assert [(item["id"], item["provider"], item["registered"])
            for item in listed.json()["items"]] == [(store_id, "CODEX", True)]
    read = client.get(prefix + "/sources/local/sessions", params={"store_id": store_id})
    assert read.status_code == 200, read.text
    session = read.json()["sessions"][0]
    assert read.json()["status"] == "AVAILABLE"
    assert session["session_id"] == "same-session" and session["store_id"] == store_id
    assert session["scope"] == "PROVIDER_STORE" and session["association"] is None
    assert session["control_capabilities"]["continue"] is False
    assert domains.calls == [("local", "application_entry"),
                             ("local", "inspect_session_store"),
                             ("local", "read_session_store")]


def test_unknown_program_path_validation_and_restart_registration(tmp_path: Path) -> None:
    discovery, domains = FakeDiscovery(), FakeDomains()
    client = _client(tmp_path, discovery, domains)
    _authorize(client)
    program = tmp_path / "unknown-tool"
    program.write_bytes(b"fixture")
    root = _store(tmp_path / "codex-home")
    prefix = "/api/v1/discovery"
    assert client.post(prefix + "/applications", json={"source_id": "local", "path": "../relative"}).json()["reason_code"] == "DISCOVERY_PATH_INVALID"
    assert client.post(prefix + "/session-stores", json={"source_id": "local", "provider": "CODEX", "path": "../relative"}).json()["reason_code"] == "DISCOVERY_PATH_INVALID"
    assert client.post(prefix + "/applications", json={"source_id": "local", "candidate_id": "missing"}).status_code == 404
    registered = client.post(prefix + "/applications", json={"source_id": "local", "path": str(program)})
    assert registered.status_code == 201, registered.text
    application_id = registered.json()["application_id"]
    store_id = _register_store(client, "local", root)
    first = client.get(prefix + "/applications", params={"source_id": "local"}).json()["items"][0]
    assert first["provider"] == "UNKNOWN"
    assert not any(first["control_capabilities"][name] for name in
                   ("create", "continue", "send", "stream", "cancel"))

    restarted = _client(tmp_path, discovery, FakeDomains())
    _authorize(restarted)
    applications = restarted.get(prefix + "/applications", params={"source_id": "local"}).json()["items"]
    stores = restarted.get(prefix + "/sources/local/session-stores").json()["items"]
    assert [(item["id"], item["registered"]) for item in applications] == [(application_id, True)]
    assert [(item["id"], item["registered"]) for item in stores] == [(store_id, True)]


def test_source_and_store_isolation_and_explicit_selection(tmp_path: Path) -> None:
    discovery, domains = FakeDiscovery(), FakeDomains()
    client = _client(tmp_path, discovery, domains)
    _authorize(client)
    first_root = _store(tmp_path / "first")
    second_root = _store(tmp_path / "second")
    local_first = _register_store(client, "local", first_root)
    local_second = _register_store(client, "local", second_root)
    other_first = _register_store(client, "other", first_root)
    assert len({local_first, local_second, other_first}) == 3
    selection = client.get("/api/v1/discovery/sources/local/sessions")
    assert selection.status_code == 200
    assert selection.json()["reason_code"] == "SESSION_STORE_SELECTION_REQUIRED"
    assert selection.json()["sessions"] == []
    assert client.get("/api/v1/discovery/sources/other/sessions",
                      params={"store_id": local_first}).status_code == 404
    local = client.get("/api/v1/discovery/sources/local/sessions",
                       params={"store_id": local_first}).json()["sessions"][0]
    other = client.get("/api/v1/discovery/sources/other/sessions",
                       params={"store_id": other_first}).json()["sessions"][0]
    second = client.get("/api/v1/discovery/sources/local/sessions",
                        params={"store_id": local_second}).json()["sessions"][0]
    assert len({local["binding_id"], other["binding_id"], second["binding_id"]}) == 3
    assert [item["id"] for item in client.get(
        "/api/v1/discovery/sources/other/session-stores").json()["items"]] == [other_first]


def test_disabled_disconnected_and_stale_sources_cannot_read(tmp_path: Path) -> None:
    discovery, domains = FakeDiscovery(), FakeDomains()
    client = _client(tmp_path, discovery, domains)
    _authorize(client)
    root = _store(tmp_path / "codex-home")
    store_id = _register_store(client, "local", root)
    route = "/api/v1/discovery/sources/local/sessions"
    initial_calls = len(domains.calls)
    for values in ({"enabled": False}, {"enabled": True, "state": "DISCONNECTED"},
                   {"state": "OBSERVED", "freshness": "STALE"}):
        discovery.change("local", **values)
        response = client.get(route, params={"store_id": store_id})
        assert response.status_code == 409
        assert response.json()["reason_code"] == "DISCOVERY_SOURCE_NOT_CURRENT"
        assert len(domains.calls) == initial_calls


def test_replaced_store_root_cannot_reuse_old_session_binding(tmp_path: Path) -> None:
    discovery, domains = FakeDiscovery(), FakeDomains()
    client = _client(tmp_path, discovery, domains)
    _authorize(client)
    root = _store(tmp_path / "codex-home")
    old_id = _register_store(client, "local", root)
    route = "/api/v1/discovery/sources/local/sessions"
    old_binding = client.get(route, params={"store_id": old_id}).json()["sessions"][0]["binding_id"]
    root.rename(tmp_path / "old-codex-home")
    _store(root)
    rejected = client.get(route, params={"store_id": old_id})
    assert rejected.status_code == 409
    assert rejected.json()["reason_code"] == "SESSION_STORE_IDENTITY_CHANGED"
    new_id = _register_store(client, "local", root)
    assert new_id != old_id
    fresh = client.get(route, params={"store_id": new_id})
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["sessions"][0]["binding_id"] != old_binding
    assert [item["id"] for item in client.get(
        "/api/v1/discovery/sources/local/session-stores").json()["items"]] == [new_id]


def test_private_title_never_enters_catalog_or_general_logs(tmp_path: Path,
                                                              caplog) -> None:
    discovery, domains = FakeDiscovery(), FakeDomains()
    client = _client(tmp_path, discovery, domains)
    _authorize(client)
    secret = "https://private.example.test/secret-session-title"
    root = _store(tmp_path / "cloudcli-home")
    database_path = root / "auth.db"
    import sqlite3
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE sessions (session_id TEXT, provider TEXT, provider_session_id TEXT, "
                       "custom_name TEXT, project_path TEXT, isArchived INTEGER, created_at TEXT, updated_at TEXT)")
    connection.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)",
                       ("app-1", "codex", "native-1", secret, "/work/project", 0,
                        "2026-09-29T10:00:00Z", "2026-09-29T10:00:00Z"))
    connection.commit()
    connection.close()
    with caplog.at_level(logging.DEBUG):
        response = client.post("/api/v1/discovery/session-stores", json={
            "source_id": "local", "provider": "CLOUDCLI", "path": str(root),
        })
        assert response.status_code == 201, response.text
        read = client.get("/api/v1/discovery/sources/local/sessions",
                          params={"store_id": response.json()["store_id"]})
    assert read.status_code == 200, read.text
    assert read.json()["sessions"][0]["title"] == "[REDACTED]"
    assert secret not in read.text and secret not in caplog.text
    state = StateDB(tmp_path / "state" / "state.db")
    state.connect()
    try:
        stored = state._conn.execute("SELECT metadata_json FROM protection_sessions").fetchone()[0]
        assert "title" not in json.loads(stored)
        assert secret not in stored
    finally:
        state.close()


def test_live_verifier_discards_unexpected_container_fields() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "verify-discovery-live.py"
    clean = runpy.run_path(str(script))["_clean_container_result"]
    result = clean({"programs": {"status": "PARTIAL", "paths": ["/usr/local/bin/codex"],
                                  "title": "private-title"},
                    "stores": [{"provider": "CODEX", "status": "AVAILABLE",
                                "reason_code": "SESSION_STORE_METADATA_AVAILABLE",
                                "observed_sessions": 1, "has_more": False,
                                "title": "private-title", "cwd": "/private/work"}],
                    "transcript": "private-body"})
    assert result["status"] == "AVAILABLE"
    assert result["programs"]["paths"] == ["/usr/local/bin/codex"]
    assert result["stores"][0]["observed_sessions"] == 1
    assert "private" not in json.dumps(result)
    assert clean({"programs": {"status": [], "paths": []}, "stores": []})[
        "reason_code"] == "CONTAINER_METADATA_PROBE_FAILED"


def test_live_verifier_api_uses_authenticated_gets_and_counts_only() -> None:
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append((self.command, self.path, self.headers.get("X-Session-Token")))
            if self.path == "/api/session":
                body = {"token": "test-token"}
            elif self.headers.get("X-Session-Token") != "test-token":
                self.send_error(401)
                return
            elif self.path == "/api/v1/discovery/sources":
                body = {"items": [{"source_id": "local"}]}
            elif self.path == "/api/v1/discovery/applications?limit=100":
                body = {"items": [{"path": "/usr/local/bin/codex"}], "has_more": False}
            elif self.path == "/api/v1/discovery/sources/local/session-stores":
                body = {"items": [{"id": "store-1", "provider": "CODEX"}]}
            elif self.path == "/api/v1/discovery/sources/local/sessions?store_id=store-1&limit=50":
                body = {"status": "AVAILABLE", "sessions": [
                    {"session_id": "private-id", "title": "private-title", "cwd": "/private/work"}],
                    "has_more": False}
            else:
                self.send_error(404)
                return
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        script = Path(__file__).resolve().parents[1] / "scripts" / "verify-discovery-live.py"
        report = runpy.run_path(str(script))["_api"](
            f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    assert report["status"] == "AVAILABLE"
    assert report["source_count"] == report["store_count"] == 1
    assert report["stores"][0]["observed_sessions"] == 1
    assert report["programs"]["paths"] == ["/usr/local/bin/codex"]
    assert "private" not in json.dumps(report)
    assert requests[0] == ("GET", "/api/session", None)
    assert all(method == "GET" for method, _, _ in requests)
    assert all(token == "test-token" for _, _, token in requests[1:])


def test_live_automatic_process_lifecycle_without_refresh(tmp_path):
    """A real new/closed process must change GET facts without a refresh POST."""
    import os
    import subprocess
    import sys
    import time

    import pytest

    if os.environ.get("AGENTGUARD_LIVE_DOMAINS") != "1":
        pytest.skip("explicit live discovery fixture only")
    state = tmp_path / "automatic-state"
    app = create_app(state_db_path=state / "state.db", config={"base_dir": str(state)},
                     product_startup_discovery=True)
    report = {}
    with TestClient(app) as client:
        _authorize(client)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                 cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def present():
            offset = 0
            while True:
                response = client.get("/api/v1/discovery/applications", params={
                    "source_id": "local", "include_unclassified": True, "limit": 100, "offset": offset})
                assert response.status_code == 200, response.text
                page = response.json()
                if any(instance["pid"] == child.pid for item in page["items"]
                       for instance in item.get("instances", [])):
                    return True
                if not page["has_more"]:
                    return False
                offset = page["next_offset"]
        def wait_for(expected):
            started = time.monotonic()
            while time.monotonic() - started < 75:
                if present() is expected:
                    return round(time.monotonic() - started, 2)
                time.sleep(1)
            pytest.fail("automatic process state did not converge")
        try:
            report["appearance_seconds"] = wait_for(True)
        finally:
            child.terminate()
            child.wait(timeout=10)
        report["exit_seconds"] = wait_for(False)
        report["refresh_posts"] = 0
    output = Path("reports/core-protection-20260929/discovery-automatic-lifecycle.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
