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
from agentguard.discovery.workspace_authority import (
    ResolvedWorkspaceAuthority,
    workspace_root_digest,
)
from agentguard.evidence import discovery_adapter
from agentguard.evidence.ledger import EvidenceLedger, verify_ledger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.evidence.product_target import record_product_target_binding
from agentguard.policy.models import PolicyInput
from agentguard.recovery.workspace_scope import WorkspaceScopeService
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore
from agentguard.supervision.service import SupervisionService

OBSERVED_AT = datetime(2026, 8, 9, 8, 30, tzinfo=UTC)
PRODUCT_SHA = "a" * 40


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


def _correlation(database: StateDB, tmp_path) -> dict[str, object]:
    return _project(database, tmp_path)["items"][0]["workspace_correlation"]


def _supervision(database: StateDB, tmp_path) -> dict[str, object]:
    return R4ReadProjectionService(
        database,
        SnapshotStore(tmp_path / "snapshots"),
    ).supervision()


def _append_host_activity(
    database: StateDB,
    *,
    event_id: str,
    event_type: EventType,
    reason_code: str,
) -> None:
    agent_event_id = database._conn.execute(
        "SELECT event_id FROM evidence_ledger_events "
        "WHERE event_type = 'AGENT_DETECTED' ORDER BY sequence DESC LIMIT 1"
    ).fetchone()[0]
    with database.transaction() as connection:
        EvidenceLedger().append(
            connection,
            EvidenceEvent(
                schema_version=1,
                event_id=event_id,
                recorded_at=OBSERVED_AT + timedelta(seconds=5),
                observed_at=OBSERVED_AT + timedelta(seconds=5),
                event_family=EventFamily.SUPERVISION,
                event_type=event_type,
                source="host-native-observer",
                result="OBSERVED",
                execution_domain_id="agent-dev-domain",
                supervision_session_id=None,
                transaction_id=None,
                checkpoint_id=None,
                subject_ref="cloudcli-agent",
                evidence_refs=(agent_event_id,),
                payload_safe={
                    "activity_kind": "CHILD_PROCESS",
                    "agent_ref": "cloudcli-agent",
                    "executable_basename": "git.exe",
                    "process_ref": "f" * 64,
                    "reason_code": reason_code,
                },
            ),
        )


def _policy_input(target: str = "agentstate-guard-workspace") -> PolicyInput:
    return PolicyInput(
        intent_kind="discovery",
        effect_kind="read_metadata",
        target_refs=(target,),
        execution_domain_id="agent-dev-domain",
        declared_scope=(target,),
        requested_capabilities=(),
        network_effect=False,
        privilege_effect=False,
        destructive_effect=False,
        secret_access=False,
        evidence_refs=("caller-evidence",),
    )


def _without_binding(snapshot: DiscoverySnapshot | None = None) -> DiscoverySnapshot:
    return replace(snapshot or _snapshot(), workspaces=())


def _binding_count(database: StateDB) -> int:
    return database._conn.execute(
        "SELECT COUNT(*) FROM evidence_ledger_events WHERE event_type = 'WORKSPACE_LINKED'"
    ).fetchone()[0]


def _bind_protection_authority(database: StateDB, root) -> None:
    root.mkdir(exist_ok=True)
    digest = workspace_root_digest(root.resolve(), "agent-dev-domain")
    WorkspaceScopeService(database).bind(
        ResolvedWorkspaceAuthority(
            status="BOUND",
            reason_code="WORKSPACE_SCOPE_VERIFIED",
            root_path=root.resolve(),
            workspace_id="agentstate-guard-workspace",
            root_digest=digest,
            execution_domain_id="agent-dev-domain",
            agent_ids=("cloudcli-agent",),
            process_instance_ids=("cloudcli-process",),
            evidence_refs=("workspace-evidence",),
        ),
        recorded_at=OBSERVED_AT,
        discovery_snapshot_id="snapshot-authoritative-1",
    )


def test_workspace_linked_alone_is_correlation_not_protection_authority(
    database,
    tmp_path,
):
    receipts = _record(database)

    item = _project(database, tmp_path)["items"][0]

    assert item["workspace_correlation"] == {
        "status": "LINKED",
        "workspace_id": "agentstate-guard-workspace",
        "binding_ref": receipts[2].event_id,
        "reason_code": "WORKSPACE_CORRELATION_VERIFIED",
    }
    assert item["workspace"]["authority_state"] == "UNKNOWN"
    assert item["workspace"]["reason_code"] == (
        "WORKSPACE_PROTECTION_AUTHORITY_MISSING"
    )


