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


def _registry_text(hive, key: str, value_name: str = "") -> str | None:
    """Read one Windows registry string without turning errors into facts."""
    if _winreg is None:
        return None
    try:
        with _winreg.OpenKey(hive, key) as handle:
            raw, _kind = _winreg.QueryValueEx(handle, value_name)
    except OSError:
        return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.strip()


def _registry_subkeys(hive, key: str) -> list[str]:
    if _winreg is None:
        return []
    try:
        with _winreg.OpenKey(hive, key) as handle:
            names: list[str] = []
            index = 0
            while True:
                try:
                    names.append(_winreg.EnumKey(handle, index))
                except OSError:
                    break
                index += 1
            return names
    except OSError:
        return []


def _registered_product_values(display_name: str) -> list[dict[str, str]]:
    """Return bounded uninstall registration fields for one exact product."""
    if _winreg is None:
        return []
    roots = (
        (_winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (_winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (
            _winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
    )
    matches: list[dict[str, str]] = []
    for hive, root in roots:
        for child in _registry_subkeys(hive, root):
            key = rf"{root}\{child}"
            if _registry_text(hive, key, "DisplayName") != display_name:
                continue
            values: dict[str, str] = {}
            for field in ("InstallLocation", "DisplayVersion"):
                if value := _registry_text(hive, key, field):
                    values[field] = value
            matches.append(values)
    return matches


def _windows_registered_tool_paths(name: str) -> list[str]:
    """Bounded registered executable locations for supported Windows tools.

    These are installation authorities Windows already owns: App Paths,
    PEP 514, exact product registrations, and the current Codex MSIX package
    repository. Stale registrations are candidates only; final resolution
    still requires an existing regular file.
    """
    if not platform_is_windows() or _winreg is None:
        return []
    normalized = PureWindowsPath(name).stem.casefold()
    executable = name if PureWindowsPath(name).suffix else f"{name}.exe"
    candidates: list[str] = []

    app_path_roots = (
        (_winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (_winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (
            _winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths",
        ),
    )
    for hive, root in app_path_roots:
        if value := _registry_text(hive, rf"{root}\{executable}"):
            candidates.append(value.strip('"'))

    if normalized in {"python", "python3", "py"}:
        python_roots = (
            (_winreg.HKEY_CURRENT_USER, r"SOFTWARE\Python\PythonCore"),
            (_winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Python\PythonCore"),
            (_winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Python\PythonCore"),
        )
        for hive, root in python_roots:
            for version in _registry_subkeys(hive, root):
                install_key = rf"{root}\{version}\InstallPath"
                if value := _registry_text(hive, install_key, "ExecutablePath"):
                    candidates.append(value)
                if value := _registry_text(hive, install_key):
                    candidates.append(str(PureWindowsPath(value) / "python.exe"))

    if normalized == "git":
        git_roots = (
            (_winreg.HKEY_CURRENT_USER, r"SOFTWARE\GitForWindows"),
            (_winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\GitForWindows"),
            (_winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\GitForWindows"),
        )
        for hive, key in git_roots:
            if value := _registry_text(hive, key, "InstallPath"):
                candidates.append(str(PureWindowsPath(value) / "cmd" / "git.exe"))
        for values in _registered_product_values("GitHub Desktop"):
            install = values.get("InstallLocation")
            version = values.get("DisplayVersion")
            if install and version:
                candidates.append(
                    str(
                        PureWindowsPath(install)
                        / f"app-{version}"
                        / "resources"
                        / "app"
                        / "git"
                        / "cmd"
                        / "git.exe"
                    )
                )

    if normalized == "docker":
        for values in _registered_product_values("Docker Desktop"):
            if install := values.get("InstallLocation"):
                candidates.append(
                    str(PureWindowsPath(install) / "resources" / "bin" / "docker.exe")
                )

    if normalized == "codex":
        package_root = (
            r"Software\Classes\Local Settings\Software\Microsoft\Windows"
            r"\CurrentVersion\AppModel\Repository\Packages"
        )
        for child in _registry_subkeys(_winreg.HKEY_CURRENT_USER, package_root):
            if not child.casefold().startswith("openai.codex_"):
                continue
            key = rf"{package_root}\{child}"
            if value := _registry_text(
                _winreg.HKEY_CURRENT_USER, key, "PackageRootFolder"
            ):
                candidates.append(
                    str(PureWindowsPath(value) / "app" / "resources" / "codex.exe")
                )

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        marker = candidate.casefold()
        if marker not in seen:
            seen.add(marker)
            unique.append(candidate)
    return unique


def windows_registered_tool_version(name: str) -> str | None:
    """Return an authoritative registered version for a resolved tool.

    The only current fallback is the Codex MSIX package version. WindowsApps
    can permit metadata reads while denying direct execution of the packaged
    CLI, so the package repository is the truthful version authority in that
    case. Callers must still require a resolved executable before using this
    fallback; a stale registration alone never proves installation.
    """
    if not platform_is_windows() or _winreg is None:
        return None
    if PureWindowsPath(name).stem.casefold() != "codex":
        return None
    package_root = (
        r"Software\Classes\Local Settings\Software\Microsoft\Windows"
        r"\CurrentVersion\AppModel\Repository\Packages"
    )
    prefix = "openai.codex_"
    for child in _registry_subkeys(_winreg.HKEY_CURRENT_USER, package_root):
        folded = child.casefold()
        if not folded.startswith(prefix):
            continue
        version = child[len(prefix) :].split("_", 1)[0]
        components = version.split(".")
        if len(components) == 4 and all(part.isdigit() for part in components):
            return f"Codex {version}"
    return None


def _resolve_registered_candidates(paths: list[str]) -> str | None:
    for entry in paths:
        cleaned = os.path.expandvars(entry.strip().strip('"'))
        if not cleaned:
            continue
        try:
            candidate = Path(cleaned)
            if candidate.is_file():
                return str(candidate)
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
    found = _resolve_candidates(name, list(paths)) if paths is not None else None
    if found is None and search is None and platform_is_windows():
        found = _resolve_registered_candidates(_windows_registered_tool_paths(name))
    if found is not None:
        return ToolResolution(HOST_INSTALLED, sidecar_path is not None, found)
    if paths is None:
        return ToolResolution(UNKNOWN, sidecar_path is not None, None)
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
    "windows_registered_tool_version",
]
