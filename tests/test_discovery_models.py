"""Contract tests for the R4-P1 Runtime Discovery data model."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from agentguard.discovery import (
    AgentDescriptor,
    AgentLifecycleStatus,
    CapabilityAssessment,
    CapabilityStatus,
    DiscoveryError,
    DiscoveryErrorCode,
    DiscoverySnapshot,
    DomainCapabilities,
    EvidenceReliability,
    ExecutionDomainDescriptor,
    ExecutionDomainKind,
    ProbeEvidence,
    RuntimeDescriptor,
    WorkspaceDescriptor,
)


OBSERVED_AT = datetime(2026, 8, 2, 8, 30, tzinfo=timezone.utc)


def _evidence(evidence_id: str, fact_type: str) -> ProbeEvidence:
    return ProbeEvidence(
        evidence_id=evidence_id,
        collector="fixture",
        source="controlled_test",
        observed_at=OBSERVED_AT,
        fact_type=fact_type,
        value={"present": True},
        reliability=EvidenceReliability.HIGH,
        confidence=0.95,
        status=CapabilityStatus.AVAILABLE,
        sanitized=True,
    )


def test_windows_wsl_container_domains_remain_nested_after_round_trip():
    container = ExecutionDomainDescriptor(
        domain_id="container-1",
        kind=ExecutionDomainKind.CONTAINER,
        label="Docker container",
        evidence_ids=("ev-container",),
        confidence=0.95,
    )
    wsl = ExecutionDomainDescriptor(
        domain_id="wsl-ubuntu",
        kind=ExecutionDomainKind.WSL,
        label="WSL2 Ubuntu",
        children=(container,),
        evidence_ids=("ev-wsl",),
        confidence=0.95,
    )
    windows = ExecutionDomainDescriptor(
        domain_id="windows-host",
        kind=ExecutionDomainKind.WINDOWS,
        label="Windows host",
        children=(wsl,),
        evidence_ids=("ev-windows",),
        confidence=0.95,
    )
    snapshot = DiscoverySnapshot(
        snapshot_id="snapshot-nested",
        observed_at=OBSERVED_AT,
        domains=(windows,),
        evidence=(
            _evidence("ev-windows", "domain.windows"),
            _evidence("ev-wsl", "domain.wsl"),
            _evidence("ev-container", "domain.container"),
        ),
        status=CapabilityStatus.AVAILABLE,
    )

    restored = DiscoverySnapshot.from_dict(snapshot.to_dict())

    assert restored.domains[0].kind is ExecutionDomainKind.WINDOWS
    assert restored.domains[0].children[0].kind is ExecutionDomainKind.WSL
    assert restored.domains[0].children[0].children[0].kind is ExecutionDomainKind.CONTAINER


def test_plain_linux_host_has_independent_capability_facts():
    capabilities = DomainCapabilities(
        {
            "filesystem_read": CapabilityAssessment(
                status=CapabilityStatus.AVAILABLE,
                reason_code="PROBE_CONFIRMED",
                evidence_ids=("ev-linux",),
                confidence=0.9,
            )
        }
    )
    linux = ExecutionDomainDescriptor(
        domain_id="linux-host",
        kind=ExecutionDomainKind.LINUX,
        capabilities=capabilities,
        evidence_ids=("ev-linux",),
        confidence=0.9,
    )

    payload = linux.to_dict()

    assert payload["kind"] == "LINUX"
    assert payload["children"] == []
    assert payload["capabilities"]["filesystem_read"]["status"] == "AVAILABLE"


def test_unreachable_and_not_present_are_distinct_non_available_states():
    unreachable = CapabilityAssessment(
        status=CapabilityStatus.UNREACHABLE,
        reason_code="DOMAIN_BOUNDARY",
    )
    not_present = CapabilityAssessment(
        status=CapabilityStatus.NOT_PRESENT,
        reason_code="CONFIRMED_ABSENT",
    )

    assert unreachable.status is not not_present.status
    assert unreachable.to_dict()["status"] == "UNREACHABLE"
    assert not_present.to_dict()["status"] == "NOT_PRESENT"
    assert unreachable.is_available is False
    assert not_present.is_available is False


def test_permission_denied_keeps_machine_readable_error_reason():
    error = DiscoveryError(
        code=DiscoveryErrorCode.PERMISSION_DENIED,
        message="Access was denied by the execution boundary",
        collector="fixture",
        source="controlled_test",
        retryable=False,
        details={"operation": "metadata_read"},
    )
    assessment = CapabilityAssessment(
        status=CapabilityStatus.PERMISSION_DENIED,
        reason_code="ACCESS_POLICY_DENIED",
        error=error,
    )

    payload = assessment.to_dict()

    assert payload["status"] == "PERMISSION_DENIED"
    assert payload["reason_code"] == "ACCESS_POLICY_DENIED"
    assert payload["error"]["code"] == "PERMISSION_DENIED"
    assert payload["error"]["retryable"] is False


def test_agent_lifecycle_levels_are_five_distinct_non_equivalent_states():
    levels = (
        AgentLifecycleStatus.DETECTED,
        AgentLifecycleStatus.RUNNING,
        AgentLifecycleStatus.OBSERVED,
        AgentLifecycleStatus.INTEGRATED,
        AgentLifecycleStatus.ENFORCED,
    )

    assert len(set(levels)) == 5
    assert [level.value for level in levels] == [
        "DETECTED",
        "RUNNING",
        "OBSERVED",
        "INTEGRATED",
        "ENFORCED",
    ]


def test_partial_probe_failure_preserves_other_valid_snapshot_facts():
    failure = DiscoveryError(
        code=DiscoveryErrorCode.COLLECTOR_FAILURE,
        message="One optional collector failed",
        collector="optional_fixture",
        source="controlled_test",
        retryable=True,
    )
    snapshot = DiscoverySnapshot(
        snapshot_id="snapshot-partial",
        observed_at=OBSERVED_AT,
        domains=(
            ExecutionDomainDescriptor(
                domain_id="linux-host",
                kind=ExecutionDomainKind.LINUX,
                evidence_ids=("ev-linux",),
                confidence=0.9,
            ),
        ),
        runtimes=(
            RuntimeDescriptor(
                runtime_id="python-runtime",
                runtime_type="PYTHON",
                domain_id="linux-host",
                status=CapabilityStatus.AVAILABLE,
                evidence_ids=("ev-python",),
                confidence=0.9,
            ),
        ),
        evidence=(
            _evidence("ev-linux", "domain.linux"),
            _evidence("ev-python", "runtime.python"),
        ),
        errors=(failure,),
        status=CapabilityStatus.DEGRADED,
    )

    restored = DiscoverySnapshot.from_dict(snapshot.to_dict())

    assert restored.status is CapabilityStatus.DEGRADED
    assert restored.domains[0].kind is ExecutionDomainKind.LINUX
    assert restored.runtimes[0].runtime_id == "python-runtime"
    assert restored.errors[0].code is DiscoveryErrorCode.COLLECTOR_FAILURE


def test_all_core_descriptors_have_json_compatible_stable_round_trip():
    snapshot = DiscoverySnapshot(
        snapshot_id="snapshot-json",
        observed_at=OBSERVED_AT,
        domains=(
            ExecutionDomainDescriptor(
                domain_id="host-probe",
                kind=ExecutionDomainKind.HOST_PROBE,
                evidence_ids=("ev-host",),
                confidence=0.8,
            ),
        ),
        agents=(
            AgentDescriptor(
                agent_id="agent-1",
                agent_type="CLAUDE_CODE",
                lifecycle=AgentLifecycleStatus.OBSERVED,
                domain_id="host-probe",
                evidence_ids=("ev-agent",),
                confidence=0.8,
            ),
        ),
        workspaces=(
            WorkspaceDescriptor(
                workspace_id="workspace-1",
                domain_id="host-probe",
                evidence_ids=("ev-workspace",),
                confidence=0.8,
            ),
        ),
        evidence=(
            _evidence("ev-host", "domain.host_probe"),
            _evidence("ev-agent", "agent.observed"),
            _evidence("ev-workspace", "workspace.present"),
        ),
        status=CapabilityStatus.AVAILABLE,
    )

    payload = snapshot.to_dict()
    encoded = json.dumps(payload, sort_keys=True)
    restored = DiscoverySnapshot.from_dict(json.loads(encoded))

    assert restored.to_dict() == payload


def test_times_are_normalized_to_utc_and_snapshot_has_schema_version():
    plus_eight = datetime(2026, 8, 2, 16, 30, tzinfo=timezone(timedelta(hours=8)))
    snapshot = DiscoverySnapshot(snapshot_id="snapshot-utc", observed_at=plus_eight)

    payload = snapshot.to_dict()

    assert payload["observed_at"] == "2026-08-02T08:30:00+00:00"
    assert payload["schema_version"] == DiscoverySnapshot.CURRENT_SCHEMA_VERSION
    assert snapshot.observed_at.tzinfo is timezone.utc
    with pytest.raises(ValueError, match="timezone-aware"):
        DiscoverySnapshot(snapshot_id="naive", observed_at=datetime(2026, 8, 2, 8, 30))


def test_unknown_fields_and_missing_optional_fields_degrade_without_crashing():
    payload = {
        "schema_version": "9.9-future",
        "snapshot_id": "future-snapshot",
        "observed_at": "2026-08-02T08:30:00Z",
        "domains": [
            {
                "domain_id": "future-domain",
                "kind": "FUTURE_HYPERVISOR",
                "future_field": {"ignored": True},
            }
        ],
        "status": "FUTURE_STATUS",
        "future_top_level": True,
    }

    restored = DiscoverySnapshot.from_dict(payload)

    assert restored.schema_version == "9.9-future"
    assert restored.domains[0].kind is ExecutionDomainKind.UNKNOWN
    assert restored.status is CapabilityStatus.UNKNOWN
    assert restored.runtimes == ()
    assert restored.agents == ()
    assert restored.workspaces == ()
    assert restored.errors == ()


def test_serialized_evidence_drops_sensitive_fields_and_unredacted_urls():
    evidence = ProbeEvidence(
        evidence_id="ev-private",
        collector="fixture",
        source="https://user:password@example.invalid/path",
        observed_at=OBSERVED_AT,
        fact_type="privacy.contract",
        value={
            "safe_fact": "retained",
            "api_key": "secret-value",
            "access_token": "secret-value",
            "environment": {"PASSWORD": "secret-value"},
            "command_line": "tool --token secret-value",
            "remote_url": "https://example.invalid/private",
            "nested": {"client_secret": "secret-value", "count": 2},
        },
        summary="Remote endpoint https://example.invalid/private was checked",
        reliability=EvidenceReliability.LOW,
        confidence=0.2,
        status=CapabilityStatus.DEGRADED,
        sanitized=False,
    )

    encoded = json.dumps(evidence.to_dict(), sort_keys=True).lower()

    assert "safe_fact" in encoded
    assert "secret-value" not in encoded
    assert "api_key" not in encoded
    assert "token" not in encoded
    assert "command_line" not in encoded
    assert "client_secret" not in encoded
    assert "https://" not in encoded


def test_high_confidence_available_conclusion_requires_evidence():
    with pytest.raises(ValueError, match="high-confidence AVAILABLE"):
        CapabilityAssessment(
            status=CapabilityStatus.AVAILABLE,
            confidence=0.95,
        )

    supported = CapabilityAssessment(
        status=CapabilityStatus.AVAILABLE,
        confidence=0.95,
        evidence_ids=("ev-confirmed",),
    )
    failed = CapabilityAssessment(
        status=CapabilityStatus.ERROR,
        confidence=0.95,
    )

    assert supported.is_available is True
    assert failed.is_available is False
