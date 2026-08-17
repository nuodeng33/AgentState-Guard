"""Server-owned Host-native workspace checkpoint API contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime

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
from agentguard.recovery import workspace_adapter
from agentguard.recovery.workspace_permissions import (
    PermissionCapabilityError,
    PosixPermissionBackend,
)
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore

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
    client = TestClient(app)
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

    projection = client.get("/api/v1/recovery", headers=headers).json()
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
    changes = client.get("/api/v1/changes", headers=headers).json()
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
    repeated_changes = client.get("/api/v1/changes", headers=headers).json()
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
        for item in client.get("/api/v1/changes", headers=headers).json()["items"]
        if item["type"] == "OBSERVED_CHANGE"
    ]
    assert {item["recovery_disposition"] for item in observed} == {
        "AUDIT_ONLY",
        "EXCLUDED",
    }
    assert all(item["change_kind"] == "CREATED" for item in observed)
    assert all(item["recovery_disposition"] != "RECOVERABLE" for item in observed)


def test_workspace_test_restore_then_restore_quarantines_created_restorable(tmp_path):
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

    assert restored.status_code == 200
    body = restored.json()
    assert body["reason_code"] == "WORKSPACE_RESTORED_AND_VERIFIED"
    assert body["post_restore_status"] == "POST_RESTORE_VERIFIED"
    assert body["verified_targets"] == 2
    assert body["quarantined_targets"] == 1
    assert body["residue_targets"] == 0
    assert modified.read_text(encoding="utf-8") == "before"
    assert deleted.read_text(encoding="utf-8") == "restore me"
    assert not created.exists()
    assert sensitive.read_text(encoding="utf-8") == "API_KEY=after"
    assert generated.read_text(encoding="utf-8") == "after excluded"

    quarantine_root = Config(product_state).snapshot_dir().parent / "workspace-quarantine"
    quarantined = list(quarantine_root.rglob("*.item"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "quarantine me"
    database = StateDB(product_state / "state.db")
    database.connect()
    try:
        record = database._conn.execute(
            """SELECT status, reason_code, relative_path_digest
               FROM workspace_quarantine_records"""
        ).fetchone()
    finally:
        database.close()
    assert record[0:2] == ("QUARANTINED", "POST_CHECKPOINT_FILE_QUARANTINED")
    assert len(record[2]) == 64

    projection = client.get("/api/v1/recovery", headers=headers).json()
    assert projection["latest_checkpoint"]["actual_restore_status"] == "VERIFIED"
    assert projection["recovery_verified"] is True


def test_quarantine_failure_leaves_residue_and_downgrades_restore(tmp_path, monkeypatch):
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

    def _unavailable(*_args, **_kwargs):
        raise OSError("quarantine unavailable")

    monkeypatch.setattr(
        "agentguard.recovery.workspace_adapter._quarantine_file", _unavailable
    )
    restored = client.post(
        f"/api/v1/recovery/{checkpoint['checkpoint_id']}/restore",
        json={"confirm": True},
        headers=headers,
    )

    assert restored.status_code == 200
    body = restored.json()
    assert body["reason_code"] == "RESTORABLE_SET_RESTORED"
    assert body["post_restore_status"] == "POST_CHECKPOINT_RESIDUE_PRESENT"
    assert body["quarantined_targets"] == 0
    assert body["residue_targets"] == 1
    assert baseline.read_text(encoding="utf-8") == "before"
    assert created.read_text(encoding="utf-8") == "must remain"
    projection = client.get("/api/v1/recovery", headers=headers).json()
    assert projection["latest_checkpoint"]["actual_restore_status"] != "VERIFIED"
    assert projection["recovery_verified"] is False


def test_current_user_permission_apply_failure_does_not_require_uac_or_mutate_target(
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
    assert restored.json()["reason_code"] == "WORKSPACE_PERMISSION_APPLY_DENIED"
    assert baseline.read_text(encoding="utf-8") == "after"


def test_quarantine_root_inside_workspace_is_rejected_without_creating_it(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    unsafe = workspace / ".agentguard" / "workspace-quarantine"

    resolved = workspace_adapter._prepare_quarantine_root(unsafe, workspace)

    assert resolved is None
    assert not unsafe.exists()
