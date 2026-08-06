"""Deterministic validation for P6 content-addressed Snapshot V3 artifacts."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePath
from typing import Any

from agentguard.evidence.canonical import canonical_json


def manifest_digest(snapshot: dict[str, Any]) -> str:
    """Return the canonical digest of a Snapshot V3 manifest only."""
    manifest = snapshot.get("manifest")
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


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
    }
)


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
    if not isinstance(manifest, list) or not isinstance(blobs, dict):
        return False, "RECOVERY_MANIFEST_INVALID", None
    if not manifest:
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
            or not PurePath(logical_path).is_absolute()
            or ".." in PurePath(logical_path).parts
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
            blob_sha256 is not None or validator is not None
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
    return True, "RECOVERY_MANIFEST_VERIFIED", manifest_digest(snapshot)
