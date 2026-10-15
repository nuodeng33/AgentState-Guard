"""Pure workspace candidate creation with explicit path privacy boundaries."""

from __future__ import annotations

import hashlib
import re

from ..capabilities import CapabilityStatus
from .models import (
    ProcessWarningCode,
    WorkspaceCandidate,
    WorkspacePathKind,
    WorkspaceSource,
)

_REMOTE_URL = re.compile(r"^(?:https?|wss?)://", re.IGNORECASE)
_WINDOWS_HOME = re.compile(
    r"^[a-z]:[\\/]users[\\/][^\\/]+(?:[\\/](.*))?$",
    re.IGNORECASE,
)
_UNIX_HOME = re.compile(r"^/home/[^/]+(?:/(.*))?$")


def workspace_candidate_from_path(
    *,
    path: str,
    source: WorkspaceSource,
    execution_domain_id: str,
    evidence_refs: tuple[str, ...],
    home_path: str | None = None,
    git_root_candidate: bool = False,
) -> WorkspaceCandidate:
    """Create one candidate without filesystem traversal or cross-domain mapping."""

    _require_evidence(evidence_refs)
    path_hint, path_kind, warnings = _minimize_path(path, home_path=home_path)
    candidate_id = _workspace_id(
        execution_domain_id,
        source,
        path_hint,
        path_kind,
    )
    return WorkspaceCandidate(
        candidate_id=candidate_id,
        source=source,
        execution_domain_id=execution_domain_id,
        path_hint=path_hint,
        path_kind=path_kind,
        access_status=CapabilityStatus.AVAILABLE,
        evidence_refs=evidence_refs,
        confidence=0.5 if path_kind is WorkspacePathKind.DISPLAY_ONLY else 0.7,
        sanitized=True,
        warnings=warnings,
        git_root_candidate=git_root_candidate,
        is_final_binding=False,
    )


def unavailable_workspace_candidate(
    *,
    source: WorkspaceSource,
    execution_domain_id: str,
    evidence_refs: tuple[str, ...],
    access_status: CapabilityStatus,
) -> WorkspaceCandidate:
    """Represent an unreadable cwd without inventing or retaining a path."""

    _require_evidence(evidence_refs)
    if access_status not in {
        CapabilityStatus.PERMISSION_DENIED,
        CapabilityStatus.NOT_PRESENT,
        CapabilityStatus.UNSUPPORTED,
        CapabilityStatus.ERROR,
        CapabilityStatus.UNKNOWN,
    }:
        raise ValueError("unavailable workspace status must be fail-closed")
    return WorkspaceCandidate(
        candidate_id=_workspace_id(
            execution_domain_id,
            source,
            access_status.value,
            WorkspacePathKind.UNKNOWN,
        ),
        source=source,
        execution_domain_id=execution_domain_id,
        path_hint=None,
        path_kind=WorkspacePathKind.UNKNOWN,
        access_status=access_status,
        evidence_refs=evidence_refs,
        confidence=None,
        sanitized=True,
        warnings=(ProcessWarningCode.CWD_UNAVAILABLE,),
        is_final_binding=False,
    )


def deduplicate_workspace_candidates(
    candidates: tuple[WorkspaceCandidate, ...],
) -> tuple[WorkspaceCandidate, ...]:
    """Deduplicate exact candidates only; domain and source remain identity inputs."""

    unique: dict[str, WorkspaceCandidate] = {}
    for candidate in candidates:
        unique.setdefault(candidate.candidate_id, candidate)
    return tuple(unique.values())


def _minimize_path(
    path: str,
    *,
    home_path: str | None,
) -> tuple[str, WorkspacePathKind, tuple[ProcessWarningCode, ...]]:
    raw = str(path)
    if _REMOTE_URL.match(raw):
        return (
            "[REDACTED_PATH]",
            WorkspacePathKind.REDACTED,
            (ProcessWarningCode.SENSITIVE_INPUT_REJECTED,),
        )
    lowered = raw.casefold()
    if lowered.startswith(("\\\\wsl$\\", "\\\\wsl.localhost\\")):
        parts = [item for item in raw.lstrip("\\").split("\\") if item]
        root = parts[0] if parts else "wsl$"
        distribution = parts[1] if len(parts) > 1 else "<distribution>"
        return (
            f"\\\\{root}\\{distribution}\\…",
            WorkspacePathKind.DISPLAY_ONLY,
            (
                ProcessWarningCode.WSL_DISPLAY_PATH,
                ProcessWarningCode.PATH_REDACTED,
            ),
        )

    minimized = _relative_to_home(raw, home_path)
    if minimized is not None:
        return (
            minimized,
            WorkspacePathKind.REDACTED,
            (ProcessWarningCode.PATH_REDACTED,),
        )
    windows_home = _WINDOWS_HOME.match(raw)
    if windows_home:
        relative = windows_home.group(1)
        return (
            "~" if not relative else f"~\\{relative}",
            WorkspacePathKind.REDACTED,
            (ProcessWarningCode.PATH_REDACTED,),
        )
    unix_home = _UNIX_HOME.match(raw)
    if unix_home:
        relative = unix_home.group(1)
        return (
            "~" if not relative else f"~/{relative}",
            WorkspacePathKind.REDACTED,
            (ProcessWarningCode.PATH_REDACTED,),
        )
    return raw, WorkspacePathKind.NATIVE, ()


def _relative_to_home(path: str, home_path: str | None) -> str | None:
    if not home_path:
        return None
    normalized_path = path.replace("\\", "/")
    normalized_home = home_path.replace("\\", "/").rstrip("/")
    folded_path = normalized_path.casefold()
    folded_home = normalized_home.casefold()
    if folded_path == folded_home:
        relative = ""
    elif folded_path.startswith(f"{folded_home}/"):
        relative = normalized_path[len(normalized_home) + 1 :]
    else:
        return None
    if "\\" in path and "/" not in path:
        windows_relative = relative.replace("/", "\\")
        return "~" if not windows_relative else f"~\\{windows_relative}"
    return "~" if not relative else f"~/{relative}"


def _workspace_id(
    domain_id: str,
    source: WorkspaceSource,
    path_hint: str,
    path_kind: WorkspacePathKind,
) -> str:
    material = (
        f"{domain_id}\x1f{source.value}\x1f{path_kind.value}\x1f{path_hint}"
    ).encode()
    return f"workspace-candidate-{hashlib.sha256(material).hexdigest()[:24]}"


def _require_evidence(evidence_refs: tuple[str, ...]) -> None:
    if not evidence_refs:
        raise ValueError("workspace candidate requires evidence refs")


__all__ = [
    "deduplicate_workspace_candidates",
    "unavailable_workspace_candidate",
    "workspace_candidate_from_path",
]
