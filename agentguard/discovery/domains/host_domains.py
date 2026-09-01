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
from pathlib import Path

from ...core.docker import DOCKER_UNKNOWN, docker_presence
from ...core.host_tools import platform_is_windows
from ...core.runner import CommandResult, run_command
from ..agents.models import ProcessWorkspaceAuthority
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
from ..workspace_authority import storage_workspace_digest
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
    workspace_authorities: tuple[ProcessWorkspaceAuthority, ...] = ()


def _stable_id(prefix: str, *parts: str) -> str:
    material = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:24]}"


@dataclass(frozen=True)
class _SandboxMount:
    mount_type: str
    source: str
    destination: str
    read_write: bool | None


@dataclass(frozen=True)
class _SandboxMounts:
    """Bounded docker inspect facts for one container (real daemon output)."""

    working_dir: str | None
    started_at: str | None
    image_identity: str | None
    mounts: tuple[_SandboxMount, ...]


@dataclass(frozen=True)
class _DockerVolumeFacts:
    name: str
    driver: str
    scope: str
    created_at: str
    mountpoint_ref: str


def _parse_inspect(result: CommandResult) -> _SandboxMounts | None:
    """Parse the bounded inspect line: WorkingDir \\t StartedAt \\t mounts."""
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    parts = line.split("\t")
    if len(parts) not in {3, 4}:
        return None
    if len(parts) == 4:
        working_dir, started_at, image_identity, mounts_raw = (
            part.strip() for part in parts
        )
    else:
        working_dir, started_at, mounts_raw = (part.strip() for part in parts)
        image_identity = ""
    mounts: list[_SandboxMount] = []
    for entry in mounts_raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if "|" in entry:
            mount_parts = entry.split("|")
            if len(mount_parts) != 4:
                continue
            mount_type, source, destination, rw_raw = mount_parts
            read_write = (
                True
                if rw_raw.strip().casefold() == "true"
                else False
                if rw_raw.strip().casefold() == "false"
                else None
            )
        elif ":" in entry and "=>" in entry:
            # Compatibility for bounded injected fixtures written before RW
            # was part of the inspect contract. Legacy facts never grant
            # durable bind authority because writability is unknown.
            mount_type, rest = entry.split(":", 1)
            source, destination = rest.split("=>", 1)
            read_write = None
        else:
            continue
        mounts.append(
            _SandboxMount(
                mount_type=mount_type.strip()[:16],
                source=source.strip()[:256],
                destination=destination.strip()[:256],
                read_write=read_write,
            )
        )
    return _SandboxMounts(
        working_dir=working_dir[:256] or None,
        started_at=started_at[:64] or None,
        image_identity=image_identity[:128] or None,
        mounts=tuple(mounts),
    )


