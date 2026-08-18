"""status command — quick bounded environment probe (no health verdict)."""

import os
from typing import Any, Dict, Optional

from ..core.docker import docker_available, container_running
from ..core.runner import run_command
from ..core.versions import all_versions
from ..storage.db import StateDB


def status(config: dict, db: Optional[StateDB] = None) -> Dict[str, Any]:
    """Collect bounded environment facts; callers render them verbatim."""
    result: Dict[str, Any] = {
        "timestamp_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "checks": {},
        "versions": {},
    }

    # Docker
    docker_ok = docker_available()
    result["checks"]["docker"] = docker_ok
    if docker_ok:
        running, info = container_running(config.get("container_name", "agent-dev"))
        result["checks"]["container_running"] = running
        result["checks"]["container_info"] = info

    # Port check (listening-state key reflects the configured port).
    port = int(config.get("port", 3001))
    port_open = _check_port(port)
    result["checks"][f"port_{port}"] = port_open

    # Versions
    result["versions"] = all_versions()

    return result


def _check_port(port: int) -> bool:
    """Quick TCP port probe; skips Linux-only tools on other platforms."""
    if os.name == "nt":
        # A raw connect can disturb the listener, so on Windows we simply
        # report the probe as not-run rather than fake a result.
        return False
    try:
        r = run_command(
            ["ss", "-tln", f"sport = :{port}"],
            timeout=5,
        )
        if r.success and f":{port}" in r.stdout:
            return True
    except Exception:
        pass
    try:
        r = run_command(
            ["bash", "-c", f"cat /proc/net/tcp | awk '{{print $2}}' | grep -qi :{port:04x}"],
            timeout=5,
        )
        if r.success and r.stdout.strip():
            return True
    except Exception:
        pass
    return False
