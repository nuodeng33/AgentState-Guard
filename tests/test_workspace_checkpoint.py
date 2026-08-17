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
