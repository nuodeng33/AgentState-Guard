"""Durable Host-native workspace scope binding and privacy tests."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest

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


def test_latest_not_observed_scope_deactivates_previous_binding(database, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    service = WorkspaceScopeService(database)
    service.bind(
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
    assert service.active_scope() is None


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
