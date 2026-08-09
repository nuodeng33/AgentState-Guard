"""One-way projection from Discovery facts into bounded Evidence Ledger events."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

from agentguard.discovery.capabilities import CapabilityStatus
from agentguard.discovery.models import (
    AgentDescriptor,
    DiscoverySnapshot,
    ProbeEvidence,
    RuntimeDescriptor,
)
from agentguard.storage.db import StateDB

from .ledger import EvidenceLedger, LedgerReceipt, verify_ledger
from .models import EventFamily, EventType, EvidenceEvent

_FACT_EVENT_TYPES = {
    "runtime.metadata": EventType.RUNTIME_DETECTED,
    "agent.metadata": EventType.AGENT_DETECTED,
    "probe.unreachable": EventType.PROBE_UNREACHABLE,
}
_SAFE_BINDING_ATOM = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


def _event_id(snapshot_id: str, evidence: ProbeEvidence) -> str:
    material = f"{snapshot_id}\x1f{evidence.evidence_id}\x1f{evidence.observed_at.isoformat()}"
    return f"discovery-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _binding_event_id(
    snapshot: DiscoverySnapshot,
    *,
    domain_id: str,
    runtime_id: str,
    agent_id: str,
    workspace_id: str,
) -> str:
    material = (
        f"{snapshot.snapshot_id}\x1f{snapshot.observed_at.isoformat()}\x1f"
        f"{domain_id}\x1f{runtime_id}\x1f{agent_id}\x1f{workspace_id}"
    )
    return f"workspace-binding-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _unique_descriptor(
    descriptors: tuple[RuntimeDescriptor, ...] | tuple[AgentDescriptor, ...],
    evidence_id: str,
) -> RuntimeDescriptor | AgentDescriptor | None:
    matches = [item for item in descriptors if evidence_id in item.evidence_ids]
    return matches[0] if len(matches) == 1 else None


def _safe_atom(value: str | None) -> bool:
    return isinstance(value, str) and _SAFE_BINDING_ATOM.fullmatch(value) is not None


def discovery_events(
    snapshot: DiscoverySnapshot,
    *,
    recorded_at: datetime,
) -> tuple[EvidenceEvent, ...]:
    """Project only allowlisted Discovery observations without writing to storage."""
    projected: list[EvidenceEvent] = []
    events_by_evidence: dict[str, EvidenceEvent] = {}
    for evidence in snapshot.evidence:
        event_type = _FACT_EVENT_TYPES.get(evidence.fact_type)
        if event_type is None:
            continue
        descriptor: RuntimeDescriptor | AgentDescriptor | None = None
        descriptor_payload: dict[str, object] = {}
        if event_type is EventType.RUNTIME_DETECTED:
            descriptor = _unique_descriptor(snapshot.runtimes, evidence.evidence_id)
            if isinstance(descriptor, RuntimeDescriptor):
                descriptor_payload = {
                    "runtime_id": descriptor.runtime_id,
                    "runtime_type": descriptor.runtime_type,
                }
        elif event_type is EventType.AGENT_DETECTED:
            descriptor = _unique_descriptor(snapshot.agents, evidence.evidence_id)
            if isinstance(descriptor, AgentDescriptor):
                descriptor_payload = {
                    "agent_id": descriptor.agent_id,
                    "agent_type": descriptor.agent_type,
                    "runtime_id": descriptor.runtime_id,
                    "workspace_ids": list(descriptor.workspace_ids),
                }
        event = EvidenceEvent(
            schema_version=1,
            event_id=_event_id(snapshot.snapshot_id, evidence),
            recorded_at=recorded_at,
            observed_at=evidence.observed_at,
            event_family=EventFamily.DISCOVERY,
            event_type=event_type,
            source=evidence.source,
            result=evidence.status.value.lower(),
            execution_domain_id=descriptor.domain_id if descriptor else None,
            supervision_session_id=None,
            transaction_id=None,
            checkpoint_id=None,
            subject_ref=(
                descriptor.runtime_id
                if isinstance(descriptor, RuntimeDescriptor)
                else descriptor.agent_id
                if isinstance(descriptor, AgentDescriptor)
                else evidence.evidence_id
            ),
            evidence_refs=(evidence.evidence_id,),
            payload_safe={
                "collector": evidence.collector,
                "fact_type": evidence.fact_type,
                "reliability": evidence.reliability.value,
                "confidence": descriptor.confidence if descriptor else evidence.confidence,
                "snapshot_id": snapshot.snapshot_id,
                **descriptor_payload,
                "value": evidence.value,
            },
        )
        projected.append(event)
        events_by_evidence[evidence.evidence_id] = event
    projected.extend(_workspace_binding_events(snapshot, recorded_at, events_by_evidence))
    return tuple(projected)


def _workspace_binding_events(
    snapshot: DiscoverySnapshot,
    recorded_at: datetime,
    events_by_evidence: dict[str, EvidenceEvent],
) -> tuple[EvidenceEvent, ...]:
    evidence_by_id = {item.evidence_id: item for item in snapshot.evidence}
    domains = {item.domain_id: item for item in snapshot.domains}
    runtimes = {item.runtime_id: item for item in snapshot.runtimes}
    agents = {item.agent_id: item for item in snapshot.agents}
    bindings: list[EvidenceEvent] = []
    for workspace in snapshot.workspaces:
        if (
            len(workspace.agent_ids) != 1
            or len(workspace.runtime_ids) != 1
            or not all(
                _safe_atom(item)
                for item in (
                    workspace.workspace_id,
                    workspace.domain_id,
                    workspace.agent_ids[0],
                    workspace.runtime_ids[0],
                    snapshot.snapshot_id,
                )
            )
        ):
            continue
        agent = agents.get(workspace.agent_ids[0])
        runtime = runtimes.get(workspace.runtime_ids[0])
        domain = domains.get(workspace.domain_id)
        if (
            agent is None
            or runtime is None
            or domain is None
            or agent.runtime_id != runtime.runtime_id
            or agent.workspace_ids != (workspace.workspace_id,)
            or agent.domain_id != workspace.domain_id
            or runtime.domain_id != workspace.domain_id
            or not domain.evidence_ids
        ):
            continue
        workspace_evidence = [evidence_by_id.get(item) for item in workspace.evidence_ids]
        if (
            not workspace_evidence
            or any(
                item is None
                or item.fact_type != "workspace.present"
                or item.status is not CapabilityStatus.AVAILABLE
                for item in workspace_evidence
            )
        ):
            continue
        runtime_events = [
            events_by_evidence[item]
            for item in runtime.evidence_ids
            if item in events_by_evidence
            and events_by_evidence[item].event_type is EventType.RUNTIME_DETECTED
        ]
        agent_events = [
            events_by_evidence[item]
            for item in agent.evidence_ids
            if item in events_by_evidence
            and events_by_evidence[item].event_type is EventType.AGENT_DETECTED
        ]
        if len(runtime_events) != 1 or len(agent_events) != 1:
            continue
        runtime_event = runtime_events[0]
        agent_event = agent_events[0]
        if any(
            event.result.casefold() != "available"
            for event in (runtime_event, agent_event)
        ):
            continue
        source_refs = tuple(
            sorted(
                set(
                    domain.evidence_ids
                    + runtime.evidence_ids
                    + agent.evidence_ids
                    + workspace.evidence_ids
                )
            )
        )
        if any(item not in evidence_by_id for item in source_refs):
            continue
        event_id = _binding_event_id(
            snapshot,
            domain_id=workspace.domain_id,
            runtime_id=runtime.runtime_id,
            agent_id=agent.agent_id,
            workspace_id=workspace.workspace_id,
        )
        bindings.append(
            EvidenceEvent(
                schema_version=1,
                event_id=event_id,
                recorded_at=recorded_at,
                observed_at=snapshot.observed_at,
                event_family=EventFamily.DISCOVERY,
                event_type=EventType.WORKSPACE_LINKED,
                source="discovery.workspace-binding",
                result="available",
                execution_domain_id=workspace.domain_id,
                supervision_session_id=None,
                transaction_id=None,
                checkpoint_id=None,
                subject_ref=agent.agent_id,
                evidence_refs=source_refs,
                payload_safe={
                    "fact_type": "workspace.binding",
                    "snapshot_id": snapshot.snapshot_id,
                    "binding_id": event_id,
                    "workspace_id": workspace.workspace_id,
                    "runtime_id": runtime.runtime_id,
                    "agent_id": agent.agent_id,
                    "runtime_event_id": runtime_event.event_id,
                    "agent_event_id": agent_event.event_id,
                },
            )
        )
    return tuple(bindings)


def record_discovery_snapshot(
    database: StateDB,
    snapshot: DiscoverySnapshot,
    *,
    recorded_at: datetime,
) -> tuple[LedgerReceipt, ...]:
    """Atomically append one internal Discovery snapshot to a valid Ledger."""
    events = discovery_events(snapshot, recorded_at=recorded_at)
    with database.transaction() as connection:
        if verify_ledger(connection):
            raise ValueError("DISCOVERY_LEDGER_INVALID")
        ledger = EvidenceLedger()
        receipts = tuple(ledger.append(connection, event) for event in events)
        if verify_ledger(connection):
            raise ValueError("DISCOVERY_LEDGER_INVALID")
    return receipts
