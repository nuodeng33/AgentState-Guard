"""Deterministic CloudCLI Adapter behavior over normalized facts."""

from __future__ import annotations

import ast
import socket
import subprocess
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentguard.discovery import AgentLifecycleStatus, CapabilityStatus
from agentguard.discovery.agents import (
    AgentAdapterRegistry,
    AgentCandidateType,
    AgentClassification,
    AgentRole,
    ExecutableIdentityKind,
    ProcessFact,
    ProcessState,
    WorkspaceCandidate,
    WorkspacePathKind,
    WorkspaceSource,
    make_process_instance_id,
)
from agentguard.discovery.agents.adapters import cloudcli

CREATED_AT = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)
DOMAIN = "container-agent-dev"


def _api(name):
    value = getattr(cloudcli, name, None)
    assert value is not None, f"CloudCLI public API is missing: {name}"
    return value


def _fact(
    *,
    pid=150,
    domain=DOMAIN,
    basename="node",
    state=ProcessState.RUNNING,
    access=CapabilityStatus.AVAILABLE,
    created_at=CREATED_AT,
    fixed_facts=None,
    supported_fixed_fact_names=(),
    collector="fixture",
):
    instance_id = make_process_instance_id(
        execution_domain_id=domain,
        pid=pid,
        create_time=created_at,
        collector=collector,
    )
    return ProcessFact(
        process_instance_id=instance_id,
        pid=pid,
        parent_pid=1,
        executable_basename=basename,
        executable_identity_digest=(
            "sha256:" + "a" * 64 if basename is not None else None
        ),
        executable_identity_kind=(
            ExecutableIdentityKind.BASENAME_SHA256
            if basename is not None
            else ExecutableIdentityKind.UNKNOWN
        ),
        executable_identity_verified=False,
        create_time=created_at if state is ProcessState.RUNNING else None,
        execution_domain_id=domain,
        current_state=state,
        evidence_refs=(f"process:{domain}:{pid}",),
        access_status=access,
        sanitized=True,
        fixed_facts=fixed_facts or {},
        supported_fixed_fact_names=supported_fixed_fact_names,
        collector=collector,
    )


def _marker(
    kind,
    *,
    marker_id=None,
    domain=DOMAIN,
    verification=None,
    pid=None,
    present=True,
    reason_code=None,
    version=None,
    source_kind=None,
    collector="",
    deployment_binding_id="default",
):
    marker_type = _api("CloudCliMarker")
    source = _api("CloudCliMarkerSource")
    verification_type = _api("CloudCliMarkerVerification")
    fixed_ids = {
        "INSTALLATION_MARKER": "cloudcli.package",
        "SERVICE_MARKER": "cloudcli.service",
        "PID_MARKER": "cloudcli.pid",
        "PROCESS_MARKER": "cloudcli.process",
        "VERSION_MARKER": "cloudcli.version",
        "WORKSPACE_ROOT_MARKER": "cloudcli.workspace",
        "RUNTIME_MARKER": "cloudcli.runtime.observed",
    }
    stable_marker_id = marker_id or fixed_ids[kind.value]
    return marker_type(
        marker_id=stable_marker_id,
        kind=kind,
        present=present,
        source=source_kind or source.HOST_PROBE,
        collector=collector,
        evidence_ref=f"host:{stable_marker_id}",
        sanitized=True,
        verification=verification or verification_type.VERIFIED,
        execution_domain_id=domain,
        deployment_binding_id=deployment_binding_id,
        pid=pid,
        reason_code=reason_code,
        version=version,
    )


def _adapter(*markers, workspaces=(), relationships=()):
    return _api("CloudCliAdapter")(
        markers=tuple(markers),
        workspace_candidates=tuple(workspaces),
        relationships=tuple(relationships),
    )


def _installation():
    return _trusted("cloudcli.package")


def _service():
    return _trusted("cloudcli.service")


def _pid(fact, **overrides):
    values = {"pid": fact.pid, "domain": fact.execution_domain_id}
    values.update(overrides)
    return _parse_pid(**values)


def test_no_markers_and_no_processes_returns_no_cloudcli_candidate():
    assert _adapter().assess(()) == ()


