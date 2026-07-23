"""Docker status checks — purely diagnostic, no mutations."""

from typing import Dict, Optional, Tuple
from .runner import run_command, CommandResult, which


def docker_available() -> bool:
    """Check if docker CLI is available and responsive."""
    return which("docker") is not None


def docker_version() -> Optional[str]:
    """Return docker client version string or None."""
    try:
        r = run_command(["docker", "--version"], timeout=10)
        if r.success:
            return r.stdout
    except (FileNotFoundError, PermissionError, RuntimeError):
        pass
    return None


def container_running(name: str = "agent-dev") -> Tuple[bool, Optional[str]]:
    """Check if a named container is running.

    Returns (is_running, container_id_or_status_info).
    """
    if not docker_available():
        return False, "Docker not available"

    try:
        r = run_command(
            ["docker", "ps", "--filter", f"name={name}", "--format", "{{.ID}} {{.Status}}"],
            timeout=10,
        )
        if r.success and r.stdout:
            return True, r.stdout.split("\n")[0].strip()
        return False, "Not running"
    except (FileNotFoundError, PermissionError, RuntimeError) as e:
        return False, str(e)


def container_info(name: str = "agent-dev") -> Dict[str, str]:
    """Return container metadata dict."""
    info: Dict[str, str] = {}
    if not docker_available():
        return {"error": "Docker not available"}

    try:
        r = run_command([
            "docker", "inspect", name,
            "--format", "{{.State.Status}}|{{.State.StartedAt}}|{{.Config.Image}}|{{.HostConfig.Privileged}}",
        ], timeout=10)
        if r.success and r.stdout:
            parts = r.stdout.split("|")
            info["status"] = parts[0] if len(parts) > 0 else "unknown"
            info["started_at"] = parts[1] if len(parts) > 1 else ""
            info["image"] = parts[2] if len(parts) > 2 else ""
            info["privileged"] = parts[3] if len(parts) > 3 else ""
    except Exception:
        info["error"] = "Inspect failed"
    return info
