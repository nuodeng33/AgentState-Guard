"""R4-P2C strict Host Probe envelope import tests."""

import importlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime

import pytest

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def _host_api():
    try:
        return importlib.import_module("agentguard.discovery.host")
    except ModuleNotFoundError:
        pytest.fail("agentguard.discovery.host is not implemented")


def _valid_payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "probe_version": "1.2.0",
        "probe_id": "probe-001",
        "source_domain_id": "windows-host",
        "source_kind": "HOST_PROBE",
        "source_binding_id": "local-helper-01",
        "trust_level": "SOURCE_BOUND",
        "issued_at": "2026-08-02T11:59:58Z",
        "observed_at": "2026-08-02T11:59:55Z",
        "ttl_seconds": 60,
        "sequence": 7,
        "host": {
            "domain_id": "windows-host",
            "kind": "WINDOWS",
            "label": "Windows host",
            "capabilities": {},
            "children": [],
            "evidence_ids": ["host-os"],
            "confidence": 0.8,
        },
        "capabilities": {
            "host_os": {
                "status": "AVAILABLE",
                "reason_code": "HOST_OS_OBSERVED",
                "evidence_ids": ["host-os"],
                "confidence": 0.8,
                "error": None,
            },
            "docker_daemon": {
                "status": "AVAILABLE",
                "reason_code": "DAEMON_AVAILABLE",
                "evidence_ids": ["docker-daemon"],
                "confidence": 0.7,
                "error": None,
            },
        },
        "evidence": [
            {
                "evidence_id": "host-os",
                "collector": "trusted_helper",
                "source": "local-helper",
                "observed_at": "2026-08-02T11:59:55Z",
                "fact_type": "host.os",
                "value": {"os_kind": "WINDOWS", "version_summary": "11"},
                "summary": "Host operating system",
                "reliability": "HIGH",
                "confidence": 0.8,
                "status": "AVAILABLE",
                "error": None,
                "sanitized": True,
            },
            {
                "evidence_id": "docker-daemon",
                "collector": "trusted_helper",
                "source": "local-helper",
                "observed_at": "2026-08-02T11:59:55Z",
                "fact_type": "host.docker_daemon",
                "value": {"state": "DAEMON_AVAILABLE"},
                "summary": None,
                "reliability": "MEDIUM",
                "confidence": 0.7,
                "status": "AVAILABLE",
                "error": None,
                "sanitized": True,
            },
        ],
        "errors": [],
        "sanitized": True,
        "warnings": [],
    }


def _import(payload: object, *, now: datetime = NOW, max_input_bytes: int = 65_536):
    api = _host_api()
    importer = api.HostProbeImporter(max_input_bytes=max_input_bytes)
    return importer.import_payload(payload, now=now)


def _reason(result) -> str | None:
    if result.error is None:
        return None
    return result.error.details.get("reason_code")


def test_valid_envelope_is_fresh_source_bound_and_not_self_visible():
    api = _host_api()

    result = _import(_valid_payload())

    assert result.freshness is api.HostFreshness.FRESH
    assert result.envelope is not None
    assert result.envelope.imported is True
    assert result.envelope.self_visible is False
    assert result.envelope.source_authenticated is False
    assert result.envelope.source_kind is api.HostSourceKind.HOST_PROBE
    assert result.envelope.trust_level is api.HostTrustLevel.SOURCE_BOUND
    assert result.envelope.observed_at.tzinfo is UTC
    assert result.envelope.expires_at == datetime(2026, 8, 2, 12, 0, 55, tzinfo=UTC)
    assert result.envelope.imported_evidence_id == "host-import:probe-001"
    assert all(item.source == "HOST_PROBE" for item in result.envelope.evidence)


def test_source_bound_claim_without_binding_id_is_downgraded_unverified():
    api = _host_api()
    payload = _valid_payload()
    del payload["source_binding_id"]

    result = _import(payload)

    assert result.envelope is not None
    assert result.envelope.trust_level is api.HostTrustLevel.UNVERIFIED
    assert result.envelope.source_authenticated is False
    assert any(item.code.value == "SOURCE_BINDING_UNVERIFIED" for item in result.warnings)


def test_incompatible_schema_is_rejected_without_guessing():
    api = _host_api()
    payload = _valid_payload()
    payload["schema_version"] = "2.0"

    result = _import(payload)

    assert result.freshness is api.HostFreshness.INVALID
    assert result.envelope is None
    assert _reason(result) == "INCOMPATIBLE_SCHEMA"


def test_unknown_fields_are_ignored_and_reported_without_round_trip():
    payload = _valid_payload()
    payload["future_top_level"] = {"ignored": True}

    result = _import(payload)

    assert result.envelope is not None
    encoded = json.dumps(result.envelope.to_dict(), sort_keys=True)
    assert "future_top_level" not in encoded
    assert any(item.code.value == "UNKNOWN_FIELD_IGNORED" for item in result.warnings)