@pytest.mark.parametrize(
    ("kind", "confidence"),
    [
        ("INSTALLATION_MARKER", 0.45),
        ("WORKSPACE_ROOT_MARKER", 0.3),
    ],
)
def test_static_marker_only_is_detected_not_running(kind, confidence):
    fixed_ids = {
        "INSTALLATION_MARKER": "cloudcli.package",
        "WORKSPACE_ROOT_MARKER": "cloudcli.workspace",
    }

    result = _adapter(_trusted(fixed_ids[kind])).assess(())[0]

    assert result.detection_level is _api("CloudCliDetectionLevel").TRACE_ONLY
    assert result.classification.lifecycle is AgentLifecycleStatus.DETECTED
    assert result.classification.role is AgentRole.AGENT_HOST
    assert result.classification.confidence == confidence
    assert "CLOUDCLI_EVIDENCE_INSUFFICIENT" in result.reason_codes


def test_pid_marker_without_process_is_stale_and_not_running():
    pid_marker = _parse_pid(150)

    result = _adapter(pid_marker).assess(())[0]

    assert result.detection_level is _api("CloudCliDetectionLevel").STALE_RUNTIME_TRACE
    assert result.classification.lifecycle is AgentLifecycleStatus.DETECTED
    assert result.status is CapabilityStatus.DEGRADED
    assert result.reason_codes == ("CLOUDCLI_STALE_PID",)


def test_pid_instance_identity_is_taken_from_matching_process_fact():
    process = _fact()

    result = _adapter(_pid(process), _installation()).assess((process,))[0]

    assert result.classification.process_instance_id == process.process_instance_id
    assert "process_instance_id" not in _pid(process).to_dict()


def test_generic_node_with_unbound_pid_is_only_process_candidate():
    process = _fact()
    pid_marker = _pid(process)

    result = _adapter(pid_marker).assess((process,))[0]

    assert result.detection_level is _api("CloudCliDetectionLevel").PROCESS_CANDIDATE
    assert result.classification.confidence == 0.25
    assert result.confirmed is False
    assert "CLOUDCLI_EVIDENCE_INSUFFICIENT" in result.reason_codes


def test_bound_live_pid_plus_verified_installation_confirms_running():
    process = _fact()

    result = _adapter(_pid(process), _installation()).assess((process,))[0]

    assert result.detection_level is _api("CloudCliDetectionLevel").CONFIRMED_RUNNING
    assert result.classification.role is AgentRole.AGENT_HOST
    assert result.classification.lifecycle is AgentLifecycleStatus.RUNNING
    assert result.confirmed is True
    assert result.status is CapabilityStatus.AVAILABLE


def test_arbitrary_verified_marker_id_cannot_confirm_running():
    process = _fact()
    untrusted_installation_name = _marker(
        _api("CloudCliMarkerKind").INSTALLATION_MARKER,
        marker_id="cloudcli.user-controlled-installation",
    )

    result = _adapter(_pid(process), untrusted_installation_name).assess(
        (process,)
    )[0]

    assert result.confirmed is False
    assert result.detection_level is _api("CloudCliDetectionLevel").PROCESS_CANDIDATE
    assert "CLOUDCLI_EVIDENCE_INSUFFICIENT" in result.reason_codes


def test_domain_mismatch_is_unknown_not_running():
    process = _fact(domain="container-other")
    marker = _marker(
        _api("CloudCliMarkerKind").PID_MARKER,
        pid=process.pid,
        domain=DOMAIN,
        source_kind=_api("CloudCliMarkerSource").FIXED_CONTAINER_PATH,
        collector="cloudcli-fixed-container-probe",
    )

    result = _adapter(marker, _installation()).assess((process,))[0]

    assert result.classification.lifecycle is AgentLifecycleStatus.UNKNOWN
    assert result.status is CapabilityStatus.DEGRADED
    assert "CLOUDCLI_DOMAIN_MISMATCH" in result.reason_codes


def test_exited_process_makes_pid_trace_stale():
    process = _fact(state=ProcessState.EXITED, access=CapabilityStatus.NOT_PRESENT)

    result = _adapter(_pid(process), _service()).assess((process,))[0]

    assert result.classification.lifecycle is AgentLifecycleStatus.DETECTED
    assert "CLOUDCLI_STALE_PID" in result.reason_codes


