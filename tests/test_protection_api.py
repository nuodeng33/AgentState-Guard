"""Real Windows/POSIX files through authenticated Core HTTP routes."""

import base64
import time
from uuid import uuid4

from fastapi.testclient import TestClient

from agentguard.api.server import create_app
from agentguard.recovery.protection_domains import DomainRegistry


def app_and_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.txt").write_text("before\n")
    state = tmp_path / "state"
    state.mkdir()
    domains = DomainRegistry(state)
    app = create_app(state_db_path=state / "state.db", config={"base_dir": str(state)}, protection_domains=domains)
    return app, project


def authorize(client):
    client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]


def test_http_file_versions_preview_and_durable_worker(tmp_path):
    app, project = app_and_project(tmp_path)
    with TestClient(app) as client:
        authorize(client)
        response = client.post("/api/v1/protection/scopes", json={"source_id": "local", "path": str(project), "request_id": str(uuid4())})
        assert response.status_code == 201, response.text
        scope_id = response.json()["scope_id"]
        route = f"/api/v1/protection/scopes/{scope_id}"
        first = client.get(route + "/versions").json()["items"][0]
        file_id = next(e["file_id"] for e in first["files"] if e["relative_path"] == "a.txt")
        (project / "a.txt").write_text("after\n")
        preimage = (project / "a.txt").read_bytes()
        assert client.post(route + "/capture", json={}).json()["status"] == "RECORDED"
        changes = client.get(route + "/changes").json()["items"]
        preview = client.post("/api/v1/recovery/previews", json={"scope_id": scope_id, "mode": "undo_batch", "batch_id": changes[0]["id"], "file_ids": [file_id]})
        assert preview.status_code == 200, preview.text
        p = preview.json()
        diff = client.get(f"/api/v1/recovery/previews/{p['preview_id']}/files/{file_id}/diff")
        assert diff.status_code == 200, diff.text
        assert "-after" in diff.json()["text_diff"] and "+before" in diff.json()["text_diff"]
        command = {"preview_id": p["preview_id"], "digest": p["digest"], "request_id": str(uuid4())}
        response = client.post("/api/v1/recovery/operations", json=command)
        assert response.status_code == 202, response.text
        operation_id = response.json()["id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = client.get(f"/api/v1/recovery/operations/{operation_id}").json()
            if result["state"] not in {"QUEUED", "RUNNING"}:
                break
            time.sleep(.05)
        assert result["state"] == "SUCCEEDED", result
        assert (project / "a.txt").read_text() == "before\n"
        events = client.get(f"/api/v1/recovery/operations/{operation_id}/events").json()["items"]
        assert [event["state"] for event in events] == ["INTENT", "APPLIED", "VERIFIED"]
        assert "content_b64" not in str(events)
        retained = client.get(f"/api/v1/recovery/operations/{operation_id}/steps/000000/retained").json()
        assert base64.b64decode(retained["entry"]["content_b64"]) == preimage
        assert client.post("/api/v1/recovery/operations", json=command).json()["id"] == operation_id
    # A fresh app can query completed durable results without replaying writes.
    reopened = create_app(state_db_path=tmp_path / "state/state.db", config={"base_dir": str(tmp_path / "state")})
    client = TestClient(reopened)
    authorize(client)
    assert client.get(f"/api/v1/recovery/operations/{operation_id}").json()["state"] == "SUCCEEDED"


def test_new_routes_require_session_and_validation_never_echoes_paths(tmp_path):
    app, project = app_and_project(tmp_path)
    client = TestClient(app)
    assert client.get("/api/v1/protection/scopes").status_code == 401
    assert client.post("/api/v1/recovery/previews", json={}).status_code == 401
    authorize(client)
    response = client.post("/api/v1/protection/scopes", json={"source_id": "local", "path": str(project), "request_id": "request", "command": "private-value"})
    assert response.status_code == 422
    assert "private-value" not in response.text and str(project) not in response.text


def test_legacy_restore_cannot_bypass_preview(tmp_path):
    app, _ = app_and_project(tmp_path)
    client = TestClient(app)
    authorize(client)
    response = client.post("/api/v1/recovery/123/restore", json={"confirm": True})
    assert response.status_code == 409
    assert response.json()["reason_code"] == "PREVIEW_REQUIRED"
