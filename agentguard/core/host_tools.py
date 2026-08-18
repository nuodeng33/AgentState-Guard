"""Host-native tool resolution with explicit presence semantics.

The packaged sidecar inherits only the GUI session's PATH, which can be a
strict subset of the real Windows host PATH (machine/user environment).
"the sidecar PATH can't find it" must never be reported as "the host does
not have it".

Presence levels:
- ``HOST_INSTALLED``: resolvable from host-native environment sources
  (Windows registry union PATH, or the process PATH on POSIX).
- ``SIDECAR_ONLY``: resolvable host-native sources were searched, the tool
  was not there, but the sidecar's own PATH can invoke it.
- ``HOST_NOT_FOUND``: host-native sources resolved fully and the tool is
  absent (an authoritative absence; sidecar cannot call it either).
- ``UNKNOWN``: the probe could not complete; nothing about presence is
  claimed.

Resolved absolute paths stay inside this process: they are used to execute
bounded version probes only and are never serialized into DTOs, evidence,
logs, or the database.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .runner import which

if sys.platform == "win32":  # pragma: no cover - exercised only on Windows
    import winreg as _winreg
else:
    _winreg = None  # type: ignore[assignment]

HOST_INSTALLED = "HOST_INSTALLED"
SIDECAR_ONLY = "SIDECAR_ONLY"
HOST_NOT_FOUND = "HOST_NOT_FOUND"
UNKNOWN = "UNKNOWN"

_PATHEXT_DEFAULT = ".COM;.EXE;.BAT;.CMD"


@dataclass(frozen=True)
class ToolResolution:
    presence: str
    sidecar_callable: bool
    path: str | None  # internal only; must never be serialized


def platform_is_windows() -> bool:
    """Central platform seam (patchable in tests without touching ``os``)."""
    return os.name == "nt"


def _host_environment_path() -> str | None:
    """Union of user + machine PATH stored in the Windows registry.

    Returns ``None`` when no host-environment PATH could be read at all.
    """
    if not platform_is_windows():
        return os.environ.get("PATH")
    if _winreg is None:
        return os.environ.get("PATH")
    parts: list[str] = []
    for hive, key in (
        (_winreg.HKEY_CURRENT_USER, r"Environment"),
        (
            _winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    ):
        try:
            with _winreg.OpenKey(hive, key) as handle:
                raw, _kind = _winreg.QueryValueEx(handle, "Path")
        except OSError:
            continue
        if isinstance(raw, str) and raw.strip():
            parts.append(raw)
    if not parts:
        return None
    return ";".join(parts)


def host_search_path(extra: list[str] | None = None) -> list[str] | None:
    """Host-native search path entries; ``None`` when unresolvable."""
    raw = _host_environment_path()
    if raw is None:
        return None
    separator = ";" if platform_is_windows() else os.pathsep
    paths = [entry for entry in raw.split(separator) if entry.strip()]
    if extra:
        paths.extend(entry for entry in extra if entry)
    return paths


def _expand_names(name: str) -> list[str]:
    """Executable name candidates honoring Windows PATHEXT resolution."""
    if not platform_is_windows() or PureWindowsPath(name).suffix:
        return [name]
    pathext = os.environ.get("PATHEXT", _PATHEXT_DEFAULT)
    return [name + ext.casefold() for ext in pathext.casefold().split(";")]


def _resolve_candidates(name: str, paths: list[str]) -> str | None:
    names = _expand_names(name)
    for entry in paths:
        cleaned = os.path.expandvars(entry.strip().strip('"'))
        if not cleaned:
            continue
        base = Path(cleaned)
        for candidate in names:
            full = base / candidate
            try:
                if full.is_file():
                    return str(full)
            except OSError:
                continue
    return None


def host_tool_resolution(
    name: str,
    *,
    search: list[str] | None = None,
) -> ToolResolution:
    """Resolve *name* against host-native sources without guessing.

    ``search`` overrides the search path (bounded injection point for tests).
    ``presence`` is one of HOST_INSTALLED / SIDECAR_ONLY / HOST_NOT_FOUND /
    UNKNOWN.
    """
    sidecar_path = which(name)
    paths = search if search is not None else host_search_path()
    if paths is None:
        return ToolResolution(UNKNOWN, sidecar_path is not None, None)
    found = _resolve_candidates(name, list(paths))
    if found is not None:
        return ToolResolution(HOST_INSTALLED, sidecar_path is not None, found)
    if sidecar_path is not None:
        return ToolResolution(SIDECAR_ONLY, True, None)
    return ToolResolution(HOST_NOT_FOUND, False, None)


__all__ = [
    "HOST_INSTALLED",
    "HOST_NOT_FOUND",
    "SIDECAR_ONLY",
    "UNKNOWN",
    "ToolResolution",
    "host_search_path",
    "host_tool_resolution",
    "platform_is_windows",
]
