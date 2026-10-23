"""Compose bounded native and container observations into one discovery generation.

Foreign paths are observation data only. They never become native recovery authority.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import PurePosixPath

from .agents import AgentRole
from .capabilities import AgentLifecycleStatus, CapabilityStatus, EvidenceReliability
from .domains.docker_host import DockerTargetListResult, DomainProcessObservation
from .errors import redact_text
from .models import (
    AgentDescriptor,
    DiscoverySnapshot,
    ExecutionDomainDescriptor,
    ExecutionDomainKind,
    ProbeEvidence,
    RuntimeDescriptor,
    WorkspaceDescriptor,
)
from .product import ProductDiscoveryReport, ProductDiscoveryService
from .signatures import classify_metadata
from .source_identity import docker_source_id


def _id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()[:24]
    return f"{prefix}-{digest}"


class ManagedDiscoveryService:
    """Management controls observation scope, never a discovered process's execution."""

    def __init__(self, *, native=None, docker=None, preferences=None, clock=None,
                 domain_registry=None):
        self.native = native or ProductDiscoveryService()
        self.docker = docker
        self.preferences = preferences or dict
        self.clock = clock or (lambda: datetime.now(UTC))
        self.domain_registry = domain_registry
        self.lock = threading.RLock()
        self._view_lock = threading.RLock()
        self._sources: list[dict] = []
        self._session_targets: dict[str, object] = {}
        self._session_probes: dict[str, dict] = {}

    def _docker_disconnected(self, source_id: str) -> bool:
        query = getattr(self.domain_registry, "is_docker_disconnected", None)
        return query(source_id) if query is not None else False

    def read_sessions(self, source_id: str, *, reader=None, store_id=None, limit=50, offset=0) -> dict:
        """The product resolves a store; legacy embedded callers use observed providers."""
        from .sessions import SessionReader

        context = getattr(self, "catalog_context", None)
        if context is not None and reader is None:
            with context() as catalog:
                return catalog.read_sessions(source_id, store_id, limit, offset)
        with self.lock:
            self._session_probes.pop(source_id, None)
            source = next((item for item in self.sources()["items"] if item["source_id"] == source_id), None)
            if source is None:
                return {"status": "UNAVAILABLE", "reason_code": "DISCOVERY_SOURCE_NOT_FOUND", "sessions": []}
            if source["kind"] == "CONTAINER" and self._docker_disconnected(source_id):
                return {"status": "UNAVAILABLE", "reason_code": "DOMAIN_DISCONNECTED", "sessions": []}
            if not source["enabled"] or source["freshness"] != "FRESH" or source["state"] not in {"OBSERVED", "PARTIAL", "EMPTY"}:
                return {"status": "UNAVAILABLE", "reason_code": "DISCOVERY_SOURCE_NOT_CURRENT", "sessions": []}
            target = self._session_targets.get(source_id, {})
            providers = target.get("providers", ())
            if len(providers) != 1:
                return {"status": "UNSUPPORTED", "reason_code": "SESSION_STORE_SELECTION_REQUIRED" if providers else "SESSION_PROVIDER_UNSUPPORTED", "sessions": []}
            provider = providers[0]
            reader = reader or SessionReader()
            if target.get("kind") == "WSL":
                result = self.domain_registry.read_sessions(source_id, provider)
            elif target.get("kind") == "CONTAINER":
                transport = self.docker.session_transport(target["target"])
                if transport is None:
                    return {"status": "UNAVAILABLE", "reason_code": "DISCOVERY_SOURCE_NOT_CURRENT", "sessions": []}
                executable, environment = transport
                result = reader.read(provider, container_id=target["target"].container_id,
                                     docker_executable=executable, environment=environment)
            else:
                result = reader.read(provider)
            result = {**result, "source_id": source_id, "scope": "PROVIDER_STORE"}
            self._session_probes[source_id] = {"status": result["status"], "observed_at": self.clock().isoformat()}
            return result

    def sources(self) -> dict:
        # Serving the last observation must not wait behind process/remote I/O.
        # Mutations still use lock; generations publish this view atomically.
        with self._view_lock:
            preferences = self.preferences()
            items = []
            for source in self._sources:
                item = dict(source)
                setting = preferences.get(item["source_id"], {})
                item["revision"] = setting.get("revision", 0)
                item["enabled"] = setting.get("enabled", True)
                if item["kind"] == "CONTAINER" and not preferences.get("docker", {}).get("enabled", True):
                    item["enabled"] = False
                if not item["enabled"]:
                    item["state"] = "DISABLED"
                elif item["kind"] == "CONTAINER" and self._docker_disconnected(item["source_id"]):
                    item["state"] = "NOT_PROBED"
                    item["reason_code"] = "DOCKER_PROCESS_PROBE_DISCONNECTED"
                observed = datetime.fromisoformat(item["observed_at"])
                age = (self.clock() - observed).total_seconds()
                item["freshness"] = "FRESH" if -5 <= age <= 120 else "STALE"
                if item["freshness"] == "STALE" and item["state"] not in {"DISABLED", "NOT_PROBED"}:
                    item["state"] = "STALE"
                item["capabilities"] = dict(source["capabilities"])
                current = item["enabled"] and item["freshness"] == "FRESH" and item["state"] in {"OBSERVED", "PARTIAL", "EMPTY"}
                item["capabilities"]["observe"] = current and item["capabilities"]["observe"]
                session_probe = self._session_probes.get(item["source_id"])
                if session_probe is not None and current:
                    probe_age = (self.clock() - datetime.fromisoformat(session_probe["observed_at"])).total_seconds()
                    item["session_status"] = session_probe["status"] if -5 <= probe_age <= 120 else "STALE"
                    item["capabilities"]["sessions"] = item["session_status"] in {"AVAILABLE", "EMPTY"}
                item["session_probe_available"] = current and item["kind"] in {"NATIVE", "CONTAINER", "WSL"}
                items.append(item)
            if self.domain_registry is not None:
                known = {item['source_id']: item for item in items}
                for domain in self.domain_registry.sources(refresh=False):
                    source_id = domain['source_id']
                    if source_id in known and domain.get('container_id'):
                        known[source_id]['short_id'] = domain['container_id'][:12]
                    protection = {'running': domain['running'], 'connected': domain['connected'],
                                  'epoch': domain['epoch'], 'capabilities': domain['capabilities']}
                    if source_id in known:
                        known[source_id]['protection'] = protection
                        continue
                    item = {'source_id': source_id, 'kind': domain['kind'], 'label': domain['label'],
                            'state': 'NOT_PROBED', 'reason_code': 'PROCESS_PROBE_NOT_CONNECTED',
                            'agent_count': 0, 'observed_at': self.clock().isoformat(),
                            'configurable': False, 'capabilities': {'observe': False,
                            'sessions': False, 'start': False, 'cancel': False},
                            'session_status': 'PROTOCOL_NOT_CONNECTED', 'revision': 0,
                            'enabled': True, 'freshness': 'FRESH',
                            'session_probe_available': False, 'protection': protection}
                    items.append(item)
            return {"schema_version": "discovery-sources-1", "items": items,
                    "inventory_coverage": getattr(self, "inventory_coverage", [])}

    def discover(self) -> DiscoverySnapshot:
        return self.discover_with_authority().snapshot

    def discover_with_authority(self) -> ProductDiscoveryReport:
        with self.lock:
            return self._discover()

    def _discover(self) -> ProductDiscoveryReport:
        native_report = self.native.discover_with_authority()
        local = native_report.snapshot
        now = self.clock()
        preferences = self.preferences()
        sources: list[dict] = []
        session_targets: dict[str, object] = {"local": {"kind": "NATIVE", "providers": tuple(sorted({
            a.agent_type for a in local.agents if a.agent_type in {"CODEX", "KIMI_CODE"}}))}}
        domains, runtimes = list(local.domains), list(local.runtimes)
        agents, workspaces, evidence = list(local.agents), list(local.workspaces), list(local.evidence)
        application_observations = list(getattr(native_report, "application_observations", ()))
        registered_programs = getattr(self, "registered_programs", dict)()
        status = local.status
        deadline = time.monotonic() + 20

        def enabled(source_id):
            return preferences.get(source_id, {}).get("enabled", True)

        def source(source_id, kind, label, state, reason, count=0):
            sources.append({
                "source_id": source_id, "kind": kind, "label": redact_text(label)[:128],
                "state": state, "reason_code": reason, "agent_count": count,
                "observed_at": now.isoformat(), "configurable": source_id != "local",
                "capabilities": {"observe": True, "sessions": False, "start": False, "cancel": False},
                "session_status": "PROTOCOL_NOT_CONNECTED",
            })

        def failure(source_id, domain_id, reason, capability=CapabilityStatus.UNREACHABLE):
            evidence.append(ProbeEvidence(
                evidence_id=_id("probe", local.snapshot_id, source_id),
                collector="managed-discovery", source="bounded-domain-probe", observed_at=now,
                fact_type="probe.unreachable",
                value={"scope": "coverage" if reason == "SOURCE_DISABLED" else "agents", "source_id": source_id,
                       "execution_domain_id": domain_id, "reason_code": reason},
                reliability=EvidenceReliability.HIGH, status=capability, sanitized=True,
            ))

        local_reason = next((
            item.value.get("reason_code") for item in local.evidence
            if item.fact_type == "probe.unreachable" and isinstance(item.value, dict)
            and item.value.get("scope") == "agents"
            and isinstance(item.value.get("reason_code"), str)
        ), "LOCAL_PROCESS_OBSERVATION")
        source("local", "NATIVE", "Local processes",
               "OBSERVED" if local.status is CapabilityStatus.AVAILABLE else "PARTIAL",
               local_reason, len(local.agents))
        if not enabled("docker"):
            source("docker", "DOCKER", "Local Docker", "DISABLED", "SOURCE_DISABLED")
            # Emit a coverage record, so a disabled source is not mistaken for scanned empty.
            failure("docker", None, "SOURCE_DISABLED", CapabilityStatus.UNSUPPORTED)
        elif self.docker is None:
            source("docker", "DOCKER", "Local Docker", "NOT_PROBED", "DOCKER_PROBE_NOT_CONFIGURED")
            failure("docker", None, "DOCKER_PROBE_NOT_CONFIGURED", CapabilityStatus.UNSUPPORTED)
        else:
            try:
                listing = self.docker.list_targets()
            except (OSError, RuntimeError, ValueError):
                listing = DockerTargetListResult(CapabilityStatus.UNREACHABLE, "DOCKER_TARGET_LIST_FAILED")
            if listing.status is CapabilityStatus.NOT_PRESENT and listing.reason_code == "DOCKER_TARGETS_EMPTY":
                source("docker", "DOCKER", "Local Docker", "EMPTY", listing.reason_code)
            elif listing.status not in {CapabilityStatus.AVAILABLE, CapabilityStatus.DEGRADED}:
                status = CapabilityStatus.DEGRADED
                source("docker", "DOCKER", "Local Docker", "UNREACHABLE", listing.reason_code)
                failure("docker", None, listing.reason_code, listing.status)
            else:
                source("docker", "DOCKER", "Local Docker",
                       "PARTIAL" if listing.status is CapabilityStatus.DEGRADED else "OBSERVED" if listing.targets else "EMPTY",
                       listing.reason_code)
                if listing.status is CapabilityStatus.DEGRADED:
                    status = CapabilityStatus.DEGRADED
                    failure("docker", None, listing.reason_code, listing.status)
                for index, target in enumerate(listing.targets):
                    source_id = docker_source_id(target.container_id)
                    domain_id = _id("container", target.container_id)
                    if not enabled(source_id):
                        source(source_id, "CONTAINER", target.name, "DISABLED", "SOURCE_DISABLED")
                        failure(source_id, domain_id, "SOURCE_DISABLED", CapabilityStatus.UNSUPPORTED)
                        continue
                    runtime_id = _id("container-runtime", target.container_id)
                    runtime_ref = _id("container-evidence", local.snapshot_id, domain_id)
                    domains.append(ExecutionDomainDescriptor(
                        domain_id=domain_id, kind=ExecutionDomainKind.CONTAINER,
                        evidence_ids=(runtime_ref,), confidence=0.9,
                    ))
                    runtimes.append(RuntimeDescriptor(
                        runtime_id=runtime_id, runtime_type="CONTAINER", domain_id=domain_id,
                        status=CapabilityStatus.AVAILABLE, evidence_ids=(runtime_ref,), confidence=0.9,
                    ))
                    runtime_capabilities = ["container_observation"]
                    evidence.append(ProbeEvidence(
                        evidence_id=runtime_ref, collector="managed-discovery",
                        source="docker-local-metadata", observed_at=now,
                        fact_type="runtime.metadata",
                        value={"runtime_kind": "CONTAINER", "execution_domain_id": domain_id,
                               "source_id": source_id, "capabilities": runtime_capabilities},
                        reliability=EvidenceReliability.HIGH, status=CapabilityStatus.AVAILABLE, sanitized=True,
                    ))
                    if self._docker_disconnected(source_id):
                        source(source_id, "CONTAINER", target.name, "NOT_PROBED", "DOCKER_PROCESS_PROBE_DISCONNECTED")
                        sources[-1]["capabilities"]["observe"] = False
                        continue
                    if index >= 16 or time.monotonic() >= deadline:
                        source(source_id, "CONTAINER", target.name, "NOT_PROBED", "DISCOVERY_BUDGET_EXHAUSTED")
                        failure(source_id, domain_id, "DISCOVERY_BUDGET_EXHAUSTED", CapabilityStatus.DEGRADED)
                        status = CapabilityStatus.DEGRADED
                        continue
                    try:
                        observation = self.docker.probe_target(target)
                    except (OSError, RuntimeError, ValueError):
                        observation = DomainProcessObservation(
                            target, now, status=CapabilityStatus.UNREACHABLE,
                            reason_code="DOCKER_PROCESS_PROBE_FAILED",
                        )
                    count_before = len(agents)
                    state = observation.status
                    if state not in {CapabilityStatus.AVAILABLE, CapabilityStatus.DEGRADED}:
                        source(source_id, "CONTAINER", target.name, "UNREACHABLE", observation.reason_code)
                        failure(source_id, domain_id, observation.reason_code, state)
                        status = CapabilityStatus.DEGRADED
                        continue
                    runtime_capabilities.append("process_observation")
                    shared: dict[str, list[str]] = {}
                    for process in observation.processes:
                        signature = classify_metadata(process, domain_id)
                        if signature is None and (process.get("entry_path") or process.get("path")) in registered_programs.get(source_id, ()):
                            signature = ("UNKNOWN", AgentRole.TOOL_PROCESS)
                        if signature is None or observation.boot_id is None:
                            application_observations.append(_process_observation(process, source_id, now))
                            continue
                        kind, role = signature
                        agent_id = _id("external-agent", domain_id, observation.boot_id,
                                       process["pid"], process["start_ticks"], kind)
                        application_observations.append(_process_observation(process, source_id, now, agent_id))
                        agent_ref = _id("agent-evidence", local.snapshot_id, agent_id)
                        workspace_ids = ()
                        cwd = process.get("cwd")
                        if cwd and PurePosixPath(cwd).is_absolute() and cwd != "/":
                            workspace_id = _id("workspace", domain_id, cwd)
                            workspace_ids = (workspace_id,)
                            shared.setdefault(workspace_id, []).append(agent_id)
                        evidence.append(ProbeEvidence(
                            evidence_id=agent_ref, collector="managed-discovery",
                            source="container-process-metadata", observed_at=observation.observed_at,
                            fact_type="agent.metadata",
                            value={"agent_kind": kind, "role": role.value,
                                   "lifecycle": "RUNNING", "execution_domain_id": domain_id,
                                   "source_id": source_id, "identity_basis": "USER_REGISTRATION" if kind == "UNKNOWN" else "PACKAGE_ENTRY_MATCH" if process.get("package_name") else "PROCESS_NAME_MATCH"},
                            reliability=EvidenceReliability.MEDIUM, confidence=0.5,
                            status=CapabilityStatus.AVAILABLE, sanitized=True,
                        ))
                        agents.append(AgentDescriptor(
                            agent_id=agent_id, agent_type=kind,
                            lifecycle=AgentLifecycleStatus.RUNNING, domain_id=domain_id,
                            runtime_id=runtime_id, workspace_ids=workspace_ids,
                            evidence_ids=(agent_ref,), confidence=0.5,
                        ))
                    for workspace_id, agent_ids in shared.items():
                        workspace_ref = _id("workspace-evidence", local.snapshot_id, workspace_id)
                        evidence.append(ProbeEvidence(
                            evidence_id=workspace_ref, collector="managed-discovery",
                            source="container-process-cwd", observed_at=observation.observed_at,
                            fact_type="workspace.present",
                            value={"candidate_id": workspace_id, "workspace_kind": "PROCESS_CWD"},
                            reliability=EvidenceReliability.MEDIUM,
                            status=CapabilityStatus.AVAILABLE, sanitized=True,
                        ))
                        workspaces.append(WorkspaceDescriptor(
                            workspace_id=workspace_id, domain_id=domain_id,
                            runtime_ids=(runtime_id,), agent_ids=tuple(agent_ids),
                            evidence_ids=(workspace_ref,), confidence=0.5,
                        ))
                    count = len(agents) - count_before
                    providers = tuple(sorted({item.agent_type for item in agents[count_before:]
                                              if item.agent_type in {"CODEX", "KIMI_CODE"}}))
                    session_targets[source_id] = {"kind": "CONTAINER", "target": target, "providers": providers}
                    source(source_id, "CONTAINER", target.name,
                           "PARTIAL" if state is CapabilityStatus.DEGRADED else "OBSERVED" if count else "EMPTY",
                           observation.reason_code, count)
                    if state is CapabilityStatus.DEGRADED:
                        status = CapabilityStatus.DEGRADED
                        failure(source_id, domain_id, observation.reason_code, state)
        # Only a user-established WSL helper channel may inspect /proc.
        source("wsl", "WSL", "WSL", "NOT_PROBED" if enabled("wsl") else "DISABLED",
               "WSL_PROCESS_PROBE_NOT_CONNECTED" if enabled("wsl") else "SOURCE_DISABLED")
        sources[-1]["configurable"] = False
        sources[-1]["capabilities"]["observe"] = False
        if not enabled("wsl"):
            failure("wsl", None, "SOURCE_DISABLED", CapabilityStatus.UNSUPPORTED)
        if self.domain_registry is not None:
            wsl_sources = [item for item in self.domain_registry.sources(refresh=False)
                           if item["kind"] == "WSL"]
            for index, distro in enumerate(wsl_sources):
                source_id = distro["source_id"]
                domain_id = _id("wsl", distro["guid"], distro["disk_identity"])
                if not enabled("wsl") or not enabled(source_id):
                    source(source_id, "WSL", distro["label"], "DISABLED", "SOURCE_DISABLED")
                    failure(source_id, domain_id, "SOURCE_DISABLED", CapabilityStatus.UNSUPPORTED)
                    continue
                if not distro["connected"]:
                    source(source_id, "WSL", distro["label"], "NOT_PROBED", "WSL_PROCESS_PROBE_NOT_CONNECTED")
                    sources[-1]["capabilities"]["observe"] = False
                    continue
                if index >= 16 or time.monotonic() >= deadline:
                    source(source_id, "WSL", distro["label"], "NOT_PROBED", "DISCOVERY_BUDGET_EXHAUSTED")
                    failure(source_id, domain_id, "DISCOVERY_BUDGET_EXHAUSTED", CapabilityStatus.DEGRADED)
                    status = CapabilityStatus.DEGRADED
                    continue
                try:
                    observation = self.domain_registry.probe_processes(source_id)
                except (OSError, RuntimeError, ValueError):
                    source(source_id, "WSL", distro["label"], "UNREACHABLE", "WSL_PROCESS_PROBE_FAILED")
                    failure(source_id, domain_id, "WSL_PROCESS_PROBE_FAILED")
                    status = CapabilityStatus.DEGRADED
                    continue
                runtime_id = _id("wsl-runtime", domain_id, observation.boot_id)
                runtime_ref = _id("wsl-runtime-evidence", local.snapshot_id, domain_id)
                domains.append(ExecutionDomainDescriptor(
                    domain_id=domain_id, kind=ExecutionDomainKind.WSL,
                    evidence_ids=(runtime_ref,), confidence=0.9,
                ))
                runtimes.append(RuntimeDescriptor(
                    runtime_id=runtime_id, runtime_type="WSL", domain_id=domain_id,
                    status=CapabilityStatus.AVAILABLE, evidence_ids=(runtime_ref,), confidence=0.9,
                ))
                evidence.append(ProbeEvidence(
                    evidence_id=runtime_ref, collector="managed-discovery",
                    source="wsl-process-metadata", observed_at=now,
                    fact_type="runtime.metadata",
                    value={"runtime_kind": "WSL", "execution_domain_id": domain_id,
                           "source_id": source_id, "capabilities": ["process_observation"]},
                    reliability=EvidenceReliability.HIGH, status=CapabilityStatus.AVAILABLE, sanitized=True,
                ))
                shared: dict[str, list[str]] = {}
                count = 0
                for process in observation.processes:
                    signature = classify_metadata(process, domain_id)
                    if signature is None and (process.get("entry_path") or process.get("path")) in registered_programs.get(source_id, ()):
                        signature = ("UNKNOWN", AgentRole.TOOL_PROCESS)
                    if signature is None or observation.boot_id is None:
                        application_observations.append(_process_observation(process, source_id, now))
                        continue
                    kind, role = signature
                    agent_id = _id("external-agent", domain_id, observation.boot_id,
                                   process["pid"], process["start_ticks"], kind)
                    application_observations.append(_process_observation(process, source_id, now, agent_id))
                    agent_ref = _id("agent-evidence", local.snapshot_id, agent_id)
                    workspace_ids = ()
                    cwd = process.get("cwd")
                    if cwd and PurePosixPath(cwd).is_absolute() and cwd != "/":
                        workspace_id = _id("workspace", domain_id, cwd)
                        workspace_ids = (workspace_id,)
                        shared.setdefault(workspace_id, []).append(agent_id)
                    evidence.append(ProbeEvidence(
                        evidence_id=agent_ref, collector="managed-discovery",
                        source="wsl-process-metadata", observed_at=now,
                        fact_type="agent.metadata",
                        value={"agent_kind": kind, "role": role.value,
                               "lifecycle": "RUNNING", "execution_domain_id": domain_id,
                               "source_id": source_id, "identity_basis": "USER_REGISTRATION" if kind == "UNKNOWN" else "PACKAGE_ENTRY_MATCH" if process.get("package_name") else "PROCESS_NAME_MATCH"},
                        reliability=EvidenceReliability.MEDIUM, confidence=0.5,
                        status=CapabilityStatus.AVAILABLE, sanitized=True,
                    ))
                    agents.append(AgentDescriptor(
                        agent_id=agent_id, agent_type=kind,
                        lifecycle=AgentLifecycleStatus.RUNNING, domain_id=domain_id,
                        runtime_id=runtime_id, workspace_ids=workspace_ids,
                        evidence_ids=(agent_ref,), confidence=0.5,
                    ))
                    count += 1
                for workspace_id, agent_ids in shared.items():
                    workspace_ref = _id("workspace-evidence", local.snapshot_id, workspace_id)
                    evidence.append(ProbeEvidence(
                        evidence_id=workspace_ref, collector="managed-discovery",
                        source="wsl-process-cwd", observed_at=now,
                        fact_type="workspace.present",
                        value={"candidate_id": workspace_id, "workspace_kind": "PROCESS_CWD"},
                        reliability=EvidenceReliability.MEDIUM,
                        status=CapabilityStatus.AVAILABLE, sanitized=True,
                    ))
                    workspaces.append(WorkspaceDescriptor(
                        workspace_id=workspace_id, domain_id=domain_id,
                        runtime_ids=(runtime_id,), agent_ids=tuple(agent_ids),
                        evidence_ids=(workspace_ref,), confidence=0.5,
                    ))
                reason = observation.partial_reason or "WSL_PROCESSES_OBSERVED"
                source(source_id, "WSL", distro["label"],
                       "PARTIAL" if observation.partial_reason else "OBSERVED" if count else "EMPTY",
                       reason, count)
                session_targets[source_id] = {"kind": "WSL", "providers": tuple(sorted({
                    agent.agent_type for agent in agents if agent.domain_id == domain_id
                    and agent.agent_type in {"CODEX", "KIMI_CODE"}}))}
                if observation.partial_reason:
                    status = CapabilityStatus.DEGRADED
                    failure(source_id, domain_id, reason, CapabilityStatus.DEGRADED)
        for item in sources:
            if item["source_id"] == "docker":
                item["agent_count"] = sum(child["agent_count"] for child in sources if child["kind"] == "CONTAINER")
            if item["source_id"] == "wsl":
                item["agent_count"] = sum(child["agent_count"] for child in sources
                                          if child["kind"] == "WSL" and child["source_id"] != "wsl")
        with self._view_lock:
            self._sources = sources
            self._session_targets = session_targets
            # A new process generation cannot inherit an earlier session connection.
            self._session_probes.clear()
        snapshot = replace(local, domains=tuple(domains), runtimes=tuple(runtimes),
                           agents=tuple(agents), workspaces=tuple(workspaces),
                           evidence=tuple(evidence), status=status)
        return ProductDiscoveryReport(snapshot=snapshot,
                                      workspace_authorities=native_report.workspace_authorities,
                                      application_observations=tuple(application_observations))


def _process_observation(process, source_id, observed_at, agent_id=None):
    program = {key: process[key] for key in ("path", "entry_path", "name", "executable_basename", "package_name", "version") if key in process}
    program["path"] = process.get("entry_path") or process.get("path")
    program.update(status="RUNNING", reason_code="PROCESS_PATH_OBSERVED")
    return {"agent_id": agent_id, "source_id": source_id, "pid": process["pid"],
            "parent_pid": process["ppid"], "start_ticks": process.get("start_ticks"), "cwd": process.get("cwd"),
            "observed_at": observed_at.isoformat(), "program": program}
