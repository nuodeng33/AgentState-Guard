"""Minimal Discovery -> Ledger -> authoritative workspace binding contract."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from agentguard.api.r4_projection import R4ReadProjectionService
from agentguard.discovery import (
    AgentDescriptor,
    AgentLifecycleStatus,
    CapabilityStatus,
    DiscoverySnapshot,
    ExecutionDomainDescriptor,
    ExecutionDomainKind,
    ProbeEvidence,
    RuntimeDescriptor,
    WorkspaceDescriptor,
)
from agentguard.evidence import discovery_adapter
from agentguard.evidence.ledger import EvidenceLedger, verify_ledger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore

OBSERVED_AT = datetime(2026, 8, 9, 8, 30, tzinfo=UTC)


def _evidence(
    evidence_id: str,
    fact_type: str,
    value: dict[str, object],
) -> ProbeEvidence:
    return ProbeEvidence(
        evidence_id=evidence_id,
        collector="server-discovery",
        source="local",
        observed_at=OBSERVED_AT,
        fact_type=fact_type,
        value=value,
        status=CapabilityStatus.AVAILABLE,
        sanitized=True,
    )


def _snapshot() -> DiscoverySnapshot:
    domain_id = "agent-dev-domain"
    runtime_id = "agent-dev-runtime"
    agent_id = "cloudcli-agent"
    workspace_id = "agentstate-guard-workspace"
    return DiscoverySnapshot(
        snapshot_id="snapshot-authoritative-1",
        observed_at=OBSERVED_AT,
        domains=(
            ExecutionDomainDescriptor(
                domain_id=domain_id,
                kind=ExecutionDomainKind.CONTAINER,
                evidence_ids=("domain-evidence",),
                confidence=0.8,
            ),
        ),
        runtimes=(
            RuntimeDescriptor(
                runtime_id=runtime_id,
                runtime_type="DOCKER_DESKTOP_WSL2",
                domain_id=domain_id,
                status=CapabilityStatus.AVAILABLE,
                evidence_ids=("runtime-evidence",),
                confidence=0.8,
            ),
        ),
        agents=(
            AgentDescriptor(
                agent_id=agent_id,
                agent_type="CLOUDCLI",
                lifecycle=AgentLifecycleStatus.RUNNING,
                domain_id=domain_id,
                runtime_id=runtime_id,
                workspace_ids=(workspace_id,),
                evidence_ids=("agent-evidence",),
                confidence=0.8,
            ),
        ),
        workspaces=(
            WorkspaceDescriptor(
                workspace_id=workspace_id,
                domain_id=domain_id,
                runtime_ids=(runtime_id,),
                agent_ids=(agent_id,),
                evidence_ids=("workspace-evidence",),
                confidence=0.8,
            ),
        ),
        evidence=(
            _evidence("domain-evidence", "domain.container", {"kind": "CONTAINER"}),
            _evidence(
                "runtime-evidence",
                "runtime.metadata",
                {"runtime_kind": "DOCKER_DESKTOP_WSL2"},
            ),
            _evidence(
                "agent-evidence",
                "agent.metadata",
                {"agent_kind": "CLOUDCLI", "role": "AGENT_HOST"},
            ),
            _evidence(
                "workspace-evidence",
                "workspace.present",
                {"workspace_kind": "PROJECT"},
            ),
        ),
        status=CapabilityStatus.AVAILABLE,
    )


@pytest.fixture
def database(tmp_path):
    value = StateDB(tmp_path / "state.db")
    value.connect()
    try:
        yield value
    finally:
        value.close()


def _record(
    database: StateDB,
    snapshot: DiscoverySnapshot | None = None,
    *,
    recorded_at: datetime = OBSERVED_AT,
):
    return discovery_adapter.record_discovery_snapshot(
        database,
        snapshot or _snapshot(),
        recorded_at=recorded_at,
    )


def _project(database: StateDB, tmp_path) -> dict[str, object]:
    return R4ReadProjectionService(
        database,
        SnapshotStore(tmp_path / "snapshots"),
    ).agents()


def _workspace(database: StateDB, tmp_path) -> dict[str, object]:
    return _project(database, tmp_path)["items"][0]["workspace"]


def _without_binding(snapshot: DiscoverySnapshot | None = None) -> DiscoverySnapshot:
    return replace(snapshot or _snapshot(), workspaces=())


def _binding_count(database: StateDB) -> int:
    return database._conn.execute(
        "SELECT COUNT(*) FROM evidence_ledger_events WHERE event_type = 'WORKSPACE_LINKED'"
    ).fetchone()[0]


def _append_binding(
    database: StateDB,
    *,
    event_id: str,
    workspace_id: str = "agentstate-guard-workspace",
    domain_id: str = "agent-dev-domain",
    payload_binding_id: str | None = None,
) -> None:
    agent_event_id, agent_payload_json = database._conn.execute(
        """SELECT event_id, payload_safe_json FROM evidence_ledger_events
           WHERE event_type = 'AGENT_DETECTED' ORDER BY sequence DESC LIMIT 1"""
    ).fetchone()
    runtime_event_id = database._conn.execute(
        """SELECT event_id FROM evidence_ledger_events
           WHERE event_type = 'RUNTIME_DETECTED' ORDER BY sequence DESC LIMIT 1"""
    ).fetchone()[0]
    with database.transaction() as connection:
        EvidenceLedger().append(
            connection,
            EvidenceEvent(
                schema_version=1,
                event_id=event_id,
                recorded_at=OBSERVED_AT,
                observed_at=OBSERVED_AT,
                event_family=EventFamily.DISCOVERY,
                event_type=EventType.WORKSPACE_LINKED,
                source="discovery.workspace-binding",
                result="available",
                execution_domain_id=domain_id,
                supervision_session_id=None,
                transaction_id=None,
                checkpoint_id=None,
                subject_ref="cloudcli-agent",
                evidence_refs=(
                    "agent-evidence",
                    "runtime-evidence",
                    "workspace-evidence",
                ),
                payload_safe={
                    "fact_type": "workspace.binding",
                    "snapshot_id": json.loads(agent_payload_json)["snapshot_id"],
                    "binding_id": payload_binding_id or event_id,
                    "workspace_id": workspace_id,
                    "runtime_id": "agent-dev-runtime",
                    "agent_id": "cloudcli-agent",
                    "runtime_event_id": runtime_event_id,
                    "agent_event_id": agent_event_id,
                },
            ),
        )


def test_authoritative_snapshot_is_atomically_ledgered_and_projected(
    database,
    tmp_path,
):
    receipts = _record(database)

    assert len(receipts) == 3
    assert verify_ledger(database._conn) == []
    assert [
        row[0]
        for row in database._conn.execute(
            "SELECT event_type FROM evidence_ledger_events ORDER BY sequence"
        )
    ] == ["RUNTIME_DETECTED", "AGENT_DETECTED", "WORKSPACE_LINKED"]
    assert _project(database, tmp_path) == {
        "schema_version": "r4-p8-1",
        "view": "agents",
        "status": "AVAILABLE",
        "reason_code": "R4_AGENTS_AVAILABLE",
        "observed_at": OBSERVED_AT.isoformat(),
        "evidence_refs": [receipts[1].event_id, receipts[2].event_id],
        "items": [
            {
                "detected_identity": "CLOUDCLI",
                "instance_label": None,
                "role": "AGENT_HOST",
                    "lifecycle": "RUNNING",
                "confidence": 0.8,
                "execution_domain_id": "agent-dev-domain",
                "workspace": {
                    "status": "BOUND",
                    "workspace_id": "agentstate-guard-workspace",
                    "binding_ref": receipts[2].event_id,
                    "reason_code": "WORKSPACE_BINDING_VERIFIED",
                },
                "reason_code": "AGENT_DETECTED",
                "uncertainty": False,
                "observed_at": OBSERVED_AT.isoformat(),
                "evidence_refs": [receipts[1].event_id, receipts[2].event_id],
            }
        ],
    }


def test_missing_workspace_binding_fails_closed(database, tmp_path):
    _record(database, _without_binding())

    projection = _project(database, tmp_path)
    assert projection["status"] == "DEGRADED"
    assert projection["reason_code"] == "R4_WORKSPACE_BINDING_INCOMPLETE"
    assert _workspace(database, tmp_path)["reason_code"] == "WORKSPACE_BINDING_MISSING"


def test_caller_only_workspace_path_never_becomes_authority(
    database,
    tmp_path,
):
    raw_path = str(tmp_path / "caller-selected" / "AgentState-Guard")
    snapshot = _snapshot()
    candidate = replace(
        snapshot.evidence[-1],
        fact_type="workspace.candidate",
        value={"path_hint": raw_path},
    )

    _record(database, replace(snapshot, evidence=(*snapshot.evidence[:-1], candidate)))

    assert _binding_count(database) == 0
    assert raw_path not in " ".join(database._conn.iterdump())
    assert _workspace(database, tmp_path)["reason_code"] == "WORKSPACE_BINDING_MISSING"


def test_workspace_binding_requires_complete_server_evidence(database, tmp_path):
    snapshot = _snapshot()
    domain = replace(snapshot.domains[0], evidence_ids=("missing-domain-evidence",))

    _record(database, replace(snapshot, domains=(domain,)))

    assert _binding_count(database) == 0
    assert _workspace(database, tmp_path)["reason_code"] == "WORKSPACE_BINDING_MISSING"


def test_workspace_binding_requires_available_source_facts(database, tmp_path):
    snapshot = _snapshot()
    unavailable = replace(
        snapshot.evidence[1],
        status=CapabilityStatus.PERMISSION_DENIED,
    )

    _record(
        database,
        replace(snapshot, evidence=(snapshot.evidence[0], unavailable, *snapshot.evidence[2:])),
    )

    assert _binding_count(database) == 0
    assert _workspace(database, tmp_path)["reason_code"] == "WORKSPACE_BINDING_MISSING"


def test_conflicting_workspace_bindings_fail_closed(database, tmp_path):
    _record(database, _without_binding())
    _append_binding(database, event_id="workspace-binding-conflict-a")
    _append_binding(
        database,
        event_id="workspace-binding-conflict-b",
        workspace_id="other-workspace",
    )

    assert verify_ledger(database._conn) == []
    assert _workspace(database, tmp_path)["reason_code"] == "WORKSPACE_BINDING_CONFLICT"


def test_binding_payload_identity_mismatch_fails_closed(database, tmp_path):
    _record(database, _without_binding())
    _append_binding(
        database,
        event_id="workspace-binding-ledger-event",
        payload_binding_id="workspace-binding-other-identity",
    )

    assert verify_ledger(database._conn) == []
    assert _workspace(database, tmp_path)["reason_code"] == "WORKSPACE_BINDING_INVALID"


def test_binding_recorded_before_source_facts_fails_closed(database, tmp_path):
    events = discovery_adapter.discovery_events(_snapshot(), recorded_at=OBSERVED_AT)
    with database.transaction() as connection:
        for event in (events[2], events[0], events[1]):
            EvidenceLedger().append(connection, event)

    assert verify_ledger(database._conn) == []
    assert _workspace(database, tmp_path)["reason_code"] == "WORKSPACE_BINDING_STALE"


def test_newer_agent_fact_makes_old_binding_stale(database, tmp_path):
    snapshot = _snapshot()
    _record(database, snapshot)
    later = OBSERVED_AT + timedelta(minutes=1)
    newer = replace(
        snapshot,
        snapshot_id="snapshot-authoritative-2",
        observed_at=later,
        workspaces=(),
        evidence=tuple(replace(item, observed_at=later) for item in snapshot.evidence),
    )

    _record(database, newer, recorded_at=later)

    projection = _project(database, tmp_path)
    assert len(projection["items"]) == 1
    assert _workspace(database, tmp_path)["reason_code"] == "WORKSPACE_BINDING_STALE"


@pytest.mark.parametrize(
    ("workspace_id", "domain_id", "reason_code"),
    [
        (
            "agentstate-guard-workspace",
            "other-domain",
            "WORKSPACE_BINDING_DOMAIN_MISMATCH",
        ),
        (
            "other-workspace",
            "agent-dev-domain",
            "WORKSPACE_BINDING_WORKSPACE_MISMATCH",
        ),
    ],
)
def test_cross_authority_binding_fails_closed(
    database,
    tmp_path,
    workspace_id,
    domain_id,
    reason_code,
):
    _record(database, _without_binding())
    _append_binding(
        database,
        event_id=f"workspace-binding-{reason_code.casefold()}",
        workspace_id=workspace_id,
        domain_id=domain_id,
    )

    assert verify_ledger(database._conn) == []
    assert _workspace(database, tmp_path)["reason_code"] == reason_code


def test_corrupt_ledger_blocks_projection_and_further_ingestion(database, tmp_path):
    _record(database)
    database._conn.execute("DROP TRIGGER evidence_ledger_events_no_update")
    database._conn.execute(
        "UPDATE evidence_ledger_events SET result = 'forged' WHERE event_type = 'WORKSPACE_LINKED'"
    )
    database._conn.commit()

    assert _project(database, tmp_path) == {
        "schema_version": "r4-p8-1",
        "view": "agents",
        "status": "DEGRADED",
        "reason_code": "R4_LEDGER_INVALID",
        "evidence_refs": [],
        "items": [],
    }
    before = database._conn.execute(
        "SELECT COUNT(*) FROM evidence_ledger_events"
    ).fetchone()
    with pytest.raises(ValueError, match="DISCOVERY_LEDGER_INVALID"):
        _record(database, replace(_snapshot(), snapshot_id="snapshot-after-corruption"))
    assert database._conn.execute(
        "SELECT COUNT(*) FROM evidence_ledger_events"
    ).fetchone() == before


def test_authoritative_binding_does_not_touch_unrelated_dirty_content(
    database,
    tmp_path,
):
    unrelated = tmp_path / "unrelated-dirty.txt"
    original = b"user-owned dirty content\n"
    unrelated.write_bytes(original)

    _record(database)
    projection = _project(database, tmp_path)

    assert _workspace(database, tmp_path)["status"] == "BOUND"
    assert unrelated.read_bytes() == original
    assert str(unrelated) not in json.dumps(projection)
    assert str(unrelated) not in " ".join(database._conn.iterdump())
