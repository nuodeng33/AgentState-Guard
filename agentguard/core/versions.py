"""Software version and local product provenance detection."""

import re
from pathlib import Path

from .runner import run_command

_EXACT_GIT_SHA = re.compile(r"[0-9a-f]{40}")


def is_exact_git_sha(value: object) -> bool:
    """Return whether *value* is a canonical full Git object ID."""
    return isinstance(value, str) and _EXACT_GIT_SHA.fullmatch(value) is not None


def exact_product_sha(cwd: Path | None = None) -> str | None:
    """Resolve the server checkout SHA without accepting request data."""
    repository = cwd or Path(__file__).resolve().parents[2]
    try:
        result = run_command(["git", "rev-parse", "HEAD"], timeout=5, cwd=repository)
    except (FileNotFoundError, PermissionError):
        return None
    return result.stdout if result.success and is_exact_git_sha(result.stdout) else None


def _get_version(cmd: list, flag: str = "--version", label: str = "") -> str | None:
    """Run cmd with --version, return first line with digits."""
    try:
        r = run_command(cmd + [flag], timeout=10)
        if r.success and r.stdout:
            # Take first non-empty line
            for line in r.stdout.split("\n"):
                line = line.strip()
                if line and any(c.isdigit() for c in line[:20]):
                    return line
            return r.stdout.split("\n")[0].strip()
    except Exception:  # noqa: BLE001, S110 - optional external version probes degrade to None.
        pass
    return None


def all_versions() -> dict[str, str | None]:
    """Detect all relevant software versions."""
    return {
        "docker": _get_version(["docker"]),
        "node": _get_version(["node"]),
        "python": _get_version(["python3"]),
        "claude": _get_version(["claude"]),
        "cloudcli": _get_version(["cloudcli"]),
        "ccr": _get_version(["ccr"]),
        "tailscale": _get_version(["tailscale"]),
        "git": _get_version(["git"]),
    }
