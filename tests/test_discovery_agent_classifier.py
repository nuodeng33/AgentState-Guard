"""R4-P3A product-neutral classifier and static Adapter Registry tests."""

from datetime import UTC, datetime

import pytest

from agentguard.discovery import AgentLifecycleStatus, CapabilityStatus
from agentguard.discovery.agents import (
    AgentAdapterRegistry,
    AgentCandidateType,
    AgentClassification,
    AgentRole,
    AgentSignatureRule,
    ExecutableIdentityKind,
    ProcessFact,
    ProcessState,
    classify_executable,
    classify_process,
    make_process_instance_id,
)

CREATED_AT = datetime(2026, 8, 2, 13, 0, tzinfo=UTC)


def _fact(
    *,
    basename="generic-tool",
    fixed_facts=None,
    supported_fixed_fact_names=(),
    state=ProcessState.RUNNING,
):
    instance_id = make_process_instance_id(
        execution_domain_id="linux-host",
        pid=410,
        create_time=CREATED_AT,
        collector="fixture",
    )
    return ProcessFact(
        process_instance_id=instance_id,
        pid=410,
        parent_pid=100,
        executable_basename=basename,
        executable_identity_digest="sha256:" + "a" * 64,
        executable_identity_kind=ExecutableIdentityKind.BASENAME_SHA256,
        executable_identity_verified=False,
        create_time=CREATED_AT,
        execution_domain_id="linux-host",
        current_state=state,
        evidence_refs=("process:410",),
        access_status=CapabilityStatus.AVAILABLE,
        sanitized=True,
        fixed_facts=fixed_facts or {},
        supported_fixed_fact_names=supported_fixed_fact_names,
        collector="fixture",
    )


def _rule(role, basename, *, observed_flag=None):
    return AgentSignatureRule(
        rule_id=f"generic-{role.value.lower()}",
        role=role,
        executable_basenames=(basename,),
        signature_evidence_ref=f"signature:{role.value.lower()}",
        observed_flag=observed_flag,
        required_checks=("PRODUCT_ADAPTER_CONFIRMATION",),
    )


def test_generic_execution_agent_candidate_uses_explicit_signature():
    result = classify_process(
        _fact(basename="exec-agent"),
        (_rule(AgentRole.EXECUTION_AGENT, "exec-agent"),),
    )

    assert result.role is AgentRole.EXECUTION_AGENT
    assert result.lifecycle is AgentLifecycleStatus.RUNNING
    assert result.candidate_type is AgentCandidateType.PROCESS


def test_generic_agent_host_candidate_uses_explicit_signature():
    result = classify_process(
        _fact(basename="agent-host"),
        (_rule(AgentRole.AGENT_HOST, "agent-host"),),
    )

    assert result.role is AgentRole.AGENT_HOST


def test_generic_model_router_candidate_uses_explicit_signature():
    result = classify_process(
        _fact(basename="model-router"),
        (_rule(AgentRole.MODEL_ROUTER, "model-router"),),
    )

    assert result.role is AgentRole.MODEL_ROUTER


def test_unmatched_process_is_only_a_tool_process():
    result = classify_process(_fact(), ())

    assert result.role is AgentRole.TOOL_PROCESS
    assert result.lifecycle is AgentLifecycleStatus.RUNNING
    assert result.confidence == 0.5


def test_missing_executable_fact_remains_unknown_not_tool_process():
    result = classify_process(_fact(basename=None), ())

    assert result.role is AgentRole.UNKNOWN
    assert result.lifecycle is AgentLifecycleStatus.RUNNING
    assert "EXECUTABLE_BASENAME_REQUIRED" in result.required_checks


def test_executable_without_process_is_detected_not_running():
    result = classify_executable(
        executable_basename="exec-agent",
        execution_domain_id="linux-host",
        evidence_refs=("executable:exec-agent",),
        rules=(_rule(AgentRole.EXECUTION_AGENT, "exec-agent"),),
    )

    assert result.role is AgentRole.EXECUTION_AGENT
    assert result.lifecycle is AgentLifecycleStatus.DETECTED
    assert result.process_instance_id is None


def test_running_process_does_not_automatically_become_observed():
    result = classify_process(
        _fact(basename="exec-agent"),
        (_rule(AgentRole.EXECUTION_AGENT, "exec-agent", observed_flag="runtime_supported"),),
    )

    assert result.lifecycle is AgentLifecycleStatus.RUNNING
    assert "SUPPORTED_RUNTIME_EVIDENCE_REQUIRED" in result.required_checks


