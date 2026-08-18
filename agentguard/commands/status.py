"""status command — quick bounded environment probe (no health verdict)."""

from typing import Any

from ..core import host_tools
from ..core.docker import DOCKER_UNKNOWN, container_running, docker_presence
from ..core.runner import run_command
from ..core.versions import all_versions
from ..storage.db import StateDB


def status(config: dict, db: StateDB | None = None) -> dict[str, Any]:
    """Collect bounded environment facts; callers render them verbatim."""
    result: dict[str, Any] = {
        "timestamp_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "checks": {},
        "versions": {},
    }

    # Docker — host-native presence with truthful tri-state semantics.
    presence = docker_presence()
    if presence is DOCKER_UNKNOWN:
        # The host environment could not be probed; never a false ABSENT.
        result["checks"]["docker"] = None
    else:
        result["checks"]["docker"] = bool(presence)
    if presence is True:
        running, info = container_running(config.get("container_name", "agent-dev"))
        result["checks"]["container_running"] = running
        result["checks"]["container_info"] = info

    # Port probe exists only where there is a real, product-owned port to
    # check. The historical CloudCLI port-3001 fact is not probed on the
    # V1 host surface, and an unprobed port is never reported as ``false``.
    port = config.get("port")
    if not host_tools.platform_is_windows() and port is not None:
        result["checks"][f"port_{int(port)}"] = _check_port(int(port))

    # Versions
    result["versions"] = all_versions()

    return result



def _check_port(port: int) -> bool | None:
    ss_seen = False
    try:
        r = run_command(
            ["ss", "-tln", f"sport = :{port}"],
            timeout=5,
        )
        ss_seen = True
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
    if ss_seen:
        # ss ran successfully and showed no listener: authoritative not-listening.
        return False
    return None