def test_access_denied_process_remains_low_confidence_candidate():
    process = _fact(
        state=ProcessState.UNKNOWN,
        access=CapabilityStatus.PERMISSION_DENIED,
    )

    result = _adapter(_pid(process), _service()).assess((process,))[0]

    assert result.confirmed is False
    assert result.status is CapabilityStatus.DEGRADED
    assert "CLOUDCLI_EVIDENCE_INSUFFICIENT" in result.reason_codes


def test_multiple_bound_processes_for_one_trace_are_conflict():
    first = _fact(pid=150)
    second = _fact(pid=151)

    results = _adapter(
        _pid(first),
        _pid(second),
        _installation(),
    ).assess((first, second))

    assert len(results) == 2
    assert all(result.confirmed is False for result in results)
    assert all(
        "CLOUDCLI_SIGNATURE_CONFLICT" in result.reason_codes
        for result in results
    )


def test_invalid_version_marker_downgrades_otherwise_valid_signature():
    process = _fact()
    invalid_version = _marker(
        _api("CloudCliMarkerKind").VERSION_MARKER,
        verification=_api("CloudCliMarkerVerification").INVALID,
        reason_code="CLOUDCLI_VERSION_INVALID",
    )

    result = _adapter(_pid(process), _installation(), invalid_version).assess(
        (process,)
    )[0]

    assert result.confirmed is False
    assert result.status is CapabilityStatus.DEGRADED
    assert "CLOUDCLI_SIGNATURE_CONFLICT" in result.reason_codes


def test_version_unknown_does_not_block_other_independent_evidence():
    process = _fact()

    result = _adapter(_pid(process), _service()).assess((process,))[0]

    assert result.confirmed is True
    assert "CLOUDCLI_VERSION_REQUIRED" in result.classification.uncertainties


def test_parent_relationship_or_cwd_alone_never_confirms_cloudcli():
    process = _fact()
    workspace = WorkspaceCandidate(
        candidate_id="workspace-agent-dev",
        source=WorkspaceSource.PROCESS_CWD,
        execution_domain_id=DOMAIN,
        path_hint="~/projects",
        path_kind=WorkspacePathKind.REDACTED,
        access_status=CapabilityStatus.AVAILABLE,
        evidence_refs=process.evidence_refs,
        confidence=0.7,
    )

    assert _adapter(workspaces=(workspace,)).assess((process,)) == ()


def test_runtime_or_port_like_marker_alone_is_detected_not_running():
    marker = _marker(
        _api("CloudCliMarkerKind").RUNTIME_MARKER,
        marker_id="cloudcli.runtime.port-marker",
        verification=_api("CloudCliMarkerVerification").DECLARED,
    )

    result = _adapter(marker).assess(())[0]

    assert result.classification.lifecycle is AgentLifecycleStatus.DETECTED
    assert result.confirmed is False


def test_weak_workspace_marker_cannot_override_process_fact_binding():
    process = _fact()
    marker = _trusted("cloudcli.workspace")

    result = _adapter(_pid(process), marker).assess((process,))[0]

    assert result.confirmed is False
    assert "CLOUDCLI_EVIDENCE_INSUFFICIENT" in result.reason_codes


def test_candidate_id_uses_domain_and_process_instance_not_pid_alone():
    first = _fact(domain=DOMAIN)
    second = _fact(domain="container-other")
    first_result = _adapter(_pid(first), _installation()).assess((first,))[0]
    second_result = _adapter(
        _pid(second),
        _marker(
            _api("CloudCliMarkerKind").INSTALLATION_MARKER,
            domain="container-other",
        ),
    ).assess((second,))[0]

    assert first_result.classification.candidate_id != second_result.classification.candidate_id
    assert str(first.pid) not in first_result.classification.candidate_id


def test_trace_candidate_identity_does_not_fabricate_process_id():
    result = _adapter(_installation()).assess(())[0]

    assert result.classification.candidate_type is AgentCandidateType.EXECUTABLE
    assert result.classification.process_instance_id is None
    assert result.classification.candidate_id.startswith("cloudcli-trace-")


