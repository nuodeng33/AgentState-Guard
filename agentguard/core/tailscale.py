"""Tailscale status checks — read-only, no mutations."""

from typing import Optional, Tuple
from .runner import run_command, which


def tailscale_available() -> bool:
    """Check if tailscale CLI is available."""
    return which("tailscale") is not None


def tailscale_status() -> Tuple[bool, Optional[str]]:
    """Check Tailscale status.

    Returns (is_connected, status_string).
    """
    if not tailscale_available():
        return False, "Tailscale CLI not found"

    try:
        r = run_command(["tailscale", "status", "--json"], timeout=10)
        if r.success:
            # Parse minimal JSON to check if connected
            if '"Online":true' in r.stdout or '"BackendState":"Running"' in r.stdout:
                return True, "Connected"
            return False, "Not connected"
        return False, r.stderr or "Unknown"
    except (FileNotFoundError, PermissionError, RuntimeError) as e:
        return False, str(e)


def tailscale_ip() -> Optional[str]:
    """Return Tailscale IP if connected."""
    try:
        r = run_command(["tailscale", "ip", "-4"], timeout=5)
        if r.success and r.stdout:
            return r.stdout.strip()
    except Exception:
        pass
    return None
