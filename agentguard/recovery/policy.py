"""P6 explicit restore-policy primitives."""

from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from agentguard.core.hasher import hash_file
from agentguard.core.sanitizer import contains_sensitive_data


class RestoreStatus(str, Enum):
    AUDIT_ONLY = "AUDIT_ONLY"
    SENSITIVE_DOWNGRADED = "SENSITIVE_DOWNGRADED"
    PATH_NOT_APPROVED = "PATH_NOT_APPROVED"
    USER_APPROVAL_REQUIRED = "USER_APPROVAL_REQUIRED"
    SIZE_LIMIT_EXCEEDED = "SIZE_LIMIT_EXCEEDED"
    VALIDATOR_UNDEFINED = "VALIDATOR_UNDEFINED"


@dataclass(frozen=True)
class RestoreDecision:
    mode: str
    status: RestoreStatus
    validator: str | None = None


class RestorePolicy:
    """Default-deny policy for whether a local file may retain restore content."""

    def __init__(
        self,
        *,
        approved_paths: dict[str, tuple[Path, ...]] | None = None,
        validators: dict[str, str] | None = None,
        max_bytes: int = 1_048_576,
    ) -> None:
        self._approved_paths = approved_paths or {}
        self._validators = validators or {}
        self._max_bytes = max_bytes

    def classify(
        self,
        path: Path,
        execution_domain_id: str,
        *,
        user_approved: bool = False,
    ) -> RestoreDecision:
        path = path.resolve()
        validator = self._validators.get(execution_domain_id)
        if not user_approved:
            return RestoreDecision("audit_only", RestoreStatus.USER_APPROVAL_REQUIRED)
        approved = self._approved_paths.get(execution_domain_id, ())
        if not any(path == item.resolve() for item in approved):
            return RestoreDecision("audit_only", RestoreStatus.PATH_NOT_APPROVED)
        if path.stat().st_size > self._max_bytes:
            return RestoreDecision("audit_only", RestoreStatus.SIZE_LIMIT_EXCEEDED)
        if contains_sensitive_data(path.read_text(errors="replace")):
            return RestoreDecision("audit_only", RestoreStatus.SENSITIVE_DOWNGRADED)
        if not validator:
            return RestoreDecision("audit_only", RestoreStatus.VALIDATOR_UNDEFINED)
        return RestoreDecision("restorable", RestoreStatus.AUDIT_ONLY, validator)

    def snapshot_v3(
        self,
        path: Path,
        execution_domain_id: str,
        *,
        user_approved: bool = False,
    ) -> dict:
        path = path.resolve()
        decision = self.classify(path, execution_domain_id, user_approved=user_approved)
        file_stat = path.stat()
        entry = {
            "domain": execution_domain_id,
            "logical_path": str(path),
            "classification": decision.mode,
            "blob_sha256": None,
            "size": file_stat.st_size,
            "mode": oct(stat.S_IMODE(file_stat.st_mode)),
            "uid": getattr(file_stat, "st_uid", None),
            "gid": getattr(file_stat, "st_gid", None),
            "validator": decision.validator,
            "sha256": hash_file(path),
            "status": decision.status.value,
        }
        blobs: dict[str, bytes] = {}
        if decision.mode == "restorable":
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            entry["blob_sha256"] = digest
            blobs[digest] = content
        return {"format_version": 3, "manifest": [entry], "blobs": blobs}