def _parse_volume_facts(result: CommandResult) -> _DockerVolumeFacts | None:
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    parts = [part.strip() for part in line.split("\t")]
    if len(parts) != 5 or any(not part for part in parts[:4]):
        return None
    name, driver, scope, created_at, mountpoint = parts
    if _SAFE_LABEL_RE.fullmatch(name) is None or _SAFE_LABEL_RE.fullmatch(driver) is None:
        return None
    if _SAFE_LABEL_RE.fullmatch(scope) is None or len(created_at) > 64:
        return None
    mountpoint_ref = "sha256:" + hashlib.sha256(mountpoint.encode()).hexdigest()
    return _DockerVolumeFacts(
        name=name,
        driver=driver,
        scope=scope,
        created_at=created_at,
        mountpoint_ref=mountpoint_ref,
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
    owners: list[str] = []
    for mount in facts.mounts:
        root = _canonical_sandbox_root(mount.destination)
        if root is None:
            continue
        if workdir_normalized == root or workdir_normalized.startswith(root + "/"):
            owners.append(root)
    if not owners:
        if workdir is not None and not facts.mounts:
            return workdir, "SANDBOX_WORKSPACE_INTERNAL_FS"
        return None, "SANDBOX_WORKSPACE_NO_MOUNT_OWNER"
    if len(owners) > 1:
        return None, "SANDBOX_WORKSPACE_AMBIGUOUS"
    return owners[0], "SANDBOX_WORKSPACE_MOUNT_OWNED"


def _map_bind_workspace(
    facts: _SandboxMounts,
    owning_mount: _SandboxMount | None,
) -> Path | None:
    """Map one canonical container workdir into one host bind.

    Mount writability is an Agent capability fact, not workspace authority.
    """

    if (
        owning_mount is None
        or owning_mount.mount_type.casefold() != "bind"
        or facts.working_dir is None
    ):
        return None
    destination = _canonical_sandbox_root(owning_mount.destination)
    workdir = _canonical_sandbox_root(facts.working_dir)
    if destination is None or workdir is None:
        return None
    if workdir != destination and not workdir.startswith(destination + "/"):
        return None
    relative = workdir[len(destination) :].lstrip("/")
    relative_parts = tuple(part for part in relative.split("/") if part)
    if any(part in {".", ".."} for part in relative_parts):
        return None
    source = Path(owning_mount.source)
    if not source.is_absolute():
        return None
    try:
        source_root = source.resolve(strict=False)
        mapped = source_root.joinpath(*relative_parts).resolve(strict=False)
        mapped.relative_to(source_root)
    except (OSError, RuntimeError, ValueError):
        return None
    return mapped


def _logical_volume_root(facts: _SandboxMounts, mount: _SandboxMount) -> str | None:
    destination = _canonical_sandbox_root(mount.destination)
    workdir = _canonical_sandbox_root(facts.working_dir or "")
    if destination is None or workdir is None:
        return None
    if workdir != destination and not workdir.startswith(destination + "/"):
        return None
    suffix = workdir[len(destination) :].strip("/")
    if any(part in {".", ".."} for part in suffix.split("/") if part):
        return None
    return "/" + suffix if suffix else "/"


def _volume_resource_identity(engine_id: str, facts: _DockerVolumeFacts) -> str:
    material = (
        f"docker-volume-v1\x1f{engine_id}\x1f{facts.name}\x1f{facts.driver}"
        f"\x1f{facts.scope}\x1f{facts.created_at}\x1f{facts.mountpoint_ref}"
    ).encode()
    return "sha256:" + hashlib.sha256(material).hexdigest()


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
    docker_executable: str = "docker",
    collector: str = "product-discovery",
    authority_domain_id: str = "host-native",
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

    list_result = runner(
        [docker_executable, "ps", "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}"]
    )
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
    workspace_authorities: list[ProcessWorkspaceAuthority] = []
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
        top_result = runner(
            [docker_executable, "top", container_id, "-o", "pid,ppid,args"]
        )
        admitted_candidates: list[tuple[str, str, str]] = []
        parent_by_pid: dict[str, str | None] = {}
        top_available = top_result.success
        if top_available:
            for _line, row_fields in _output_lines(top_result.stdout):
                if len(row_fields) < 2 or not row_fields[0].isdigit():
                    continue
                pid = row_fields[0]
                if len(row_fields) >= 3 and row_fields[1].isdigit():
                    parent_by_pid[pid] = row_fields[1]
                    argv = row_fields[2:]
                else:
                    # Backward-compatible parsing for injected legacy rows.
                    parent_by_pid[pid] = None
                    argv = row_fields[1:]
                verdict = admit_process(argv)
                if verdict is None:
                    continue
                identity, role = verdict
                admitted_candidates.append((identity, role, pid))

        admitted_by_pid = {
            pid: (identity, role) for identity, role, pid in admitted_candidates
        }
        admitted: list[tuple[str, str, str]] = []
        for identity, role, pid in admitted_candidates:
            ancestor = parent_by_pid.get(pid)
            seen: set[str] = set()
            duplicate_child = False
            while ancestor is not None and ancestor not in seen:
                seen.add(ancestor)
                ancestor_identity = admitted_by_pid.get(ancestor)
                if ancestor_identity is not None and ancestor_identity[0] == identity:
                    duplicate_child = True
                    break
                ancestor = parent_by_pid.get(ancestor)
            if not duplicate_child:
                admitted.append((identity, role, pid))
        if not top_available:
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
        # Runtime correlation starts with the real mount that owns WorkingDir.
        # Storage identity (not host-path availability) decides durable
        # workspace authority; backend capability remains a separate fact.
        inspect_result = runner(
            [
                docker_executable,
                "inspect",
                container_id,
                "--format",
                (
                    "{{.Config.WorkingDir}}\t{{.State.StartedAt}}\t{{.Image}}\t"
                    "{{range .Mounts}}{{.Type}}|{{.Source}}|{{.Destination}}|{{.RW}};{{end}}"
                ),
            ]
        )
        workspace_id: str | None = None
        workspace_evidence_id: str | None = None
        mapped_host_root: Path | None = None
        storage_kind: str | None = None
        storage_resource_identity: str | None = None
        storage_locator: str | None = None
        logical_root: str | None = None
        durability = "UNKNOWN"
        current_reachability = "UNKNOWN"
        protection_capability = "UNSUPPORTED"
        protection_reason_code = "STORAGE_BACKEND_UNSUPPORTED"
        agent_mutation_capability = "UNKNOWN"
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
                    (
                        m
                        for m in inspect_facts.mounts
                        if _canonical_sandbox_root(m.destination) == root
                    ),
                    None,
                )
                mapped_host_root = _map_bind_workspace(inspect_facts, owning_mount)
                if owning_mount is None:
                    storage_kind = "CONTAINER_EPHEMERAL_FS"
                    logical_root = "/"
                    storage_resource_identity = "sha256:" + hashlib.sha256(
                        (
                            f"container-fs\x1f{domain_id}\x1f"
                            f"{inspect_facts.started_at}\x1f{root}"
                        ).encode()
                    ).hexdigest()
                    durability = "EPHEMERAL"
                    current_reachability = "AVAILABLE"
                    protection_reason_code = (
                        "CONTAINER_EPHEMERAL_BACKEND_UNSUPPORTED"
                    )
                else:
                    mount_type = owning_mount.mount_type.casefold()
                    agent_mutation_capability = (
                        "READ_WRITE"
                        if owning_mount.read_write is True
                        else "READ_ONLY"
                        if owning_mount.read_write is False
                        else "UNKNOWN"
                    )
                    if mount_type == "bind":
                        if mapped_host_root is not None:
                            storage_kind = "DOCKER_BIND"
                            durability = "DURABLE"
                            current_reachability = "AVAILABLE"
                            protection_capability = "SUPPORTED"
                            protection_reason_code = "HOST_PATH_BACKEND_SUPPORTED"
                    elif mount_type == "volume" and owning_mount.source:
                        engine_result = runner(
                            [docker_executable, "info", "--format", "{{.ID}}"]
                        )
                        volume_result = runner(
                            [
                                docker_executable,
                                "volume",
                                "inspect",
                                owning_mount.source,
                                "--format",
                                (
                                    "{{.Name}}\t{{.Driver}}\t{{.Scope}}\t"
                                    "{{.CreatedAt}}\t{{.Mountpoint}}"
                                ),
                            ]
                        )
                        volume_facts = (
                            _parse_volume_facts(volume_result)
                            if volume_result.success
                            else None
                        )
                        engine_id = engine_result.stdout.strip()[:128]
                        logical_root = _logical_volume_root(
                            inspect_facts, owning_mount
                        )
                        if volume_facts is not None and engine_id and logical_root:
                            storage_kind = "DOCKER_NAMED_VOLUME"
                            storage_resource_identity = _volume_resource_identity(
                                engine_id, volume_facts
                            )
                            storage_locator = "\x1f".join(
                                (
                                    volume_facts.name,
                                    volume_facts.driver,
                                    volume_facts.scope,
                                    volume_facts.created_at,
                                    volume_facts.mountpoint_ref,
                                    inspect_facts.image_identity or "",
                                )
                            )
                            durability = "DURABLE"
                            current_reachability = "AVAILABLE"
                            protection_capability = (
                                "SUPPORTED"
                                if volume_facts.driver == "local"
                                and inspect_facts.image_identity is not None
                                else "UNSUPPORTED"
                            )
                            protection_reason_code = (
                                "DOCKER_VOLUME_BACKEND_SUPPORTED"
                                if protection_capability == "SUPPORTED"
                                else "DOCKER_VOLUME_HELPER_RUNTIME_UNAVAILABLE"
                            )
                    elif mount_type == "tmpfs":
                        storage_kind = "TMPFS"
                        logical_root = _logical_volume_root(inspect_facts, owning_mount)
                        storage_resource_identity = "sha256:" + hashlib.sha256(
                            f"tmpfs\x1f{domain_id}\x1f{inspect_facts.started_at}\x1f{root}".encode()
                        ).hexdigest()
                        durability = "VOLATILE"
                        current_reachability = "AVAILABLE"
                    else:
                        storage_kind = "CONTAINER_EPHEMERAL_FS"
                        logical_root = _logical_volume_root(inspect_facts, owning_mount)
                        storage_resource_identity = "sha256:" + hashlib.sha256(
                            f"container-fs\x1f{domain_id}\x1f{inspect_facts.started_at}\x1f{root}".encode()
                        ).hexdigest()
                        durability = "EPHEMERAL"
                        current_reachability = "AVAILABLE"
                if storage_resource_identity is not None and logical_root is not None:
                    storage_digest = storage_workspace_digest(
                        storage_resource_identity,
                        logical_root,
                        authority_domain_id,
                    )
                    workspace_id = (
                        f"workspace-{storage_digest.split(':', 1)[1][:24]}"
                    )
                authority_reason_code = (
                    "WORKSPACE_AUTHORITY_CANDIDATE"
                    if mapped_host_root is not None
                    or (storage_resource_identity is not None and logical_root is not None)
                    else "WORKSPACE_STORAGE_IDENTITY_UNAVAILABLE"
                )
                mount_source_ref = (
                    "sha256:"
                    + hashlib.sha256(owning_mount.source.encode()).hexdigest()
                    if owning_mount is not None and owning_mount.source
                    else None
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
                            "workspace_kind": (
                                "HOST_BIND_MAPPED"
                                if mapped_host_root is not None
                                else "SANDBOX_VOLUME"
                            ),
                            "storage_kind": storage_kind,
                            "storage_resource_identity": storage_resource_identity,
                            "logical_root": logical_root,
                            "durability": durability,
                            "current_reachability": current_reachability,
                            "protection_capability": protection_capability,
                            "protection_reason_code": protection_reason_code,
                            "agent_mutation_capability": agent_mutation_capability,
                            "execution_domain_id": domain_id,
                            "container_id": short_id,
                            "container_started_at": inspect_facts.started_at,
                            "root": root,
                            "mount_type": (
                                owning_mount.mount_type if owning_mount else None
                            ),
                            "mount_source_ref": mount_source_ref,
                            "mount_destination": (
                                owning_mount.destination if owning_mount else None
                            ),
                            "mount_read_write": (
                                owning_mount.read_write if owning_mount else None
                            ),
                            "working_dir": inspect_facts.working_dir,
                            "reason_code": reason,
                            "authority_reason_code": authority_reason_code,
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
            elif root is None:
                reason_code = {
                    "SANDBOX_WORKSPACE_AMBIGUOUS": "WORKSPACE_EVIDENCE_AMBIGUOUS",
                    "SANDBOX_WORKSPACE_NO_MOUNT_OWNER": (
                        "WORKSPACE_DURABLE_HOST_BACKING_MISSING"
                    ),
                    "SANDBOX_WORKDIR_UNKNOWN": "WORKSPACE_EVIDENCE_MISSING",
                }.get(reason, "WORKSPACE_EVIDENCE_INVALID")
                evidence.append(
                    ProbeEvidence(
                        evidence_id=(
                            "container-workspace-candidate-"
                            + hashlib.sha256(
                                (short_id + reason_code + nonce).encode()
                            ).hexdigest()[:24]
                        ),
                        collector=collector,
                        source="docker-inspect-host-side",
                        observed_at=observed_at,
                        fact_type="workspace.candidate",
                        value={
                            "execution_domain_id": domain_id,
                            "container_id": short_id,
                            "reason_code": reason_code,
                        },
                        reliability=EvidenceReliability.HIGH,
                        confidence=0.85,
                        status=CapabilityStatus.UNKNOWN,
                        sanitized=True,
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
            if (
                (mapped_host_root is not None or storage_resource_identity is not None)
                and workspace_id is not None
                and workspace_evidence_id is not None
                and inspect_facts is not None
                and inspect_facts.started_at is not None
            ):
                workspace_authorities.append(
                    ProcessWorkspaceAuthority(
                        process_instance_id=_stable_id(
                            "container-process",
                            short_id,
                            inspect_facts.started_at,
                            pid,
                        ),
                        candidate_id=workspace_id,
                        execution_domain_id=authority_domain_id,
                        evidence_refs=(
                            runtime_evidence_id,
                            workspace_evidence_id,
                            agent_evidence_id,
                        ),
                        agent_id=agent_id,
                        cwd=mapped_host_root,
                        storage_kind=storage_kind or "OTHER_UNSUPPORTED",
                        storage_resource_identity=storage_resource_identity,
                        storage_locator=storage_locator,
                        logical_root=logical_root,
                        durability=durability,
                        current_reachability=current_reachability,
                        protection_capability=protection_capability,
                        protection_reason_code=protection_reason_code,
                        agent_mutation_capability=agent_mutation_capability,
                    )
                )

    return HostDomainObservation(
        domains=tuple(domains),
        runtimes=tuple(runtimes),
        agents=tuple(agents),
        workspaces=tuple(workspaces),
        evidence=tuple(evidence),
        workspace_authorities=tuple(workspace_authorities),
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
        evidence.append(
            ProbeEvidence(
                evidence_id=(
                    "wsl-agent-probe-"
                    f"{hashlib.sha256((name + observed_at.isoformat()).encode()).hexdigest()[:24]}"
                ),
                collector=collector,
                source="wsl-list-host-side",
                observed_at=observed_at,
                fact_type="probe.unreachable",
                value={
                    "domain_label": name,
                    "execution_domain_id": domain_id,
                    "reason_code": "NO_BOUNDED_HOST_READ",
                    "scope": "agents",
                },
                reliability=EvidenceReliability.HIGH,
                status=CapabilityStatus.UNKNOWN,
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
