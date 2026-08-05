"""One-way projection from Discovery facts into bounded Evidence Ledger events."""

from __future__ import annotations

import hashlib
from datetime import datetime

from agentguard.discovery.models import DiscoverySnapshot, ProbeEvidence

from .models import EventFamily, EventType, EvidenceEvent

_FACT_EVENT_TYPES = {
    "runtime.metadata": EventType.RUNTIME_DETECTED,
    "agent.metadata": EventType.AGENT_DETECTED,
    "probe.unreachable": EventType.PROBE_UNREACHABLE,
}


def _event_id(snapshot_id: str, evidence: ProbeEvidence) -> str:
    material = f"{snapshot_id}\x1f{evidence.evidence_id}\x1f{evidence.observed_at.isoformat()}"
    return f"discovery-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def discovery_events(
    snapshot: DiscoverySnapshot,
    *,
    recorded_at: datetime,
) -> tuple[EvidenceEvent, ...]:
    """Project only allowlisted Discovery observations without writing to storage."""
    projected: list[EvidenceEvent] = []
    for evidence in snapshot.evidence:
        event_type = _FACT_EVENT_TYPES.get(evidence.fact_type)
        if event_type is None:
            continue
        projected.append(
            EvidenceEvent(
                schema_version=1,
                event_id=_event_id(snapshot.snapshot_id, evidence),
                recorded_at=recorded_at,
                observed_at=evidence.observed_at,
                event_family=EventFamily.DISCOVERY,
                event_type=event_type,
                source=evidence.source,
                result=evidence.status.value.lower(),
                execution_domain_id=None,
                supervision_session_id=None,
                transaction_id=None,
                checkpoint_id=None,
                subject_ref=evidence.evidence_id,
                evidence_refs=(evidence.evidence_id,),
                payload_safe={
                    "collector": evidence.collector,
                    "fact_type": evidence.fact_type,
                    "reliability": evidence.reliability.value,
                    "confidence": evidence.confidence,
                    "value": evidence.value,
                },
            )
        )
    return tuple(projected)
