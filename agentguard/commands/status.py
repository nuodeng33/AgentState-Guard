"""status command — quick bounded environment probe (no health verdict)."""

from typing import Any

from ..core.docker import docker_available
from ..core.versions import all_versions
from ..discovery.capabilities import CapabilityStatus
from ..discovery.domains.docker_host import DockerHostProbe
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

    # Docker
    docker_ok = docker_available()
    result["checks"]["docker"] = docker_ok
    if docker_ok:
        try:
            listing = DockerHostProbe().list_targets()
            listed = listing.status in {CapabilityStatus.AVAILABLE, CapabilityStatus.DEGRADED}
            result["checks"]["container_running"] = bool(listing.targets) if listed else (
                False if listing.reason_code == "DOCKER_TARGETS_EMPTY" else None)
            names = "; ".join(f"{target.name}: {target.status}" for target in listing.targets)
            result["checks"]["container_info"] = names or listing.reason_code
        except (OSError, RuntimeError, ValueError):
            result["checks"]["container_running"] = None
            result["checks"]["container_info"] = "DOCKER_TARGET_LIST_FAILED"

    # Port check
    port = int(config.get("port", 3001))
    port_open = _check_port(port)
    result["checks"]["port_3001"] = port_open

    # Versions
    result["versions"] = all_versions()

    return result


def _check_port(port: int) -> bool:
    """Check only the configured loopback service, on every supported host OS."""
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except (OSError, OverflowError, ValueError):
        return False