def test_evidence_markers_and_results_have_stable_order():
    process = _fact()
    service = _service()
    installation = _installation()

    forward = _adapter(service, _pid(process), installation).assess((process,))[0]
    reverse = _adapter(installation, _pid(process), service).assess((process,))[0]

    assert forward.to_dict() == reverse.to_dict()
    assert forward.evidence_refs == tuple(sorted(forward.evidence_refs))


def test_observed_requires_supported_whitelisted_runtime_fact():
    unsupported = _fact(fixed_facts={"cloudcli_runtime_observed": True})
    supported = _fact(
        fixed_facts={"cloudcli_runtime_observed": True},
        supported_fixed_fact_names=("cloudcli_runtime_observed",),
        collector="cloudcli-runtime-probe",
    )

    running = _adapter(_pid(unsupported), _installation()).assess((unsupported,))[0]
    observed = _adapter(_pid(supported), _installation()).assess((supported,))[0]

    assert running.classification.lifecycle is AgentLifecycleStatus.RUNNING
    assert observed.classification.lifecycle is AgentLifecycleStatus.OBSERVED
    for result in (running, observed):
        assert result.classification.lifecycle is not AgentLifecycleStatus.INTEGRATED
        assert result.classification.lifecycle is not AgentLifecycleStatus.ENFORCED


def test_adapter_identity_role_uncertainties_and_required_checks_are_explicit():
    result = _adapter(_installation()).assess(())[0]

    assert result.adapter_id == "cloudcli"
    assert result.classification.role is AgentRole.AGENT_HOST
    assert result.classification.uncertainties
    assert result.classification.required_checks
    assert result.sanitized is True


def test_registry_isolates_actual_cloudcli_adapter_failure():
    class HealthyAdapter:
        adapter_id = "healthy"

        def discover(self, facts):
            return (
                AgentClassification(
                    candidate_id="healthy-candidate",
                    candidate_type=AgentCandidateType.PROCESS,
                    role=AgentRole.AGENT_HOST,
                    lifecycle=AgentLifecycleStatus.RUNNING,
                    confidence=0.5,
                    evidence_refs=("test:healthy",),
                    uncertainties=("TEST_ONLY",),
                    required_checks=("REVIEW",),
                    process_instance_id="test-process",
                ),
            )

    registry = AgentAdapterRegistry((_adapter(_installation()), HealthyAdapter()))

    result = registry.discover((object(),))

    assert result.status is CapabilityStatus.DEGRADED
    assert [item.candidate_id for item in result.candidates] == ["healthy-candidate"]
    assert result.errors[0].details["adapter_id"] == "cloudcli"


def test_adapter_calls_no_network_process_provider_docker_or_database(monkeypatch):
    def forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError("forbidden I/O was called")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    process = _fact()

    result = _adapter(_pid(process), _installation()).assess((process,))[0]

    assert result.confirmed is True


def test_production_adapter_has_no_forbidden_imports_or_calls():
    path = (
        Path(__file__).parents[1]
        / "agentguard"
        / "discovery"
        / "agents"
        / "adapters"
        / "cloudcli.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden_imports = {
        "psutil",
        "subprocess",
        "socket",
        "urllib",
        "requests",
        "httpx",
        "docker",
        "sqlite3",
        "wmi",
    }
    imported = set()
    called_attributes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called_attributes.add(node.func.attr)

    assert imported.isdisjoint(forbidden_imports)
    assert called_attributes.isdisjoint(
        {
            "cmdline",
            "environ",
            "open_files",
            "net_connections",
            "memory_maps",
            "connect",
            "urlopen",
        }
    )


def test_allowlisted_id_with_caller_verified_flag_cannot_confirm_running():
    process = _fact()
    forged = _marker(
        _api("CloudCliMarkerKind").INSTALLATION_MARKER,
        source_kind=_api("CloudCliMarkerSource").FIXED_PACKAGE_METADATA,
        collector="cloudcli-package-metadata-probe",
    )

    result = _adapter(_pid(process), forged).assess((process,))[0]

    assert result.confirmed is False
    assert "CLOUDCLI_MARKER_PROVENANCE_UNVERIFIED" in result.reason_codes


