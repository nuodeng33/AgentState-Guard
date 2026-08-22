"""R4-P3A contracts for privacy-bounded process and Agent facts."""

from datetime import UTC, datetime

import pytest

from agentguard.discovery import CapabilityStatus
from agentguard.discovery.agents import (
    AgentRole,
    ExecutableIdentityKind,
    ProcessFact,
    ProcessState,
    ProcessWarningCode,
    WorkspaceCandidate,
    WorkspacePathKind,
    WorkspaceSource,
    make_process_instance_id,
)

CREATED_AT = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def _process_fact(**overrides):
    values = {
        "process_instance_id": make_process_instance_id(
            execution_domain_id="linux-host",
            pid=410,
            create_time=CREATED_AT,
            collector="fixture",
        ),
        "pid": 410,
        "parent_pid": 100,
        "executable_basename": "agent-tool",
        "executable_identity_digest": "sha256:" + "a" * 64,
        "executable_identity_kind": ExecutableIdentityKind.BASENAME_SHA256,
        "executable_identity_verified": False,
        "create_time": CREATED_AT,
        "execution_domain_id": "linux-host",
        "current_state": ProcessState.RUNNING,
        "evidence_refs": ("process:410",),
        "access_status": CapabilityStatus.AVAILABLE,
        "sanitized": True,
        "collector": "fixture",
    }
    values.update(overrides)
    return ProcessFact(**values)


def test_agent_roles_are_stable_and_product_neutral():
    assert [role.value for role in AgentRole] == [
        "EXECUTION_AGENT",
        "AGENT_HOST",
        "MODEL_ROUTER",
        "TOOL_PROCESS",
        "UNKNOWN",
    ]


def test_process_instance_id_distinguishes_pid_reuse_by_create_time():
    first = make_process_instance_id(
        execution_domain_id="linux-host",
        pid=410,
        create_time=CREATED_AT,
        collector="fixture",
    )
    reused = make_process_instance_id(
        execution_domain_id="linux-host",
        pid=410,
        create_time=datetime(2026, 8, 2, 12, 1, tzinfo=UTC),
        collector="fixture",
    )

    assert first != reused
    assert first.startswith("process-")
    assert "410" not in first


def test_process_instance_id_distinguishes_execution_domains():
    linux = make_process_instance_id(
        execution_domain_id="linux-host",
        pid=410,
        create_time=CREATED_AT,
        collector="fixture",
    )
    wsl = make_process_instance_id(
        execution_domain_id="wsl-runtime",
        pid=410,
        create_time=CREATED_AT,
        collector="fixture",
    )

    assert linux != wsl


def test_equivalent_float_create_times_normalize_to_same_instance_id():
    first = make_process_instance_id(
        execution_domain_id="linux-host",
        pid=410,
        create_time=1722600000.123456,
        collector="fixture",
    )
    equivalent = make_process_instance_id(
        execution_domain_id="linux-host",
        pid=410,
        create_time=1722600000.1234558,
        collector="fixture",
    )

    assert first == equivalent


def test_create_time_microsecond_boundary_changes_instance_id():
    first = make_process_instance_id(
        execution_domain_id="linux-host",
        pid=410,
        create_time=1722600000.123456,
        collector="fixture",
    )
    next_microsecond = make_process_instance_id(
        execution_domain_id="linux-host",
        pid=410,
        create_time=1722600000.123457,
        collector="fixture",
    )

    assert first != next_microsecond


def test_process_fact_round_trip_is_json_compatible_and_utc():
    restored = ProcessFact.from_dict(_process_fact().to_dict())

    assert restored == _process_fact()
    assert restored.create_time is not None
    assert restored.create_time.tzinfo is UTC
    assert restored.to_dict()["schema_version"] == "1.0"


def test_process_fact_rejects_path_in_executable_basename():
    with pytest.raises(ValueError, match="basename"):
        _process_fact(executable_basename="C:\\Users\\private\\agent-tool.exe")


def test_process_fact_rejects_non_digest_executable_identity():
    with pytest.raises(ValueError, match="identity"):
        _process_fact(executable_identity_digest="private executable detail")


def test_executable_identity_declares_basename_input_and_is_unverified():
    payload = _process_fact().to_dict()

    assert payload["executable_identity_kind"] == "BASENAME_SHA256"
    assert payload["executable_identity_verified"] is False
    assert "allow" not in payload


def test_p3a_rejects_verified_executable_identity_claim():
    with pytest.raises(ValueError, match="verified"):
        _process_fact(executable_identity_verified=True)


def test_process_fact_serialization_has_no_sensitive_process_fields():
    encoded = str(_process_fact().to_dict()).lower()

    assert "command_line" not in encoded
    assert "commandline" not in encoded
    assert "argv" not in encoded
    assert "environment" not in encoded
    assert "token" not in encoded
    assert "secret" not in encoded
    assert "api_key" not in encoded


def test_running_process_requires_create_time_for_non_pid_identity():
    with pytest.raises(ValueError, match="create_time"):
        _process_fact(create_time=None)


def test_unknown_optional_fields_degrade_without_breaking_old_reader():
    payload = _process_fact().to_dict()
    payload["future_process_detail"] = {"ignored": True}
    payload["current_state"] = "FUTURE_STATE"

    restored = ProcessFact.from_dict(payload)

    assert restored.current_state is ProcessState.UNKNOWN
    assert "future_process_detail" not in restored.to_dict()


def test_workspace_candidate_cannot_be_promoted_to_final_binding():
    with pytest.raises(ValueError):
        WorkspaceCandidate(
            candidate_id="workspace-1",
            source=WorkspaceSource.PROCESS_CWD,
            execution_domain_id="linux-host",
            path_hint="~/project",
            path_kind=WorkspacePathKind.REDACTED,
            access_status=CapabilityStatus.AVAILABLE,
            evidence_refs=("cwd:410",),
            confidence=0.7,
            is_final_binding=True,
        )


def test_workspace_model_rejects_unminimized_user_home_path():
    with pytest.raises(ValueError, match="minimized"):
        WorkspaceCandidate(
            candidate_id="workspace-raw-home",
            source=WorkspaceSource.PROCESS_CWD,
            execution_domain_id="linux-host",
            path_hint="/home/private-user/project",
            path_kind=WorkspacePathKind.NATIVE,
            access_status=CapabilityStatus.AVAILABLE,
            evidence_refs=("cwd:410",),
        )


def test_warning_codes_are_machine_readable_not_free_text():
    fact = _process_fact(warnings=(ProcessWarningCode.PARTIAL_VISIBILITY,))

    assert fact.to_dict()["warnings"] == ["PARTIAL_VISIBILITY"]


def test_sensitive_fixed_fact_name_is_rejected_before_serialization():
    with pytest.raises(ValueError, match="fixed fact"):
        _process_fact(fixed_facts={"api_key_present": True})


def test_live_process_and_workspace_models_are_exported_from_discovery_package():
    from agentguard import discovery

    assert discovery.ProcessFact is ProcessFact
    assert discovery.WorkspaceCandidate is WorkspaceCandidate
