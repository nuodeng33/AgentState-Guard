"""Deterministic CCR MODEL_ROUTER classification over normalized facts."""

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
    ProcessRelationship,
    ProcessState,
    WorkspaceCandidate,
    WorkspacePathKind,
    WorkspaceSource,
    adapters,
    make_process_instance_id,
)

DOMAIN = "container-agent-dev"
CREATED_AT = datetime(2026, 8, 4, 8, 0, tzinfo=UTC)


def _api(name):
    value = getattr(adapters, name, None)
    assert value is not None, f"CCR public API is missing: {name}"
    return value


def _fact(
    *,
    pid=321,
    domain=DOMAIN,
    basename="node",
    state=ProcessState.RUNNING,
    access=CapabilityStatus.AVAILABLE,
    created_at=CREATED_AT,
    fixed_facts=None,
    supported_fixed_fact_names=(),
    collector="fixture",
    evidence_ref=None,
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
        executable_identity_digest=("sha256:" + "b" * 64 if basename else None),
        executable_identity_kind=(
            ExecutableIdentityKind.BASENAME_SHA256
            if basename
            else ExecutableIdentityKind.UNKNOWN
        ),
        executable_identity_verified=False,
        create_time=created_at if state is ProcessState.RUNNING else None,
        execution_domain_id=domain,
        current_state=state,
        evidence_refs=(evidence_ref or f"process:{domain}:{pid}",),
        access_status=access,
        sanitized=True,
        fixed_facts=fixed_facts or {},
        supported_fixed_fact_names=supported_fixed_fact_names,
        collector=collector,
    )


def _trusted(marker_id, *, domain=DOMAIN, binding="default", **overrides):
    values = {
        "marker_id": marker_id,
        "execution_domain_id": domain,
        "deployment_binding_id": binding,
        "evidence_ref": f"host:{marker_id}:{binding}",
    }
    if marker_id == "ccr.package":
        values["package_name"] = "@musistudio/claude-code-router"
    values.update(overrides)
    return _api("create_verified_ccr_marker")(**values)


def _pid(pid=321, *, domain=DOMAIN, binding="default"):
    return _api("parse_ccr_pid_marker")(
        raw=f"{pid}\n".encode("ascii"),
        execution_domain_id=domain,
        deployment_binding_id=binding,
        evidence_ref=f"host:ccr-pid:{binding}",
    )


def _adapter(*markers, workspaces=(), relationships=()):
    return _api("CcrAdapter")(
        markers=tuple(markers),
        workspace_candidates=tuple(workspaces),
        relationships=tuple(relationships),
    )


def _raw_marker(
    *,
    marker_id="ccr.package",
    marker_kind=None,
    source_kind=None,
    collector_id="ccr-package-metadata-probe",
    binding="default",
):
    return _api("CcrMarker")(
        marker_id=marker_id,
        marker_kind=marker_kind or _api("CcrMarkerKind").PACKAGE_MARKER,
        present=True,
        source_kind=source_kind or _api("CcrMarkerSource").FIXED_PACKAGE_METADATA,
        collector_id=collector_id,
        evidence_ref="host:forged-ccr-marker",
        verification=_api("CcrMarkerVerification").VERIFIED,
        execution_domain_id=DOMAIN,
        deployment_binding_id=binding,
        sanitized=True,
    )


def test_no_markers_and_no_processes_returns_no_candidate():
    assert _adapter().assess(()) == ()


@pytest.mark.parametrize("marker_id", ["ccr.package", "ccr.service"])
def test_trusted_static_marker_is_detected_not_running(marker_id):
    result = _adapter(_trusted(marker_id)).assess(())[0]

    assert result.detection_level is _api("CcrDetectionLevel").TRACE_ONLY
    assert result.classification.lifecycle is AgentLifecycleStatus.DETECTED
    assert result.classification.role is AgentRole.MODEL_ROUTER
    assert result.confirmed is False


def test_config_marker_alone_is_low_confidence_detected_trace():
    result = _adapter(_trusted("ccr.config")).assess(())[0]

    assert result.classification.lifecycle is AgentLifecycleStatus.DETECTED
    assert result.classification.confidence == 0.2
    assert result.status is CapabilityStatus.DEGRADED


def test_pid_without_process_is_stale_not_running():
    result = _adapter(_pid()).assess(())[0]

    assert result.detection_level is _api("CcrDetectionLevel").STALE_RUNTIME_TRACE
    assert result.reason_codes == ("CCR_STALE_PID",)
    assert result.classification.lifecycle is AgentLifecycleStatus.DETECTED


