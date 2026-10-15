"""Opt-in HTTP and worker acceptance against real local execution domains.

Run only with AGENTGUARD_LIVE_DOMAINS=1. Every database, directory, container,
and volume belongs to this test run; no existing project is registered.
"""

import json
import os
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentguard.api.server import create_app

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENTGUARD_LIVE_DOMAINS") != "1",
    reason="explicit live fixture only",
)

_BEFORE = {"a.txt": "A before\n", "b.txt": "B before\n", "c.txt": None}
_CHANGED = {"a.txt": "A after\n", "b.txt": None, "c.txt": "C new\n"}
_RESTORED = {"a.txt": "A before\n", "b.txt": "B before\n", "c.txt": "C new\n"}

_REMOTE_FILES = """
import json
import pathlib
import shutil
import sys

root = pathlib.Path(sys.argv[1])
action = sys.argv[2]
if action == 'seed':
    root.mkdir(parents=True, exist_ok=False)
    (root / 'a.txt').write_text('A before\\n', encoding='utf-8')
    (root / 'b.txt').write_text('B before\\n', encoding='utf-8')
elif action == 'change':
    (root / 'a.txt').write_text('A after\\n', encoding='utf-8')
    (root / 'b.txt').unlink()
    (root / 'c.txt').write_text('C new\\n', encoding='utf-8')
elif action == 'read':
    print(json.dumps({name: (root / name).read_text(encoding='utf-8')
        if (root / name).exists() else None for name in ('a.txt', 'b.txt', 'c.txt')}))
elif action == 'cleanup':
    if root.parent != pathlib.Path('/tmp') or not root.name.startswith('asg-api-'):
        raise ValueError('fixture root outside /tmp/asg-api-*')
    if root.is_symlink() or root.resolve().parent != pathlib.Path('/tmp'):
        raise ValueError('fixture root changed')
    if root.exists():
        shutil.rmtree(root)
else:
    raise ValueError('unknown fixture action')
"""


