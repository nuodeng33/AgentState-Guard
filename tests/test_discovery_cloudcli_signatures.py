"""Bounded CloudCLI marker and PID parsing contracts."""

from __future__ import annotations

import json

import pytest

from agentguard.discovery.agents.adapters import cloudcli


def _api(name):
    value = getattr(cloudcli, name, None)
    assert value is not None, f"CloudCLI public API is missing: {name}"
    return value


def _parse(raw: bytes, **overrides):
    parser = _api("parse_cloudcli_pid_marker")
    values = {
        "raw": raw,
        "execution_domain_id": "container-agent-dev",
        "evidence_ref": "host:cloudcli-pid",
    }
    values.update(overrides)
    return parser(**values)


def test_marker_kinds_are_explicit_and_complete():
    marker_kind = _api("CloudCliMarkerKind")

    assert [item.value for item in marker_kind] == [
        "INSTALLATION_MARKER",
        "SERVICE_MARKER",
        "PID_MARKER",
        "PROCESS_MARKER",
        "VERSION_MARKER",
        "WORKSPACE_ROOT_MARKER",
        "RUNTIME_MARKER",
    ]


def test_marker_serialization_contains_only_bounded_fields():
    marker_type = _api("CloudCliMarker")
    marker_kind = _api("CloudCliMarkerKind")
    source = _api("CloudCliMarkerSource")
    verification = _api("CloudCliMarkerVerification")
    marker = marker_type(
        marker_id="cloudcli.package",
        kind=marker_kind.INSTALLATION_MARKER,
        present=True,
        source=source.FIXED_CONTAINER_PATH,
        evidence_ref="host:cloudcli-package",
        sanitized=True,
        verification=verification.VERIFIED,
        execution_domain_id="container-agent-dev",
    )

    payload = marker.to_dict()

    assert payload["marker_id"] == "cloudcli.package"
    assert payload["kind"] == "INSTALLATION_MARKER"
    assert payload["source"] == "FIXED_CONTAINER_PATH"
    encoded = json.dumps(payload, sort_keys=True).casefold()
    for forbidden in (
        "command_line",
        "argv",
        "environment",
        "provider",
        "token",
        "secret",
        "remote_url",
        "raw_content",
    ):
        assert forbidden not in encoded


def test_valid_pid_marker_accepts_ascii_digits_and_one_newline():
    verification = _api("CloudCliMarkerVerification")

    marker = _parse(b"150\n")

    assert marker.present is True
    assert marker.pid == 150
    assert "process_instance_id" not in marker.to_dict()
    assert marker.verification is verification.STRUCTURED
    assert marker.reason_code is None


def test_pid_marker_over_64_bytes_is_rejected_before_parsing():
    verification = _api("CloudCliMarkerVerification")

    marker = _parse(b"1" * 65)

    assert marker.verification is verification.INVALID
    assert marker.pid is None
    assert marker.reason_code == "CLOUDCLI_PID_OUTPUT_TOO_LARGE"


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"150\x00", "CLOUDCLI_PID_INVALID"),
        (b"150\n151\n", "CLOUDCLI_PID_INVALID"),
        (b"15x", "CLOUDCLI_PID_INVALID"),
        (b"+150", "CLOUDCLI_PID_INVALID"),
        (b" 150 ", "CLOUDCLI_PID_INVALID"),
    ],
)
def test_pid_marker_rejects_nul_multiline_nondigit_and_signs(raw, reason):
    verification = _api("CloudCliMarkerVerification")

    marker = _parse(raw)

    assert marker.verification is verification.INVALID
    assert marker.pid is None
    assert marker.reason_code == reason


def test_pid_marker_rejects_symlink_without_reading_content():
    verification = _api("CloudCliMarkerVerification")

    marker = _parse(b"150\n", is_symlink=True)

    assert marker.verification is verification.INVALID
    assert marker.pid is None
    assert marker.reason_code == "CLOUDCLI_PID_SYMLINK_UNSUPPORTED"


def test_marker_rejects_unsanitized_or_sensitive_source_text():
    marker_type = _api("CloudCliMarker")
    marker_kind = _api("CloudCliMarkerKind")
    verification = _api("CloudCliMarkerVerification")

    with pytest.raises((TypeError, ValueError)):
        marker_type(
            marker_id="cloudcli.config",
            kind=marker_kind.SERVICE_MARKER,
            present=True,
            source="C:/Users/private/.cloudcli/settings.json",
            evidence_ref="host:service",
            sanitized=False,
            verification=verification.VERIFIED,
            execution_domain_id="windows-native",
        )


def test_version_marker_accepts_only_bounded_normalized_version():
    marker_type = _api("CloudCliMarker")
    marker_kind = _api("CloudCliMarkerKind")
    source = _api("CloudCliMarkerSource")
    verification = _api("CloudCliMarkerVerification")

    marker = marker_type(
        marker_id="cloudcli.version",
        kind=marker_kind.VERSION_MARKER,
        present=True,
        source=source.FIXED_PACKAGE_METADATA,
        evidence_ref="host:cloudcli-version",
        sanitized=True,
        verification=verification.VERIFIED,
        execution_domain_id="container-agent-dev",
        version="1.36.3",
    )

    assert marker.to_dict()["version"] == "1.36.3"
    with pytest.raises(ValueError, match="version"):
        marker_type(
            marker_id="cloudcli.version",
            kind=marker_kind.VERSION_MARKER,
            present=True,
            source=source.FIXED_PACKAGE_METADATA,
            evidence_ref="host:cloudcli-version",
            sanitized=True,
            verification=verification.VERIFIED,
            execution_domain_id="container-agent-dev",
            version="1.36.3 secret=value",
        )


def test_cloudcli_contracts_are_exported_from_discovery_packages():
    from agentguard import discovery
    from agentguard.discovery import agents

    for name in (
        "CloudCliAdapter",
        "CloudCliDetectionLevel",
        "CloudCliDiscoveryResult",
        "CloudCliMarker",
        "CloudCliMarkerKind",
        "CloudCliMarkerSource",
        "CloudCliMarkerVerification",
        "create_verified_cloudcli_marker",
        "parse_cloudcli_pid_marker",
    ):
        assert getattr(agents, name, None) is getattr(cloudcli, name)
        assert getattr(discovery, name, None) is getattr(cloudcli, name)


def test_pid_parser_does_not_accept_caller_process_instance_identity():
    with pytest.raises(TypeError):
        _parse(b"150\n", process_instance_id="caller-spoofed-instance")


def test_pid_marker_serialization_contains_no_process_instance_identity():
    marker = _parse(b"150\n")

    assert "process_instance_id" not in marker.to_dict()


def test_controlled_marker_factory_derives_fixed_provenance():
    factory = _api("create_verified_cloudcli_marker")
    source = _api("CloudCliMarkerSource")

    marker = factory(
        marker_id="cloudcli.package",
        execution_domain_id="container-agent-dev",
        evidence_ref="host:cloudcli-package",
    )

    assert marker.source is source.FIXED_PACKAGE_METADATA
    assert marker.collector == "cloudcli-package-metadata-probe"
    assert marker.verification is _api("CloudCliMarkerVerification").VERIFIED
