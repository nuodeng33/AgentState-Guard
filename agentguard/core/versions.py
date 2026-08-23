"""Software version and local product provenance detection."""

import re
import sys
from pathlib import Path

from .host_tools import (
    host_tool_resolution,
    platform_is_windows,
    windows_registered_tool_version,
)
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


def _resolved_command(name: str, extra_args: tuple[str, ...] = ()) -> list[str] | None:
    """Host-native executable command for *name*, or ``None`` if unresolvable.

    Prefers the host-native resolved absolute path; falls back to the
    sidecar's own PATH entry only when the tool is genuinely sidecar-only.
    The absolute path is used only for this bounded version probe and is
    never serialized into any DTO.
    """
    resolution = host_tool_resolution(name)
    if resolution.path is not None:
        base = [resolution.path]
    elif resolution.sidecar_callable:
        sidecar_path = which(name)
        if sidecar_path is None:
            return None
        base = [sidecar_path]
    else:
        return None
    return [*base, *extra_args]


def _external_python() -> str | None:
    """Detect the external interpreter with truthful per-OS semantics.

    Windows uses real interpreter semantics (the ``py`` launcher first, then
    ``python.exe``) resolved against the *host* environment — the sidecar's
    inherited PATH alone never decides truth. POSIX keeps ``python3`` first,
    then ``python``. An undetected interpreter stays ``None`` (unknown),
    never a false ABSENT.
    """
    if platform_is_windows():
        candidates = (("py", ("-3",)), ("python", ()), ("python3", ()))
    else:
        candidates = (("python3", ()), ("python", ()))
    for name, extra in candidates:
        cmd = _resolved_command(name, extra)
        if cmd is None:
            continue
        version = _get_version(cmd)
        if version:
            return version
    return None


def all_versions() -> dict[str, str | None]:
    """Detect all relevant software versions.

    The Agent CLIs the product documentedly supports (Claude Code, Codex,
    Kimi Code) are probed truthfully per platform. version-unavailability
    stays ``None`` (unknown); the bundled sidecar runtime is reported
    distinctly and never replaces the external probe.
    """
    versions: dict[str, str | None] = {}
    for name in ("docker", "node", "git"):
        cmd = _resolved_command(name)
        versions[name] = _get_version(cmd) if cmd is not None else None
    versions["python"] = _external_python()
    if bundled_sidecar_active():
        # The packaged runtime is the sidecar's own interpreter, reported
        # distinctly and never conflated with an external Python.
        versions["python_bundled"] = "bundled"
    if platform_is_windows():
        for name in ("claude", "codex", "kimi"):
            cmd = _resolved_command(name)
            version = _get_version(cmd) if cmd is not None else None
            if name == "codex" and cmd is not None and version is None:
                version = windows_registered_tool_version(name)
            versions[name] = version
    else:
        for name in ("claude", "cloudcli", "ccr"):
            cmd = _resolved_command(name)
            versions[name] = _get_version(cmd) if cmd is not None else None
    return versions
