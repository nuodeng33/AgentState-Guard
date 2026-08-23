"""Host-side read-only observation of Docker and WSL execution domains.

A real Windows installation is not limited to the host process namespace:
supported Agents also run inside Docker containers and WSL distros. This
module observes those execution domains strictly from the host with
bounded, read-only CLI mechanisms:

- Docker: ``docker ps`` (container list) and ``docker top`` (per-container
  process rows). No socket mount, no privileged container, nothing
  installed inside the container, no mutation.
- WSL: ``wsl.exe --list --verbose`` (running distros only). Nothing is
  executed inside a distro; V1 has no bounded host-side read of distro
  processes, so agent observability stays honestly UNKNOWN.

Failure semantics: an unreachable Docker daemon or a failing observer
degrades to truthful UNREACHABLE/UNKNOWN evidence and never raises into
the caller; host-native discovery is unaffected. Raw ``docker top``
command lines are reduced in-process to bounded identity answers by a
caller-supplied admission callable and are never persisted.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from ...core.docker import DOCKER_UNKNOWN, docker_presence
from ...core.host_tools import platform_is_windows
from ...core.runner import CommandResult, run_command
from ..capabilities import (
    AgentLifecycleStatus,
    CapabilityAssessment,
    CapabilityStatus,
    DomainCapabilities,
    EvidenceReliability,
)
from ..errors import DiscoveryError, DiscoveryErrorCode
from ..models import (
    AgentDescriptor,
    ExecutionDomainDescriptor,
    ExecutionDomainKind,
    ProbeEvidence,
    RuntimeDescriptor,
    WorkspaceDescriptor,
)
from .wsl import parse_wsl_list, validate_distro_name

_CONTAINER_ID_RE = re.compile(r"[0-9a-f]{12,64}\Z")
_SAFE_LABEL_RE = re.compile(r"[A-Za-z0-9_.-]{1,48}\Z")

#: Sandbox workspace roots that are never a bounded product workspace,
#: regardless of being a mount destination (e.g. the container rootfs "/").
_BLOCKED_SANDBOX_ROOTS = frozenset(
    {
        "/",
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/home",
        "/lib",
        "/media",
        "/mnt",
        "/opt",
        "/proc",
        "/root",
        "/run",
        "/sbin",
        "/srv",
        "/sys",
        "/tmp",
        "/usr",
        "/var",
    }
)

#: Admission callback: bounded argv tokens -> (identity, role) or None.
#: Provided by ProductDiscoveryService so V1 admission policy stays in
#: one place; this module never invents identities.
AdmitProcess = Callable[[Sequence[str]], tuple[str, str] | None]

_RUN: Callable[[list[str]], CommandResult] = run_command


@dataclass(frozen=True)
class HostDomainObservation:
    """Bounded multi-domain observation results for one discovery pass."""

    domains: tuple[ExecutionDomainDescriptor, ...] = ()
    runtimes: tuple[RuntimeDescriptor, ...] = ()
    agents: tuple[AgentDescriptor, ...] = ()
    workspaces: tuple[WorkspaceDescriptor, ...] = ()
    evidence: tuple[ProbeEvidence, ...] = ()
    errors: tuple[DiscoveryError, ...] = ()


def _stable_id(prefix: str, *parts: str) -> str:
    material = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:24]}"


@dataclass(frozen=True)
class _SandboxMounts:
    """Bounded docker inspect facts for one container (real daemon output)."""

    working_dir: str | None
    started_at: str | None
    mounts: tuple[tuple[str, str, str], ...]  # (type, source, destination)


def _parse_inspect(result: CommandResult) -> _SandboxMounts | None:
    """Parse the bounded inspect line: WorkingDir \\t StartedAt \\t mounts."""
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    parts = line.split("\t")
    if len(parts) != 3:
        return None
    working_dir, started_at, mounts_raw = (part.strip() for part in parts)
    mounts: list[tuple[str, str, str]] = []
    for entry in mounts_raw.split(";"):
        entry = entry.strip()
        if not entry or ":" not in entry or "=>" not in entry:
            continue
        mount_type, rest = entry.split(":", 1)
        source, destination = rest.split("=>", 1)
        mounts.append(
            (mount_type.strip()[:16], source.strip()[:256], destination.strip()[:256])
        )
    return _SandboxMounts(
        working_dir=working_dir[:256] or None,
        started_at=started_at[:64] or None,
        mounts=tuple(mounts),
    )


def _canonical_sandbox_root(destination: str) -> str | None:
    """Canonical, bounded sandbox workspace root or None (fail-closed)."""
    candidate = destination.strip()
    if not candidate.startswith("/") or candidate.endswith("/.."):
        return None
    segments: list[str] = []
    for segment in candidate.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            return None
        segments.append(segment)
    if not segments:
        return None  # "/"
    root = "/" + "/".join(segments)
    if root in _BLOCKED_SANDBOX_ROOTS:
        return None
    return root


def _select_workspace_root(
    facts: _SandboxMounts,
) -> tuple[str | None, str]:
    """Pick the single mount destination that owns the container workdir.

    Returns (canonical_root, reason). Fail-closed on ambiguity, blocked
    roots, or a workdir that no real mount destination owns.
    """
    if facts.working_dir is None:
        return None, "SANDBOX_WORKDIR_UNKNOWN"
    workdir = _canonical_sandbox_root(facts.working_dir)
    if workdir is None:
        # A working dir at a blocked/system root is never a workspace.
        workdir_normalized = facts.working_dir.rstrip("/") or "/"
    else:
        workdir_normalized = workdir
    owners: set[str] = set()
    for _mount_type, _source, destination in facts.mounts:
        root = _canonical_sandbox_root(destination)
        if root is None:
            continue
        if workdir_normalized == root or workdir_normalized.startswith(root + "/"):
            owners.add(root)
    if not owners:
        return None, "SANDBOX_WORKSPACE_NO_MOUNT_OWNER"
    if len(owners) > 1:
        return None, "SANDBOX_WORKSPACE_AMBIGUOUS"
    return next(iter(owners)), "SANDBOX_WORKSPACE_MOUNT_OWNED"


def _unreachable_evidence(
    *,
    collector: str,
    observed_at: datetime,
    reason_code: str,
    scope: str,
    execution_domain_id: str | None = None,
    error: DiscoveryError | None = None,
) -> ProbeEvidence:
    value: dict[str, object] = {"reason_code": reason_code, "scope": scope}
    if execution_domain_id is not None:
        value["execution_domain_id"] = execution_domain_id
    return ProbeEvidence(
        evidence_id=(
            f"host-domain-probe-{hashlib.sha256(reason_code.encode()).hexdigest()[:16]}"
            f"-{int(observed_at.timestamp())}"
        ),
        collector=collector,
        source="host-domain-observation",
        observed_at=observed_at,
        fact_type="probe.unreachable",
        value=value,
        reliability=EvidenceReliability.HIGH,
        status=CapabilityStatus.UNREACHABLE,
        error=error,
        sanitized=True,
    )


def _output_lines(output: str) -> list[tuple[str, list[str]]]:
    """Line-oriented rows with whitespace-split fields; malformed → dropped."""
    rows: list[tuple[str, list[str]]] = []
    for line in output.splitlines():
        line = line.strip()
        if line:
            rows.append((line, line.split()))
    return rows


def observe_docker_domains(
    *,
    admit_process: AdmitProcess,
    clock: Callable[[], datetime],
    runner: Callable[[list[str]], CommandResult] = _RUN,
    presence: Callable[[], object] = docker_presence,
    collector: str = "product-discovery",
) -> HostDomainObservation:
    """Observe Docker container domains from the host, read-only.

    ``admit_process`` receives the bounded argv tokens of one container
    process row and returns ``(identity, role)`` only when the current
    admission policy accepts it; the raw tokens never leave this call.
    """
    observed_at = clock()
    presence_value = presence()
    if presence_value is DOCKER_UNKNOWN or presence_value is None:
        return HostDomainObservation(
            evidence=(
                _unreachable_evidence(
                    collector=collector,
                    observed_at=observed_at,
                    reason_code="DOCKER_PRESENCE_UNKNOWN",
                    scope="runtime",
                ),
            )
        )
    if presence_value is not True:
        # Docker genuinely absent: no container domains exist. Absence is
        # not unreachability and produces no fake surfaces.
        return HostDomainObservation()

    list_result = runner(["docker", "ps", "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}"])
    if not list_result.success:
        return HostDomainObservation(
            evidence=(
                _unreachable_evidence(
                    collector=collector,
                    observed_at=observed_at,
                    reason_code="DOCKER_LIST_UNAVAILABLE",
                    scope="runtime",
                    error=DiscoveryError(
                        DiscoveryErrorCode.UNREACHABLE,
                        "docker ps did not complete on the host",
                        details={"stage": "docker ps"},
                    ),
                ),
            )
        )

    domains: list[ExecutionDomainDescriptor] = []
    runtimes: list[RuntimeDescriptor] = []
    agents: list[AgentDescriptor] = []
    workspaces: list[WorkspaceDescriptor] = []
    evidence: list[ProbeEvidence] = []
    nonce = observed_at.isoformat()

    for raw_line, _fields in _output_lines(list_result.stdout):
        if raw_line.count("\t") < 2:
            continue
        container_id_raw, name_raw, _status = raw_line.split("\t", 2)
        container_id = container_id_raw.strip().casefold()
        if _CONTAINER_ID_RE.fullmatch(container_id) is None:
            continue
        short_id = container_id[:12]
        label_source = name_raw.strip()
        label = label_source if _SAFE_LABEL_RE.fullmatch(label_source) else short_id
        domain_id = f"docker-container-{short_id}"
        runtime_evidence_id = (
            f"container-runtime-{hashlib.sha256((short_id + nonce).encode()).hexdigest()[:24]}"
        )
        evidence.append(
            ProbeEvidence(
                evidence_id=runtime_evidence_id,
                collector=collector,
                source="docker-ps-host-side",
                observed_at=observed_at,
                fact_type="runtime.metadata",
                value={
                    "capabilities": ["domain_visible"],
                    "container_id": short_id,
                    "execution_domain_id": domain_id,
                    "runtime_kind": "CONTAINER_RUNTIME",
                },
                reliability=EvidenceReliability.HIGH,
                confidence=0.9,
                status=CapabilityStatus.AVAILABLE,
                sanitized=True,
            )
        )

        top_result = runner(["docker", "top", container_id, "-o", "pid,args"])
        admitted: list[tuple[str, str, str]] = []
        top_available = top_result.success
        if top_available:
            for _line, row_fields in _output_lines(top_result.stdout):
                if len(row_fields) < 2 or not row_fields[0].isdigit():
                    continue
                verdict = admit_process(row_fields[1:])
                if verdict is None:
                    continue
                identity, role = verdict
                admitted.append((identity, role, row_fields[0]))
        else:
            evidence.append(
                _unreachable_evidence(
                    collector=collector,
                    observed_at=observed_at,
                    reason_code="DOCKER_TOP_UNAVAILABLE",
                    scope="agents",
                    execution_domain_id=domain_id,
                    error=DiscoveryError(
                        DiscoveryErrorCode.UNREACHABLE,
                        "docker top did not complete for the container",
                        details={"stage": "docker top", "container_id": short_id},
                    ),
                )
            )

        domains.append(
            ExecutionDomainDescriptor(
                domain_id=domain_id,
                kind=ExecutionDomainKind.CONTAINER,
                label=label,
                capabilities=DomainCapabilities({
                    "domain_visible": CapabilityAssessment(
                        status=CapabilityStatus.AVAILABLE,
                        reason_code="CONTAINER_RUNNING",
                        evidence_ids=(runtime_evidence_id,),
                        confidence=0.9,
                    ),
                    "agent_processes": CapabilityAssessment(
                        status=(
                            CapabilityStatus.AVAILABLE
                            if top_available
                            else CapabilityStatus.UNKNOWN
                        ),
                        reason_code=(
                            "CONTAINER_PROCESSES_OBSERVED"
                            if top_available
                            else "DOCKER_TOP_UNAVAILABLE"
                        ),
                        evidence_ids=(runtime_evidence_id,),
                    ),
                }),
                evidence_ids=(runtime_evidence_id,),
                confidence=0.9,
            )
        )
        runtime_id = f"container-runtime-{short_id}"
        runtimes.append(
            RuntimeDescriptor(
                runtime_id=runtime_id,
                runtime_type="CONTAINER_RUNTIME",
                domain_id=domain_id,
                status=CapabilityStatus.AVAILABLE,
                label=label,
                evidence_ids=(runtime_evidence_id,),
                confidence=0.9,
            )
        )
        # Real mount-backed workspace binding (L3): the workspace root must
        # be a real docker-inspect mount destination that owns the
        # container's working dir; container StartedAt seeds the identity
        # so a restart can never inherit a stale binding. Fail-closed on
        # ambiguity, blocked roots, or unverifiable inspect output.
        inspect_result = runner(
            [
                "docker",
                "inspect",
                container_id,
                "--format",
                (
                    "{{.Config.WorkingDir}}\t{{.State.StartedAt}}\t"
                    "{{range .Mounts}}{{.Type}}:{{.Source}}=>{{.Destination}};{{end}}"
                ),
            ]
        )
        workspace_id: str | None = None
        workspace_evidence_id: str | None = None
        inspect_facts = _parse_inspect(inspect_result) if inspect_result.success else None
        if inspect_facts is not None:
            root, reason = _select_workspace_root(inspect_facts)
            if root is not None and inspect_facts.started_at:
                workspace_id = _stable_id(
                    "sandbox-workspace", domain_id, root, inspect_facts.started_at
                )
                workspace_evidence_id = (
                    f"container-workspace-"
                    f"{hashlib.sha256((short_id + workspace_id).encode()).hexdigest()[:24]}"
                )
                owning_mount = next(
                    (m for m in inspect_facts.mounts if _canonical_sandbox_root(m[2]) == root),
                    None,
                )
                evidence.append(
                    ProbeEvidence(
                        evidence_id=workspace_evidence_id,
                        collector=collector,
                        source="docker-inspect-host-side",
                        observed_at=observed_at,
                        fact_type="workspace.present",
                        value={
                            "candidate_id": workspace_id,
                            "workspace_kind": "SANDBOX_VOLUME",
                            "execution_domain_id": domain_id,
                            "container_id": short_id,
                            "container_started_at": inspect_facts.started_at,
                            "root": root,
                            "mount_type": owning_mount[0] if owning_mount else None,
                            "mount_source": owning_mount[1] if owning_mount else None,
                            "working_dir": inspect_facts.working_dir,
                            "reason_code": reason,
                        },
                        reliability=EvidenceReliability.HIGH,
                        confidence=0.85,
                        status=CapabilityStatus.AVAILABLE,
                        sanitized=True,
                    )
                )
                workspaces.append(
                    WorkspaceDescriptor(
                        workspace_id=workspace_id,
                        domain_id=domain_id,
                        runtime_ids=(runtime_id,),
                        agent_ids=tuple(
                            sorted(
                                _stable_id(
                                    "external-agent",
                                    identity,
                                    f"docker:{short_id}:{pid}",
                                )
                                for identity, _role, pid in admitted
                            )
                        ),
                        evidence_ids=(workspace_evidence_id,),
                        confidence=0.85,
                    )
                )
        for identity, role, pid in admitted:
            agent_id = _stable_id(
                "external-agent", identity, f"docker:{short_id}:{pid}"
            )
            instance_label = f"{identity} {agent_id[-6:]}"
            agent_evidence_id = (
                f"container-agent-{hashlib.sha256(agent_id.encode()).hexdigest()[:24]}"
            )
            evidence.append(
                ProbeEvidence(
                    evidence_id=agent_evidence_id,
                    collector=collector,
                    source="docker-top-host-side",
                    observed_at=observed_at,
                    fact_type="agent.metadata",
                    value={
                        "agent_kind": identity,
                        "execution_domain_id": domain_id,
                        "instance_label": instance_label,
                        "lifecycle": AgentLifecycleStatus.RUNNING.value,
                        "role": role,
                    },
                    reliability=EvidenceReliability.HIGH,
                    confidence=0.7,
                    status=CapabilityStatus.AVAILABLE,
                    sanitized=True,
                )
            )
            agents.append(
                AgentDescriptor(
                    agent_id=agent_id,
                    agent_type=identity,
                    lifecycle=AgentLifecycleStatus.RUNNING,
                    domain_id=domain_id,
                    runtime_id=runtime_id,
                    label=instance_label,
                    workspace_ids=(workspace_id,) if workspace_id else (),
                    evidence_ids=(agent_evidence_id,),
                    confidence=0.7,
                )
            )

    return HostDomainObservation(
        domains=tuple(domains),
        runtimes=tuple(runtimes),
        agents=tuple(agents),
        workspaces=tuple(workspaces),
        evidence=tuple(evidence),
    )


def observe_wsl_domains(
    *,
    clock: Callable[[], datetime],
    runner: Callable[[list[str]], CommandResult] = _RUN,
    collector: str = "product-discovery",
    windows: bool | None = None,
) -> HostDomainObservation:
    """Observe running WSL distro domains from the Windows host, read-only.

    ``wsl.exe --list --verbose`` only enumerates distros; V1 executes
    nothing inside any distro, so per-distro Agent-process observability
    is reported as honestly UNKNOWN with its reason recorded.
    """
    observed_at = clock()
    is_windows = platform_is_windows() if windows is None else windows
    if not is_windows:
        return HostDomainObservation()
    result = runner(["wsl.exe", "--list", "--verbose"])
    if not result.success or not result.stdout.strip():
        # WSL feature absent or unusable: truthful empty, not unreachable.
        return HostDomainObservation()
    # wsl.exe emits UTF-16LE on some builds; text=True with UTF-8 default
    # decoding then leaves NUL bytes between ASCII characters.
    output = result.stdout.replace("\x00", "") if "\x00" in result.stdout else result.stdout
    parsed = parse_wsl_list(output)

    domains: list[ExecutionDomainDescriptor] = []
    runtimes: list[RuntimeDescriptor] = []
    evidence: list[ProbeEvidence] = []
    for distro in parsed.distros:
        try:
            name = validate_distro_name(distro.name)
        except ValueError:
            continue
        if distro.state.value != "RUNNING":
            continue
        domain_id = f"wsl-distro-{hashlib.sha256(name.encode()).hexdigest()[:12]}"
        runtime_evidence_id = (
            f"wsl-runtime-{hashlib.sha256((name + observed_at.isoformat()).encode()).hexdigest()[:24]}"
        )
        evidence.append(
            ProbeEvidence(
                evidence_id=runtime_evidence_id,
                collector=collector,
                source="wsl-list-host-side",
                observed_at=observed_at,
                fact_type="runtime.metadata",
                value={
                    "capabilities": ["domain_visible"],
                    "execution_domain_id": domain_id,
                    "runtime_kind": "WSL_DISTRO_RUNTIME",
                },
                reliability=EvidenceReliability.HIGH,
                confidence=0.8,
                status=CapabilityStatus.AVAILABLE,
                sanitized=True,
            )
        )
        domains.append(
            ExecutionDomainDescriptor(
                domain_id=domain_id,
                kind=ExecutionDomainKind.WSL,
                label=name,
                capabilities=DomainCapabilities({
                    "domain_visible": CapabilityAssessment(
                        status=CapabilityStatus.AVAILABLE,
                        reason_code="WSL_DISTRO_RUNNING",
                        evidence_ids=(runtime_evidence_id,),
                        confidence=0.8,
                    ),
                    "agent_processes": CapabilityAssessment(
                        status=CapabilityStatus.UNKNOWN,
                        reason_code="NO_BOUNDED_HOST_READ",
                        evidence_ids=(runtime_evidence_id,),
                    ),
                }),
                evidence_ids=(runtime_evidence_id,),
                confidence=0.8,
            )
        )
        runtimes.append(
            RuntimeDescriptor(
                runtime_id=f"wsl-runtime-{hashlib.sha256(name.encode()).hexdigest()[:12]}",
                runtime_type="WSL_DISTRO_RUNTIME",
                domain_id=domain_id,
                status=CapabilityStatus.AVAILABLE,
                label=name,
                evidence_ids=(runtime_evidence_id,),
                confidence=0.8,
            )
        )
    return HostDomainObservation(
        domains=tuple(domains),
        runtimes=tuple(runtimes),
        evidence=tuple(evidence),
    )


__all__ = [
    "AdmitProcess",
    "HostDomainObservation",
    "observe_docker_domains",
    "observe_wsl_domains",
]
