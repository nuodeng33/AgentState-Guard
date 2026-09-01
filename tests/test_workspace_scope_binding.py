"""Durable Host-native workspace scope binding and privacy tests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from agentguard.api.r4_projection import R4ReadProjectionService
from agentguard.api.server import create_app
from agentguard.discovery import CapabilityStatus, DiscoverySnapshot
from agentguard.discovery.agents import ProcessWorkspaceAuthority
from agentguard.discovery.product import ProductDiscoveryReport
from agentguard.discovery.workspace_authority import (
    ResolvedWorkspaceAuthority,
    workspace_root_digest,
)
from agentguard.evidence.ledger import verify_ledger
from agentguard.recovery import workspace_scope
from agentguard.recovery.workspace_scope import (
    WorkspaceScopeError,
    WorkspaceScopeService,
)
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore

NOW = datetime(2026, 8, 17, 11, 0, tzinfo=UTC)


@pytest.fixture
def database(tmp_path):
    value = StateDB(tmp_path / "state.db")
    value.connect()
    try:
        yield value
    finally:
        value.close()


def _resolved(root):
    digest = workspace_root_digest(root.resolve(), "windows-current")
    return ResolvedWorkspaceAuthority(
        status="BOUND",
        reason_code="WORKSPACE_SCOPE_VERIFIED",
        root_path=root.resolve(),
        workspace_id=f"workspace-{digest.split(':', 1)[1][:24]}",
        root_digest=digest,
        execution_domain_id="windows-current",
        agent_ids=("agent-codex",),
        process_instance_ids=("process-codex",),
        evidence_refs=("product-process-evidence",),
    )


def _resolved_volume():
    from agentguard.discovery.workspace_authority import storage_workspace_digest

    identity = "sha256:" + "1" * 64
    digest = storage_workspace_digest(identity, "/", "host-native")
    return ResolvedWorkspaceAuthority(
        status="BOUND",
        reason_code="WORKSPACE_SCOPE_VERIFIED",
        root_path=None,
        workspace_id=f"workspace-{digest.split(':', 1)[1][:24]}",
        root_digest=digest,
        execution_domain_id="host-native",
        agent_ids=("agent-kimi",),
        process_instance_ids=("process-kimi",),
        evidence_refs=("volume-evidence",),
        storage_kind="DOCKER_NAMED_VOLUME",
        storage_resource_identity=identity,
        storage_locator=(
            "agent-k3-workspace\x1flocal\x1flocal\x1f2026-07-28T13:33:50Z\x1f"
            + "sha256:"
            + "2" * 64
            + "\x1fsha256:"
            + "3" * 64
        ),
        logical_root="/",
        durability="DURABLE",
        current_reachability="AVAILABLE",
        protection_capability="SUPPORTED",
        protection_reason_code="DOCKER_VOLUME_BACKEND_SUPPORTED",
        agent_mutation_capability="READ_WRITE",
    )


def test_scope_row_and_ledger_event_commit_together(database, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()

    result = WorkspaceScopeService(database).bind(
        _resolved(root),
        recorded_at=NOW,
        discovery_snapshot_id="snapshot-1",
    )

    assert result.reason_code == "WORKSPACE_PROTECTION_BOUND"
    assert verify_ledger(database._conn) == []
    row = database._conn.execute(
        """SELECT root_path, root_digest, ledger_event_id
           FROM workspace_scope_observations WHERE observation_id = ?""",
        (result.observation_id,),
    ).fetchone()
    assert row[0] == str(root.resolve())
    assert row[1] == result.root_digest
    payload = json.loads(
        database._conn.execute(
            "SELECT payload_safe_json FROM evidence_ledger_events WHERE event_id = ?",
            (row[2],),
        ).fetchone()[0]
    )
    assert payload["root_digest"] == result.root_digest
    assert str(root.resolve()) not in json.dumps(payload)


def test_named_volume_scope_persists_without_host_path(database, tmp_path):
    resolved = _resolved_volume()
    result = WorkspaceScopeService(database).bind(
        resolved,
        recorded_at=NOW,
        discovery_snapshot_id="snapshot-volume",
    )

    authority = WorkspaceScopeService(database).resolve_authority(result.workspace_id)
    assert authority.authority_state == "BOUND"
    assert authority.scope is not None
    assert authority.scope.root_path is None
    assert authority.storage_kind == "DOCKER_NAMED_VOLUME"
    assert authority.storage_resource_identity == resolved.storage_resource_identity
    assert authority.logical_root == "/"
    assert authority.protection_capability == "SUPPORTED"
    assert "agent-k3-workspace" not in json.dumps(authority.safe_summary())
    recovery = R4ReadProjectionService(
        database,
        SnapshotStore(tmp_path / "snapshots"),
    ).recovery()
    assert recovery["scope_kind"] == "DOCKER_NAMED_VOLUME"
    assert recovery["workspace_id"] == resolved.workspace_id
    assert recovery["capability_supported"] is True
    assert recovery["action_eligible"] is True


def test_named_volume_authority_remains_bound_when_backend_is_unsupported(
    database, tmp_path
):
    resolved = replace(
        _resolved_volume(),
        protection_capability="UNSUPPORTED",
        protection_reason_code="DOCKER_VOLUME_HELPER_RUNTIME_UNAVAILABLE",
    )
    result = WorkspaceScopeService(database).bind(
        resolved,
        recorded_at=NOW,
        discovery_snapshot_id="snapshot-volume-unsupported",
    )

    authority = WorkspaceScopeService(database).resolve_authority(result.workspace_id)
    recovery = R4ReadProjectionService(
        database,
        SnapshotStore(tmp_path / "snapshots"),
    ).recovery()

    assert authority.authority_state == "BOUND"
    assert authority.protection_capability == "UNSUPPORTED"
    assert recovery["scope_kind"] == "DOCKER_NAMED_VOLUME"
    assert recovery["capability_supported"] is False
    assert recovery["action_eligible"] is False
    assert recovery["eligibility_reason_code"] == (
        "DOCKER_VOLUME_HELPER_RUNTIME_UNAVAILABLE"
    )


def test_scope_ledger_failure_rolls_back_private_path_row(
    database, tmp_path, monkeypatch
):
    root = tmp_path / "workspace"
    root.mkdir()
    checks = iter(([], ["LEDGER_CURR_HASH_INVALID"]))
    monkeypatch.setattr(workspace_scope, "verify_ledger", lambda _conn: next(checks))

    with pytest.raises(WorkspaceScopeError, match="WORKSPACE_SCOPE_LEDGER_INVALID"):
        WorkspaceScopeService(database).bind(
            _resolved(root),
            recorded_at=NOW,
            discovery_snapshot_id="snapshot-1",
        )

    assert database._conn.execute(
        "SELECT COUNT(*) FROM workspace_scope_observations"
    ).fetchone()[0] == 0
    assert database._conn.execute(
        "SELECT COUNT(*) FROM evidence_ledger_events"
    ).fetchone()[0] == 0


def test_active_scope_revalidates_private_root_and_ledger(database, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    service = WorkspaceScopeService(database)
    result = service.bind(
        _resolved(root),
        recorded_at=NOW,
        discovery_snapshot_id="snapshot-1",
    )

    active = service.active_scope()

    assert active is not None
    assert active.observation_id == result.observation_id
    assert active.root_path == root.resolve()
    assert active.agent_ids == ("agent-codex",)


def test_not_observed_does_not_clear_existing_workspace_authority(database, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    service = WorkspaceScopeService(database)
    bound = service.bind(
        _resolved(root),
        recorded_at=NOW,
        discovery_snapshot_id="snapshot-1",
    )

    result = service.bind(
        ResolvedWorkspaceAuthority(
            status="NOT_OBSERVED",
            reason_code="WORKSPACE_SCOPE_NOT_OBSERVED",
        ),
        recorded_at=NOW.replace(minute=1),
        discovery_snapshot_id="snapshot-2",
    )

    assert result.status == "NOT_OBSERVED"
    authority = service.resolve_authority(bound.workspace_id)
    assert authority.authority_state == "BOUND"
    assert authority.authority_observation_ref == bound.observation_id
    assert authority.scope is not None
    assert authority.scope.root_path == root.resolve()


def test_workspace_authorities_are_resolved_independently(database, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    service = WorkspaceScopeService(database)
    first_result = service.bind(
        _resolved(first),
        recorded_at=NOW,
        discovery_snapshot_id="snapshot-first",
    )
    second_result = service.bind(
        _resolved(second),
        recorded_at=NOW.replace(minute=1),
        discovery_snapshot_id="snapshot-second",
    )

    first_authority = service.resolve_authority(first_result.workspace_id)
    second_authority = service.resolve_authority(second_result.workspace_id)

    assert first_authority.authority_state == "BOUND"
    assert first_authority.authority_observation_ref == first_result.observation_id
    assert second_authority.authority_state == "BOUND"
    assert second_authority.authority_observation_ref == second_result.observation_id


def test_stale_workspace_authority_only_affects_that_workspace(database, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    service = WorkspaceScopeService(database)
    first_result = service.bind(
        _resolved(first),
        recorded_at=NOW,
        discovery_snapshot_id="snapshot-first",
    )
    second_result = service.bind(
        _resolved(second),
        recorded_at=NOW.replace(minute=1),
        discovery_snapshot_id="snapshot-second",
    )
    first.rmdir()

    first_authority = service.resolve_authority(first_result.workspace_id)
    second_authority = service.resolve_authority(second_result.workspace_id)

    assert first_authority.authority_state == "UNKNOWN"
    assert first_authority.reason_code == "WORKSPACE_SCOPE_BINDING_INVALID"
    assert second_authority.authority_state == "BOUND"


class _AuthorityDiscovery:
    def __init__(self, root) -> None:
        self._root = root

    def discover(self):
        raise AssertionError("server must consume the authority-bearing report")

    def discover_with_authority(self):
        snapshot = DiscoverySnapshot(
            snapshot_id="startup-authority-snapshot",
            observed_at=NOW,
            status=CapabilityStatus.AVAILABLE,
        )
        return ProductDiscoveryReport(
            snapshot=snapshot,
            workspace_authorities=(
                ProcessWorkspaceAuthority(
                    process_instance_id="process-codex",
                    candidate_id="candidate-codex",
                    execution_domain_id="windows-current",
                    cwd=self._root,
                    evidence_refs=("product-process-evidence",),
                    agent_id="agent-codex",
                ),
            ),
        )


def test_packaged_startup_persists_exact_authority_report(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    state_path = tmp_path / "product-state" / "state.db"

    create_app(
        state_db_path=state_path,
        config={"base_dir": str(tmp_path / "product-state")},
        discovery_service=_AuthorityDiscovery(root),
        product_startup_discovery=True,
    )

    connection = sqlite3.connect(state_path)
    try:
        row = connection.execute(
            """SELECT status, root_path, reason_code
               FROM workspace_scope_observations
               ORDER BY observation_sequence DESC LIMIT 1"""
        ).fetchone()
    finally:
        connection.close()
    assert row == ("BOUND", str(root.resolve()), "WORKSPACE_PROTECTION_BOUND")
