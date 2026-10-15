"""Pure decision rules for current execution-domain evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .capabilities import CapabilityAssessment, CapabilityStatus, DomainCapabilities
from .errors import DiscoveryError
from .models import ExecutionDomainDescriptor, ExecutionDomainKind, ProbeEvidence


@dataclass(frozen=True)
class DomainResolution:
    domains: tuple[ExecutionDomainDescriptor, ...]
    status: CapabilityStatus
    errors: tuple[DiscoveryError, ...] = ()


def _available(evidence: ProbeEvidence) -> bool:
    return evidence.status is CapabilityStatus.AVAILABLE


def _value(evidence: ProbeEvidence | None) -> Mapping[str, Any]:
    if evidence is None or not isinstance(evidence.value, Mapping):
        return {}
    return evidence.value


def _first(evidence: Sequence[ProbeEvidence], fact_type: str) -> ProbeEvidence | None:
    return next((item for item in evidence if item.fact_type == fact_type), None)


def _all(evidence: Sequence[ProbeEvidence], *fact_types: str) -> tuple[ProbeEvidence, ...]:
    wanted = set(fact_types)
    return tuple(item for item in evidence if item.fact_type in wanted)


def _self_visible(
    status: CapabilityStatus,
    evidence_ids: tuple[str, ...],
    confidence: float | None,
    reason_code: str,
) -> DomainCapabilities:
    return DomainCapabilities(
        {
            "self_visible": CapabilityAssessment(
                status=status,
                reason_code=reason_code,
                evidence_ids=evidence_ids,
                confidence=confidence,
            )
        }
    )


def _container_capabilities(
    evidence_ids: tuple[str, ...],
    confidence: float,
) -> DomainCapabilities:
    return DomainCapabilities(
        {
            "self_visible": CapabilityAssessment(
                status=CapabilityStatus.AVAILABLE,
                reason_code="CURRENT_PROCESS_CONTAINER_FACTS",
                evidence_ids=evidence_ids,
                confidence=confidence,
            ),
            "host_docker_daemon": CapabilityAssessment(
                status=CapabilityStatus.UNKNOWN,
                reason_code="NOT_PROBED_OFFLINE",
                evidence_ids=evidence_ids,
            ),
        }
    )


def _kernel_generation(evidence: ProbeEvidence) -> str:
    value = _value(evidence)
    generation = str(value.get("generation", "UNKNOWN")).upper()
    if generation in {"WSL1", "WSL2"}:
        return generation
    if bool(value.get("wsl2")):
        return "WSL2"
    if bool(value.get("wsl1")):
        return "WSL1"
    return "UNKNOWN"


def _wsl_capabilities(
    presence_evidence_ids: tuple[str, ...],
    presence_confidence: float,
    generation: str,
    generation_evidence_ids: tuple[str, ...],
    generation_confidence: float | None,
    generation_conflict: bool,
) -> DomainCapabilities:
    if generation in {"WSL1", "WSL2"}:
        generation_assessment = CapabilityAssessment(
            status=CapabilityStatus.AVAILABLE,
            reason_code=f"{generation}_KERNEL_SIGNATURE",
            evidence_ids=generation_evidence_ids,
            confidence=generation_confidence,
        )
    elif generation_conflict:
        generation_assessment = CapabilityAssessment(
            status=CapabilityStatus.DEGRADED,
            reason_code="CONFLICTING_GENERATION_EVIDENCE",
            evidence_ids=generation_evidence_ids,
            confidence=0.3,
        )
    else:
        generation_assessment = CapabilityAssessment(
            status=CapabilityStatus.UNKNOWN,
            reason_code="INSUFFICIENT_GENERATION_EVIDENCE",
            evidence_ids=generation_evidence_ids,
        )
    return DomainCapabilities(
        {
            "self_visible": CapabilityAssessment(
                status=CapabilityStatus.AVAILABLE,
                reason_code="CURRENT_PROCESS_WSL_FACTS",
                evidence_ids=presence_evidence_ids,
                confidence=presence_confidence,
            ),
            "wsl_generation": generation_assessment,
        }
    )


def resolve_execution_domains(evidence: Sequence[ProbeEvidence]) -> DomainResolution:
    """Merge sanitized evidence without performing any I/O."""

    records = tuple(evidence)
    errors = tuple(item.error for item in records if item.error is not None)
    platform_record = _first(records, "platform.identity")
    platform_value = _value(platform_record) if platform_record and _available(platform_record) else {}
    os_name = str(platform_value.get("os_name", "")).lower()
    system = str(platform_value.get("system", "")).lower()
    windows_signal = os_name == "nt" or system == "windows"
    linux_signal = os_name == "posix" or system == "linux"
    platform_conflict = windows_signal and linux_signal

    if platform_conflict or not platform_record or not _available(platform_record):
        base_kind = ExecutionDomainKind.UNKNOWN
        base_label = "Unknown host"
        base_confidence = 0.2 if platform_record else None
    elif windows_signal:
        base_kind = ExecutionDomainKind.WINDOWS
        base_label = "Windows native"
        base_confidence = 0.95
    elif linux_signal:
        base_kind = ExecutionDomainKind.LINUX
        base_label = "Linux host"
        base_confidence = 0.95
    else:
        base_kind = ExecutionDomainKind.UNKNOWN
        base_label = "Unknown host"
        base_confidence = 0.2

    platform_ids = (
        (platform_record.evidence_id,)
        if platform_record is not None
        else ()
    )

    interop = _first(records, "wsl.interop_presence")
    kernel_records = _all(records, "kernel.osrelease", "kernel.proc_version")
    interop_present = bool(_value(interop).get("present")) if interop and _available(interop) else False
    wsl_kernel_records = tuple(
        item for item in kernel_records if _available(item) and bool(_value(item).get("wsl"))
    )
    generation_records = tuple(
        item for item in wsl_kernel_records if _kernel_generation(item) != "UNKNOWN"
    )
    generations = {_kernel_generation(item) for item in generation_records}
    generation_conflict = len(generations) > 1
    wsl_generation = next(iter(generations)) if len(generations) == 1 else "UNKNOWN"
    generation_ids = tuple(item.evidence_id for item in generation_records)
    if wsl_generation == "UNKNOWN" and not generation_conflict:
        generation_ids = tuple(item.evidence_id for item in wsl_kernel_records)
    generation_confidence = (
        0.85 if len(generation_records) >= 2 else 0.75 if generation_records else None
    )
    wsl_detected = interop_present or bool(wsl_kernel_records)
    wsl_ids = tuple(
        dict.fromkeys(
            (
                *((interop.evidence_id,) if interop_present and interop else ()),
                *(item.evidence_id for item in wsl_kernel_records),
            )
        )
    )
    wsl_confidence = 0.95 if interop_present and wsl_kernel_records else 0.65

    dockerenv = _first(records, "container.dockerenv")
    containerenv = _first(records, "container.containerenv")
    dockerenv_present = bool(_value(dockerenv).get("present")) if dockerenv and _available(dockerenv) else False
    containerenv_present = (
        bool(_value(containerenv).get("present"))
        if containerenv and _available(containerenv)
        else False
    )
    cgroup_records = _all(records, "container.cgroup_init", "container.cgroup_self")
    positive_cgroups = tuple(
        item
        for item in cgroup_records
        if _available(item) and bool(_value(item).get("container_hint"))
    )
    mount = _first(records, "container.mountinfo")
    mount_value = _value(mount)
    mount_runtime_hint = bool(
        mount_value.get("docker")
        or mount_value.get("containerd")
        or mount_value.get("podman")
    )
    mount_support = bool(mount_runtime_hint or mount_value.get("overlay")) if mount and _available(mount) else False
    marker_present = dockerenv_present or containerenv_present
    cgroup_present = bool(positive_cgroups)
    container_detected = marker_present or cgroup_present or mount_runtime_hint

    positive_container_records = []
    if dockerenv_present and dockerenv:
        positive_container_records.append(dockerenv)
    if containerenv_present and containerenv:
        positive_container_records.append(containerenv)
    positive_container_records.extend(positive_cgroups)
    if mount_support and mount:
        positive_container_records.append(mount)
    container_ids = tuple(dict.fromkeys(item.evidence_id for item in positive_container_records))

    independent_categories = sum((marker_present, cgroup_present, mount_support))
    docker_signal = dockerenv_present or any(
        bool(_value(item).get("docker") or _value(item).get("containerd"))
        for item in (*positive_cgroups, *((mount,) if mount_runtime_hint and mount else ()))
    )
    podman_signal = containerenv_present or any(
        bool(_value(item).get("podman"))
        for item in (*positive_cgroups, *((mount,) if mount_runtime_hint and mount else ()))
    )
    readable_negative_cgroups = bool(cgroup_records) and all(
        _available(item) and not bool(_value(item).get("container_hint"))
        for item in cgroup_records
    )
    readable_negative_mount = bool(mount and _available(mount) and not mount_support)
    lone_marker_conflict = (
        independent_categories == 1
        and marker_present
        and readable_negative_cgroups
        and readable_negative_mount
    )
    runtime_conflict = docker_signal and podman_signal
    container_conflict = lone_marker_conflict or runtime_conflict

    if container_conflict:
        container_confidence = 0.45
    elif independent_categories >= 2:
        container_confidence = 0.9
    else:
        container_confidence = 0.6

    if runtime_conflict:
        container_label = "Container (conflicting runtime signals)"
    elif podman_signal:
        container_label = "Podman container"
    elif docker_signal:
        container_label = "Docker/containerd container"
    else:
        container_label = "Container (runtime unknown)"

    container_domain = None
    if container_detected:
        container_domain = ExecutionDomainDescriptor(
            domain_id="current-container",
            kind=ExecutionDomainKind.CONTAINER,
            label=container_label,
            capabilities=_container_capabilities(container_ids, container_confidence),
            evidence_ids=container_ids,
            confidence=container_confidence,
        )

    if wsl_detected:
        wsl_evidence_ids = tuple(dict.fromkeys((*platform_ids, *wsl_ids)))
        wsl_domain = ExecutionDomainDescriptor(
            domain_id="current-wsl",
            kind=ExecutionDomainKind.WSL,
            label=(
                f"{wsl_generation} current runtime"
                if wsl_generation in {"WSL1", "WSL2"}
                else "WSL generation unknown"
            ),
            capabilities=_wsl_capabilities(
                wsl_evidence_ids,
                wsl_confidence,
                wsl_generation,
                generation_ids,
                generation_confidence,
                generation_conflict,
            ),
            children=((container_domain,) if container_domain else ()),
            evidence_ids=wsl_evidence_ids,
            confidence=wsl_confidence,
        )
        windows_domain = ExecutionDomainDescriptor(
            domain_id="windows-host-inferred",
            kind=ExecutionDomainKind.WINDOWS,
            label="Windows host inferred from WSL",
            capabilities=_self_visible(
                CapabilityStatus.UNKNOWN,
                wsl_ids,
                None,
                "HOST_NOT_PROBED_OFFLINE",
            ),
            children=(wsl_domain,),
            evidence_ids=wsl_ids,
            confidence=0.8,
        )
        domains = (windows_domain,)
    else:
        base_available = base_kind is not ExecutionDomainKind.UNKNOWN
        base_capabilities = _self_visible(
            CapabilityStatus.AVAILABLE if base_available else CapabilityStatus.UNKNOWN,
            platform_ids,
            base_confidence if base_available else None,
            "CURRENT_PROCESS_PLATFORM_FACTS" if base_available else "INSUFFICIENT_PLATFORM_EVIDENCE",
        )
        base_domain = ExecutionDomainDescriptor(
            domain_id=(
                "windows-native"
                if base_kind is ExecutionDomainKind.WINDOWS
                else "linux-host"
                if base_kind is ExecutionDomainKind.LINUX
                else "unknown-host"
            ),
            kind=base_kind,
            label=base_label,
            capabilities=base_capabilities,
            children=((container_domain,) if container_domain else ()),
            evidence_ids=platform_ids,
            confidence=base_confidence,
        )
        domains = (base_domain,)

    failure_statuses = {CapabilityStatus.ERROR, CapabilityStatus.PERMISSION_DENIED}
    has_probe_failure = any(item.status in failure_statuses for item in records)
    proc_fact_types = {
        "kernel.osrelease",
        "kernel.proc_version",
        "container.cgroup_init",
        "container.cgroup_self",
        "container.mountinfo",
        "process.security_status",
    }
    proc_records = tuple(item for item in records if item.fact_type in proc_fact_types)
    proc_unavailable = bool(proc_records) and all(
        item.status is CapabilityStatus.NOT_PRESENT for item in proc_records
    )
    has_valid_fact = any(_available(item) for item in records)

    if has_probe_failure and not has_valid_fact:
        status = CapabilityStatus.ERROR
    elif (
        has_probe_failure
        or platform_conflict
        or container_conflict
        or generation_conflict
        or proc_unavailable
        or (base_kind is ExecutionDomainKind.UNKNOWN and container_detected)
    ):
        status = CapabilityStatus.DEGRADED
    elif base_kind is ExecutionDomainKind.UNKNOWN and not wsl_detected:
        status = CapabilityStatus.UNKNOWN
    else:
        status = CapabilityStatus.AVAILABLE

    return DomainResolution(domains=domains, status=status, errors=errors)


__all__ = ["DomainResolution", "resolve_execution_domains"]