def test_agent_and_supervision_share_durable_workspace_authority(
    database,
    tmp_path,
):
    _record(database)
    _bind_protection_authority(database, tmp_path / "protected")

    agent = _project(database, tmp_path)["items"][0]
    supervised = _supervision(database, tmp_path)["observed_agents"][0]

    assert agent["workspace"]["authority_state"] == "BOUND"
    assert agent["workspace"]["authority_observation_ref"] is not None
    assert supervised["workspace"] == agent["workspace"]
    assert supervised["workspace_correlation"] == agent["workspace_correlation"]


def test_workspace_correlation_alone_is_checkpoint_ineligible(database, tmp_path):
    _record(database)

    recovery = R4ReadProjectionService(
        database,
        SnapshotStore(tmp_path / "snapshots"),
    ).recovery()

    assert recovery["action_eligible"] is False
    assert recovery["eligibility_reason_code"] == "NO_VERIFIED_WORKSPACE"


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
    projection = _project(database, tmp_path)
    item = projection["items"][0]
    assert projection["status"] == "DEGRADED"
    assert projection["reason_code"] == "R4_WORKSPACE_BINDING_INCOMPLETE"
    assert projection["identified_count"] == 1
    assert item["detected_identity"] == "CLOUDCLI"
    assert item["workspace_correlation"]["status"] == "LINKED"
    assert item["workspace_correlation"]["binding_ref"] == receipts[2].event_id
    assert item["workspace"]["authority_state"] == "UNKNOWN"
    assert item["workspace"]["reason_code"] == (
        "WORKSPACE_PROTECTION_AUTHORITY_MISSING"
    )


def test_workspace_binding_uses_agent_observation_time_when_probe_finishes_later(
    database,
    tmp_path,
):
    delayed = OBSERVED_AT + timedelta(seconds=9)
    snapshot = _snapshot()
    snapshot = replace(
        snapshot,
        evidence=tuple(
            replace(item, observed_at=delayed)
            if item.fact_type in {"runtime.metadata", "agent.metadata"}
            else item
            for item in snapshot.evidence
        ),
    )

    receipts = _record(database, snapshot)

    assert len(receipts) == 3
    assert _correlation(database, tmp_path) == {
        "status": "LINKED",
        "workspace_id": "agentstate-guard-workspace",
        "binding_ref": receipts[2].event_id,
        "reason_code": "WORKSPACE_CORRELATION_VERIFIED",
    }


def test_missing_workspace_binding_fails_closed(database, tmp_path):
    _record(database, _without_binding())

    projection = _project(database, tmp_path)
    assert projection["status"] == "DEGRADED"
    assert projection["reason_code"] == "R4_WORKSPACE_BINDING_INCOMPLETE"
    assert _correlation(database, tmp_path)["reason_code"] == "WORKSPACE_BINDING_MISSING"


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
    assert _correlation(database, tmp_path)["reason_code"] == "WORKSPACE_BINDING_MISSING"


def test_workspace_binding_requires_complete_server_evidence(database, tmp_path):
    snapshot = _snapshot()
    domain = replace(snapshot.domains[0], evidence_ids=("missing-domain-evidence",))

    _record(database, replace(snapshot, domains=(domain,)))

    assert _binding_count(database) == 0
    assert _correlation(database, tmp_path)["reason_code"] == "WORKSPACE_BINDING_MISSING"


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
    assert _correlation(database, tmp_path)["reason_code"] == "WORKSPACE_BINDING_MISSING"


def test_conflicting_workspace_bindings_fail_closed(database, tmp_path):
    _record(database, _without_binding())
    _append_binding(database, event_id="workspace-binding-conflict-a")
    _append_binding(
        database,
        event_id="workspace-binding-conflict-b",
        workspace_id="other-workspace",
    )

    assert verify_ledger(database._conn) == []
    assert _correlation(database, tmp_path)["reason_code"] == "WORKSPACE_BINDING_CONFLICT"


