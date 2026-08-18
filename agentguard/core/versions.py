"""Software version and local product provenance detection."""

import os
import re
import sys
from pathlib import Path

from .runner import run_command, which

try:
    from ._build_provenance import PRODUCT_SHA as _EMBEDDED_PRODUCT_SHA
except ImportError:  # pragma: no cover - source trees include the placeholder.
    _EMBEDDED_PRODUCT_SHA = None

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
    if result.success and is_exact_git_sha(result.stdout):
        return result.stdout
    if cwd is None and is_exact_git_sha(_EMBEDDED_PRODUCT_SHA):
        return _EMBEDDED_PRODUCT_SHA
    return None


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


def bundled_sidecar_active() -> bool:
    """Whether this process is the packaged sidecar (PyInstaller bundle)."""
    return bool(getattr(sys, "_MEIPASS", None) or getattr(sys, "frozen", False))


def _python_version() -> str | None:
    """Detect the external Python interpreter with truthful per-OS semantics.

    Windows uses real interpreter semantics: the ``py`` launcher first, then
    ``python.exe``. POSIX keeps ``python3`` first, then ``python``. An
    undetected interpreter stays ``None`` (unknown), never a false ABSENT.
    """
    if os.name == "nt":
        candidates = (["py", "-3"], ["python"], ["python3"])
    else:
        candidates = (["python3"], ["python"])
    for cmd in candidates:
        if which(cmd[0]) is None:
            continue
        version = _get_version(cmd)
        if version:
            return version
    return None


def all_versions() -> dict[str, str | None]:
    """Detect all relevant software versions."""
    versions = {
        "docker": _get_version(["docker"]) if which("docker") else None,
        "node": _get_version(["node"]) if which("node") else None,
        "claude": _get_version(["claude"]) if which("claude") else None,
        "cloudcli": _get_version(["cloudcli"]) if which("cloudcli") else None,
        "ccr": _get_version(["ccr"]) if which("ccr") else None,
        "git": _get_version(["git"]) if which("git") else None,
    }
    if bundled_sidecar_active():
        # The packaged runtime is the sidecar's own interpreter; it must be
        # reported distinctly and never conflated with an external Python.
        versions["python_bundled"] = "bundled"
    else:
        versions["python"] = _python_version()
    return versions
