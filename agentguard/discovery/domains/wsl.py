"""WSL distribution facts and path-boundary helpers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

from ..command_runner import validate_distro_name


class WslDistroState(str, Enum):
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class WslDistribution:
    name: str
    state: WslDistroState
    generation: str = "UNKNOWN"
    is_default: bool = False


@dataclass(frozen=True)
class WslListParseResult:
    distros: tuple[WslDistribution, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class WslWorkspaceLocation:
    distro: str
    native_path: str
    windows_display_path: str
    windows_display_only: bool = True


_ROW_RE = re.compile(
    r"^\s*(?P<default>\*)?\s*(?P<name>.+?)\s{2,}"
    r"(?P<state>\S(?:.*?\S)?)\s{2,}(?P<version>\S+)\s*$"
)


def parse_wsl_list(output: str) -> WslListParseResult:
    """Parse `wsl.exe --list --verbose` without treating names as commands."""

    distros: list[WslDistribution] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for raw_line in output.replace("\x00", "").lstrip("\ufeff").splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        match = _ROW_RE.fullmatch(line)
        if match is None:
            warnings.append("INVALID_DISTRO_ENTRY_REJECTED")
            continue
        version = match.group("version")
        if not distros and not version.isdecimal():
            # Column titles are localized; identify the first header by its
            # non-numeric version column rather than English title text.
            continue
        try:
            name = validate_distro_name(match.group("name"))
        except ValueError:
            warnings.append("INVALID_DISTRO_ENTRY_REJECTED")
            continue
        if name in seen:
            warnings.append("DUPLICATE_DISTRO_ENTRY_REJECTED")
            continue
        seen.add(name)
        state_text = match.group("state").strip().upper()
        state = (
            WslDistroState.RUNNING
            if state_text == "RUNNING"
            else WslDistroState.STOPPED
            if state_text == "STOPPED"
            else WslDistroState.UNKNOWN
        )
        generation = "WSL1" if version == "1" else "WSL2" if version == "2" else "UNKNOWN"
        distros.append(
            WslDistribution(
                name=name,
                state=state,
                generation=generation,
                is_default=bool(match.group("default")),
            )
        )
    return WslListParseResult(
        distros=tuple(distros),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def distro_domain_id(name: str) -> str:
    validated = validate_distro_name(name)
    digest = hashlib.sha256(validated.encode("utf-8")).hexdigest()[:12]
    return f"wsl-distro-{digest}"


def workspace_location(distro: str, native_path: object) -> WslWorkspaceLocation:
    validated_distro = validate_distro_name(distro)
    if not isinstance(native_path, str) or any(ord(character) < 32 for character in native_path):
        raise ValueError("WSL native path is invalid")
    path = PurePosixPath(native_path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("WSL native path is invalid")
    if not (
        native_path in {"/home", "/workspace"}
        or native_path.startswith(("/home/", "/workspace/"))
    ):
        raise ValueError("WSL native path is outside the supported workspace roots")
    display_suffix = native_path.replace("/", "\\")
    return WslWorkspaceLocation(
        distro=validated_distro,
        native_path=native_path,
        windows_display_path=f"\\\\wsl$\\{validated_distro}{display_suffix}",
    )


__all__ = [
    "WslDistribution",
    "WslDistroState",
    "WslListParseResult",
    "WslWorkspaceLocation",
    "distro_domain_id",
    "parse_wsl_list",
    "workspace_location",
]
