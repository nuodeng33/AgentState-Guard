"""Fail-closed resolution of exact Host-native workspace authority facts."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .product import ProductDiscoveryReport

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


@dataclass(frozen=True)
class ResolvedWorkspaceAuthority:
    status: str
    reason_code: str
    root_path: Path | None = field(default=None, repr=False)
    workspace_id: str | None = None
    root_digest: str | None = None
    execution_domain_id: str | None = None
    agent_ids: tuple[str, ...] = ()
    process_instance_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()


class _UnsafeWorkspace(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def resolve_host_workspace(
    report: ProductDiscoveryReport,
    *,
    home_path: str | Path | None = None,
    system_roots: tuple[str | Path, ...] | None = None,
) -> ResolvedWorkspaceAuthority:
    """Resolve exactly one verified filesystem root without DTO path recovery."""

    authorities = tuple(report.workspace_authorities)
    if not authorities:
        return ResolvedWorkspaceAuthority(
            status="NOT_OBSERVED",
            reason_code="WORKSPACE_SCOPE_NOT_OBSERVED",
        )
    domain_ids = {item.execution_domain_id for item in authorities}
    if len(domain_ids) != 1:
        return _unavailable("WORKSPACE_SCOPE_DOMAIN_AMBIGUOUS")

    blocked_roots = _blocked_roots(home_path=home_path, system_roots=system_roots)
    resolved: list[tuple[Path, object]] = []
    try:
        for authority in authorities:
            root = _resolve_one_root(authority.cwd, blocked_roots)
            resolved.append((root, authority))
    except _UnsafeWorkspace as exc:
        return _unavailable(exc.reason_code)

    roots_by_key: dict[str, Path] = {}
    for root, _authority in resolved:
        roots_by_key.setdefault(_path_key(root), root)
    if len(roots_by_key) != 1:
        return _unavailable("WORKSPACE_SCOPE_AMBIGUOUS")

    root = next(iter(roots_by_key.values()))
    domain_id = next(iter(domain_ids))
    root_digest = workspace_root_digest(root, domain_id)
    return ResolvedWorkspaceAuthority(
        status="BOUND",
        reason_code="WORKSPACE_SCOPE_VERIFIED",
        root_path=root,
        workspace_id=f"workspace-{root_digest.split(':', 1)[1][:24]}",
        root_digest=root_digest,
        execution_domain_id=domain_id,
        agent_ids=tuple(
            sorted({item.agent_id for item in authorities if item.agent_id is not None})
        ),
        process_instance_ids=tuple(
            sorted({item.process_instance_id for item in authorities})
        ),
        evidence_refs=tuple(
            sorted({ref for item in authorities for ref in item.evidence_refs})
        ),
    )


def _resolve_one_root(cwd: Path, blocked_roots: tuple[Path, ...]) -> Path:
    raw = Path(cwd)
    if not raw.is_absolute():
        raise _UnsafeWorkspace("WORKSPACE_SCOPE_NOT_ABSOLUTE")
    _reject_reparse_components(raw)
    try:
        current = raw.resolve(strict=True)
    except (OSError, RuntimeError):
        raise _UnsafeWorkspace("WORKSPACE_SCOPE_UNREACHABLE") from None
    if not current.is_dir():
        raise _UnsafeWorkspace("WORKSPACE_SCOPE_NOT_DIRECTORY")

    root = _nearest_git_root(current) or current
    root_key = _path_key(root)
    if root == Path(root.anchor) or any(root_key == _path_key(item) for item in blocked_roots):
        raise _UnsafeWorkspace("WORKSPACE_SCOPE_TOO_BROAD")
    return root


def _reject_reparse_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except OSError:
            raise _UnsafeWorkspace("WORKSPACE_SCOPE_UNREACHABLE") from None
        if current.is_symlink() or (
            _REPARSE_POINT and bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)
        ):
            raise _UnsafeWorkspace("WORKSPACE_SCOPE_REPARSE_POINT")


def _nearest_git_root(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        marker = candidate / ".git"
        try:
            if marker.exists() and not marker.is_symlink():
                return candidate
        except OSError:
            continue
    return None


def _blocked_roots(
    *,
    home_path: str | Path | None,
    system_roots: tuple[str | Path, ...] | None,
) -> tuple[Path, ...]:
    values: list[str | Path] = []
    if home_path is not None:
        values.append(home_path)
    if system_roots is None:
        values.append(tempfile.gettempdir())
        for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            value = os.environ.get(name)
            if value:
                values.append(value)
    else:
        values.extend(system_roots)
    result: list[Path] = []
    for value in values:
        try:
            result.append(Path(value).resolve(strict=False))
        except (OSError, RuntimeError):
            continue
    return tuple(result)


def workspace_root_digest(root: Path, execution_domain_id: str) -> str:
    """Digest an exact local root without projecting the path itself."""

    material = f"workspace-root-v1\x1f{execution_domain_id}\x1f{_path_key(root)}".encode()
    return f"sha256:{hashlib.sha256(material).hexdigest()}"


def validate_workspace_root_binding(
    root: Path,
    *,
    execution_domain_id: str,
    expected_digest: str,
) -> Path | None:
    """Revalidate a durable exact root without broad-root policy expansion."""

    raw = Path(root)
    try:
        verified = _resolve_one_root(raw, ())
        canonical = raw.resolve(strict=True)
    except _UnsafeWorkspace:
        return None
    except (OSError, RuntimeError):
        return None
    if _path_key(verified) != _path_key(canonical):
        return None
    if workspace_root_digest(canonical, execution_domain_id) != expected_digest:
        return None
    return canonical


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False))).replace("\\", "/").casefold()


def _unavailable(reason_code: str) -> ResolvedWorkspaceAuthority:
    return ResolvedWorkspaceAuthority(status="UNAVAILABLE", reason_code=reason_code)


__all__ = [
    "ResolvedWorkspaceAuthority",
    "resolve_host_workspace",
    "validate_workspace_root_binding",
    "workspace_root_digest",
]