def test_supported_fixed_boolean_fact_can_produce_observed():
    result = classify_process(
        _fact(
            basename="exec-agent",
            fixed_facts={"runtime_supported": True},
            supported_fixed_fact_names=("runtime_supported",),
        ),
        (_rule(AgentRole.EXECUTION_AGENT, "exec-agent", observed_flag="runtime_supported"),),
    )

    assert result.lifecycle is AgentLifecycleStatus.OBSERVED
    assert result.lifecycle is not AgentLifecycleStatus.INTEGRATED
    assert result.lifecycle is not AgentLifecycleStatus.ENFORCED


def test_unapproved_boolean_fact_cannot_produce_observed():
    result = classify_process(
        _fact(basename="exec-agent", fixed_facts={"runtime_supported": True}),
        (_rule(AgentRole.EXECUTION_AGENT, "exec-agent", observed_flag="runtime_supported"),),
    )

    assert result.lifecycle is AgentLifecycleStatus.RUNNING
    assert "SUPPORTED_RUNTIME_EVIDENCE_REQUIRED" in result.required_checks


def test_classifier_uses_exact_names_not_fuzzy_natural_language():
    result = classify_process(
        _fact(basename="exec-agent-helper"),
        (_rule(AgentRole.EXECUTION_AGENT, "exec-agent"),),
    )

    assert result.role is AgentRole.TOOL_PROCESS


def test_rule_can_restrict_execution_domain_without_cross_domain_guessing():
    rule = AgentSignatureRule(
        rule_id="wsl-only",
        role=AgentRole.AGENT_HOST,
        executable_basenames=("agent-host",),
        execution_domain_ids=("wsl-runtime",),
        signature_evidence_ref="signature:wsl-only",
    )

    result = classify_process(_fact(basename="agent-host"), (rule,))

    assert result.role is AgentRole.TOOL_PROCESS


def test_classification_contains_evidence_uncertainties_and_required_checks():
    result = classify_process(
        _fact(basename="exec-agent"),
        (_rule(AgentRole.EXECUTION_AGENT, "exec-agent"),),
    )

    assert result.evidence_refs == ("process:410", "signature:execution_agent")
    assert "PRODUCT_IDENTITY_UNVERIFIED" in result.uncertainties
    assert result.required_checks == ("PRODUCT_ADAPTER_CONFIRMATION",)


class FakeAdapter:
    def __init__(self, adapter_id, role=None, failure=None):
        self.adapter_id = adapter_id
        self._role = role
        self._failure = failure

    def discover(self, facts):
        if self._failure is not None:
            raise self._failure
        return (
            AgentClassification(
                candidate_id=f"candidate-{self.adapter_id}",
                candidate_type=AgentCandidateType.PROCESS,
                role=self._role,
                lifecycle=AgentLifecycleStatus.RUNNING,
                confidence=0.6,
                evidence_refs=facts[0].evidence_refs,
                uncertainties=("TEST_ADAPTER",),
                required_checks=("REVIEW",),
                process_instance_id=facts[0].process_instance_id,
            ),
        )


def test_static_registry_preserves_explicit_registration_order():
    registry = AgentAdapterRegistry(
        (
            FakeAdapter("first", AgentRole.EXECUTION_AGENT),
            FakeAdapter("second", AgentRole.MODEL_ROUTER),
        )
    )

    assert registry.adapter_ids == ("first", "second")
    result = registry.discover((_fact(),))
    assert [item.role for item in result.candidates] == [
        AgentRole.EXECUTION_AGENT,
        AgentRole.MODEL_ROUTER,
    ]


def test_registry_rejects_empty_adapter_id():
    with pytest.raises(ValueError, match="adapter_id"):
        AgentAdapterRegistry((FakeAdapter("", AgentRole.EXECUTION_AGENT),))


def test_registry_rejects_duplicate_adapter_id():
    with pytest.raises(ValueError, match="unique"):
        AgentAdapterRegistry(
            (
                FakeAdapter("same", AgentRole.EXECUTION_AGENT),
                FakeAdapter("same", AgentRole.MODEL_ROUTER),
            )
        )


def test_one_adapter_failure_does_not_discard_other_results():
    registry = AgentAdapterRegistry(
        (
            FakeAdapter("broken", failure=RuntimeError("private detail")),
            FakeAdapter("healthy", AgentRole.AGENT_HOST),
        )
    )

    result = registry.discover((_fact(),))

    assert result.status is CapabilityStatus.DEGRADED
    assert [item.role for item in result.candidates] == [AgentRole.AGENT_HOST]
    assert result.errors[0].details == {
        "adapter_id": "broken",
        "reason_code": "ADAPTER_DISCOVERY_FAILED",
    }
    assert "private detail" not in str(result.to_dict())
