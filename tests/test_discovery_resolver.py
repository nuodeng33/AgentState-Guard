"""Decision tests for merging current-runtime probe evidence."""

from datetime import UTC, datetime

from agentguard.discovery import (
    CapabilityStatus,
    DiscoveryError,
    DiscoveryErrorCode,
    EvidenceReliability,
    ExecutionDomainKind,
    ProbeEvidence,
)
from agentguard.discovery.resolver import resolve_execution_domains

OBSERVED_AT = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)


def _evidence(
    evidence_id: str,
    fact_type: str,
    value,
    *,
    status: CapabilityStatus = CapabilityStatus.AVAILABLE,
    error: DiscoveryError | None = None,
) -> ProbeEvidence:
    return ProbeEvidence(
        evidence_id=evidence_id,
        collector="resolver-fixture",
        source="controlled_test",
        observed_at=OBSERVED_AT,
        fact_type=fact_type,
        value=value,
        reliability=EvidenceReliability.HIGH,
        confidence=0.95,
        status=status,
        error=error,
        sanitized=True,
    )


def test_resolver_builds_windows_wsl_container_hierarchy_from_independent_facts():
    evidence = (
        _evidence(
            "platform",
            "platform.identity",
            {"os_name": "posix", "system": "Linux", "release": "wsl", "version": "wsl"},
        ),
        _evidence("interop", "wsl.interop_presence", {"present": True}),
        _evidence(
            "kernel",
            "kernel.osrelease",
            {"microsoft": True, "wsl": True, "wsl2": True},
        ),
        _evidence("docker", "container.dockerenv", {"present": True}),
        _evidence(
            "cgroup",
            "container.cgroup_self",
            {"docker": True, "containerd": False, "podman": False, "container_hint": True},
        ),
    )

    result = resolve_execution_domains(evidence)

    windows = result.domains[0]
    wsl = windows.children[0]
    container = wsl.children[0]
    assert [windows.kind, wsl.kind, container.kind] == [
        ExecutionDomainKind.WINDOWS,
        ExecutionDomainKind.WSL,
        ExecutionDomainKind.CONTAINER,
    ]
    assert result.status is CapabilityStatus.AVAILABLE


def test_wsl_presence_does_not_imply_generation_without_kernel_signature():
    evidence = (
        _evidence(
            "platform",
            "platform.identity",
            {"os_name": "posix", "system": "Linux", "release": "test", "version": "test"},
        ),
        _evidence("interop", "wsl.interop_presence", {"present": True}),
    )

    result = resolve_execution_domains(evidence)

    windows = result.domains[0]
    wsl = windows.children[0]
    generation = wsl.capabilities.get("wsl_generation")
    assert wsl.kind is ExecutionDomainKind.WSL
    assert wsl.label == "WSL generation unknown"
    assert wsl.confidence == 0.65
    assert wsl.capabilities.get("self_visible").status is CapabilityStatus.AVAILABLE
    assert generation.status is CapabilityStatus.UNKNOWN
    assert generation.reason_code == "INSUFFICIENT_GENERATION_EVIDENCE"
    assert windows.capabilities.get("self_visible").status is CapabilityStatus.UNKNOWN


def test_single_container_marker_limits_confidence_and_host_daemon_stays_unknown():
    evidence = (
        _evidence(
            "platform",
            "platform.identity",
            {"os_name": "posix", "system": "Linux", "release": "6.8", "version": "test"},
        ),
        _evidence("docker", "container.dockerenv", {"present": True}),
    )

    result = resolve_execution_domains(evidence)

    container = result.domains[0].children[0]
    daemon = container.capabilities.get("host_docker_daemon")
    assert container.confidence is not None and container.confidence <= 0.65
    assert daemon.status is CapabilityStatus.UNKNOWN
    assert daemon.is_available is False


def test_unknown_host_can_contain_available_current_container_without_safety_inference():
    evidence = (
        _evidence(
            "platform",
            "platform.identity",
            {"os_name": "mystery", "system": "Plan9", "release": "x", "version": "x"},
        ),
        _evidence("podman", "container.containerenv", {"present": True}),
        _evidence(
            "cgroup",
            "container.cgroup_self",
            {"docker": False, "containerd": False, "podman": True, "container_hint": True},
        ),
    )

    result = resolve_execution_domains(evidence)

    host = result.domains[0]
    container = host.children[0]
    assert host.kind is ExecutionDomainKind.UNKNOWN
    assert host.capabilities.get("self_visible").status is CapabilityStatus.UNKNOWN
    assert container.kind is ExecutionDomainKind.CONTAINER
    assert container.capabilities.get("self_visible").status is CapabilityStatus.AVAILABLE
    assert container.capabilities.get("host_docker_daemon").status is CapabilityStatus.UNKNOWN
    assert result.status is CapabilityStatus.DEGRADED


def test_probe_error_degrades_but_does_not_replace_valid_platform_fact():
    failure = DiscoveryError(
        code=DiscoveryErrorCode.COLLECTOR_FAILURE,
        message="Controlled failure",
        collector="resolver-fixture",
        source="controlled_test",
    )
    evidence = (
        _evidence(
            "platform",
            "platform.identity",
            {"os_name": "posix", "system": "Linux", "release": "6.8", "version": "test"},
        ),
        _evidence(
            "failed",
            "container.cgroup_self",
            None,
            status=CapabilityStatus.ERROR,
            error=failure,
        ),
    )

    result = resolve_execution_domains(evidence)

    assert result.status is CapabilityStatus.DEGRADED
    assert result.domains[0].kind is ExecutionDomainKind.LINUX
    assert result.errors == (failure,)
