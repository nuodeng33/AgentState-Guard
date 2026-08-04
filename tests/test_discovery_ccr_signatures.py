"""Bounded Claude Code Router marker and PID parsing contracts."""

from __future__ import annotations

import json

import pytest

from agentguard.discovery.agents import adapters

DOMAIN = "container-agent-dev"


def _api(name):
    value = getattr(adapters, name, None)
    assert value is not None, f"CCR public API is missing: {name}"
    return value


def _parse(raw: bytes, **overrides):
    values = {
        "raw": raw,
        "execution_domain_id": DOMAIN,
        "evidence_ref": "host:ccr-pid",
    }
    values.update(overrides)
    return _api("parse_ccr_pid_marker")(**values)


def test_ccr_marker_kinds_are_explicit_and_complete():
    assert [item.value for item in _api("CcrMarkerKind")] == [
        "INSTALLATION_MARKER",
        "PACKAGE_MARKER",
        "SERVICE_MARKER",
        "PID_MARKER",
        "PROCESS_MARKER",
        "VERSION_MARKER",
        "CONFIG_MARKER",
        "RUNTIME_MARKER",
    ]


def test_controlled_package_marker_binds_exact_deployment_provenance():
    marker = _api("create_verified_ccr_marker")(
        marker_id="ccr.package",
        execution_domain_id=DOMAIN,
        evidence_ref="host:ccr-package",
        package_name="@musistudio/claude-code-router",
    )

    assert marker.marker_kind is _api("CcrMarkerKind").PACKAGE_MARKER
    assert marker.source_kind is _api("CcrMarkerSource").FIXED_PACKAGE_METADATA
    assert marker.collector_id == "ccr-package-metadata-probe"
    assert marker.verification is _api("CcrMarkerVerification").VERIFIED


def test_wrong_package_name_becomes_invalid_without_retaining_raw_name():
    marker = _api("create_verified_ccr_marker")(
        marker_id="ccr.package",
        execution_domain_id=DOMAIN,
        evidence_ref="host:ccr-package",
        package_name="near-name-with-secret-token",
    )

    assert marker.verification is _api("CcrMarkerVerification").INVALID
    assert marker.reason_code == "CCR_PACKAGE_NAME_MISMATCH"
    assert marker.package_name is None
    assert "near-name" not in json.dumps(marker.to_dict())


def test_unknown_version_is_optional_but_malformed_version_is_invalid():
    factory = _api("create_verified_ccr_marker")
    unknown = factory(
        marker_id="ccr.version",
        execution_domain_id=DOMAIN,
        evidence_ref="host:ccr-version",
    )
    malformed = factory(
        marker_id="ccr.version",
        execution_domain_id=DOMAIN,
        evidence_ref="host:ccr-version-invalid",
        version="3.0.7 token=value",
    )

    assert unknown.verification is _api("CcrMarkerVerification").UNKNOWN
    assert unknown.version is None
    assert malformed.verification is _api("CcrMarkerVerification").INVALID
    assert malformed.reason_code == "CCR_VERSION_INVALID"
    assert malformed.version is None


def test_valid_pid_marker_contains_pid_but_no_process_instance_identity():
    marker = _parse(b"321\n")

    assert marker.pid == 321
    assert marker.verification is _api("CcrMarkerVerification").STRUCTURED
    assert "process_instance_id" not in marker.to_dict()


def test_pid_parser_rejects_caller_process_instance_identity():
    with pytest.raises(TypeError):
        _parse(b"321\n", process_instance_id="caller-spoof")


def test_pid_marker_over_64_bytes_is_rejected_before_parsing():
    marker = _parse(b"1" * 65)

    assert marker.pid is None
    assert marker.reason_code == "CCR_PID_OUTPUT_TOO_LARGE"
    assert marker.verification is _api("CcrMarkerVerification").INVALID


@pytest.mark.parametrize(
    "raw",
    [b"0", b"+321", b" 321", b"321 ", b"321\x00", b"321\n322\n", b"3x1"],
)
def test_pid_marker_rejects_zero_sign_space_nul_multiline_and_nondigit(raw):
    marker = _parse(raw)

    assert marker.pid is None
    assert marker.reason_code == "CCR_PID_INVALID"


def test_pid_marker_rejects_symlink_without_using_content():
    marker = _parse(b"321\n", is_symlink=True)

    assert marker.pid is None
    assert marker.reason_code == "CCR_PID_SYMLINK_UNSUPPORTED"


def test_marker_accepts_no_raw_config_mapping_or_config_content():
    factory = _api("create_verified_ccr_marker")

    with pytest.raises(TypeError):
        factory(
            marker_id="ccr.config",
            execution_domain_id=DOMAIN,
            evidence_ref="host:ccr-config",
            config={"provider_url": "https://secret.invalid", "api_key": "fake"},
        )


def test_marker_serialization_contains_only_bounded_normalized_fields():
    marker = _api("create_verified_ccr_marker")(
        marker_id="ccr.config",
        execution_domain_id=DOMAIN,
        evidence_ref="host:ccr-config",
    )
    encoded = json.dumps(marker.to_dict(), sort_keys=True).casefold()

    for forbidden in (
        "command_line",
        "argv",
        "environment",
        "provider_url",
        "api_key",
        "token",
        "secret",
        "route_table",
        "raw_config",
    ):
        assert forbidden not in encoded


def test_ccr_contracts_are_exported_from_discovery_packages():
    from agentguard import discovery
    from agentguard.discovery import agents

    for name in (
        "CcrAdapter",
        "CcrDetectionLevel",
        "CcrDiscoveryResult",
        "CcrMarker",
        "CcrMarkerKind",
        "CcrMarkerSource",
        "CcrMarkerVerification",
        "create_verified_ccr_marker",
        "parse_ccr_pid_marker",
    ):
        assert getattr(agents, name, None) is getattr(adapters, name)
        assert getattr(discovery, name, None) is getattr(adapters, name)
