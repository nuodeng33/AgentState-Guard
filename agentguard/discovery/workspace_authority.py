"""Fail-closed resolution of exact Host-native workspace authority facts."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
    storage_kind: str = "HOST_PATH"
    storage_resource_identity: str | None = None
    storage_locator: str | None = field(default=None, repr=False)
    logical_root: str | None = None
    durability: str = "DURABLE"
    current_reachability: str = "AVAILABLE"
    protection_capability: str = "SUPPORTED"
    protection_reason_code: str = "STORAGE_BACKEND_SUPPORTED"
    agent_mutation_capability: str = "UNKNOWN"


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

    results = resolve_workspace_authorities(
        report,
        home_path=home_path,
        system_roots=system_roots,
    )
    if not results:
        return ResolvedWorkspaceAuthority(
            status="NOT_OBSERVED",
            reason_code="WORKSPACE_SCOPE_NOT_OBSERVED",
        )
    if len(results) != 1:
        return _unavailable("WORKSPACE_SCOPE_AMBIGUOUS")
    return results[0]


def resolve_workspace_authorities(
    report: ProductDiscoveryReport,
    *,
    home_path: str | Path | None = None,
    system_roots: tuple[str | Path, ...] | None = None,
) -> tuple[ResolvedWorkspaceAuthority, ...]:
    """Resolve independent candidates through the one generic authority engine."""

    authorities = tuple(report.workspace_authorities)
    if not authorities:
        return ()

    blocked_roots, blocked_subtrees = _blocked_roots(
        home_path=home_path, system_roots=system_roots
    )
    grouped: dict[tuple[str, str], list[tuple[Path | None, object]]] = {}
    unavailable: list[ResolvedWorkspaceAuthority] = []
    for authority in authorities:
        if authority.cwd is None:
            identity = authority.storage_resource_identity
            logical_root = authority.logical_root
            if not identity or not logical_root or not logical_root.startswith("/"):
                unavailable.append(_unavailable("WORKSPACE_STORAGE_IDENTITY_UNAVAILABLE"))
                continue
            key = (authority.execution_domain_id, f"{identity}\x1f{logical_root}")
            grouped.setdefault(key, []).append((None, authority))
            continue
        try:
            root = _resolve_one_root(authority.cwd, blocked_roots, blocked_subtrees)
        except _UnsafeWorkspace as exc:
            unavailable.append(_unavailable(exc.reason_code))
            continue
        key = (authority.execution_domain_id, _path_key(root))
        grouped.setdefault(key, []).append((root, authority))

    resolved_results: list[ResolvedWorkspaceAuthority] = []
    for (domain_id, _root_key), items in sorted(grouped.items()):
        root = items[0][0]
        grouped_authorities = tuple(item[1] for item in items)
        first = grouped_authorities[0]
        if root is None:
            if any(
                item.storage_resource_identity != first.storage_resource_identity
                or item.logical_root != first.logical_root
                or item.storage_kind != first.storage_kind
                for item in grouped_authorities
            ):
                resolved_results.append(_unavailable("WORKSPACE_STORAGE_IDENTITY_AMBIGUOUS"))
                continue
            root_digest = storage_workspace_digest(
                first.storage_resource_identity,
                first.logical_root,
                domain_id,
            )
            resolved_results.append(
                ResolvedWorkspaceAuthority(
                    status="BOUND",
                    reason_code="WORKSPACE_SCOPE_VERIFIED",
                    root_path=None,
                    workspace_id=f"workspace-{root_digest.split(':', 1)[1][:24]}",
                    root_digest=root_digest,
                    execution_domain_id=domain_id,
                    agent_ids=tuple(
                        sorted({item.agent_id for item in grouped_authorities if item.agent_id})
                    ),
                    process_instance_ids=tuple(
                        sorted({item.process_instance_id for item in grouped_authorities})
                    ),
                    evidence_refs=tuple(
                        sorted({ref for item in grouped_authorities for ref in item.evidence_refs})
                    ),
                    storage_kind=first.storage_kind,
                    storage_resource_identity=first.storage_resource_identity,
                    storage_locator=first.storage_locator,
                    logical_root=first.logical_root,
                    durability=first.durability,
                    current_reachability=first.current_reachability,
                    protection_capability=first.protection_capability,
                    protection_reason_code=first.protection_reason_code,
                    agent_mutation_capability=first.agent_mutation_capability,
                )
            )
            continue
        assert root is not None
        root_digest = workspace_root_digest(root, domain_id)
        resolved_results.append(
            ResolvedWorkspaceAuthority(
                status="BOUND",
                reason_code="WORKSPACE_SCOPE_VERIFIED",
                root_path=root,
                workspace_id=f"workspace-{root_digest.split(':', 1)[1][:24]}",
                root_digest=root_digest,
                execution_domain_id=domain_id,
                agent_ids=tuple(
                    sorted(
                        {
                            item.agent_id
                            for item in grouped_authorities
                            if item.agent_id is not None
                        }
                    )
                ),
                process_instance_ids=tuple(
                    sorted({item.process_instance_id for item in grouped_authorities})
                ),
                evidence_refs=tuple(
                    sorted(
                        {
                            ref
                            for item in grouped_authorities
                            for ref in item.evidence_refs
                        }
                    )
                ),
                storage_kind=first.storage_kind,
                durability=first.durability,
                current_reachability=first.current_reachability,
                protection_capability=first.protection_capability,
                protection_reason_code=first.protection_reason_code,
                agent_mutation_capability=first.agent_mutation_capability,
            )
        )
    return (*resolved_results, *unavailable)


def _resolve_one_root(
    cwd: Path,
    blocked_roots: tuple[Path, ...],
    blocked_subtrees: tuple[Path, ...] = (),
) -> Path:
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
    if (
        root == Path(root.anchor)
        or any(root_key == _path_key(item) for item in blocked_roots)
        or any(_is_at_or_below(root, item) for item in blocked_subtrees)
    ):
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
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    exact_values: list[str | Path] = []
    subtree_values: list[str | Path] = []
    if home_path is not None:
        exact_values.append(home_path)
    if system_roots is None:
        exact_values.append(tempfile.gettempdir())
        for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            value = os.environ.get(name)
            if value:
                subtree_values.append(value)
    else:
        subtree_values.extend(system_roots)

    def resolved(values: list[str | Path]) -> tuple[Path, ...]:
        result: list[Path] = []
        for value in values:
            try:
                result.append(Path(value).resolve(strict=False))
            except (OSError, RuntimeError):
                continue
        return tuple(result)

    return resolved(exact_values), resolved(subtree_values)


def _is_at_or_below(path: Path, root: Path) -> bool:
    path_key = _path_key(path)
    root_key = _path_key(root).rstrip("/")
    return path_key == root_key or path_key.startswith(root_key + "/")


def workspace_root_digest(root: Path, execution_domain_id: str) -> str:
    """Digest an exact local root without projecting the path itself."""

    material = f"workspace-root-v1\x1f{execution_domain_id}\x1f{_path_key(root)}".encode()
    return f"sha256:{hashlib.sha256(material).hexdigest()}"


def storage_workspace_digest(
    storage_resource_identity: str,
    logical_root: str,
    execution_domain_id: str,
) -> str:
    """Digest a bounded non-host storage root without exposing its locator."""

    material = (
        f"storage-workspace-v1\x1f{execution_domain_id}\x1f"
        f"{storage_resource_identity}\x1f{logical_root}"
    ).encode()
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
    "resolve_workspace_authorities",
    "storage_workspace_digest",
    "validate_workspace_root_binding",
    "workspace_root_digest",
]
