"""Server-owned Host-native workspace checkpoint API contracts."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from agentguard.api.server import create_app
from agentguard.core.config import Config
from agentguard.discovery import (
    CapabilityStatus,
    DiscoverySnapshot,
    RuntimeDescriptor,
)
from agentguard.discovery.agents import ProcessWorkspaceAuthority
from agentguard.discovery.product import ProductDiscoveryReport
from agentguard.recovery.workspace_permissions import (
    PermissionCapabilityError,
    PosixPermissionBackend,
)
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore
from tests.verified_reads_wait import read_verified

_CLIENTS = []


@pytest.fixture(autouse=True)
def close_clients():
    yield
    while _CLIENTS:
        _CLIENTS.pop().__exit__(None, None, None)


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


class _AuthorityDiscovery:
    def __init__(self, *roots) -> None:
        self._roots = roots
        self._snapshot = DiscoverySnapshot(
            snapshot_id="workspace-checkpoint-snapshot",
            observed_at=NOW,
            runtimes=(
                RuntimeDescriptor(
                    runtime_id="core-runtime",
                    runtime_type="SELF_RUNTIME",
                    domain_id="windows-current",
                    status=CapabilityStatus.AVAILABLE,
                ),
            ),
            status=CapabilityStatus.AVAILABLE,
        )

    def discover(self):
        return self._snapshot

    def discover_with_authority(self):
        return ProductDiscoveryReport(
            snapshot=self._snapshot,
            workspace_authorities=tuple(
                ProcessWorkspaceAuthority(
                    process_instance_id=f"process-{index}",
                    candidate_id=f"candidate-{index}",
                    execution_domain_id="windows-current",
                    cwd=root,
                    evidence_refs=(f"process-evidence-{index}",),
                    agent_id=f"agent-{index}",
                )
                for index, root in enumerate(self._roots, 1)
            ),
        )


def _client(tmp_path, *roots):
    product_state = tmp_path / "product-state"
    app = create_app(
        state_db_path=product_state / "state.db",
        config={"base_dir": str(product_state)},
        discovery_service=_AuthorityDiscovery(*roots),
        product_startup_discovery=True,
    )
    client = TestClient(app).__enter__()
    _CLIENTS.append(client)
    assert app.state.protection.initial_discovery_complete.wait(5), "initial fixture discovery did not finish"
    token = client.get("/api/session").json()["token"]
    return client, {"X-Session-Token": token}, product_state


def test_empty_checkpoint_request_uses_active_host_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.txt").write_text("safe content", encoding="utf-8")
    (workspace / ".env").write_text("API_KEY=not-retained", encoding="utf-8")
    dependency = workspace / "node_modules"
    dependency.mkdir()
    (dependency / "package.js").write_text("excluded", encoding="utf-8")
    client, headers, product_state = _client(tmp_path, workspace)

    response = client.post("/api/v1/recovery/checkpoints", json={}, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["scope_kind"] == "HOST_WORKSPACE"
    assert body["workspace_id"].startswith("workspace-")
    assert body["coverage"] == {
        "restorable": 1,
        "audit_only": 1,
        "excluded": 1,
        "unreachable": 0,
    }
    assert str(workspace.resolve()) not in json.dumps(body)

    projection = read_verified(client, "/api/v1/recovery", headers=headers).json()
    assert projection["latest_checkpoint"]["scope_kind"] == "HOST_WORKSPACE"
    assert projection["latest_checkpoint"]["workspace_id"] == body["workspace_id"]
    assert projection["latest_checkpoint"]["coverage"]["counts"] == body["coverage"]
    assert "PRODUCT_CONFIG_TARGET_ONLY" not in projection["limitations"]

    database = StateDB(product_state / "state.db")
    database.connect()
    try:
        checkpoint = database.get_checkpoint(int(body["checkpoint_id"]))
        artifact = SnapshotStore(Config(product_state).snapshot_dir()).load_recovery_v3(
            checkpoint["snapshot_path"]
        )
    finally:
        database.close()
    assert artifact["workspace"]["workspace_id"] == body["workspace_id"]
    assert artifact["manifest"][0]["logical_path"].startswith(
        f"/workspace/{body['workspace_id']}/"
    )
    assert str(workspace.resolve()) not in json.dumps(
        {**artifact, "blobs": list(artifact["blobs"])}
    )


def test_ambiguous_observed_workspaces_do_not_fall_back_to_product_config(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a.txt").write_text("a", encoding="utf-8")
    (second / "b.txt").write_text("b", encoding="utf-8")
    client, headers, product_state = _client(tmp_path, first, second)

    response = client.post("/api/v1/recovery/checkpoints", json={}, headers=headers)

    assert response.status_code == 409
    assert response.json()["reason_code"] == "WORKSPACE_SCOPE_AMBIGUOUS"
    database = StateDB(product_state / "state.db")
    database.connect()
    try:
        assert database.list_checkpoints() == []
    finally:
        database.close()


def test_workspace_checkpoint_rejects_caller_path_and_domain(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.txt").write_text("safe", encoding="utf-8")
    client, headers, _product_state = _client(tmp_path, workspace)

    response = client.post(
        "/api/v1/recovery/checkpoints",
        json={"target_path": str(tmp_path), "execution_domain_id": "caller"},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["reason_code"] == "RECOVERY_REQUEST_INVALID"


def test_refresh_records_checkpoint_diff_without_agent_attribution(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    modified = workspace / "modified.txt"
    deleted = workspace / "deleted.txt"
    modified.write_text("before", encoding="utf-8")
    deleted.write_text("delete me", encoding="utf-8")
    client, headers, _product_state = _client(tmp_path, workspace, workspace)
    checkpoint = client.post(
        "/api/v1/recovery/checkpoints", json={}, headers=headers
    ).json()

    modified.write_text("after", encoding="utf-8")
    deleted.unlink()
    (workspace / "created.txt").write_text("created", encoding="utf-8")

    refresh = client.post("/api/v1/discovery/refresh", json={}, headers=headers)

    assert refresh.status_code == 200
    assert refresh.json()["workspace_change_status"] == "AVAILABLE"
    assert refresh.json()["workspace_change_count"] == 3
    changes = read_verified(client, "/api/v1/changes", headers=headers).json()
    observed = [item for item in changes["items"] if item["type"] == "OBSERVED_CHANGE"]
    assert len(observed) == 3
    by_kind = {item["change_kind"]: item for item in observed}
    assert set(by_kind) == {"CREATED", "MODIFIED", "DELETED"}
    assert by_kind["CREATED"]["recovery_disposition"] == "NOT_IN_CHECKPOINT"
    assert by_kind["MODIFIED"]["recovery_disposition"] == "RECOVERABLE"
    assert by_kind["DELETED"]["recovery_disposition"] == "RECOVERABLE"
    assert all(item["attribution"] == "UNATTRIBUTED" for item in observed)
    assert all(item["checkpoint_id"] == checkpoint["checkpoint_id"] for item in observed)
    assert all(item["affected_objects"] for item in observed)
    serialized = json.dumps(observed)
    assert str(workspace.resolve()) not in serialized
    assert "agent-1" not in serialized
    assert "agent-2" not in serialized

    detail = client.get(
        f"/api/v1/evidence/{by_kind['CREATED']['event_id']}", headers=headers
    ).json()
    assert detail["sanitized_detail"]["change_kind"] == "CREATED"
    assert detail["sanitized_detail"]["attribution"] == "UNATTRIBUTED"
    assert detail["sanitized_detail"]["recovery_disposition"] == "NOT_IN_CHECKPOINT"

    repeated = client.post("/api/v1/discovery/refresh", json={}, headers=headers)
    assert repeated.status_code == 200
    repeated_changes = read_verified(client, "/api/v1/changes", headers=headers).json()
    assert (
        len(
            [
                item
                for item in repeated_changes["items"]
                if item["type"] == "OBSERVED_CHANGE"
            ]
        )
        == 3
    )


def test_created_audit_and_excluded_objects_are_never_projected_recoverable(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "baseline.txt").write_text("baseline", encoding="utf-8")
    client, headers, _product_state = _client(tmp_path, workspace)
    response = client.post("/api/v1/recovery/checkpoints", json={}, headers=headers)
    assert response.status_code == 200

    (workspace / ".env").write_text("API_KEY=sensitive", encoding="utf-8")
    dependency = workspace / "node_modules"
    dependency.mkdir()
    (dependency / "package.js").write_text("excluded", encoding="utf-8")

    refresh = client.post("/api/v1/discovery/refresh", json={}, headers=headers)

    assert refresh.status_code == 200
    observed = [
        item
        for item in read_verified(client, "/api/v1/changes", headers=headers).json()["items"]
        if item["type"] == "OBSERVED_CHANGE"
    ]
    assert {item["recovery_disposition"] for item in observed} == {
        "AUDIT_ONLY",
        "EXCLUDED",
    }
    assert all(item["change_kind"] == "CREATED" for item in observed)
    assert all(item["recovery_disposition"] != "RECOVERABLE" for item in observed)


def test_workspace_test_restore_then_legacy_restore_requires_preview_without_writes(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    modified = workspace / "modified.txt"
    deleted = workspace / "deleted.txt"
    sensitive = workspace / ".env"
    modified.write_text("before", encoding="utf-8")
    deleted.write_text("restore me", encoding="utf-8")
    sensitive.write_text("API_KEY=before", encoding="utf-8")
    dependency = workspace / "node_modules"
    dependency.mkdir()
    generated = dependency / "package.js"
    generated.write_text("before excluded", encoding="utf-8")
    client, headers, product_state = _client(tmp_path, workspace)
    checkpoint = client.post(
        "/api/v1/recovery/checkpoints", json={}, headers=headers
    ).json()

    modified.write_text("after", encoding="utf-8")
    deleted.unlink()
    created = workspace / "created.txt"
    created.write_text("quarantine me", encoding="utf-8")
    sensitive.write_text("API_KEY=after", encoding="utf-8")
    generated.write_text("after excluded", encoding="utf-8")

    tested = client.post(
        f"/api/v1/recovery/{checkpoint['checkpoint_id']}/test",
        json={},
        headers=headers,
    )

    assert tested.status_code == 200
    assert tested.json()["reason_code"] == "TEST_RESTORE_VERIFIED"
    assert tested.json()["verified_targets"] == 2
    assert modified.read_text(encoding="utf-8") == "after"
    assert not deleted.exists()
    assert created.read_text(encoding="utf-8") == "quarantine me"

    restored = client.post(
        f"/api/v1/recovery/{checkpoint['checkpoint_id']}/restore",
        json={"confirm": True},
        headers=headers,
    )

    assert restored.status_code == 409
    assert restored.json()["reason_code"] == "PREVIEW_REQUIRED"
    assert modified.read_text(encoding="utf-8") == "after"
    assert not deleted.exists()
    assert created.read_text(encoding="utf-8") == "quarantine me"
    assert sensitive.read_text(encoding="utf-8") == "API_KEY=after"
    assert generated.read_text(encoding="utf-8") == "after excluded"

    quarantine_root = Config(product_state).snapshot_dir().parent / "workspace-quarantine"
    quarantined = list(quarantine_root.rglob("*.item"))
    assert quarantined == []
    database = StateDB(product_state / "state.db")
    database.connect()
    try:
        record = database._conn.execute(
            """SELECT status, reason_code, relative_path_digest
               FROM workspace_quarantine_records"""
        ).fetchone()
    finally:
        database.close()
    assert record is None

    projection = read_verified(client, "/api/v1/recovery", headers=headers).json()
    assert projection["latest_checkpoint"]["actual_restore_status"] != "VERIFIED"
    assert projection["recovery_verified"] is False


def test_legacy_restore_does_not_enter_writer_without_preview(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    baseline = workspace / "baseline.txt"
    baseline.write_text("before", encoding="utf-8")
    client, headers, _product_state = _client(tmp_path, workspace)
    checkpoint = client.post(
        "/api/v1/recovery/checkpoints", json={}, headers=headers
    ).json()
    baseline.write_text("after", encoding="utf-8")
    created = workspace / "created.txt"
    created.write_text("must remain", encoding="utf-8")

    calls = []

    def _unavailable(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("legacy writer must not run")

    monkeypatch.setattr(
        "agentguard.recovery.workspace_adapter.HostWorkspaceRecoveryAdapter._materialize_restorable_set", _unavailable
    )
    restored = client.post(
        f"/api/v1/recovery/{checkpoint['checkpoint_id']}/restore",
        json={"confirm": True},
        headers=headers,
    )

    assert restored.status_code == 409
    assert restored.json()["reason_code"] == "PREVIEW_REQUIRED"
    assert calls == []
    assert baseline.read_text(encoding="utf-8") == "after"
    assert created.read_text(encoding="utf-8") == "must remain"
    projection = read_verified(client, "/api/v1/recovery", headers=headers).json()
    assert projection["latest_checkpoint"]["actual_restore_status"] != "VERIFIED"
    assert projection["recovery_verified"] is False


def test_legacy_restore_does_not_apply_permissions_without_preview(
    tmp_path, monkeypatch
):
    class _ApplyDeniedBackend(PosixPermissionBackend):
        def apply(self, path, proof):
            raise PermissionCapabilityError("WORKSPACE_PERMISSION_APPLY_DENIED")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    baseline = workspace / "baseline.txt"
    baseline.write_text("before", encoding="utf-8")
    client, headers, _product_state = _client(tmp_path, workspace)
    checkpoint = client.post(
        "/api/v1/recovery/checkpoints", json={}, headers=headers
    ).json()
    baseline.write_text("after", encoding="utf-8")
    monkeypatch.setattr(
        "agentguard.api.product_recovery.current_user_permission_backend",
        lambda: _ApplyDeniedBackend(),
    )

    restored = client.post(
        f"/api/v1/recovery/{checkpoint['checkpoint_id']}/restore",
        json={"confirm": True},
        headers=headers,
    )

    assert restored.status_code == 409
    assert restored.json()["reason_code"] == "PREVIEW_REQUIRED"
    assert baseline.read_text(encoding="utf-8") == "after"


def test_import_legacy_v3_checkpoint_then_preview_and_restore_native_bytes(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.txt"
    original = b"first line\r\nsecond line\r\n"
    target.write_bytes(original)
    client, headers, _product_state = _client(tmp_path, workspace)

    checkpoint = client.post("/api/v1/recovery/checkpoints", json={}, headers=headers)
    assert checkpoint.status_code == 200, checkpoint.text
    checkpoint_id = checkpoint.json()["checkpoint_id"]
    target.write_bytes(b"changed\n")

    registered = client.post(
        "/api/v1/protection/scopes",
        json={"source_id": "local", "path": str(workspace), "request_id": "import-register"},
        headers=headers,
    )
    assert registered.status_code == 201, registered.text
    scope_id = registered.json()["scope_id"]
    imported = client.post(
        f"/api/v1/protection/scopes/{scope_id}/checkpoints/{checkpoint_id}/import",
        json={}, headers=headers,
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["status"] == "IMPORTED"
    version_id = imported.json()["version_id"]
    versions = client.get(f"/api/v1/protection/scopes/{scope_id}/versions", headers=headers)
    assert versions.status_code == 200
    version = next(item for item in versions.json()["items"] if item["id"] == version_id)
    assert version["kind"] == "IMPORTED_V3"
    file_id = next(item["file_id"] for item in version["files"] if item["relative_path"] == "notes.txt")

    preview = client.post(
        "/api/v1/recovery/previews",
        json={"scope_id": scope_id, "mode": "restore_version", "file_ids": [file_id], "version_id": version_id},
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == "READY"
    queued = client.post(
        "/api/v1/recovery/operations",
        json={"preview_id": preview.json()["preview_id"], "digest": preview.json()["digest"], "request_id": "import-restore"},
        headers=headers,
    )
    assert queued.status_code == 202, queued.text
    # The owned lifecycle worker executes the queued operation. Reconciliation
    # must wait for its lease, then remain idempotent on the terminal result.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        observed = client.get(f"/api/v1/recovery/operations/{queued.json()['id']}", headers=headers)
        assert observed.status_code == 200, observed.text
        if observed.json()["state"] not in {"QUEUED", "RUNNING", "COMPENSATING"}:
            break
        time.sleep(.01)
    assert observed.json()["state"] == "SUCCEEDED", observed.text
    executed = client.post(
        f"/api/v1/recovery/operations/{queued.json()['id']}/reconcile",
        json={}, headers=headers,
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["state"] == "SUCCEEDED"
    assert target.read_bytes() == original
