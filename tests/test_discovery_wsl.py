"""Tests for WSL list parsing and the untrusted one-shot probe protocol."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from agentguard.discovery import (
    CapabilityStatus,
    DiscoveryErrorCode,
    ExecutionDomainKind,
)
from agentguard.discovery.domains.wsl import WslDistroState, parse_wsl_list
from agentguard.discovery.probes.wsl_probe import (
    PROBE_SCHEMA_VERSION,
    WslProbeProtocolError,
    build_probe_document,
    normalize_wsl_probe,
)

OBSERVED_AT = "2026-08-02T10:30:00+00:00"


def _probe_evidence(evidence_id="probe-wsl", fact_type="wsl.kernel"):
    return {
        "evidence_id": evidence_id,
        "collector": "wsl_probe",
        "source": "fixed_probe",
        "observed_at": OBSERVED_AT,
        "fact_type": fact_type,
        "value": {"present": True},
        "reliability": "HIGH",
        "confidence": 0.9,
        "status": "AVAILABLE",
        "sanitized": True,
    }


def _payload(**overrides):
    payload = {
        "schema_version": PROBE_SCHEMA_VERSION,
        "probe_version": "1.0",
        "distro": "Ubuntu",
        "observed_at": OBSERVED_AT,
        "execution_domain": {"kind": "WSL", "generation": "WSL2"},
        "kernel": {"system": "Linux", "release": "6.8-wsl2", "version": "test"},
        "identity": {"uid": 1000, "gid": 1000},
        "capabilities": {
            "DISCOVERY_LITE": "AVAILABLE",
            "DISCOVERY_FULL": "AVAILABLE",
            "RECOVERY_CAPABLE": "UNKNOWN",
        },
        "evidence": [_probe_evidence()],
        "errors": [],
        "sanitized": True,
        "warnings": [],
    }
    payload.update(overrides)
    return payload


def test_empty_wsl_list_is_a_valid_empty_result():
    result = parse_wsl_list("  NAME      STATE      VERSION\n")

    assert result.distros == ()
    assert result.warnings == ()


def test_running_wsl2_distro_is_parsed_from_verbose_columns():
    result = parse_wsl_list(
        "  NAME            STATE      VERSION\n* Ubuntu-22.04    Running    2\n"
    )

    assert len(result.distros) == 1
    assert result.distros[0].name == "Ubuntu-22.04"
    assert result.distros[0].state is WslDistroState.RUNNING
    assert result.distros[0].generation == "WSL2"
    assert result.distros[0].is_default is True


def test_multiple_running_and_stopped_distros_remain_distinct():
    result = parse_wsl_list(
        "  NAME            STATE      VERSION\n"
        "* Ubuntu         Running    2\n"
        "  Debian         Stopped    2\n"
        "  Legacy         Stopped    1\n"
    )

    assert [(item.name, item.state.value, item.generation) for item in result.distros] == [
        ("Ubuntu", "RUNNING", "WSL2"),
        ("Debian", "STOPPED", "WSL2"),
        ("Legacy", "STOPPED", "WSL1"),
    ]


def test_localized_header_and_chinese_state_preserve_distro_as_unknown():
    result = parse_wsl_list(
        "  名称          状态        版本\n"
        "  Ubuntu      正在运行    2\n"
    )

    assert len(result.distros) == 1
    assert result.distros[0].name == "Ubuntu"
    assert result.distros[0].state is WslDistroState.UNKNOWN
    assert result.distros[0].generation == "WSL2"


def test_arbitrary_unknown_state_is_not_treated_as_stopped():
    result = parse_wsl_list(
        "  NAME       STATE       VERSION\n"
        "  Ubuntu    Paused      2\n"
    )

    assert result.distros[0].state is WslDistroState.UNKNOWN


def test_distro_name_with_spaces_is_preserved():
    result = parse_wsl_list(
        "  NAME                  STATE      VERSION\n"
        "  Ubuntu Development    Stopped    2\n"
    )

    assert result.distros[0].name == "Ubuntu Development"


def test_malicious_distro_list_entry_is_rejected_with_machine_warning():
    result = parse_wsl_list(
        "  NAME                         STATE      VERSION\n"
        "  Ubuntu;curl example.invalid  Running    2\n"
    )

    assert result.distros == ()
    assert "INVALID_DISTRO_ENTRY_REJECTED" in result.warnings


def test_valid_probe_schema_normalizes_to_wsl_domain_and_capability_tiers():
    result = normalize_wsl_probe(json.dumps(_payload()), expected_distro="Ubuntu")

    assert result.status is CapabilityStatus.AVAILABLE
    assert result.domain.kind is ExecutionDomainKind.WSL
    assert result.domain.capabilities.get("discovery_lite").status is CapabilityStatus.AVAILABLE
    assert result.domain.capabilities.get("discovery_full").status is CapabilityStatus.AVAILABLE
    assert result.domain.capabilities.get("recovery_capable").status is CapabilityStatus.UNKNOWN
    assert result.observed_at.tzinfo is UTC


def test_unknown_probe_fields_are_ignored_without_crashing():
    payload = _payload(future_top_level={"ignored": True})
    payload["execution_domain"]["future_nested"] = "ignored"

    result = normalize_wsl_probe(json.dumps(payload), expected_distro="Ubuntu")

    encoded = result.to_dict()
    assert result.status is CapabilityStatus.AVAILABLE
    assert "future_top_level" not in encoded
    assert "future_nested" not in encoded["domain"]


def test_unknown_execution_domain_enum_degrades_to_unknown():
    payload = _payload(execution_domain={"kind": "FUTURE_VM", "generation": "FUTURE"})

    result = normalize_wsl_probe(json.dumps(payload), expected_distro="Ubuntu")

    assert result.status is CapabilityStatus.DEGRADED
    assert result.domain.kind is ExecutionDomainKind.UNKNOWN
    assert result.domain.capabilities.get("discovery_full").status is CapabilityStatus.UNKNOWN


def test_corrupt_probe_json_maps_to_invalid_data():
    with pytest.raises(WslProbeProtocolError) as raised:
        normalize_wsl_probe("{not-json", expected_distro="Ubuntu")

    assert raised.value.status is CapabilityStatus.ERROR
    assert raised.value.error.code is DiscoveryErrorCode.INVALID_DATA


def test_incompatible_probe_schema_is_unsupported_not_invalid_data():
    payload = _payload(schema_version="9.0")

    with pytest.raises(WslProbeProtocolError) as raised:
        normalize_wsl_probe(json.dumps(payload), expected_distro="Ubuntu")

    assert raised.value.status is CapabilityStatus.UNSUPPORTED
    assert raised.value.error.code is DiscoveryErrorCode.UNSUPPORTED
    assert raised.value.error.details["reason_code"] == "SCHEMA_MISMATCH"


def test_partial_probe_error_preserves_valid_domain_and_evidence():
    payload = _payload(
        errors=[
            {
                "code": "COLLECTOR_FAILURE",
                "message": "One optional fact failed",
                "collector": "wsl_probe",
                "source": "fixed_probe",
                "retryable": False,
            }
        ]
    )

    result = normalize_wsl_probe(json.dumps(payload), expected_distro="Ubuntu")

    assert result.status is CapabilityStatus.DEGRADED
    assert result.domain.kind is ExecutionDomainKind.WSL
    assert result.evidence[0].evidence_id == "probe-wsl"
    assert result.errors[0].code is DiscoveryErrorCode.COLLECTOR_FAILURE


def test_container_fact_from_probe_remains_child_of_wsl_and_host_daemon_unknown():
    payload = _payload(
        execution_domain={
            "kind": "WSL",
            "generation": "WSL2",
            "container": {
                "detected": True,
                "runtime": "DOCKER",
                "evidence_ids": ["probe-container"],
            },
        },
        evidence=[
            _probe_evidence(),
            _probe_evidence("probe-container", "container.current"),
        ],
    )

    result = normalize_wsl_probe(json.dumps(payload), expected_distro="Ubuntu")

    container = result.domain.children[0]
    assert container.kind is ExecutionDomainKind.CONTAINER
    assert container.capabilities.get("self_visible").status is CapabilityStatus.AVAILABLE
    assert container.capabilities.get("host_docker_daemon").status is CapabilityStatus.UNKNOWN


def test_cwd_is_rejected_unless_workspace_probe_was_explicitly_requested():
    payload = _payload(cwd="/home/user/project")

    result = normalize_wsl_probe(json.dumps(payload), expected_distro="Ubuntu")

    assert result.workspace is None
    assert "UNREQUESTED_CWD_REJECTED" in result.warnings
    assert "/home/user/project" not in json.dumps(result.to_dict())


def test_explicit_workspace_probe_keeps_native_path_and_read_only_display_path_separate():
    payload = _payload(cwd="/workspace/project")

    result = normalize_wsl_probe(
        json.dumps(payload),
        expected_distro="Ubuntu",
        allow_workspace=True,
    )

    assert result.workspace is not None
    assert result.workspace.native_path == "/workspace/project"
    assert result.workspace.windows_display_path == r"\\wsl$\Ubuntu\workspace\project"
    assert result.workspace.windows_display_only is True


def test_sensitive_probe_fields_are_rejected_and_never_reach_normalized_output():
    payload = _payload(
        kernel={
            "system": "Linux",
            "release": "safe",
            "api_key": "do-not-store-this",
            "remote_url": "https://example.invalid/private",
        },
        evidence=[
            {
                **_probe_evidence(),
                "value": {"token": "do-not-store-this", "safe": True},
            }
        ],
        errors=[
            {
                "code": "COLLECTOR_FAILURE",
                "message": "token=do-not-store-this",
                "collector": "wsl_probe",
                "source": "fixed_probe",
                "retryable": False,
            }
        ],
    )

    result = normalize_wsl_probe(json.dumps(payload), expected_distro="Ubuntu")
    encoded = json.dumps(result.to_dict(), sort_keys=True).lower()

    assert "SENSITIVE_FIELD_REJECTED" in result.warnings
    assert "do-not-store-this" not in encoded
    assert "api_key" not in encoded
    assert "https://example.invalid/private" not in encoded
    assert "token" not in encoded
    assert "https://" not in encoded


class FakeProbeSource:
    def kernel_system(self):
        return "Linux"

    def kernel_release(self):
        return "5.15.0-microsoft-standard-WSL2"

    def kernel_version(self):
        return "test kernel"

    def uid(self):
        return 1000

    def gid(self):
        return 1000

    def cwd(self):
        return "/workspace/project"

    def path_exists(self, _path):
        return False

    def read_bytes(self, _path):
        raise FileNotFoundError


def test_fixed_probe_document_contains_strict_required_protocol_without_cwd_by_default():
    document = build_probe_document(
        "Ubuntu",
        source=FakeProbeSource(),
        clock=lambda: datetime(2026, 8, 2, 10, 30, tzinfo=UTC),
    )

    assert set(document) == {
        "schema_version",
        "probe_version",
        "distro",
        "observed_at",
        "execution_domain",
        "kernel",
        "identity",
        "capabilities",
        "evidence",
        "errors",
        "sanitized",
        "warnings",
    }
    assert document["sanitized"] is True
    assert document["capabilities"]["RECOVERY_CAPABLE"] == "UNKNOWN"
    assert "cwd" not in document
    json.dumps(document, allow_nan=False)


def test_fixed_probe_document_includes_cwd_only_after_explicit_workspace_request():
    document = build_probe_document(
        "Ubuntu",
        include_workspace=True,
        source=FakeProbeSource(),
        clock=lambda: datetime(2026, 8, 2, 10, 30, tzinfo=UTC),
    )

    assert document["cwd"] == "/workspace/project"