def test_unknown_field_name_content_is_not_reflected_in_warning_output():
    payload = _valid_payload()
    payload["future_DO_NOT_PERSIST_91ac"] = True

    result = _import(payload)

    encoded = json.dumps(result.to_dict(), sort_keys=True).upper()
    assert "DO_NOT_PERSIST" not in encoded


def test_unknown_enums_degrade_to_unknown_without_crashing():
    api = _host_api()
    payload = _valid_payload()
    payload["source_kind"] = "FUTURE_RELAY"
    payload["trust_level"] = "FUTURE_TRUST"
    payload["host"]["kind"] = "FUTURE_HYPERVISOR"

    result = _import(payload)

    assert result.envelope is not None
    assert result.envelope.source_kind is api.HostSourceKind.UNKNOWN
    assert result.envelope.trust_level is api.HostTrustLevel.UNKNOWN
    assert result.envelope.host.kind.value == "UNKNOWN"
    assert any(item.code.value == "UNKNOWN_ENUM_DOWNGRADED" for item in result.warnings)


@pytest.mark.parametrize("field", ["probe_id", "source_domain_id", "observed_at", "host"])
def test_missing_required_fields_are_invalid(field: str):
    api = _host_api()
    payload = _valid_payload()
    del payload[field]

    result = _import(payload)

    assert result.freshness is api.HostFreshness.INVALID
    assert result.envelope is None
    assert _reason(result) == "MISSING_REQUIRED_FIELD"


def test_future_observation_beyond_clock_skew_is_invalid():
    api = _host_api()
    payload = _valid_payload()
    payload["issued_at"] = "2026-08-02T12:00:06Z"
    payload["observed_at"] = "2026-08-02T12:00:06Z"

    result = _import(payload)

    assert result.freshness is api.HostFreshness.INVALID
    assert _reason(result) == "FUTURE_TIMESTAMP"


def test_observation_later_than_issue_time_is_invalid():
    api = _host_api()
    payload = _valid_payload()
    payload["issued_at"] = "2026-08-02T11:59:54Z"

    result = _import(payload)

    assert result.freshness is api.HostFreshness.INVALID
    assert result.envelope is None
    assert _reason(result) == "OBSERVED_AFTER_ISSUED"


@pytest.mark.parametrize(
    ("ttl", "reason"),
    [(-1, "INVALID_TTL"), (301, "TTL_TOO_LONG")],
)
def test_invalid_ttl_values_are_rejected(ttl: int, reason: str):
    api = _host_api()
    payload = _valid_payload()
    payload["ttl_seconds"] = ttl

    result = _import(payload)

    assert result.freshness is api.HostFreshness.INVALID
    assert _reason(result) == reason


def test_expires_at_is_accepted_as_the_ttl_boundary():
    payload = _valid_payload()
    del payload["ttl_seconds"]
    payload["expires_at"] = "2026-08-02T12:00:55Z"

    result = _import(payload)

    assert result.envelope is not None
    assert result.envelope.ttl_seconds == 60
    assert result.envelope.expires_at == datetime(2026, 8, 2, 12, 0, 55, tzinfo=UTC)


def test_conflicting_expires_at_and_ttl_is_invalid():
    api = _host_api()
    payload = _valid_payload()
    payload["expires_at"] = "2026-08-02T12:00:54Z"

    result = _import(payload)

    assert result.freshness is api.HostFreshness.INVALID
    assert result.envelope is None
    assert _reason(result) == "TTL_MISMATCH"


