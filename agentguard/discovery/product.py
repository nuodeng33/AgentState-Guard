"""Read-only product composition for local Runtime and external Agent discovery."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
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
    launcher_identity,
)
from .agents.processes import bounded_launcher_anchors_for
from .capabilities import AgentLifecycleStatus, CapabilityStatus, EvidenceReliability
from .domains import SelfRuntimeAdapter
from .domains.host_domains import (
    HostDomainObservation,
    observe_docker_domains,
    observe_wsl_domains,
)
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
_BASENAME_IDENTITY_EVIDENCE: dict[str, str] = {
    **dict.fromkeys(("ccr", "ccr.exe"), "CCR"),
    **dict.fromkeys(
        ("claude", "claude.exe", "claude-code", "claude-code.exe"), "CLAUDE"
    ),
    **dict.fromkeys(("cloudcli", "cloudcli.exe"), "CLOUDCLI"),
    **dict.fromkeys(("codex", "codex.exe"), "CODEX"),
    **dict.fromkeys(("kimi", "kimi.exe", "kimi-code", "kimi-code.exe"), "KIMI_CODE"),
}

#: Generic runtimes that must never be guessed as Agents by basename alone.
_GENERIC_RUNTIME_BASENAMES = frozenset({"node", "node.exe", "nodejs", "nodejs.exe"})

#: Canonical role enrichment for identities established by bounded evidence.
_IDENTITY_ROLE_ENRICHMENT: dict[str, AgentRole] = {
    "CCR": AgentRole.MODEL_ROUTER,
    "CLAUDE": AgentRole.EXECUTION_AGENT,
    "CLOUDCLI": AgentRole.AGENT_HOST,
    "CODEX": AgentRole.EXECUTION_AGENT,
    "KIMI_CODE": AgentRole.EXECUTION_AGENT,
}

# V1 passive discovery admits only identities established by bounded evidence.
# Workspace binding and mutation authority remain separate and fail closed.
_V1_PASSIVE_ADMISSION_IDENTITIES = frozenset(
    ("CCR", "CLAUDE", "CLOUDCLI", "CODEX", "KIMI_CODE")
)

#: Unambiguous wrapper-directory tokens for container executable paths.
#: Mirrors the admitted identities only — no new identity is invented for
#: cross-filesystem (docker top) rows, where on-disk anchors cannot exist.
_WRAPPER_PATH_TOKENS: dict[str, str] = {
    "claude": "CLAUDE",
    "codex": "CODEX",
    "kimi": "KIMI_CODE",
}
_VERSIONED_TAIL = re.compile(r"\d+(\.\d+)*(-[0-9a-z._-]+)?\Z")
#: ``node_modules/<scope>/<name>`` path segment → identity; exact package
#: names only (the string form of the manifest identity the host flow
#: reads on disk), so generic names never match.
_PACKAGE_PATH_RE = re.compile(
    r"node_modules[/\\]((?:@[^/\\]+/)?[^/\\]+)", re.IGNORECASE
)


def _admit_container_process(tokens: Sequence[str]) -> tuple[str, str] | None:
    """Admit a container process row using bounded, reduced argv evidence.

    ``tokens`` are the argv tokens of one ``docker top`` row. Only the
    executable (first token) and, for generic script hosts such as node,
    the leading script arguments are consulted — via the admitted basename
    map, the exact ``node_modules`` package-path identity, or unambiguous
    wrapper-directory path tokens. Deeper argument tokens are never used,
    the raw command line is never persisted, and unknown executables stay
    unclassified.
    """
    if not tokens:
        return None
    executable = str(tokens[0])
    basename = executable.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    identity = _BASENAME_IDENTITY_EVIDENCE.get(basename)
    if identity is None and basename in _GENERIC_RUNTIME_BASENAMES:
        for token in tokens[1:4]:
            match = _PACKAGE_PATH_RE.search(str(token))
            if match is not None:
                identity = launcher_identity.KNOWN_PACKAGE_IDENTITIES.get(
                    match.group(1).casefold()
                )
                if identity is not None:
                    break
        if identity is None:
            for part in executable.replace("\\", "/").split("/"):
                token = part.casefold()
                if not token:
                    continue
                base, _, tail = token.rpartition("-")
                if base and tail and _VERSIONED_TAIL.fullmatch(tail):
                    token = base
                identity = _WRAPPER_PATH_TOKENS.get(token)
                if identity is not None:
                    break
    if identity is None or identity not in _V1_PASSIVE_ADMISSION_IDENTITIES:
        return None
    return identity, _IDENTITY_ROLE_ENRICHMENT[identity].value


def _resolve_bounded_launcher_identity(anchors: tuple[str, ...] | list[str]) -> str | None:
    """Map bounded filesystem anchors to Agent identity, or ``None``.

    Raw process command lines are never accepted, returned, or persisted.
    """
    return launcher_identity.resolve_launcher_identity(list(anchors))


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
        host_domain_observers: tuple[Callable[[], HostDomainObservation], ...] | None = None,
    ) -> None:
        self._runtime_adapter = runtime_adapter or SelfRuntimeAdapter()
        self._process_backend = process_backend or PsutilProcessBackend()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._home_path = home_path if home_path is not None else str(Path.home())
        self._host_domain_observers = host_domain_observers

    def _default_host_domain_observers(
        self,
    ) -> tuple[Callable[[], HostDomainObservation], ...]:
        clock = self._clock

        return (
            lambda: observe_docker_domains(
                admit_process=_admit_container_process, clock=clock
            ),
            lambda: observe_wsl_domains(clock=clock),
        )

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
            basename = (fact.executable_basename or "").casefold()
            identity = _BASENAME_IDENTITY_EVIDENCE.get(basename)
            if identity is None and basename in _GENERIC_RUNTIME_BASENAMES:
                # Node-hosted Agents resolve only through bounded launcher
                # identity; unresolved runtimes stay unclassified.
                identity = _resolve_bounded_launcher_identity(
                    bounded_launcher_anchors_for(self._process_backend, fact.pid)
                )
            if fact.current_state is not ProcessState.RUNNING:
                continue
            if not identity or identity not in _V1_PASSIVE_ADMISSION_IDENTITIES:
                continue
            agent_type = identity
            role = _IDENTITY_ROLE_ENRICHMENT[identity]
            source = next(
                (process_evidence.get(ref) for ref in fact.evidence_refs if ref in process_evidence),
                None,
            )
            if source is None:
                continue
            agent_id = _stable_id("external-agent", agent_type, fact.process_instance_id)
            instance_label = f"{agent_type} {agent_id[-6:]}"
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
                    "instance_label": instance_label,
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
                    label=instance_label,
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

        # Host-side multi-domain observation (Docker containers, WSL
        # distros). Observer failures degrade to truthful UNREACHABLE
        # evidence and never break the host-native discovery above.
        observers = (
            self._host_domain_observers
            if self._host_domain_observers is not None
            else self._default_host_domain_observers()
        )
        extra_domains: list[ExecutionDomainDescriptor] = []
        extra_runtimes: list[RuntimeDescriptor] = []
        extra_agents: list[AgentDescriptor] = []
        extra_workspaces: list[WorkspaceDescriptor] = []
        observer_errors: list = []
        for observe in observers:
            try:
                observation = observe()
            except Exception:  # noqa: BLE001 - isolate one domain's failure
                evidence.append(
                    ProbeEvidence(
                        evidence_id=f"host-domain-observer-{nonce}-{len(evidence)}",
                        collector=_COLLECTOR,
                        source="host-domain-observation",
                        observed_at=observed_at,
                        fact_type="probe.unreachable",
                        value={
                            "reason_code": "HOST_DOMAIN_OBSERVER_FAILED",
                            "scope": "runtime",
                        },
                        reliability=EvidenceReliability.HIGH,
                        status=CapabilityStatus.UNREACHABLE,
                        sanitized=True,
                    )
                )
                continue
            extra_domains.extend(observation.domains)
            extra_runtimes.extend(observation.runtimes)
            extra_agents.extend(observation.agents)
            extra_workspaces.extend(observation.workspaces)
            evidence.extend(observation.evidence)

        return ProductDiscoveryReport(
            snapshot=DiscoverySnapshot(
                snapshot_id=f"product-{nonce}",
                observed_at=observed_at,
                domains=(domain, *extra_domains),
                runtimes=(runtime, *extra_runtimes),
                agents=(*agents, *extra_agents),
                workspaces=(*workspaces, *extra_workspaces),
                evidence=tuple(evidence),
                errors=(*runtime_snapshot.errors, *processes.errors, *observer_errors),
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
