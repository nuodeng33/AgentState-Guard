"""Compatibility tests for the legacy host-import command."""

import json

from agentguard.commands.host_import import cmd_host_import


def test_legacy_host_import_preserves_allowlist_and_is_explicitly_degraded():
    payload = {
        "docker_version": "27.0",
        "docker_available": True,
        "hostname": "controlled-host",
        "unknown_field": "discarded",
    }

    result = cmd_host_import(json.dumps(payload))

    assert result["status"] == "success"
    assert result["host_data"] == {
        "docker_version": "27.0",
        "docker_available": True,
        "hostname": "controlled-host",
    }
    assert result["fields_rejected"] == 1
    assert result["discovery_import"] == {
        "schema_version": "legacy-1",
        "status": "DEGRADED",
        "reason_code": "LEGACY_HOST_IMPORT_UNBOUND",
        "source_kind": "HOST_PROBE",
        "trust_level": "UNVERIFIED",
        "source_binding_id": None,
        "source_authenticated": False,
        "imported": True,
        "self_visible": False,
        "warnings": [{"code": "LEGACY_INPUT_DEGRADED"}],
    }


def test_legacy_host_import_does_not_echo_disallowed_sensitive_values():
    result = cmd_host_import(
        json.dumps(
            {
                "hostname": "controlled-host",
                "api-key": "DO_NOT_ECHO",
                "command_line": "tool --token DO_NOT_ECHO",
            }
        )
    )

    encoded = json.dumps(result, sort_keys=True)
    assert "DO_NOT_ECHO" not in encoded
    assert "api-key" not in encoded
    assert "command_line" not in encoded
