"""R4-P2C deterministic SelfRuntime and Host Probe merge tests."""

from copy import deepcopy
from datetime import timedelta

from agentguard.discovery import (
    CapabilityAssessment,
    CapabilityStatus,
    DiscoverySnapshot,
    DomainCapabilities,
    ExecutionDomainDescriptor,
    ExecutionDomainKind,
    ProbeEvidence,
)
from agentguard.discovery.host import HostProbeImporter
from tests.test_discovery_host_import import NOW, _valid_payload


def _evidence(evidence_id: str, fact_type: str, *, status=CapabilityStatus.AVAILABLE):
    return ProbeEvidence(
        evidence_id=evidence_id,
        collector="self_runtime_fixture",
        source="SELF_RUNTIME",
        observed_at=NOW,
        fact_type=fact_type,
        value={"present": True},
        reliability="HIGH",
        confidence=0.95,
        status=status,
        sanitized=True,
    )


def _snapshot(domain: ExecutionDomainDescriptor, *, status=CapabilityStatus.AVAILABLE):
    evidence_ids = []

    def collect(item):
        evidence_ids.extend(item.evidence_ids)
        for child in item.children:
            collect(child)

    collect(domain)
    evidence = tuple(
        _evidence(item, "local.domain")
        for item in dict.fromkeys(evidence_ids)
    )
    return DiscoverySnapshot(
        snapshot_id="self-runtime-fixture",
        observed_at=NOW,
        domains=(domain,),
        evidence=evidence,
        status=status,
    )


def _local_linux(*, docker_status=CapabilityStatus.PERMISSION_DENIED):
    return ExecutionDomainDescriptor(
        domain_id="linux-host",
        kind=ExecutionDomainKind.LINUX,
        label="Direct Linux host",
        capabilities=DomainCapabilities(
            {
                "docker_daemon": CapabilityAssessment(
                    status=docker_status,
                    reason_code="LOCAL_DOCKER_PROBE",
                    evidence_ids=("local-linux",),
                    confidence=0.95,
                ),
                "self_visible": CapabilityAssessment(
                    status=CapabilityStatus.AVAILABLE,
                    reason_code="CURRENT_PROCESS_PLATFORM_FACTS",
                    evidence_ids=("local-linux",),
                    confidence=0.95,
                ),
            }
        ),
        evidence_ids=("local-linux",),
        confidence=0.95,
    )


def _nested_local():
    container = ExecutionDomainDescriptor(
        domain_id="current-container",
        kind=ExecutionDomainKind.CONTAINER,
        label="Current container",
        evidence_ids=("local-container",),
        confidence=0.9,
    )
    wsl = ExecutionDomainDescriptor(
        domain_id="current-wsl",
        kind=ExecutionDomainKind.WSL,
        label="Current WSL2",
        children=(container,),
        evidence_ids=("local-wsl",),
        confidence=0.9,
    )
    windows = ExecutionDomainDescriptor(
        domain_id="windows-host-inferred",
        kind=ExecutionDomainKind.WINDOWS,
        label="Windows host inferred from WSL",
        capabilities=DomainCapabilities(
            {
                "self_visible": CapabilityAssessment(
                    status=CapabilityStatus.UNKNOWN,
                    reason_code="HOST_NOT_PROBED_OFFLINE",
                    evidence_ids=("local-windows-inference",),
                )
            }
        ),
        children=(wsl,),
        evidence_ids=("local-windows-inference",),
        confidence=0.8,
    )
    return windows


def _import(payload=None, *, now=NOW):
    return HostProbeImporter().import_payload(payload or _valid_payload(), now=now)


def _find(domains, domain_id):
    for domain in domains:
        if domain.domain_id == domain_id:
            return domain
        found = _find(domain.children, domain_id)
        if found is not None:
            return found
    return None


def _reason_codes(snapshot):
    return {
        item.details.get("reason_code")
        for item in snapshot.errors
        if item.details.get("reason_code")
    }


def test_fresh_host_probe_supplements_inferred_parent_without_claiming_self_visibility():
    from agentguard.discovery.host import merge_host_probe_snapshot

    local = _snapshot(_nested_local())
    payload = _valid_payload()
    payload["source_domain_id"] = "windows-host-inferred"
    payload["host"]["domain_id"] = "windows-host-inferred"

    merged = merge_host_probe_snapshot(local, _import(payload))

    windows = merged.domains[0]
    assert len(merged.domains) == 1
    assert windows.domain_id == "windows-host-inferred"
    assert windows.capabilities.get("host_os").status is CapabilityStatus.AVAILABLE
    assert windows.capabilities.get("self_visible").status is CapabilityStatus.UNKNOWN
    assert windows.children[0].domain_id == "current-wsl"
    assert windows.children[0].children[0].domain_id == "current-container"


def test_direct_domain_and_capability_evidence_wins_host_conflict_and_keeps_refs():
    from agentguard.discovery.host import merge_host_probe_snapshot

    local = _snapshot(_local_linux())
    payload = _valid_payload()
    payload["source_domain_id"] = "linux-host"
    payload["host"]["domain_id"] = "linux-host"
    payload["host"]["kind"] = "WINDOWS"

    merged = merge_host_probe_snapshot(local, _import(payload))

    domain = merged.domains[0]
    docker = domain.capabilities.get("docker_daemon")
    assert domain.kind is ExecutionDomainKind.LINUX
    assert docker.status is CapabilityStatus.PERMISSION_DENIED
    assert "local-linux" in docker.evidence_ids
    assert any(item.startswith("host:probe-001:") for item in docker.evidence_ids)
    assert merged.status is CapabilityStatus.DEGRADED
    assert "HOST_PROBE_DOMAIN_CONFLICT" in _reason_codes(merged)
    assert "HOST_PROBE_CAPABILITY_CONFLICT" in _reason_codes(merged)


