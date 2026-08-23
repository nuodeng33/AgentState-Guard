"""Docker status checks — purely diagnostic, no mutations."""


from . import host_tools
from .runner import run_command, which

DOCKER_UNKNOWN = "UNKNOWN"


def docker_available() -> bool:
    """Check if docker CLI is available and responsive."""
    return resolved_docker_executable() is not None


def resolved_docker_executable() -> str | None:
    """Return one absolute Docker CLI identity for host-side operations."""
    resolution = host_tools.host_tool_resolution("docker")
    if resolution.path is not None:
        return resolution.path
    if resolution.sidecar_callable:
        return which("docker")
    return None


def docker_presence() -> bool | str:
    """Host-native Docker presence with truthful tri-state semantics.

    Returns ``True`` (host-resolved or sidecar-callable), ``False`` (both
    host-native sources and the sidecar PATH agree it is absent), or the
    ``DOCKER_UNKNOWN`` marker when the host environment could not be probed.
    A sidecar PATH miss is never reported as an authoritative host absence.
    """
    resolution = host_tools.host_tool_resolution("docker")
    if resolution.presence == host_tools.UNKNOWN:
        return DOCKER_UNKNOWN
    return bool(resolution.path or resolution.sidecar_callable)


def docker_version() -> str | None:
    """Return docker client version string or None."""
    executable = resolved_docker_executable()
    if executable is None:
        return None
    try:
        r = run_command([executable, "--version"], timeout=10)
        if r.success:
            return r.stdout
    except (FileNotFoundError, PermissionError, RuntimeError):
        pass
    return None


def container_running(name: str = "agent-dev") -> tuple[bool, str | None]:
    """Check if a named container is running.

    Returns (is_running, container_id_or_status_info).
    """
    executable = resolved_docker_executable()
    if executable is None:
        return False, "Docker not available"

    try:
        r = run_command(
            [
                executable,
                "ps",
                "--filter",
                f"name={name}",
                "--format",
                "{{.ID}} {{.Status}}",
            ],
            timeout=10,
        )
        if r.success and r.stdout:
            return True, r.stdout.split("\n")[0].strip()
        return False, "Not running"
    except (FileNotFoundError, PermissionError, RuntimeError) as e:
        return False, str(e)


def container_info(name: str = "agent-dev") -> dict[str, str]:
    """Return container metadata dict."""
    info: dict[str, str] = {}
    executable = resolved_docker_executable()
    if executable is None:
        return {"error": "Docker not available"}

    try:
        r = run_command([
            executable,
            "inspect",
            name,
            "--format",
            "{{.State.Status}}|{{.State.StartedAt}}|{{.Config.Image}}|{{.HostConfig.Privileged}}",
        ], timeout=10)
        if r.success and r.stdout:
            parts = r.stdout.split("|")
            info["status"] = parts[0] if len(parts) > 0 else "unknown"
            info["started_at"] = parts[1] if len(parts) > 1 else ""
            info["image"] = parts[2] if len(parts) > 2 else ""
            info["privileged"] = parts[3] if len(parts) > 3 else ""
    except (FileNotFoundError, PermissionError, RuntimeError):
        info["error"] = "Inspect failed"
    return info
