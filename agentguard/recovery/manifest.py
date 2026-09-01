"""Deterministic validation for P6 content-addressed Snapshot V3 artifacts."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Any

from agentguard.evidence.canonical import canonical_json, canonical_json_unbounded


def manifest_digest(snapshot: dict[str, Any]) -> str:
    """Return the legacy digest or a workspace-bound extension digest."""
    manifest = snapshot.get("manifest")
    authority = (
        manifest
        if "workspace" not in snapshot
        else {"manifest": manifest, "workspace": snapshot.get("workspace")}
    )
    encoder = canonical_json_unbounded if "workspace" in snapshot else canonical_json
    return hashlib.sha256(encoder(authority).encode("utf-8")).hexdigest()


_ENTRY_FIELDS = frozenset(
    {
        "domain",
        "logical_path",
        "classification",
        "blob_sha256",
        "size",
        "mode",
        "uid",
        "gid",
        "validator",
        "sha256",
        "status",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MODE = re.compile(r"0o[0-7]+")
_AUDIT_STATUSES = frozenset(
    {
        "AUDIT_ONLY",
        "SENSITIVE_DOWNGRADED",
        "PATH_NOT_APPROVED",
        "USER_APPROVAL_REQUIRED",
        "SIZE_LIMIT_EXCEEDED",
        "VALIDATOR_UNDEFINED",
        "WORKSPACE_RESTORABLE",
    }
)
_WORKSPACE_FIELDS = frozenset(
    {
        "schema_version",
        "workspace_id",
        "scope_observation_id",
        "execution_domain_id",
        "root_digest",
        "coverage",
        "coverage_counts",
        "coverage_digest",
        "scan_complete",
        "scan_reason_code",
    }
)
_WORKSPACE_STORAGE_FIELDS = frozenset(
    {
        "storage_kind",
        "storage_resource_identity",
        "logical_root",
        "durability",
        "protection_capability",
    }
)
_COVERAGE_FIELDS = frozenset(
    {
        "relative_path",
        "object_kind",
        "category",
        "reason_code",
        "size",
        "content_digest",
        "observation_digest",
        "permission_proof",
        "link_target",
    }
)
_SAFE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


def validate_snapshot_v3(
    snapshot: object,
    *,
    expected_domain: str | None = None,
) -> tuple[bool, str, str | None]:
    """Validate manifest/blob integrity without reading or writing target files."""
    if not isinstance(snapshot, dict) or snapshot.get("format_version") != 3:
        return False, "LEGACY_SNAPSHOT_READ_ONLY", None
    manifest = snapshot.get("manifest")
    blobs = snapshot.get("blobs")
    workspace = snapshot.get("workspace")
    if not isinstance(manifest, list) or not isinstance(blobs, dict):
        return False, "RECOVERY_MANIFEST_INVALID", None
    if "workspace" in snapshot and not isinstance(workspace, dict):
        return False, "RECOVERY_WORKSPACE_EXTENSION_INVALID", None
    if not manifest and workspace is None:
        return False, "RECOVERY_MANIFEST_EMPTY", None

    paths: set[str] = set()
    referenced_blobs: set[str] = set()
    for entry in manifest:
        if not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS:
            return False, "RECOVERY_MANIFEST_INVALID", None
        logical_path = entry.get("logical_path")
        domain = entry.get("domain")
        classification = entry.get("classification")
        if (
            not isinstance(domain, str)
            or not domain
            or not isinstance(logical_path, str)
            or not logical_path
            or not PurePosixPath(logical_path).is_absolute()
            or ".." in PurePosixPath(logical_path).parts
            or logical_path.casefold() in paths
        ):
            return False, "RECOVERY_MANIFEST_INVALID", None
        if expected_domain is not None and domain != expected_domain:
            return False, "RECOVERY_DOMAIN_MISMATCH", None
        paths.add(logical_path.casefold())
        blob_sha256 = entry.get("blob_sha256")
        size = entry.get("size")
        mode = entry.get("mode")
        uid = entry.get("uid")
        gid = entry.get("gid")
        validator = entry.get("validator")
        sha256 = entry.get("sha256")
        status = entry.get("status")
        if classification not in {"audit_only", "restorable"}:
            return False, "RECOVERY_MANIFEST_INVALID", None
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(mode, str)
            or _MODE.fullmatch(mode) is None
            or oct(int(mode, 8)) != mode
            or any(
                value is not None
                and (not isinstance(value, int) or isinstance(value, bool) or value < 0)
                for value in (uid, gid)
            )
            or not isinstance(sha256, str)
            or _SHA256.fullmatch(sha256) is None
            or not isinstance(status, str)
            or status not in _AUDIT_STATUSES
        ):
            return False, "RECOVERY_MANIFEST_INVALID", None
        if classification == "audit_only" and (
            blob_sha256 is not None
            or validator is not None
            or status == "WORKSPACE_RESTORABLE"
        ):
            return False, "RECOVERY_MANIFEST_INVALID", None
        if classification == "restorable":
            if (
                not isinstance(validator, str)
                or not validator
                or not isinstance(blob_sha256, str)
                or _SHA256.fullmatch(blob_sha256) is None
                or blob_sha256 not in blobs
            ):
                return False, "RECOVERY_MANIFEST_INVALID", None
            content = blobs[blob_sha256]
            if not isinstance(content, bytes):
                return False, "RECOVERY_MANIFEST_INVALID", None
            if (
                hashlib.sha256(content).hexdigest() != blob_sha256
                or sha256 != blob_sha256
                or len(content) != size
            ):
                return False, "RECOVERY_MANIFEST_INVALID", None
            referenced_blobs.add(blob_sha256)
    if set(blobs) != referenced_blobs:
        return False, "RECOVERY_MANIFEST_INVALID", None
    if workspace is not None and not _validate_workspace_extension(
        workspace,
        manifest=manifest,
        expected_domain=expected_domain,
    ):
        return False, "RECOVERY_WORKSPACE_EXTENSION_INVALID", None
    return True, "RECOVERY_MANIFEST_VERIFIED", manifest_digest(snapshot)


def _validate_workspace_extension(
    workspace: object,
    *,
    manifest: list[dict[str, Any]],
    expected_domain: str | None,
) -> bool:
    from .workspace_permissions import PermissionProof

    if not isinstance(workspace, dict) or set(workspace) not in {
        _WORKSPACE_FIELDS,
        _WORKSPACE_FIELDS | _WORKSPACE_STORAGE_FIELDS,
    }:
        return False
    workspace_id = workspace.get("workspace_id")
    observation_id = workspace.get("scope_observation_id")
    execution_domain_id = workspace.get("execution_domain_id")
    root_digest = workspace.get("root_digest")
    coverage = workspace.get("coverage")
    counts = workspace.get("coverage_counts")
    coverage_digest = workspace.get("coverage_digest")
    complete = workspace.get("scan_complete")
    scan_reason = workspace.get("scan_reason_code")
    if _WORKSPACE_STORAGE_FIELDS.issubset(workspace):
        storage_kind = workspace.get("storage_kind")
        resource_identity = workspace.get("storage_resource_identity")
        logical_root = workspace.get("logical_root")
        durability = workspace.get("durability")
        protection_capability = workspace.get("protection_capability")
        if (
            storage_kind not in {
                "HOST_PATH",
                "DOCKER_BIND",
                "DOCKER_NAMED_VOLUME",
                "WSL_FS",
                "CONTAINER_EPHEMERAL_FS",
                "TMPFS",
                "OTHER_UNSUPPORTED",
            }
            or not isinstance(resource_identity, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", resource_identity) is None
            or not isinstance(logical_root, str)
            or not PurePosixPath(logical_root).is_absolute()
            or ".." in PurePosixPath(logical_root).parts
            or durability not in {"DURABLE", "EPHEMERAL", "VOLATILE", "UNKNOWN"}
            or protection_capability
            not in {"SUPPORTED", "UNSUPPORTED", "UNKNOWN"}
        ):
            return False
    if (
        not isinstance(workspace_id, str)
        or _SAFE_ID.fullmatch(workspace_id) is None
        or not isinstance(observation_id, str)
        or _SAFE_ID.fullmatch(observation_id) is None
        or not isinstance(execution_domain_id, str)
        or _SAFE_ID.fullmatch(execution_domain_id) is None
        or (
            expected_domain is not None
            and execution_domain_id != expected_domain
        )
        or not isinstance(root_digest, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", root_digest) is None
        or not isinstance(coverage, list)
        or not isinstance(counts, dict)
        or set(counts) != {"restorable", "audit_only", "excluded", "unreachable"}
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts.values()
        )
        or not isinstance(coverage_digest, str)
        or _SHA256.fullmatch(coverage_digest) is None
        or not isinstance(complete, bool)
        or not isinstance(scan_reason, str)
        or not scan_reason
    ):
        return False
    if hashlib.sha256(canonical_json_unbounded(coverage).encode()).hexdigest() != coverage_digest:
        return False

    seen: set[str] = set()
    relative_order: list[tuple[str, str]] = []
    actual_counts = {name: 0 for name in counts}
    restorable: dict[str, dict[str, Any]] = {}
    for entry in coverage:
        if not isinstance(entry, dict) or set(entry) != _COVERAGE_FIELDS:
            return False
        relative = entry.get("relative_path")
        category = entry.get("category")
        kind = entry.get("object_kind")
        reason = entry.get("reason_code")
        size = entry.get("size")
        content_digest = entry.get("content_digest")
        observation_digest = entry.get("observation_digest")
        proof = entry.get("permission_proof")
        link_target = entry.get("link_target")
        if (
            not isinstance(relative, str)
            or not relative
            or "\\" in relative
            or PurePosixPath(relative).is_absolute()
            or relative in {".", ".."}
            or ".." in PurePosixPath(relative).parts
            or PurePosixPath(relative).as_posix() != relative
            or relative.casefold() in seen
            or category not in actual_counts
            or kind not in {"FILE", "DIRECTORY", "SYMLINK", "SPECIAL"}
            or not isinstance(reason, str)
            or not reason
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or (
                content_digest is not None
                and (
                    not isinstance(content_digest, str)
                    or _SHA256.fullmatch(content_digest) is None
                )
            )
            or not isinstance(observation_digest, str)
            or _SHA256.fullmatch(observation_digest) is None
            or (link_target is not None and not isinstance(link_target, str))
        ):
            return False
        seen.add(relative.casefold())
        relative_order.append((relative.casefold(), relative))
        actual_counts[category] += 1
        if category == "restorable":
            if proof is None:
                return False
            try:
                PermissionProof.from_dict(proof)
            except ValueError:
                return False
            if kind == "FILE":
                if content_digest is None or link_target is not None:
                    return False
                restorable[relative] = entry
            elif kind == "DIRECTORY":
                if content_digest is not None or link_target is not None:
                    return False
            elif kind == "SYMLINK":
                if content_digest is not None or not link_target:
                    return False
            else:
                return False
        elif proof is not None:
            return False
    if actual_counts != counts:
        return False
    if relative_order != sorted(relative_order):
        return False

    expected_manifest_paths = {
        f"/workspace/{workspace_id}/{relative}": entry
        for relative, entry in restorable.items()
    }
    if len(manifest) != len(expected_manifest_paths):
        return False
    for manifest_entry in manifest:
        coverage_entry = expected_manifest_paths.get(manifest_entry.get("logical_path"))
        if coverage_entry is None:
            return False
        try:
            proof = PermissionProof.from_dict(coverage_entry.get("permission_proof"))
        except ValueError:
            return False
        expected_uid = proof.values.get("uid")
        expected_gid = proof.values.get("gid")
        if (
            manifest_entry.get("classification") != "restorable"
            or manifest_entry.get("status") != "WORKSPACE_RESTORABLE"
            or manifest_entry.get("validator") != "workspace-hash-permission-v1"
            or manifest_entry.get("sha256") != coverage_entry.get("content_digest")
            or manifest_entry.get("blob_sha256")
            != coverage_entry.get("content_digest")
            or manifest_entry.get("size") != coverage_entry.get("size")
            or manifest_entry.get("domain") != execution_domain_id
            or manifest_entry.get("mode") != proof.values.get("mode")
            or manifest_entry.get("uid") != expected_uid
            or manifest_entry.get("gid") != expected_gid
        ):
            return False
    return True
