"""Deterministic bounded coverage policy for one verified workspace root."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from agentguard.core.sanitizer import contains_sensitive_data
from agentguard.evidence.canonical import canonical_json, canonical_json_unbounded

from .workspace_permissions import (
    PermissionBackend,
    PermissionCapabilityError,
    PermissionProof,
)

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
_CATEGORIES = ("restorable", "audit_only", "excluded", "unreachable")
_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".agentguard",
        ".git",
        ".gradle",
        ".hg",
        ".idea",
        ".svn",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "target",
        "venv",
    }
)
_EXCLUDED_SUFFIXES = frozenset(
    {".class", ".dll", ".dylib", ".exe", ".o", ".obj", ".pyc", ".pyo", ".so"}
)
_SENSITIVE_NAMES = frozenset(
    {
        ".env",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
    }
)
_SENSITIVE_SUFFIXES = frozenset({".key", ".p12", ".pfx", ".pem"})


@dataclass(frozen=True)
class WorkspaceScanLimits:
    max_entries: int = 25_000
    max_file_bytes: int = 4 * 1024 * 1024
    max_total_restorable_bytes: int = 128 * 1024 * 1024
    max_depth: int = 64

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in (
                self.max_entries,
                self.max_file_bytes,
                self.max_total_restorable_bytes,
                self.max_depth,
            )
        ):
            raise ValueError("WORKSPACE_SCAN_LIMITS_INVALID")


@dataclass(frozen=True)
class WorkspaceCoverageEntry:
    relative_path: str
    object_kind: str
    category: str
    reason_code: str
    size: int
    content_digest: str | None
    observation_digest: str
    permission_proof: PermissionProof | None = None
    link_target: str | None = None
    content: bytes | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.category not in _CATEGORIES:
            raise ValueError("WORKSPACE_COVERAGE_INVALID")
        if self.object_kind not in {"FILE", "DIRECTORY", "SYMLINK", "SPECIAL"}:
            raise ValueError("WORKSPACE_COVERAGE_INVALID")
        if not self.relative_path or self.relative_path.startswith(("/", "\\")):
            raise ValueError("WORKSPACE_COVERAGE_INVALID")
        if self.category == "restorable":
            if self.permission_proof is None:
                raise ValueError("WORKSPACE_COVERAGE_INVALID")
            if self.object_kind == "FILE" and (
                self.content_digest is None or self.content is None or self.link_target is not None
            ):
                raise ValueError("WORKSPACE_COVERAGE_INVALID")
            if self.object_kind == "DIRECTORY" and (
                self.content_digest is not None or self.content is not None or self.link_target is not None
            ):
                raise ValueError("WORKSPACE_COVERAGE_INVALID")
            if self.object_kind == "SYMLINK" and (
                self.content_digest is not None or self.content is not None or not self.link_target
            ):
                raise ValueError("WORKSPACE_COVERAGE_INVALID")
            if self.object_kind == "SPECIAL":
                raise ValueError("WORKSPACE_COVERAGE_INVALID")
        if self.category != "restorable" and (
            self.permission_proof is not None or self.content is not None
        ):
            raise ValueError("WORKSPACE_COVERAGE_INVALID")

    def to_extension_dict(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "object_kind": self.object_kind,
            "category": self.category,
            "reason_code": self.reason_code,
            "size": self.size,
            "content_digest": self.content_digest,
            "observation_digest": self.observation_digest,
            "permission_proof": (
                self.permission_proof.to_dict()
                if self.permission_proof is not None
                else None
            ),
            "link_target": self.link_target,
        }


@dataclass(frozen=True)
class WorkspaceScan:
    root: Path = field(repr=False)
    entries: tuple[WorkspaceCoverageEntry, ...] = ()
    complete: bool = True
    reason_code: str = "WORKSPACE_SCAN_COMPLETE"

    @property
    def counts(self) -> dict[str, int]:
        return {
            category: sum(item.category == category for item in self.entries)
            for category in _CATEGORIES
        }

    @property
    def coverage_digest(self) -> str:
        values = [item.to_extension_dict() for item in self.entries]
        return hashlib.sha256(canonical_json_unbounded(values).encode()).hexdigest()

    @property
    def reason_counts(self) -> dict[str, int]:
        values: dict[str, int] = {}
        for entry in self.entries:
            values[entry.reason_code] = values.get(entry.reason_code, 0) + 1
        return dict(sorted(values.items()))


def scan_workspace(
    root: Path,
    *,
    permission_backend: PermissionBackend,
    limits: WorkspaceScanLimits | None = None,
) -> WorkspaceScan:
    limits = limits or WorkspaceScanLimits()
    root = Path(root)
    try:
        root_info = root.lstat()
        canonical_root = root.resolve(strict=True)
    except OSError as exc:
        raise PermissionCapabilityError("WORKSPACE_ROOT_UNREACHABLE") from exc
    if root.is_symlink() or _is_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
        raise PermissionCapabilityError("WORKSPACE_ROOT_UNSUPPORTED")

    entries: list[WorkspaceCoverageEntry] = []
    total_restorable_bytes = 0
    complete = True
    scan_reason = "WORKSPACE_SCAN_COMPLETE"

    def add(entry: WorkspaceCoverageEntry) -> bool:
        nonlocal complete, scan_reason
        if len(entries) >= limits.max_entries:
            complete = False
            scan_reason = "WORKSPACE_SCAN_ENTRY_LIMIT_REACHED"
            return False
        entries.append(entry)
        return True

    def walk(directory: Path, depth: int) -> bool:
        nonlocal total_restorable_bytes, complete, scan_reason
        if depth > limits.max_depth:
            complete = False
            scan_reason = "WORKSPACE_SCAN_DEPTH_LIMIT_REACHED"
            return False
        try:
            children = sorted(
                os.scandir(directory),
                key=lambda item: (item.name.casefold(), item.name),
            )
        except PermissionError:
            relative = _relative(canonical_root, directory)
            if relative:
                add(_metadata_entry(relative, "DIRECTORY", "unreachable", "WORKSPACE_DIRECTORY_PERMISSION_DENIED", 0))
            else:
                complete = False
                scan_reason = "WORKSPACE_ROOT_PERMISSION_DENIED"
            return True
        except OSError:
            relative = _relative(canonical_root, directory)
            if relative:
                add(_metadata_entry(relative, "DIRECTORY", "unreachable", "WORKSPACE_DIRECTORY_UNREACHABLE", 0))
            else:
                complete = False
                scan_reason = "WORKSPACE_ROOT_UNREACHABLE"
            return True

        for child in children:
            if len(entries) >= limits.max_entries:
                complete = False
                scan_reason = "WORKSPACE_SCAN_ENTRY_LIMIT_REACHED"
                return False
            path = Path(child.path)
            relative = _relative(canonical_root, path)
            if not relative:
                complete = False
                scan_reason = "WORKSPACE_SCAN_CONTAINMENT_FAILED"
                return False
            try:
                info = child.stat(follow_symlinks=False)
            except PermissionError:
                if not add(_metadata_entry(relative, "SPECIAL", "unreachable", "WORKSPACE_OBJECT_PERMISSION_DENIED", 0)):
                    return False
                continue
            except OSError:
                if not add(_metadata_entry(relative, "SPECIAL", "unreachable", "WORKSPACE_OBJECT_UNREACHABLE", 0)):
                    return False
                continue

            if child.is_symlink() or _is_reparse(info):
                if not add(_metadata_entry(relative, "SPECIAL", "excluded", "WORKSPACE_REPARSE_POINT_EXCLUDED", 0)):
                    return False
                continue
            if stat.S_ISDIR(info.st_mode):
                if child.name.casefold() in _EXCLUDED_DIRECTORIES:
                    if not add(_metadata_entry(relative, "DIRECTORY", "excluded", "WORKSPACE_DEPENDENCY_EXCLUDED", 0)):
                        return False
                    continue
                if not walk(path, depth + 1):
                    return False
                continue
            if not stat.S_ISREG(info.st_mode):
                if not add(_metadata_entry(relative, "SPECIAL", "excluded", "WORKSPACE_OBJECT_UNSUPPORTED", int(info.st_size))):
                    return False
                continue
            if path.suffix.casefold() in _EXCLUDED_SUFFIXES:
                if not add(_metadata_entry(relative, "FILE", "excluded", "WORKSPACE_GENERATED_OUTPUT_EXCLUDED", int(info.st_size))):
                    return False
                continue
            if info.st_size > limits.max_file_bytes:
                if not add(_metadata_entry(relative, "FILE", "audit_only", "WORKSPACE_SIZE_LIMIT_AUDIT_ONLY", int(info.st_size), info=info)):
                    return False
                continue

            try:
                content, current_info = _read_regular_once(path)
            except PermissionError:
                if not add(_metadata_entry(relative, "FILE", "unreachable", "WORKSPACE_CONTENT_PERMISSION_DENIED", int(info.st_size), info=info)):
                    return False
                continue
            except OSError:
                if not add(_metadata_entry(relative, "FILE", "unreachable", "WORKSPACE_CONTENT_UNREACHABLE", int(info.st_size), info=info)):
                    return False
                continue
            digest = hashlib.sha256(content).hexdigest()
            if _sensitive_name(path) or contains_sensitive_data(
                content.decode("utf-8", errors="replace")
            ):
                if not add(
                    WorkspaceCoverageEntry(
                        relative_path=relative,
                        object_kind="FILE",
                        category="audit_only",
                        reason_code="WORKSPACE_SENSITIVE_AUDIT_ONLY",
                        size=len(content),
                        content_digest=digest,
                        observation_digest=digest,
                    )
                ):
                    return False
                continue
            if total_restorable_bytes + len(content) > limits.max_total_restorable_bytes:
                if not add(
                    WorkspaceCoverageEntry(
                        relative_path=relative,
                        object_kind="FILE",
                        category="audit_only",
                        reason_code="WORKSPACE_TOTAL_SIZE_LIMIT_AUDIT_ONLY",
                        size=len(content),
                        content_digest=digest,
                        observation_digest=digest,
                    )
                ):
                    return False
                continue
            try:
                proof = permission_backend.capture(path)
            except PermissionCapabilityError as exc:
                if not add(
                    WorkspaceCoverageEntry(
                        relative_path=relative,
                        object_kind="FILE",
                        category="unreachable",
                        reason_code=exc.reason_code,
                        size=len(content),
                        content_digest=digest,
                        observation_digest=digest,
                    )
                ):
                    return False
                continue
            try:
                proof_info = path.lstat()
            except OSError:
                proof_info = None
            if (
                proof_info is None
                or path.is_symlink()
                or _is_reparse(proof_info)
                or current_info.st_dev != proof_info.st_dev
                or current_info.st_ino != proof_info.st_ino
                or current_info.st_size != proof_info.st_size
                or current_info.st_size != len(content)
            ):
                if not add(_metadata_entry(relative, "FILE", "unreachable", "WORKSPACE_CONTENT_CHANGED_DURING_SCAN", int(current_info.st_size), info=current_info)):
                    return False
                continue
            total_restorable_bytes += len(content)
            if not add(
                WorkspaceCoverageEntry(
                    relative_path=relative,
                    object_kind="FILE",
                    category="restorable",
                    reason_code="WORKSPACE_RESTORABLE",
                    size=len(content),
                    content_digest=digest,
                    observation_digest=digest,
                    permission_proof=proof,
                    content=content,
                )
            ):
                return False
        return True

    walk(canonical_root, 0)
    ordered = tuple(sorted(entries, key=lambda item: (item.relative_path.casefold(), item.relative_path)))
    return WorkspaceScan(
        root=canonical_root,
        entries=ordered,
        complete=complete,
        reason_code=scan_reason,
    )


def _read_regular_once(path: Path) -> tuple[bytes, os.stat_result]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("WORKSPACE_OBJECT_UNSUPPORTED")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            content = source.read()
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        info.st_dev != after.st_dev
        or info.st_ino != after.st_ino
        or info.st_size != after.st_size
        or after.st_size != len(content)
    ):
        raise OSError("WORKSPACE_CONTENT_CHANGED_DURING_SCAN")
    return content, after


def _metadata_entry(
    relative: str,
    object_kind: str,
    category: str,
    reason_code: str,
    size: int,
    *,
    info: os.stat_result | None = None,
) -> WorkspaceCoverageEntry:
    material = {
        "category": category,
        "kind": object_kind,
        "mtime_ns": getattr(info, "st_mtime_ns", None),
        "reason_code": reason_code,
        "relative_path": relative,
        "size": size,
    }
    digest = hashlib.sha256(canonical_json(material).encode()).hexdigest()
    return WorkspaceCoverageEntry(
        relative_path=relative,
        object_kind=object_kind,
        category=category,
        reason_code=reason_code,
        size=size,
        content_digest=None,
        observation_digest=digest,
    )


def _relative(root: Path, path: Path) -> str | None:
    try:
        value = path.relative_to(root).as_posix()
    except ValueError:
        return None
    if not value or value == "." or ".." in Path(value).parts:
        return None
    return value


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        _REPARSE_POINT
        and getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _sensitive_name(path: Path) -> bool:
    return (
        path.name.casefold() in _SENSITIVE_NAMES
        or path.suffix.casefold() in _SENSITIVE_SUFFIXES
    )


__all__ = [
    "WorkspaceCoverageEntry",
    "WorkspaceScan",
    "WorkspaceScanLimits",
    "scan_workspace",
]