def test_allowlisted_id_with_unknown_collector_cannot_confirm_running():
    process = _fact()
    marker_type = _api("CloudCliMarker")
    forged = marker_type(
        marker_id="cloudcli.package",
        kind=_api("CloudCliMarkerKind").INSTALLATION_MARKER,
        present=True,
        source=_api("CloudCliMarkerSource").FIXED_PACKAGE_METADATA,
        collector="caller-controlled",
        evidence_ref="host:cloudcli-package",
        sanitized=True,
        verification=_api("CloudCliMarkerVerification").VERIFIED,
        execution_domain_id=DOMAIN,
    )

    result = _adapter(_pid(process), forged).assess((process,))[0]

    assert result.confirmed is False
    assert "CLOUDCLI_MARKER_PROVENANCE_UNVERIFIED" in result.reason_codes


def test_allowlisted_id_with_wrong_source_is_a_stable_conflict():
    process = _fact()
    marker_type = _api("CloudCliMarker")
    forged = marker_type(
        marker_id="cloudcli.package",
        kind=_api("CloudCliMarkerKind").INSTALLATION_MARKER,
        present=True,
        source=_api("CloudCliMarkerSource").FIXED_HOST_PATH,
        collector="cloudcli-package-metadata-probe",
        evidence_ref="host:cloudcli-package",
        sanitized=True,
        verification=_api("CloudCliMarkerVerification").VERIFIED,
        execution_domain_id=DOMAIN,
    )

    result = _adapter(_pid(process), forged).assess((process,))[0]

    assert result.confirmed is False
    assert "CLOUDCLI_MARKER_SOURCE_MISMATCH" in result.reason_codes


def test_controlled_factory_marker_and_pid_confirm_running():
    process = _fact()
    factory = _api("create_verified_cloudcli_marker")
    installation = factory(
        marker_id="cloudcli.package",
        execution_domain_id=DOMAIN,
        evidence_ref="host:cloudcli-package",
    )

    result = _adapter(_parse_pid(process.pid), installation).assess((process,))[0]

    assert result.confirmed is True
    assert result.classification.process_instance_id == process.process_instance_id


def test_pid_plus_workspace_is_not_a_strong_running_signature():
    process = _fact()
    factory = _api("create_verified_cloudcli_marker")
    workspace = factory(
        marker_id="cloudcli.workspace",
        execution_domain_id=DOMAIN,
        evidence_ref="host:cloudcli-workspace",
    )

    result = _adapter(_parse_pid(process.pid), workspace).assess((process,))[0]

    assert result.confirmed is False
    assert result.detection_level is _api("CloudCliDetectionLevel").PROCESS_CANDIDATE


def test_same_pid_with_multiple_process_instances_is_conflict():
    first = _fact(created_at=CREATED_AT)
    second = _fact(created_at=datetime(2026, 8, 3, 8, 1, tzinfo=UTC))

    result = _adapter(_parse_pid(first.pid), _trusted("cloudcli.package")).assess(
        (first, second)
    )[0]

    assert result.confirmed is False
    assert result.reason_codes == ("CLOUDCLI_SIGNATURE_CONFLICT",)


def test_trace_identity_is_stable_across_optional_marker_changes_and_order():
    installation = _trusted("cloudcli.package")
    service = _trusted("cloudcli.service")
    version = _trusted("cloudcli.version", version="1.36.3")

    base = _adapter(installation).assess(())[0]
    richer = _adapter(version, service, installation).assess(())[0]
    reordered = _adapter(installation, version, service).assess(())[0]

    assert base.classification.candidate_id == richer.classification.candidate_id
    assert richer.classification.candidate_id == reordered.classification.candidate_id


def test_trace_identity_changes_with_domain_or_deployment_binding():
    base = _adapter(_trusted("cloudcli.package")).assess(())[0]
    other_domain = _adapter(
        _trusted("cloudcli.package", domain="container-other")
    ).assess(())[0]
    other_binding = _adapter(
        _trusted("cloudcli.package", deployment_binding_id="secondary")
    ).assess(())[0]

    assert base.classification.candidate_id != other_domain.classification.candidate_id
    assert base.classification.candidate_id != other_binding.classification.candidate_id