def test_binding_payload_identity_mismatch_fails_closed(database, tmp_path):
    _record(database, _without_binding())
    _append_binding(
        database,
        event_id="workspace-binding-ledger-event",
        payload_binding_id="workspace-binding-other-identity",
    )

    assert verify_ledger(database._conn) == []
    assert _correlation(database, tmp_path)["reason_code"] == "WORKSPACE_BINDING_INVALID"


def test_binding_recorded_before_source_facts_fails_closed(database, tmp_path):
    events = discovery_adapter.discovery_events(_snapshot(), recorded_at=OBSERVED_AT)
    with database.transaction() as connection:
        for event in (events[2], events[0], events[1]):
            EvidenceLedger().append(connection, event)

    assert verify_ledger(database._conn) == []
    assert _correlation(database, tmp_path)["reason_code"] == "WORKSPACE_BINDING_STALE"


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
    assert _correlation(database, tmp_path)["reason_code"] == "WORKSPACE_BINDING_STALE"


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
    assert _correlation(database, tmp_path)["reason_code"] == reason_code


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

    assert _correlation(database, tmp_path)["status"] == "LINKED"
    assert _workspace(database, tmp_path)["authority_state"] == "UNKNOWN"
    assert unrelated.read_bytes() == original
    assert str(unrelated) not in json.dumps(projection)
    assert str(unrelated) not in " ".join(database._conn.iterdump())


def test_supervision_projects_workspace_bound_agent_without_inventing_session(
    database,
    tmp_path,
):
    receipts = _record(database)
    _bind_protection_authority(database, tmp_path / "protected")

    projection = _supervision(database, tmp_path)
    summary = projection["observed_agents"][0]

    assert projection["status"] == "AVAILABLE"
    assert projection["reason_code"] == "R4_SUPERVISION_AVAILABLE"
    assert projection["items"] == []
    assert summary["agent_ref"] == "cloudcli-agent"
    assert summary["workspace_correlation"]["binding_ref"] == receipts[2].event_id
    assert summary["workspace"]["authority_state"] == "BOUND"
    assert summary["workspace"]["authority_observation_ref"] is not None
    assert summary["supervision_status"] == "WORKSPACE_BOUND"
    assert summary["supervision_session_id"] is None
    assert summary["reason_code"] == "AGENT_WORKSPACE_BOUND_NO_SUPERVISION_SESSION"


def test_agent_activity_projection_requires_real_observer_evidence(
    database,
    tmp_path,
):
    _record(database)

    before = _project(database, tmp_path)["items"][0]
    assert before["activity_observability"] == "UNKNOWN"
    assert before["latest_activity"] is None
    assert before["recent_activity_count"] == 0
    assert before["recent_verified_activities"] == []
    assert before["activity_reason_code"] == "ACTIVITY_OBSERVABILITY_NOT_ESTABLISHED"

    _append_host_activity(
        database,
        event_id="host-process-started-1",
        event_type=EventType.PROCESS_STARTED,
        reason_code="AGENT_CHILD_PROCESS_STARTED",
    )

    after = _project(database, tmp_path)["items"][0]
    assert after["activity_observability"] == "OBSERVABLE"
    assert after["recent_activity_count"] == 1
    assert after["latest_activity"]["event_id"] == "host-process-started-1"
    assert after["recent_verified_activities"][0]["type"] == "PROCESS_STARTED"
    assert after["activity_reason_code"] == "HOST_ACTIVITY_OBSERVED"

    supervised = _supervision(database, tmp_path)["observed_agents"][0]
    assert supervised["activity_observability"] == "OBSERVABLE"
    assert supervised["recent_activity_count"] == 1
    assert supervised["latest_activity"]["event_id"] == "host-process-started-1"