def test_live_pid_without_strong_static_anchor_is_process_candidate():
    fact = _fact()
    result = _adapter(_pid(fact.pid)).assess((fact,))[0]

    assert result.detection_level is _api("CcrDetectionLevel").PROCESS_CANDIDATE
    assert result.confirmed is False
    assert result.classification.confidence == 0.25


def test_live_pid_and_exact_package_confirm_running_model_router():
    fact = _fact()
    result = _adapter(_pid(fact.pid), _trusted("ccr.package")).assess((fact,))[0]

    assert result.detection_level is _api("CcrDetectionLevel").CONFIRMED_RUNNING
    assert result.classification.lifecycle is AgentLifecycleStatus.RUNNING
    assert result.classification.role is AgentRole.MODEL_ROUTER
    assert result.confirmed is True
    assert result.status is CapabilityStatus.AVAILABLE


def test_package_version_unknown_does_not_block_independent_signature():
    fact = _fact()
    result = _adapter(
        _pid(fact.pid),
        _trusted("ccr.package"),
        _trusted("ccr.version"),
    ).assess((fact,))[0]

    assert result.confirmed is True
    assert "CCR_VERSION_REQUIRED" in result.classification.uncertainties


@pytest.mark.parametrize(
    "marker",
    [
        lambda: _trusted("ccr.package", package_name="wrong-package"),
        lambda: _trusted("ccr.version", version="3.0.7 secret=value"),
    ],
)
def test_invalid_package_or_version_metadata_blocks_confirmation(marker):
    fact = _fact()
    result = _adapter(_pid(fact.pid), _trusted("ccr.package"), marker()).assess(
        (fact,)
    )[0]

    assert result.confirmed is False
    assert "CCR_SIGNATURE_CONFLICT" in result.reason_codes


def test_forged_verified_marker_with_unknown_collector_is_untrusted():
    fact = _fact()
    result = _adapter(
        _pid(fact.pid),
        _raw_marker(collector_id="caller-controlled"),
    ).assess((fact,))[0]

    assert result.confirmed is False
    assert "CCR_MARKER_COLLECTOR_UNTRUSTED" in result.reason_codes


def test_forged_verified_marker_with_wrong_source_is_untrusted():
    fact = _fact()
    result = _adapter(
        _pid(fact.pid),
        _raw_marker(source_kind=_api("CcrMarkerSource").FIXED_CONTAINER_PATH),
    ).assess((fact,))[0]

    assert result.confirmed is False
    assert "CCR_MARKER_SOURCE_MISMATCH" in result.reason_codes


def test_exact_allowlisted_fields_without_controlled_factory_are_unverified():
    fact = _fact()
    result = _adapter(_pid(fact.pid), _raw_marker()).assess((fact,))[0]

    assert result.confirmed is False
    assert "CCR_MARKER_PROVENANCE_UNVERIFIED" in result.reason_codes


def test_custom_object_forging_marker_is_rejected_at_adapter_boundary():
    forged = SimpleNamespace(marker_id="ccr.package", present=True)

    with pytest.raises(TypeError, match="CcrMarker"):
        _adapter(forged)


def test_cross_domain_pid_cannot_bind_same_numeric_process():
    fact = _fact(domain="container-other")
    result = _adapter(_pid(fact.pid), _trusted("ccr.package")).assess((fact,))[0]

    assert result.classification.lifecycle is AgentLifecycleStatus.UNKNOWN
    assert "CCR_DOMAIN_MISMATCH" in result.reason_codes


def test_exited_process_makes_pid_trace_stale():
    fact = _fact(state=ProcessState.EXITED, access=CapabilityStatus.NOT_PRESENT)
    result = _adapter(_pid(fact.pid), _trusted("ccr.package")).assess((fact,))[0]

    assert result.confirmed is False
    assert "CCR_STALE_PID" in result.reason_codes


def test_same_pid_multiple_process_instances_is_fail_closed():
    first = _fact(created_at=CREATED_AT, evidence_ref="process:first-instance")
    second = _fact(
        created_at=datetime(2026, 8, 4, 8, 1, tzinfo=UTC),
        evidence_ref="process:second-instance",
    )
    result = _adapter(_pid(first.pid), _trusted("ccr.package")).assess(
        (first, second)
    )[0]

    assert result.classification.lifecycle is AgentLifecycleStatus.UNKNOWN
    assert result.reason_codes == ("CCR_SIGNATURE_CONFLICT",)
    assert "process:first-instance" in result.evidence_refs
    assert "process:second-instance" in result.evidence_refs