def test_envelope_model_rejects_direct_temporal_invariant_violations():
    result = _import(_valid_payload())
    assert result.envelope is not None

    with pytest.raises(ValueError, match="observed_at must not be later"):
        replace(
            result.envelope,
            observed_at=datetime(2026, 8, 2, 11, 59, 59, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="expires_at must equal"):
        replace(
            result.envelope,
            expires_at=datetime(2026, 8, 2, 12, 0, 54, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "secret",
        "AccessToken",
        "API-KEY",
        "Pass_Word",
        "dockerCredential",
        "EnVironMent",
        "e-n-v",
        "Command-Line",
        "CMD_line",
        "PrivateKey",
        "REMOTE-url",
    ],
)
def test_sensitive_field_separator_and_case_variants_are_rejected(sensitive_key: str):
    payload = _valid_payload()
    payload["evidence"][0]["value"][sensitive_key] = "DO_NOT_PERSIST_8f36"

    result = _import(payload)

    assert result.envelope is not None
    encoded = json.dumps(result.envelope.to_dict(), sort_keys=True)
    assert "DO_NOT_PERSIST_8f36" not in encoded
    assert sensitive_key not in encoded
    assert any(item.code.value == "SENSITIVE_FIELD_REJECTED" for item in result.warnings)


def test_sensitive_keys_hidden_inside_unknown_objects_are_scanned_before_ignore():
    payload = _valid_payload()
    payload["future_extension"] = {
        "nested": {
            "Api-Key": "DO_NOT_PERSIST_API",
            "access-token": "DO_NOT_PERSIST_TOKEN",
            "private-key": "DO_NOT_PERSIST_KEY",
            "remote-url": "https://private.invalid/DO_NOT_PERSIST_URL",
            "command-line": "tool --secret DO_NOT_PERSIST_COMMAND",
        }
    }

    result = _import(payload)

    assert result.envelope is not None
    encoded = json.dumps(result.to_dict(), sort_keys=True).upper()
    assert "DO_NOT_PERSIST" not in encoded
    assert "FUTURE_EXTENSION" not in encoded
    categories = {
        item.field_category
        for item in result.warnings
        if item.code.value == "SENSITIVE_FIELD_REJECTED"
    }
    assert categories == {"API_KEY", "TOKEN", "PRIVATE_KEY", "REMOTE_URL", "COMMAND_LINE"}


def test_bound_imported_evidence_is_capped_at_imported_fact_reliability():
    result = _import(_valid_payload())

    assert result.envelope is not None
    host_os = next(
        item
        for item in result.envelope.evidence
        if item.evidence_id == "host:probe-001:host-os"
    )
    assert host_os.reliability.value == "MEDIUM"
    assert host_os.confidence == 0.7


def test_remote_url_in_a_string_is_redacted_with_a_machine_warning():
    payload = _valid_payload()
    payload["host"]["label"] = "Host https://user:pass@example.invalid/private"

    result = _import(payload)

    assert result.envelope is not None
    encoded = json.dumps(result.envelope.to_dict(), sort_keys=True)
    assert "https://" not in encoded
    assert "user:pass" not in encoded
    assert any(item.code.value == "REMOTE_URL_REJECTED" for item in result.warnings)


def test_damaged_json_and_oversized_input_fail_closed_without_echoing_content():
    api = _host_api()
    malformed = _import('{"token":"DO_NOT_ECHO"')
    oversized = _import("x" * 65, max_input_bytes=64)

    assert malformed.freshness is api.HostFreshness.INVALID
    assert oversized.freshness is api.HostFreshness.INVALID
    assert _reason(malformed) == "INVALID_JSON"
    assert _reason(oversized) == "INPUT_TOO_LARGE"
    encoded = json.dumps(
        {"malformed": malformed.to_dict(), "oversized": oversized.to_dict()},
        sort_keys=True,
    )
    assert "DO_NOT_ECHO" not in encoded
    assert "token" not in encoded.lower()


def test_imported_errors_keep_only_structured_sanitized_semantics():
    payload = deepcopy(_valid_payload())
    payload["errors"] = [
        {
            "code": "PERMISSION_DENIED",
            "message": "token DO_NOT_ECHO from https://private.invalid/path",
            "collector": "host_helper",
            "source": "raw-source",
            "retryable": False,
            "details": {"reason_code": "HOST_PERMISSION_DENIED", "secret": "DO_NOT_ECHO"},
        }
    ]

    result = _import(payload)

    assert result.envelope is not None
    encoded = json.dumps(result.envelope.to_dict(), sort_keys=True)
    assert "DO_NOT_ECHO" not in encoded
    assert "https://" not in encoded
    assert result.envelope.errors[0].details == {"reason_code": "HOST_PERMISSION_DENIED"}


def test_unknown_remote_error_reason_is_not_reflected_into_the_envelope():
    payload = deepcopy(_valid_payload())
    payload["errors"] = [
        {
            "code": "ERROR",
            "message": "ignored",
            "details": {"reason_code": "DO_NOT_PERSIST_52bd"},
        }
    ]

    result = _import(payload)

    assert result.envelope is not None
    encoded = json.dumps(result.envelope.to_dict(), sort_keys=True)
    assert "DO_NOT_PERSIST_52bd" not in encoded
    assert result.envelope.errors[0].details == {
        "reason_code": "HOST_PROBE_REPORTED_ERROR"
    }


def test_host_docker_availability_is_observation_not_authorization():
    result = _import(_valid_payload())

    assert result.envelope is not None
    docker = result.envelope.capabilities.get("docker_daemon")
    assert docker.status.value == "AVAILABLE"
    assert docker.reason_code == "DAEMON_AVAILABLE"
    encoded = json.dumps(result.envelope.to_dict(), sort_keys=True).upper()
    assert "ALLOW" not in encoded
    assert "AUTHORIZED" not in encoded


def test_unknown_capability_reason_is_replaced_by_stable_generic_code():
    payload = _valid_payload()
    payload["capabilities"]["docker_daemon"]["reason_code"] = "DO_NOT_PERSIST_73af"

    result = _import(payload)

    assert result.envelope is not None
    assessment = result.envelope.capabilities.get("docker_daemon")
    assert assessment.reason_code == "HOST_PROBE_CAPABILITY_REPORTED"
    assert "DO_NOT_PERSIST_73af" not in json.dumps(result.envelope.to_dict())


def test_public_discovery_namespace_exports_host_probe_contracts():
    from agentguard import discovery

    assert discovery.HostProbeEnvelope is _host_api().HostProbeEnvelope
    assert discovery.HostProbeImporter is _host_api().HostProbeImporter
    assert discovery.HostProbeCache is _host_api().HostProbeCache
    assert discovery.merge_host_probe_snapshot is _host_api().merge_host_probe_snapshot
