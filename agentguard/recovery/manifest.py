"""Deterministic validation for P6 content-addressed Snapshot V3 artifacts."""

from __future__ import annotations

import hashlib
from typing import Any

from agentguard.evidence.canonical import canonical_json


def manifest_digest(snapshot: dict[str, Any]) -> str:
    """Return the canonical digest of a Snapshot V3 manifest only."""
    manifest = snapshot.get("manifest")
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def validate_snapshot_v3(snapshot: object) -> tuple[bool, str, str | None]:
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
    for entry in manifest:
        if not isinstance(entry, dict):
            return False, "RECOVERY_MANIFEST_INVALID", None
        logical_path = entry.get("logical_path")
        classification = entry.get("classification")
        if not isinstance(logical_path, str) or not logical_path or logical_path in paths:
            return False, "RECOVERY_MANIFEST_INVALID", None
        paths.add(logical_path)
        blob_sha256 = entry.get("blob_sha256")
        if classification not in {"audit_only", "restorable"}:
            return False, "RECOVERY_MANIFEST_INVALID", None
        if classification == "audit_only" and blob_sha256 is not None:
            return False, "RECOVERY_MANIFEST_INVALID", None
        if classification == "restorable":
            if not isinstance(blob_sha256, str) or blob_sha256 not in blobs:
                return False, "RECOVERY_MANIFEST_INVALID", None
            content = blobs[blob_sha256]
            if not isinstance(content, bytes):
                return False, "RECOVERY_MANIFEST_INVALID", None
            if hashlib.sha256(content).hexdigest() != blob_sha256:
                return False, "RECOVERY_MANIFEST_INVALID", None
    return True, "RECOVERY_MANIFEST_VERIFIED", manifest_digest(snapshot)