@pytest.mark.parametrize("basename", ["node", "my-router-helper", "http-proxy"])
def test_process_name_without_markers_never_creates_ccr_identity(basename):
    assert _adapter().assess((_fact(basename=basename),)) == ()


def test_workspace_cwd_and_parent_relationship_alone_are_insufficient():
    fact = _fact()
    workspace = WorkspaceCandidate(
        candidate_id="workspace-ccr",
        source=WorkspaceSource.PROCESS_CWD,
        execution_domain_id=DOMAIN,
        path_hint="~/.router",
        path_kind=WorkspacePathKind.REDACTED,
        access_status=CapabilityStatus.AVAILABLE,
        evidence_refs=fact.evidence_refs,
        confidence=0.6,
    )
    relationship = ProcessRelationship(process_instance_id=fact.process_instance_id)

    assert _adapter(workspaces=(workspace,), relationships=(relationship,)).assess(
        (fact,)
    ) == ()


def test_pid_plus_config_marker_does_not_confirm_running():
    fact = _fact()
    result = _adapter(_pid(fact.pid), _trusted("ccr.config")).assess((fact,))[0]

    assert result.confirmed is False
    assert result.classification.lifecycle is AgentLifecycleStatus.DETECTED


def test_port_like_marker_is_weak_and_never_confirms_running():
    fact = _fact()
    port_marker = _api("CcrMarker")(
        marker_id="ccr.runtime.port",
        marker_kind=_api("CcrMarkerKind").RUNTIME_MARKER,
        present=True,
        source_kind=_api("CcrMarkerSource").HOST_PROBE,
        collector_id="caller-port-probe",
        evidence_ref="host:ccr-port",
        verification=_api("CcrMarkerVerification").DECLARED,
        execution_domain_id=DOMAIN,
        deployment_binding_id="default",
        sanitized=True,
    )
    result = _adapter(_pid(fact.pid), port_marker).assess((fact,))[0]

    assert result.confirmed is False


def test_cloudcli_marker_cannot_be_passed_to_ccr_adapter():
    cloudcli_marker = adapters.create_verified_cloudcli_marker(
        marker_id="cloudcli.package",
        execution_domain_id=DOMAIN,
        evidence_ref="host:cloudcli-package",
    )

    with pytest.raises(TypeError, match="CcrMarker"):
        _adapter(cloudcli_marker)


def test_ccr_role_is_isolated_from_execution_agent_host_and_provider():
    result = _adapter(_trusted("ccr.package")).assess(())[0]

    assert result.classification.role is AgentRole.MODEL_ROUTER
    assert result.classification.role is not AgentRole.EXECUTION_AGENT
    assert result.classification.role is not AgentRole.AGENT_HOST
    assert result.classification.role.value != "MODEL_PROVIDER"


def test_observed_requires_fixed_fact_and_trusted_collector():
    untrusted = _fact(
        fixed_facts={"ccr_runtime_observed": True},
        supported_fixed_fact_names=("ccr_runtime_observed",),
        collector="caller-fixture",
    )
    trusted = _fact(
        fixed_facts={"ccr_runtime_observed": True},
        supported_fixed_fact_names=("ccr_runtime_observed",),
        collector="ccr-runtime-probe",
    )

    running = _adapter(_pid(untrusted.pid), _trusted("ccr.package")).assess(
        (untrusted,)
    )[0]
    observed = _adapter(_pid(trusted.pid), _trusted("ccr.package")).assess(
        (trusted,)
    )[0]

    assert running.classification.lifecycle is AgentLifecycleStatus.RUNNING
    assert observed.classification.lifecycle is AgentLifecycleStatus.OBSERVED
    for result in (running, observed):
        assert result.classification.lifecycle is not AgentLifecycleStatus.INTEGRATED
        assert result.classification.lifecycle is not AgentLifecycleStatus.ENFORCED


def test_trace_identity_is_stable_across_markers_order_and_version_changes():
    package = _trusted("ccr.package")
    service = _trusted("ccr.service")
    version = _trusted("ccr.version", version="3.0.7")

    base = _adapter(package).assess(())[0]
    richer = _adapter(service, version, package).assess(())[0]
    reordered = _adapter(package, service, version).assess(())[0]

    assert base.deployment_trace_id == richer.deployment_trace_id
    assert richer.deployment_trace_id == reordered.deployment_trace_id


