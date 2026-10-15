"""Read-only product composition for local Runtime and external Agent discovery."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .agents import (
    AgentRole,
    ProcessBackend,
    ProcessCollector,
    ProcessFact,
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
from .signatures import (
    PRODUCT_AGENT_REGISTRY,
    PRODUCT_SIGNATURES,
    kind_for_classification,
    package_name_from_entry,
)

_COLLECTOR = "product-discovery"
_AGENT_SIGNATURES: dict[str, tuple[str, AgentRole]] = {
    basename: (kind, rule.role)
    for kind, rule in PRODUCT_SIGNATURES if not rule.package_names
    for basename in rule.executable_basenames
}


@dataclass(frozen=True)
class ProductDiscoveryReport:
    """Serializable discovery plus private, in-process workspace authority facts."""

    snapshot: DiscoverySnapshot
    workspace_authorities: tuple[ProcessWorkspaceAuthority, ...] = ()
    application_observations: tuple[dict, ...] = ()


class ProductDiscoveryService:
    """Compose fixed local probes without treating this product as an Agent."""

    def __init__(
        self,
        *,
        runtime_adapter: object | None = None,
        process_backend: ProcessBackend | None = None,
        clock: Callable[[], datetime] | None = None,
        home_path: str | None = None,
        registered_paths: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self._runtime_adapter = runtime_adapter or SelfRuntimeAdapter()
        self._process_backend = process_backend or PsutilProcessBackend()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._home_path = home_path if home_path is not None else str(Path.home())
        self._registered_paths = registered_paths or (lambda: ())

    def discover(self) -> DiscoverySnapshot:
        return self.discover_with_authority().snapshot

    def discover_with_authority(self) -> ProductDiscoveryReport:
        return self._discover(include_processes=True)

    def discover_runtime(self) -> DiscoverySnapshot:
        """Observe host authority without unrelated Agent/process enumeration."""
        return self._discover(include_processes=False).snapshot

    def _discover(self, *, include_processes: bool) -> ProductDiscoveryReport:
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

        if not include_processes:
            return ProductDiscoveryReport(snapshot=DiscoverySnapshot(
                snapshot_id=f"product-{nonce}", observed_at=observed_at,
                domains=(domain,), runtimes=(runtime,),
                evidence=(*runtime_snapshot.evidence, runtime_evidence),
                errors=runtime_snapshot.errors, status=runtime_snapshot.status,
            ))

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
        classifications = {
            item.process_instance_id: item
            for item in PRODUCT_AGENT_REGISTRY.discover(processes.facts).candidates
        }
        kinds = {
            instance_id: kind_for_classification(item.evidence_refs)
            for instance_id, item in classifications.items()
        }
        facts_by_pid = {item.pid: item for item in processes.facts}
        registered_paths = _canonical_registered_paths(self._registered_paths)
        application_observations: list[dict] = []
        seen_agent_ids: set[str] = set()

        for fact in processes.facts:
            if fact.current_state is not ProcessState.RUNNING:
                continue
            agent_type = kinds.get(fact.process_instance_id)
            classification = classifications.get(fact.process_instance_id)
            registered = False
            if agent_type is None and registered_paths and _canonical_process_path(fact) in registered_paths:
                agent_type = "UNKNOWN"
                registered = True
            role = (
                AgentRole.TOOL_PROCESS if registered else
                classification.role if classification is not None else AgentRole.UNKNOWN
            )
            root = _zcode_root(fact, facts_by_pid, kinds) if agent_type == "ZCODE" else fact
            agent_id = (
                _stable_id("external-agent", agent_type, root.process_instance_id)
                if agent_type else None
            )
            if agent_type is None:
                application_observations.append(_application_observation(fact, None, observed_at))
                continue
            if agent_id in seen_agent_ids:
                continue
            fact = root
            application_observations.append(_application_observation(fact, agent_id, observed_at))
            source = next(
                (process_evidence.get(ref) for ref in fact.evidence_refs if ref in process_evidence),
                None,
            )
            if source is None:
                continue
            seen_agent_ids.add(agent_id)
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
                    "identity_basis": (
                        "USER_REGISTRATION" if registered else
                        "PACKAGE_ENTRY_MATCH" if fact.program_metadata.get("package_name") else
                        "PROCESS_NAME_MATCH"
                    ),
                    "source_id": "local",
                },
                summary=None,
                reliability=EvidenceReliability.MEDIUM,
                confidence=0.5,
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
                    confidence=0.5,
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
            application_observations=tuple(application_observations),
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


def _canonical_path(value: str | None) -> str | None:
    if not value:
        return None
    try:
        path = Path(value).resolve(strict=True)
        return os.path.normcase(str(path)) if path.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _canonical_registered_paths(provider: Callable[[], Iterable[str]]) -> set[str]:
    try:
        values = tuple(provider())[:100]
    except (OSError, RuntimeError, TypeError, ValueError):
        return set()
    return {canonical for value in values if isinstance(value, str)
            if (canonical := _canonical_path(value)) is not None}


def _canonical_process_path(fact: ProcessFact) -> str | None:
    return _canonical_path(fact.program_metadata.get("entry_path") or fact.program_metadata.get("path"))


def _zcode_root(
    fact: ProcessFact, facts_by_pid: dict[int, ProcessFact], kinds: dict[str, str | None]
) -> ProcessFact:
    roles = {"renderer", "utility", "gpu-process", "crashpad-handler"}
    current = fact
    for _ in range(8):
        if current.program_metadata.get("electron_role") not in roles:
            break
        path = current.program_metadata.get("path")
        parent = facts_by_pid.get(current.parent_pid) if current.parent_pid is not None else None
        if (
            not path or parent is None or parent.create_time is None
            or current.create_time is None or parent.create_time > current.create_time
            or kinds.get(parent.process_instance_id) != "ZCODE"
            or os.path.normcase(os.path.normpath(parent.program_metadata.get("path") or ""))
            != os.path.normcase(os.path.normpath(path))
        ):
            break
        current = parent
    return current


def _application_observation(fact: ProcessFact, agent_id: str | None, observed_at: datetime) -> dict:
    metadata = fact.program_metadata
    path = metadata.get("path")
    entry_path = metadata.get("entry_path")
    package_name = metadata.get("package_name") or package_name_from_entry(entry_path)
    path_identity = (
        "path-sha256:" + hashlib.sha256(os.path.normcase(os.path.normpath(path)).encode()).hexdigest()
        if path else None
    )
    return {
        "agent_id": agent_id,
        "process_instance_id": fact.process_instance_id,
        "pid": fact.pid,
        "parent_pid": fact.parent_pid,
        "create_time": fact.create_time.isoformat() if fact.create_time else None,
        "source_id": "local",
        "execution_domain_id": fact.execution_domain_id,
        "program": {
            "path": path,
            "executable_basename": fact.executable_basename,
            "entry_path": entry_path,
            "package_name": package_name,
            "version": metadata.get("version"),
            "identity": metadata.get("identity") or path_identity,
            "status": "RUNNING",
            "reason_code": metadata.get("reason_code") or (
                "PROCESS_PATH_OBSERVED" if path else "PROCESS_PATH_UNAVAILABLE"
            ),
            "electron_role": metadata.get("electron_role"),
        },
        "cwd": fact.observed_cwd,
        "observed_at": observed_at.isoformat(),
    }


__all__ = [
    "ProductDiscoveryReport",
    "ProductDiscoveryService",
    "current_execution_domain",
    "unavailable_product_snapshot",
]
