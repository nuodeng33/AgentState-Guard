"""status command — quick environment health check."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.docker import docker_available, container_running
from ..core.tailscale import tailscale_available, tailscale_status
from ..core.runner import run_command
from ..core.versions import all_versions
from ..storage.db import StateDB


def status(config: dict, db: Optional[StateDB] = None) -> Dict[str, Any]:
    """Run a quick status check of the environment."""
    result: Dict[str, Any] = {
        "timestamp_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "checks": {},
        "versions": {},
        "files": {},
        "drift_detected": False,
    }

    # Docker
    docker_ok = docker_available()
    result["checks"]["docker"] = docker_ok
    if docker_ok:
        running, info = container_running(config.get("container_name", "agent-dev"))
        result["checks"]["container_running"] = running
        result["checks"]["container_info"] = info

    # Port check
    port = int(config.get("port", 3001))
    port_open = _check_port(port)
    result["checks"]["port_3001"] = port_open

    # Tailscale
    ts_ok = tailscale_available()
    result["checks"]["tailscale_available"] = ts_ok
    if ts_ok:
        connected, msg = tailscale_status()
        result["checks"]["tailscale_connected"] = connected

    # Versions
    result["versions"] = all_versions()

    # Key config files
    key_files = [
        "/home/agent/.claude/settings.json",
    ]
    for f in key_files:
        p = Path(f)
        result["files"][f] = {
            "exists": p.is_file() if p else False,
        }

    # Check for drift vs latest checkpoint
    if db is not None:
        cps = db.list_checkpoints(limit=1)
        result["drift_detected"] = len(cps) > 0  # placeholder: real diff in diff cmd

    return result


def _check_port(port: int) -> bool:
    """Quick TCP port check using /proc or ss."""
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