def test_verified_version_removes_version_required_uncertainty_from_trace():
    result = _adapter(
        _trusted("ccr.package"),
        _trusted("ccr.version", version="3.0.7"),
    ).assess(())[0]

    assert "CCR_VERSION_REQUIRED" not in result.classification.uncertainties
    assert "CCR_RUNNING_PROCESS_UNCONFIRMED" in result.classification.uncertainties


def test_trace_and_process_candidate_ids_are_linked_but_not_identical():
    fact = _fact()
    package = _trusted("ccr.package")
    trace = _adapter(package).assess(())[0]
    running = _adapter(_pid(fact.pid), package).assess((fact,))[0]

    assert trace.deployment_trace_id == running.deployment_trace_id
    assert trace.classification.candidate_id != running.classification.candidate_id
    assert running.classification.process_instance_id == fact.process_instance_id
    assert str(fact.pid) not in running.classification.candidate_id


def test_domain_or_deployment_binding_changes_identity():
    base = _adapter(_trusted("ccr.package")).assess(())[0]
    other_domain = _adapter(_trusted("ccr.package", domain="container-other")).assess(
        ()
    )[0]
    other_binding = _adapter(_trusted("ccr.package", binding="secondary")).assess(
        ()
    )[0]

    assert base.deployment_trace_id != other_domain.deployment_trace_id
    assert base.deployment_trace_id != other_binding.deployment_trace_id


def test_multiple_bindings_in_same_domain_are_explicit_conflicts():
    first = _trusted("ccr.package", binding="default")
    second = _trusted("ccr.service", binding="secondary")
    results = _adapter(
        first,
        second,
    ).assess(())

    assert len(results) == 2
    assert all("CCR_DEPLOYMENT_CONFLICT" in item.reason_codes for item in results)
    assert all(item.confirmed is False for item in results)
    assert all(first.evidence_ref in item.evidence_refs for item in results)
    assert all(second.evidence_ref in item.evidence_refs for item in results)


def test_shared_cloudcli_process_fact_is_not_claimed_by_ccr():
    fact = _fact(
        fixed_facts={"cloudcli_runtime_observed": True},
        supported_fixed_fact_names=("cloudcli_runtime_observed",),
        collector="cloudcli-runtime-probe",
    )
    result = _adapter(_pid(fact.pid), _trusted("ccr.package")).assess((fact,))[0]

    assert result.confirmed is False
    assert "CCR_PROCESS_SHARED_UNRESOLVED" in result.reason_codes


def test_evidence_refs_and_marker_output_are_stably_sorted():
    fact = _fact()
    forward = _adapter(
        _trusted("ccr.service"),
        _pid(fact.pid),
        _trusted("ccr.package"),
    ).assess((fact,))[0]
    reverse = _adapter(
        _trusted("ccr.package"),
        _pid(fact.pid),
        _trusted("ccr.service"),
    ).assess((fact,))[0]

    assert forward.to_dict() == reverse.to_dict()
    assert forward.evidence_refs == tuple(sorted(forward.evidence_refs))


def test_uncertainties_and_required_checks_are_explicit():
    result = _adapter(_trusted("ccr.config")).assess(())[0]

    assert result.classification.uncertainties
    assert result.classification.required_checks
    assert result.sanitized is True


def test_registry_isolates_ccr_adapter_failure():
    class HealthyAdapter:
        adapter_id = "healthy"

        def discover(self, facts):
            del facts
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

    registry = AgentAdapterRegistry((_adapter(_trusted("ccr.package")), HealthyAdapter()))
    result = registry.discover((object(),))

    assert result.status is CapabilityStatus.DEGRADED
    assert [item.candidate_id for item in result.candidates] == ["healthy-candidate"]
    assert result.errors[0].details["adapter_id"] == "ccr"


def test_adapter_calls_no_network_process_provider_docker_or_database(monkeypatch):
    def forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError("forbidden I/O was called")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    fact = _fact()

    result = _adapter(_pid(fact.pid), _trusted("ccr.package")).assess((fact,))[0]

    assert result.confirmed is True


def test_production_adapter_has_no_forbidden_imports_or_calls():
    path = (
        Path(__file__).parents[1]
        / "agentguard"
        / "discovery"
        / "agents"
        / "adapters"
        / "ccr.py"
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
