"""Software version detection."""

from typing import Dict, Optional
from .runner import run_command


def _get_version(cmd: list, flag: str = "--version", label: str = "") -> Optional[str]:
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
    except Exception:
        pass
    return None


def all_versions() -> Dict[str, Optional[str]]:
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
