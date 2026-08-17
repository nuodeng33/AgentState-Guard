"""Read-only product composition for local Runtime and external Agent discovery."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .agents import (
    AgentRole,
    ProcessBackend,
    ProcessCollector,
    ProcessState,
    ProcessWorkspaceAuthority,
    PsutilProcessBackend,
)
from .capabilities import AgentLifecycleStatus, CapabilityStatus, EvidenceReliability
from .domains import SelfRuntimeAdapter
from .models import (
    AgentDescriptor,
    DiscoverySnapshot,
    ExecutionDomainDescriptor,
    ExecutionDomainKind,
    ProbeEvidence,
    RuntimeDescriptor,
    WorkspaceDescriptor,
)

_COLLECTOR = "product-discovery"
_AGENT_SIGNATURES: dict[str, tuple[str, AgentRole]] = {
    "ccr": ("CCR", AgentRole.MODEL_ROUTER),
    "ccr.exe": ("CCR", AgentRole.MODEL_ROUTER),
    "claude": ("CLAUDE", AgentRole.EXECUTION_AGENT),
    "claude.exe": ("CLAUDE", AgentRole.EXECUTION_AGENT),
    "cloudcli": ("CLOUDCLI", AgentRole.AGENT_HOST),
    "cloudcli.exe": ("CLOUDCLI", AgentRole.AGENT_HOST),
    "codex": ("CODEX", AgentRole.EXECUTION_AGENT),
    "codex.exe": ("CODEX", AgentRole.EXECUTION_AGENT),
}


@dataclass(frozen=True)
class ProductDiscoveryReport:
    """Serializable discovery plus private, in-process workspace authority facts."""

    snapshot: DiscoverySnapshot
    workspace_authorities: tuple[ProcessWorkspaceAuthority, ...] = ()


class ProductDiscoveryService:
    """Compose fixed local probes without treating this product as an Agent."""

    def __init__(
        self,
        *,
        runtime_adapter: object | None = None,
        process_backend: ProcessBackend | None = None,
        clock: Callable[[], datetime] | None = None,
        home_path: str | None = None,
    ) -> None:
        self._runtime_adapter = runtime_adapter or SelfRuntimeAdapter()
        self._process_backend = process_backend or PsutilProcessBackend()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._home_path = home_path if home_path is not None else str(Path.home())

    def discover(self) -> DiscoverySnapshot:
        return self.discover_with_authority().snapshot

    def discover_with_authority(self) -> ProductDiscoveryReport:
        runtime_snapshot = self._runtime_adapter.discover()
        domain = current_execution_domain(runtime_snapshot.domains)
        if domain is None or domain.kind is ExecutionDomainKind.UNKNOWN:
            raise RuntimeError("PRODUCT_DISCOVERY_DOMAIN_UNKNOWN")
        observed_at = runtime_snapshot.observed_at
        nonce = uuid4().hex
        runtime_id = _stable_id("self-runtime", domain.domain_id)
        runtime_evidence_id = f"product-runtime-{nonce}"
        available_capabilities = sorted(
            name
            for name, assessment in domain.capabilities.assessments.items()
            if assessment.status is CapabilityStatus.AVAILABLE
        )
        runtime_evidence = ProbeEvidence(
            evidence_id=runtime_evidence_id,
            collector=_COLLECTOR,
            source="local-self-runtime",
            observed_at=observed_at,
            fact_type="runtime.metadata",
            value={
                "capabilities": available_capabilities,
                "execution_domain_id": domain.domain_id,
                "runtime_kind": "SELF_RUNTIME",
            },
            reliability=EvidenceReliability.HIGH,
            confidence=0.95,
            status=CapabilityStatus.AVAILABLE,
            sanitized=True,
        )
        runtime = RuntimeDescriptor(
            runtime_id=runtime_id,
            runtime_type="SELF_RUNTIME",
            domain_id=domain.domain_id,
            status=CapabilityStatus.AVAILABLE,
            capabilities=domain.capabilities,
            evidence_ids=(runtime_evidence_id,),
            confidence=0.95,
        )

        processes = ProcessCollector(
            backend=self._process_backend,
            execution_domain_id=domain.domain_id,
            collector=_COLLECTOR,
            clock=lambda: observed_at,
            home_path=self._home_path,
        ).collect()
        evidence: list[ProbeEvidence] = [*runtime_snapshot.evidence, runtime_evidence]
        agents: list[AgentDescriptor] = []
        workspace_authorities: list[ProcessWorkspaceAuthority] = []
        workspace_agents: dict[str, list[str]] = {}
        workspace_sources: dict[str, tuple[object, ProbeEvidence]] = {}
        process_evidence = {item.evidence_id: item for item in processes.evidence}
        candidates_by_evidence = {
            evidence_id: candidate
            for candidate in processes.workspace_candidates
            for evidence_id in candidate.evidence_refs
        }
        authorities_by_process = {
            item.process_instance_id: item for item in processes.workspace_authorities
        }

        for fact in processes.facts:
            signature = _AGENT_SIGNATURES.get((fact.executable_basename or "").casefold())
            if signature is None or fact.current_state is not ProcessState.RUNNING:
                continue
            agent_type, role = signature
            source = next(
                (process_evidence.get(ref) for ref in fact.evidence_refs if ref in process_evidence),
                None,
            )
            if source is None:
                continue
            agent_id = _stable_id("external-agent", agent_type, fact.process_instance_id)
            agent_evidence_id = f"product-agent-{hashlib.sha256(agent_id.encode()).hexdigest()[:24]}-{nonce[:8]}"
            agent_status = (
                CapabilityStatus.AVAILABLE
                if fact.access_status is CapabilityStatus.AVAILABLE
                else fact.access_status
            )
            agent_evidence = replace(
                source,
                evidence_id=agent_evidence_id,
                fact_type="agent.metadata",
                value={
                    "agent_kind": agent_type,
                    "execution_domain_id": domain.domain_id,
                    "lifecycle": AgentLifecycleStatus.RUNNING.value,
                    "role": role.value,
                },
                summary=None,
                reliability=EvidenceReliability.HIGH,
                confidence=0.8,
                status=agent_status,
                error=None,
                sanitized=True,
            )
            evidence.append(agent_evidence)
            workspace_ids: tuple[str, ...] = ()
            candidate = next(
                (
                    candidates_by_evidence[ref]
                    for ref in fact.evidence_refs
                    if ref in candidates_by_evidence
                    and candidates_by_evidence[ref].access_status is CapabilityStatus.AVAILABLE
                ),
                None,
            )
            if candidate is not None:
                workspace_ids = (candidate.candidate_id,)
                workspace_agents.setdefault(candidate.candidate_id, []).append(agent_id)
                if candidate.candidate_id not in workspace_sources:
                    workspace_evidence_id = f"product-workspace-{nonce}-{len(workspace_sources) + 1}"
                    workspace_evidence = replace(
                        source,
                        evidence_id=workspace_evidence_id,
                        fact_type="workspace.present",
                        value={
                            "candidate_id": candidate.candidate_id,
                            "workspace_kind": "PROCESS_CWD",
                        },
                        summary=None,
                        reliability=EvidenceReliability.MEDIUM,
                        confidence=candidate.confidence,
                        status=CapabilityStatus.AVAILABLE,
                        error=None,
                        sanitized=True,
                    )
                    workspace_sources[candidate.candidate_id] = (candidate, workspace_evidence)
                    evidence.append(workspace_evidence)
                authority = authorities_by_process.get(fact.process_instance_id)
                if authority is not None and authority.candidate_id == candidate.candidate_id:
                    workspace_authorities.append(replace(authority, agent_id=agent_id))
            agents.append(
                AgentDescriptor(
                    agent_id=agent_id,
                    agent_type=agent_type,
                    lifecycle=AgentLifecycleStatus.RUNNING,
                    domain_id=domain.domain_id,
                    runtime_id=runtime_id,
                    workspace_ids=workspace_ids,
                    evidence_ids=(agent_evidence_id,),
                    confidence=0.8,
                )
            )

        workspaces = tuple(
            WorkspaceDescriptor(
                workspace_id=workspace_id,
                domain_id=domain.domain_id,
                runtime_ids=(runtime_id,),
                agent_ids=tuple(agent_ids),
                evidence_ids=(workspace_sources[workspace_id][1].evidence_id,),
                confidence=workspace_sources[workspace_id][0].confidence,
            )
            for workspace_id, agent_ids in workspace_agents.items()
        )
        if processes.status is not CapabilityStatus.AVAILABLE:
            first_error = processes.errors[0] if processes.errors else None
            reason_code = (
                str(first_error.details.get("reason_code"))
                if first_error is not None
                else "PROCESS_DISCOVERY_UNAVAILABLE"
            )
            evidence.append(
                ProbeEvidence(
                    evidence_id=f"product-agent-probe-{nonce}",
                    collector=_COLLECTOR,
                    source="local-process-metadata",
                    observed_at=observed_at,
                    fact_type="probe.unreachable",
                    value={
                        "execution_domain_id": domain.domain_id,
                        "reason_code": reason_code,
                        "scope": "agents",
                    },
                    reliability=EvidenceReliability.HIGH,
                    status=processes.status,
                    error=first_error,
                    sanitized=True,
                )
            )

        return ProductDiscoveryReport(
            snapshot=DiscoverySnapshot(
                snapshot_id=f"product-{nonce}",
                observed_at=observed_at,
                domains=(domain,),
                runtimes=(runtime,),
                agents=tuple(agents),
                workspaces=workspaces,
                evidence=tuple(evidence),
                errors=(*runtime_snapshot.errors, *processes.errors),
                status=(
                    CapabilityStatus.AVAILABLE
                    if processes.status is CapabilityStatus.AVAILABLE
                    else CapabilityStatus.DEGRADED
                ),
            ),
            workspace_authorities=tuple(workspace_authorities),
        )


def unavailable_product_snapshot() -> DiscoverySnapshot:
    """Create a safe ledgerable failure without preserving exception text."""
    observed_at = datetime.now(UTC)
    nonce = uuid4().hex
    return DiscoverySnapshot(
        snapshot_id=f"product-unavailable-{nonce}",
        observed_at=observed_at,
        evidence=(
            ProbeEvidence(
                evidence_id=f"product-runtime-probe-{nonce}",
                collector=_COLLECTOR,
                source="local-runtime-discovery",
                observed_at=observed_at,
                fact_type="probe.unreachable",
                value={
                    "reason_code": "PRODUCT_DISCOVERY_UNAVAILABLE",
                    "scope": "runtime",
                },
                reliability=EvidenceReliability.HIGH,
                status=CapabilityStatus.UNREACHABLE,
                sanitized=True,
            ),
        ),
        status=CapabilityStatus.DEGRADED,
    )


def current_execution_domain(
    domains: tuple[ExecutionDomainDescriptor, ...],
) -> ExecutionDomainDescriptor | None:
    candidates: list[tuple[int, ExecutionDomainDescriptor]] = []

    def visit(domain: ExecutionDomainDescriptor, depth: int) -> None:
        self_visible = domain.capabilities.assessments.get("self_visible")
        if self_visible is not None and self_visible.status is CapabilityStatus.AVAILABLE:
            candidates.append((depth, domain))
        for child in domain.children:
            visit(child, depth + 1)

    for item in domains:
        visit(item, 0)
    if not candidates:
        return None
    deepest = max(depth for depth, _item in candidates)
    matches = [item for depth, item in candidates if depth == deepest]
    return matches[0] if len(matches) == 1 else None


def _stable_id(prefix: str, *parts: str) -> str:
    material = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:24]}"


__all__ = [
    "ProductDiscoveryReport",
    "ProductDiscoveryService",
    "current_execution_domain",
    "unavailable_product_snapshot",
]