class ProjectFiles:
    def __init__(self, root: Path | str, prefix: list[str] | None = None):
        self.root = root
        self.prefix = prefix

    def _remote(self, action: str):
        result = subprocess.run(
            [*self.prefix, "python3", "-c", _REMOTE_FILES, str(self.root), action],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout

    def seed(self):
        if self.prefix:
            self._remote("seed")
            return
        root = Path(self.root)
        root.mkdir(parents=True, exist_ok=False)
        (root / "a.txt").write_text(_BEFORE["a.txt"], encoding="utf-8")
        (root / "b.txt").write_text(_BEFORE["b.txt"], encoding="utf-8")

    def change(self):
        if self.prefix:
            self._remote("change")
            return
        root = Path(self.root)
        (root / "a.txt").write_text(_CHANGED["a.txt"], encoding="utf-8")
        (root / "b.txt").unlink()
        (root / "c.txt").write_text(_CHANGED["c.txt"], encoding="utf-8")

    def read(self):
        if self.prefix:
            return json.loads(self._remote("read"))
        root = Path(self.root)
        return {
            name: (root / name).read_text(encoding="utf-8")
            if (root / name).exists()
            else None
            for name in _BEFORE
        }

    def cleanup_wsl(self):
        assert self.prefix and str(self.root).startswith("/tmp/asg-api-")
        self._remote("cleanup")


def _command(**values):
    return {"request_id": str(uuid4()), **values}


def _app(state):
    return create_app(
        state_db_path=state / "state.db",
        config={"base_dir": str(state)},
        product_startup_discovery=False,
    )


def _authorize(client):
    response = client.get("/api/session")
    assert response.status_code == 200, response.text
    client.headers["X-Session-Token"] = response.json()["token"]


def _request(client, method, path, *, status=200, body=None):
    response = client.request(method, path, json=body)
    assert response.status_code == status, (
        f"{method} {path}: {response.status_code} {response.text}"
    )
    return response.json()


def _wait_operation(client, operation_id, *, timeout=90):
    deadline = time.monotonic() + timeout
    path = f"/api/v1/recovery/operations/{operation_id}"
    while time.monotonic() < deadline:
        operation = _request(client, "GET", path)
        if operation["state"] not in {"QUEUED", "RUNNING", "COMPENSATING"}:
            return operation
        time.sleep(0.1)
    pytest.fail(f"operation did not settle: {operation_id}")


def _source(client, *, kind, label=None, container_id=None):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        candidates = _request(client, "GET", "/api/v1/protection/scopes/candidates")[
            "sources"
        ]
        matches = [
            source
            for source in candidates
            if source["kind"] == kind
            and (label is None or source["label"] == label)
            and (container_id is None or source.get("container_id") == container_id)
        ]
        if len(matches) == 1:
            return matches[0]
        time.sleep(0.2)
    pytest.fail(f"expected one live {kind} source named {label}")


def _connect(client, source, *, start=False):
    action = "start-and-connect" if start else "connect"
    path = f"/api/v1/discovery/sources/{source['source_id']}/{action}"
    command = _command()
    queued = _request(client, "POST", path, status=202, body=command)
    settled = _wait_operation(client, queued["id"])
    assert settled["state"] == "SUCCEEDED", settled
    assert (
        _request(client, "POST", path, status=202, body=command)["id"] == queued["id"]
    )
    current = _source(
        client,
        kind=source["kind"],
        label=source["label"],
        container_id=source.get("container_id"),
    )
    assert current["running"] and current["connected"]


def _storage_kind(state, scope_id):
    with sqlite3.connect(state / "state.db") as conn:
        row = conn.execute(
            "SELECT binding_json FROM protection_scopes WHERE id=?", (scope_id,)
        ).fetchone()
    assert row is not None
    return json.loads(row[0])["identity"]["storage"]["kind"]


def _workflow(client, state, source_id, files, *, storage_kind=None):
    files.seed()
    assert files.read() == _BEFORE
    registered = _request(
        client,
        "POST",
        "/api/v1/protection/scopes",
        status=201,
        body=_command(source_id=source_id, path=str(files.root)),
    )
    scope_id = registered["scope_id"]
    scope_path = f"/api/v1/protection/scopes/{scope_id}"
    if storage_kind is not None:
        assert _storage_kind(state, scope_id) == storage_kind

    baseline = _request(client, "GET", scope_path + "/versions")["items"][0]
    assert baseline["kind"] == "BASELINE"
    file_ids = {entry["relative_path"]: entry["file_id"] for entry in baseline["files"]}
    assert set(file_ids) == {"a.txt", "b.txt"}

    work = _request(
        client,
        "POST",
        "/api/v1/works",
        status=201,
        body=_command(
            title="Live protection fixture",
            owner="local",
            requirement="Preserve selected files",
        ),
    )
    work_id = work["work_id"]
    work_path = f"/api/v1/works/{work_id}"
    linked = _request(
        client,
        "POST",
        work_path + "/protection-links",
        body=_command(expected_revision=1, kind="scope", object_id=scope_id),
    )
    assert linked["revision"] == 2

    files.change()
    assert files.read() == _CHANGED
    captured = _request(client, "POST", scope_path + "/capture", body={})
    assert captured["status"] in {"RECORDED", "UNCHANGED"}, captured
    latest = _request(client, "GET", scope_path + "/versions")["items"][0]
    assert latest["id"] != baseline["id"]
    latest_by_path = {entry["relative_path"]: entry for entry in latest["files"]}
    assert {name: entry["category"] for name, entry in latest_by_path.items()} == {
        "a.txt": "restorable",
        "c.txt": "restorable",
    }
    diff = _request(
        client,
        "GET",
        f"{scope_path}/files/{file_ids['a.txt']}/diff"
        f"?before={baseline['id']}&after={latest['id']}",
    )
    assert "-A before" in diff["text_diff"] and "+A after" in diff["text_diff"]
    changes = _request(client, "GET", scope_path + "/changes")["items"]
    observed = {
        (entry["relative_path"], entry["kind"])
        for batch in changes
        if batch["operation_id"] is None
        for entry in batch["entries"]
    }
    assert {
        ("a.txt", "MODIFIED"),
        ("b.txt", "DELETED"),
        ("c.txt", "CREATED"),
    } <= observed
    user_batch = next(batch for batch in changes if batch["operation_id"] is None)
    associated = _request(
        client,
        "POST",
        work_path + "/protection-links",
        body=_command(expected_revision=2, kind="batch", object_id=user_batch["id"]),
    )
    assert associated["revision"] == 3
    work_changes = _request(client, "GET", work_path + "/protection-changes")["items"]
    associated_batch = next(
        batch for batch in work_changes if batch["id"] == user_batch["id"]
    )
    assert associated_batch["association"] == "USER_LINKED"
    assert associated_batch["attribution"] == "UNATTRIBUTED"

    preview = _request(
        client,
        "POST",
        "/api/v1/recovery/previews",
        body={
            "scope_id": scope_id,
            "mode": "restore_version",
            "version_id": baseline["id"],
            "file_ids": [file_ids["a.txt"], file_ids["b.txt"]],
        },
    )
    assert preview["status"] == "READY"
    assert work_id in preview["affected_work_ids"]
    preview_diff = _request(
        client,
        "GET",
        f"/api/v1/recovery/previews/{preview['preview_id']}"
        f"/files/{file_ids['a.txt']}/diff",
    )
    assert (
        "-A after" in preview_diff["text_diff"]
        and "+A before" in preview_diff["text_diff"]
    )
    restore_command = _command(
        preview_id=preview["preview_id"], digest=preview["digest"]
    )
    queued = _request(
        client, "POST", "/api/v1/recovery/operations", status=202, body=restore_command
    )
    restored = _wait_operation(client, queued["id"])
    assert restored["state"] == "SUCCEEDED", restored
    assert files.read() == _RESTORED
    retry = _request(
        client, "POST", "/api/v1/recovery/operations", status=202, body=restore_command
    )
    assert retry["id"] == queued["id"] and retry["state"] == "SUCCEEDED"

    recovery_changes = _request(client, "GET", scope_path + "/changes")["items"]
    own_batch = next(
        batch for batch in recovery_changes if batch["operation_id"] == queued["id"]
    )
    assert {entry["relative_path"] for entry in own_batch["entries"]} == {
        "a.txt",
        "b.txt",
    }
    assert all(entry["attribution"] == "ASG_RECOVERY" for entry in own_batch["entries"])

    undo = _request(
        client,
        "POST",
        "/api/v1/recovery/previews",
        body={
            "scope_id": scope_id,
            "mode": "undo_operation",
            "operation_id": queued["id"],
            "file_ids": [file_ids["a.txt"], file_ids["b.txt"]],
        },
    )
    assert undo["status"] == "READY"
    undo_command = _command(preview_id=undo["preview_id"], digest=undo["digest"])
    undo_queued = _request(
        client, "POST", "/api/v1/recovery/operations", status=202, body=undo_command
    )
    undone = _wait_operation(client, undo_queued["id"])
    assert undone["state"] == "SUCCEEDED", undone
    assert files.read() == _CHANGED
    assert (
        _request(
            client, "POST", "/api/v1/recovery/operations", status=202, body=undo_command
        )["id"]
        == undo_queued["id"]
    )
    return {
        "scope_id": scope_id,
        "work_id": work_id,
        "baseline_id": baseline["id"],
        "file_id": file_ids["a.txt"],
        "batch_id": user_batch["id"],
        "restore_id": queued["id"],
        "undo_id": undo_queued["id"],
    }


def _reopened_history(state, records):
    with TestClient(_app(state)) as client:
        _authorize(client)
        for record in records:
            scope_id, work_id = record["scope_id"], record["work_id"]
            versions = _request(
                client, "GET", f"/api/v1/protection/scopes/{scope_id}/versions"
            )["items"]
            assert any(item["id"] == record["baseline_id"] for item in versions)
            history = _request(
                client,
                "GET",
                f"/api/v1/protection/scopes/{scope_id}"
                f"/files/{record['file_id']}/history",
            )["items"]
            assert any(item["version_id"] == record["baseline_id"] for item in history)
            links = _request(
                client, "GET", f"/api/v1/works/{work_id}/protection-links"
            )["items"]
            assert {(item["kind"], item["object_id"]) for item in links} >= {
                ("scope", scope_id),
                ("batch", record["batch_id"]),
            }
            assert _request(client, "GET", f"/api/v1/works/{work_id}")["id"] == work_id
            for key in ("restore_id", "undo_id"):
                operation = _request(
                    client, "GET", f"/api/v1/recovery/operations/{record[key]}"
                )
                assert operation["state"] == "SUCCEEDED"


@contextmanager
def _docker_fixture(tmp_path):
    image = subprocess.run(
        ["docker", "inspect", "--format", "{{.Config.Image}}", "agent-k3"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    assert image
    token = uuid4().hex
    name, volume = f"asg-api-{token}", f"asg-api-volume-{token}"
    bind_root = tmp_path / "bind"
    bind_root.mkdir()
    created_volume = created_container = False
    try:
        created = subprocess.run(
            ["docker", "volume", "create", volume],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert created.stdout.strip() == volume
        created_volume = True
        run = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--mount",
                f"type=bind,source={bind_root},target=/mnt/asg-bind",
                "--mount",
                f"type=volume,source={volume},target=/mnt/asg-volume",
                "--user",
                "0:0",
                "--entrypoint",
                "python3",
                image,
                "-c",
                "import time; time.sleep(900)",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=45,
        )
        container_id = run.stdout.strip()
        created_container = True
        assert len(container_id) == 64
        yield container_id
    finally:
        try:
            if created_container:
                subprocess.run(
                    ["docker", "rm", "-f", name],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
        finally:
            if created_volume:
                subprocess.run(
                    ["docker", "volume", "rm", volume],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=45,
                )


def test_live_native_http_worker_and_reopen(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    files = ProjectFiles(tmp_path / "native-project")
    with TestClient(_app(state)) as client:
        _authorize(client)
        source = _source(client, kind="NATIVE", label="Local")
        _connect(client, source)
        record = _workflow(client, state, source["source_id"], files)
    _reopened_history(state, [record])


def test_live_docker_bind_volume_and_writable_layer_http(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    with _docker_fixture(tmp_path) as container_id:
        prefix = ["docker", "exec", container_id]
        projects = [
            (ProjectFiles("/mnt/asg-bind/project", prefix), "bind"),
            (ProjectFiles("/mnt/asg-volume/project", prefix), "volume"),
            (ProjectFiles(f"/tmp/asg-api-{uuid4().hex}", prefix), "writable_layer"),
        ]
        with TestClient(_app(state)) as client:
            _authorize(client)
            source = _source(client, kind="CONTAINER", container_id=container_id)
            _connect(client, source)
            records = [
                _workflow(client, state, source["source_id"], files, storage_kind=kind)
                for files, kind in projects
            ]
        _reopened_history(state, records)


@pytest.mark.skipif(os.name != "nt", reason="WSL is available only on Windows")
def test_live_wsl_ubuntu_http_worker_and_reopen(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    files = ProjectFiles(
        f"/tmp/asg-api-{uuid4().hex}", ["wsl.exe", "--distribution", "Ubuntu", "--exec"]
    )
    seeded = False
    try:
        with TestClient(_app(state)) as client:
            _authorize(client)
            source = _source(client, kind="WSL", label="Ubuntu")
            _connect(client, source, start=True)
            # Mark the unique path before registration so a failed assertion also
            # cleans the fixture. The helper refuses paths outside /tmp/asg-api-*.
            seeded = True
            record = _workflow(client, state, source["source_id"], files)
        _reopened_history(state, [record])
    finally:
        if seeded:
            files.cleanup_wsl()