def test_two_deployment_bindings_in_one_domain_are_not_merged():
    results = _adapter(
        _trusted("cloudcli.package", deployment_binding_id="default"),
        _trusted("cloudcli.package", deployment_binding_id="secondary"),
    ).assess(())

    assert len(results) == 2
    assert len({item.classification.candidate_id for item in results}) == 2
    assert all("CLOUDCLI_SIGNATURE_CONFLICT" in item.reason_codes for item in results)


def test_deployment_identity_is_stable_when_trace_becomes_running():
    process = _fact()
    installation = _trusted("cloudcli.package")

    trace = _adapter(installation).assess(())[0]
    running = _adapter(_parse_pid(process.pid), installation).assess((process,))[0]

    assert trace.classification.candidate_id == running.classification.candidate_id


def test_default_binding_is_explicitly_single_instance_per_domain():
    result = _adapter(_trusted("cloudcli.package")).assess(())[0]

    assert result.deployment_binding_id == "default"
    assert result.single_instance_per_domain is True
    assert result.to_dict()["single_instance_per_domain"] is True


@pytest.mark.parametrize(
    "marker_id",
    [
        "cloudcli.windows.start-script",
        "cloudcli.windows.stop-script",
        "cloudcli.windows.status-script",
    ],
)
def test_windows_management_scripts_are_weak_anchors(marker_id):
    process = _fact()

    result = _adapter(_parse_pid(process.pid), _trusted(marker_id)).assess(
        (process,)
    )[0]

    assert result.confirmed is False
    assert result.classification.lifecycle is AgentLifecycleStatus.DETECTED


def test_custom_object_with_allowlisted_identity_cannot_confirm_running():
    process = _fact()
    forged = SimpleNamespace(
        marker_id="cloudcli.package",
        kind=_api("CloudCliMarkerKind").INSTALLATION_MARKER,
        present=True,
        source=_api("CloudCliMarkerSource").FIXED_PACKAGE_METADATA,
        collector="cloudcli-package-metadata-probe",
        evidence_ref="host:cloudcli-package",
        sanitized=True,
        verification=_api("CloudCliMarkerVerification").VERIFIED,
        execution_domain_id=DOMAIN,
        deployment_binding_id="default",
        pid=None,
        reason_code=None,
        version=None,
    )

    with pytest.raises(TypeError, match="CloudCliMarker"):
        _adapter(_parse_pid(process.pid), forged)


def test_observed_fact_requires_trusted_collector_provenance():
    untrusted = _fact(
        fixed_facts={"cloudcli_runtime_observed": True},
        supported_fixed_fact_names=("cloudcli_runtime_observed",),
        collector="caller-fixture",
    )
    trusted = _fact(
        fixed_facts={"cloudcli_runtime_observed": True},
        supported_fixed_fact_names=("cloudcli_runtime_observed",),
        collector="cloudcli-runtime-probe",
    )

    running = _adapter(_parse_pid(untrusted.pid), _trusted("cloudcli.package")).assess(
        (untrusted,)
    )[0]
    observed = _adapter(_parse_pid(trusted.pid), _trusted("cloudcli.package")).assess(
        (trusted,)
    )[0]

    assert running.classification.lifecycle is AgentLifecycleStatus.RUNNING
    assert observed.classification.lifecycle is AgentLifecycleStatus.OBSERVED


def _parse_pid(pid, *, domain=DOMAIN, deployment_binding_id="default"):
    return _api("parse_cloudcli_pid_marker")(
        raw=f"{pid}\n".encode("ascii"),
        execution_domain_id=domain,
        evidence_ref="host:cloudcli-pid",
        deployment_binding_id=deployment_binding_id,
    )


def _trusted(marker_id, *, domain=DOMAIN, **overrides):
    values = {
        "marker_id": marker_id,
        "execution_domain_id": domain,
        "evidence_ref": f"host:{marker_id}",
    }
    values.update(overrides)
    return _api("create_verified_cloudcli_marker")(**values)