def test_stale_host_probe_is_visible_but_cannot_add_available_capability():
    from agentguard.discovery.host import merge_host_probe_snapshot

    local = _snapshot(_local_linux())
    stale = _import(now=NOW + timedelta(seconds=60))

    merged = merge_host_probe_snapshot(local, stale)

    host = _find(merged.domains, "windows-host")
    assert host is not None
    assert host.capabilities.get("docker_daemon").status is CapabilityStatus.UNKNOWN
    assert host.capabilities.get("docker_daemon").reason_code == "STALE_HOST_EVIDENCE"
    assert merged.status is CapabilityStatus.DEGRADED
    assert "HOST_PROBE_STALE" in _reason_codes(merged)


def test_expired_host_probe_does_not_participate_in_domain_or_capability_conclusions():
    from agentguard.discovery.host import merge_host_probe_snapshot

    local = _snapshot(_local_linux())
    expired = _import(now=NOW + timedelta(seconds=121))

    merged = merge_host_probe_snapshot(local, expired)

    assert _find(merged.domains, "windows-host") is None
    assert merged.domains[0].to_dict() == local.domains[0].to_dict()
    assert any(item.source == "HOST_PROBE" for item in merged.evidence)
    assert "HOST_PROBE_EXPIRED" in _reason_codes(merged)


def test_partial_host_probe_error_preserves_all_self_runtime_facts():
    from agentguard.discovery.host import merge_host_probe_snapshot

    local = _snapshot(_nested_local())
    payload = _valid_payload()
    payload["errors"] = [
        {
            "code": "PERMISSION_DENIED",
            "message": "not retained",
            "collector": "host-helper",
            "source": "host-helper",
            "retryable": False,
            "details": {"reason_code": "HOST_DOCKER_PERMISSION_DENIED"},
        }
    ]

    merged = merge_host_probe_snapshot(local, _import(payload))

    assert _find(merged.domains, "current-wsl") is not None
    assert _find(merged.domains, "current-container") is not None
    assert merged.status is CapabilityStatus.DEGRADED
    assert any(
        item.details.get("reason_code") == "HOST_DOCKER_PERMISSION_DENIED"
        for item in merged.errors
    )


def test_windows_wsl_container_ids_and_nesting_remain_stable_on_recursive_merge():
    from agentguard.discovery.host import merge_host_probe_snapshot

    local = _snapshot(_nested_local())
    payload = _valid_payload()
    payload["source_domain_id"] = "windows-host-inferred"
    payload["host"] = {
        "domain_id": "windows-host-inferred",
        "kind": "WINDOWS",
        "label": "Imported Windows",
        "capabilities": {},
        "evidence_ids": ["host-os"],
        "confidence": 0.8,
        "children": [
            {
                "domain_id": "current-wsl",
                "kind": "WSL",
                "label": "Imported WSL",
                "capabilities": {},
                "evidence_ids": ["host-os"],
                "confidence": 0.8,
                "children": [
                    {
                        "domain_id": "current-container",
                        "kind": "CONTAINER",
                        "label": "Imported container",
                        "capabilities": {},
                        "children": [],
                        "evidence_ids": ["docker-daemon"],
                        "confidence": 0.7,
                    }
                ],
            }
        ],
    }

    merged = merge_host_probe_snapshot(local, _import(payload))

    assert [item.domain_id for item in merged.domains] == ["windows-host-inferred"]
    assert merged.domains[0].children[0].domain_id == "current-wsl"
    assert merged.domains[0].children[0].children[0].domain_id == "current-container"
    assert merged.domains[0].children[0].children[0].children == ()


def test_similar_domains_with_distinct_ids_are_not_coalesced():
    from agentguard.discovery.host import merge_host_probe_snapshot

    local = _snapshot(_local_linux())
    payload = _valid_payload()
    payload["host"]["kind"] = "LINUX"
    payload["host"]["label"] = "Direct Linux host"

    merged = merge_host_probe_snapshot(local, _import(payload))

    assert {item.domain_id for item in merged.domains} == {"linux-host", "windows-host"}


def test_host_docker_available_does_not_create_security_or_allow_capability():
    from agentguard.discovery.host import merge_host_probe_snapshot

    merged = merge_host_probe_snapshot(_snapshot(_local_linux()), _import())
    host = _find(merged.domains, "windows-host")

    assert host.capabilities.get("docker_daemon").status is CapabilityStatus.AVAILABLE
    names = {name.lower() for name in host.capabilities.assessments}
    assert not any("allow" in name or "security" in name for name in names)


def test_invalid_host_import_preserves_local_snapshot_and_adds_structured_error():
    from agentguard.discovery.host import merge_host_probe_snapshot

    local = _snapshot(_local_linux())
    payload = deepcopy(_valid_payload())
    payload["schema_version"] = "2.0"

    merged = merge_host_probe_snapshot(local, _import(payload))

    assert merged.domains[0].to_dict() == local.domains[0].to_dict()
    assert merged.status is CapabilityStatus.DEGRADED
    assert "INCOMPATIBLE_SCHEMA" in _reason_codes(merged)
