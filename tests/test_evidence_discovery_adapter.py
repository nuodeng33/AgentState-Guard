"""Tests for one-way Discovery to Evidence Ledger adaptation."""

from __future__ import annotations

from datetime import UTC, datetime

from agentguard.discovery import CapabilityStatus, DiscoverySnapshot, ProbeEvidence
from agentguard.evidence.discovery_adapter import discovery_events
from agentguard.evidence.models import EventType


def test_snapshot_projects_runtime_agent_and_unreachable_facts_without_workspace_link():
    snapshot = DiscoverySnapshot(
        snapshot_id="snapshot-1",
        observed_at=datetime(2026, 8, 5, tzinfo=UTC),
        evidence=(
            ProbeEvidence(
                evidence_id="runtime-evidence",
                collector="runtime-probe",
                source="local",
                observed_at=datetime(2026, 8, 5, tzinfo=UTC),
                fact_type="runtime.metadata",
                value={"runtime_kind": "python"},
                status=CapabilityStatus.AVAILABLE,
                sanitized=True,
            ),
            ProbeEvidence(
                evidence_id="agent-evidence",
                collector="agent-probe",
                source="local",
                observed_at=datetime(2026, 8, 5, tzinfo=UTC),
                fact_type="agent.metadata",
                value={"agent_kind": "cloudcli"},
                status=CapabilityStatus.AVAILABLE,
                sanitized=True,
            ),
            ProbeEvidence(
                evidence_id="unreachable-evidence",
                collector="wsl-probe",
                source="local",
                observed_at=datetime(2026, 8, 5, tzinfo=UTC),
                fact_type="probe.unreachable",
                value={"code": "UNREACHABLE"},
                status=CapabilityStatus.UNSUPPORTED,
                sanitized=True,
            ),
        ),
    )

    events = discovery_events(snapshot, recorded_at=datetime(2026, 8, 5, tzinfo=UTC))

    assert [event.event_type for event in events] == [
        EventType.RUNTIME_DETECTED,
        EventType.AGENT_DETECTED,
        EventType.PROBE_UNREACHABLE,
    ]
    assert all(event.evidence_refs for event in events)
    assert all(event.event_type is not EventType.WORKSPACE_LINKED for event in events)


def test_adapter_drops_unrecognized_fact_types_and_does_not_mutate_snapshot():
    evidence = ProbeEvidence(
        evidence_id="workspace-candidate",
        collector="processes",
        source="local",
        observed_at=datetime(2026, 8, 5, tzinfo=UTC),
        fact_type="workspace.candidate",
        value={"path_hint": "~/project"},
        status=CapabilityStatus.AVAILABLE,
        sanitized=True,
    )
    snapshot = DiscoverySnapshot(
        snapshot_id="snapshot-2",
        observed_at=datetime(2026, 8, 5, tzinfo=UTC),
        evidence=(evidence,),
    )

    assert discovery_events(snapshot, recorded_at=datetime(2026, 8, 5, tzinfo=UTC)) == ()
    assert snapshot.evidence == (evidence,)
