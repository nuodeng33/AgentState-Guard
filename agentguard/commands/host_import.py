"""host-import — import host-originated structured state via stdin.

First version: receives JSON from Windows PowerShell or Linux host scripts.
Only allows a whitelisted set of fields for safety.
"""

import json
import sys

from ..core.sanitizer import sanitize_text

ALLOWED_HOST_FIELDS = {
    "docker_version", "docker_available",
    "containers",  # list of {name, running, status, privileged, cap_drop, security_opt, mounts}
    "ports",       # list of {port, listening}
    "tailscale_running", "tailscale_ip", "tailscale_online",
    "timestamp_utc", "hostname", "os",
}

ALLOWED_CONTAINER_FIELDS = {
    "name", "running", "status", "privileged", "cap_drop",
    "security_opt", "mounts",
}

ALLOWED_MOUNT_FIELDS = {"source", "destination", "mode"}


def cmd_host_import(stdin_data: str | None = None) -> dict[str, object]:
    """Import host state from stdin JSON.

    Validates all fields against the allowlist. Rejects any disallowed fields.
    """
    if stdin_data is None:
        stdin_data = sys.stdin.read()

    if not stdin_data or not stdin_data.strip():
        return {"status": "error", "message": "No data received on stdin"}

    try:
        data = json.loads(stdin_data)
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"Invalid JSON: {e}"}

    if not isinstance(data, dict):
        return {"status": "error", "message": "Expected a JSON object"}

    # Validate and strip
    cleaned = _validate_host_data(data)

    # Write to host_state marker
    return {
        "status": "success",
        "message": "Host state imported",
        "host_data": cleaned,
        "fields_received": len(cleaned),
        "fields_rejected": len(data) - len(cleaned),
        "discovery_import": {
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
        },
    }


def _validate_host_data(raw: dict) -> dict[str, object]:
    """Strip disallowed fields and validate allowed ones."""
    result: dict[str, object] = {}

    for key, value in raw.items():
        if key not in ALLOWED_HOST_FIELDS:
            continue  # silently drop disallowed fields

        if key == "containers":
            if isinstance(value, list):
                result[key] = [_validate_container(c) for c in value if isinstance(c, dict)]
        elif key == "ports":
            if isinstance(value, list):
                result[key] = [
                    {"port": p.get("port"), "listening": bool(p.get("listening", False))}
                    for p in value if isinstance(p, dict)
                ]
        elif isinstance(value, str):
            result[key] = sanitize_text(value)
        elif isinstance(value, (int, float, bool)):
            result[key] = value
        else:
            result[key] = str(value)

    return result


def _validate_container(c: dict) -> dict[str, object]:
    """Validate and sanitize a container entry."""
    result: dict[str, object] = {}
    for key, value in c.items():
        if key not in ALLOWED_CONTAINER_FIELDS:
            continue
        if key == "mounts" and isinstance(value, list):
            result[key] = [
                {k: v for k, v in m.items() if k in ALLOWED_MOUNT_FIELDS}
                for m in value if isinstance(m, dict)
            ]
        elif isinstance(value, (str, int, float, bool)):
            result[key] = value
    return result