def test_agent_projection_scopes_wsl_observability_degradation(database, tmp_path):
    snapshot = _snapshot()
    snapshot = replace(
        snapshot,
        evidence=(
            *snapshot.evidence,
            ProbeEvidence(
                evidence_id="wsl-agent-processes-unreachable",
                collector="product-discovery",
                source="wsl-list-host-side",
                observed_at=OBSERVED_AT,
                fact_type="probe.unreachable",
                value={
                    "domain_label": "Ubuntu",
                    "execution_domain_id": "wsl-distro-ubuntu",
                    "reason_code": "NO_BOUNDED_HOST_READ",
                    "scope": "agents",
                },
                status=CapabilityStatus.UNKNOWN,
                sanitized=True,
            ),
        ),
        status=CapabilityStatus.DEGRADED,
    )
    _record(database, snapshot)

    projection = _project(database, tmp_path)
    assert projection["status"] == "DEGRADED"
    assert projection["reason_code"] == "R4_AGENT_DISCOVERY_PARTIAL"
    assert projection["identified_count"] == 1
    assert projection["degradation_scopes"] == [
        {
            "execution_domain_id": "wsl-distro-ubuntu",
            "label": "Ubuntu",
            "reason_code": "NO_BOUNDED_HOST_READ",
        }
    ]


def test_supervision_projects_observed_only_when_workspace_binding_is_missing(
    database,
    tmp_path,
):
    _record(database, _without_binding())

    projection = _supervision(database, tmp_path)
    summary = projection["observed_agents"][0]

    assert projection["status"] == "AVAILABLE"
    assert summary["agent_ref"] == "cloudcli-agent"
    assert summary["lifecycle"] == "RUNNING"
    assert summary["workspace"]["status"] == "UNKNOWN"
    assert summary["supervision_status"] == "OBSERVED_ONLY"
    assert summary["supervision_session_id"] is None
    assert summary["reason_code"] == "AGENT_OBSERVED_ONLY"


def test_supervision_links_session_only_through_exact_workspace_agent_binding(
    database,
    tmp_path,
):
    receipts = _record(database)
    _bind_protection_authority(database, tmp_path / "protected")
    snapshots = SnapshotStore(tmp_path / "snapshots")
    session, decision, _facts = SupervisionService(
        database,
        snapshots=snapshots,
        product_sha=PRODUCT_SHA,
    ).create_authoritative(
        "observe workspace",
        _policy_input(),
        checkpoint_id=None,
    )

    projection = R4ReadProjectionService(database, snapshots).supervision()
    summary = projection["observed_agents"][0]

    assert decision.decision.value == "ALLOW"
    assert summary["agent_ref"] == "cloudcli-agent"
    assert summary["supervision_status"] == "SUPERVISED"
    assert summary["supervision_session_id"] == session.supervision_session_id
    assert summary["policy_decision"] == "ALLOW"
    assert summary["pending_approval"] is False
    assert summary["latest_checkpoint"] is None
    assert summary["reason_code"] == "AGENT_SUPERVISION_SESSION_VERIFIED"
    assert receipts[1].event_id in summary["evidence_refs"]
    assert receipts[2].event_id in summary["evidence_refs"]
    assert set(projection["items"][0]["evidence_refs"]).issubset(
        summary["evidence_refs"]
    )


def test_same_domain_product_target_session_does_not_claim_agent_supervision(
    database,
    tmp_path,
):
    receipts = _record(database)
    target = "server-owned-config"
    target_binding = record_product_target_binding(
        database,
        snapshot_id=_snapshot().snapshot_id,
        execution_domain_id="agent-dev-domain",
        target_refs=(target,),
        recorded_at=OBSERVED_AT,
    )
    snapshots = SnapshotStore(tmp_path / "snapshots")
    session, _decision, _facts = SupervisionService(
        database,
        snapshots=snapshots,
        product_sha=PRODUCT_SHA,
    ).create_authoritative(
        "observe server target",
        _policy_input(target),
        checkpoint_id=None,
        product_target_binding_ref=target_binding.event_id,
    )

    projection = R4ReadProjectionService(database, snapshots).supervision()
    summary = projection["observed_agents"][0]

    assert (
        projection["items"][0]["supervision_session_id"]
        == session.supervision_session_id
    )
    assert summary["agent_ref"] == "cloudcli-agent"
    assert summary["execution_domain_id"] == "agent-dev-domain"
    assert summary["workspace_correlation"]["binding_ref"] == receipts[2].event_id
    assert summary["workspace"]["authority_state"] == "UNKNOWN"
    assert summary["supervision_status"] == "OBSERVED_ONLY"
    assert summary["supervision_session_id"] is None
    assert summary["policy_decision"] is None
    assert summary["reason_code"] == "AGENT_OBSERVED_ONLY"
