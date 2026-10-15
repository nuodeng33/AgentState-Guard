"""P6 explicit restore-policy primitives."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

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
        lexical_path = self._lexical_absolute(path)
        validator = self._validators.get(execution_domain_id)
        if not user_approved:
            return RestoreDecision("audit_only", RestoreStatus.USER_APPROVAL_REQUIRED)
        approved = self._approved_paths.get(execution_domain_id, ())
        if self._path_is_unsafe(lexical_path) or not any(
            lexical_path == self._lexical_absolute(item) for item in approved
        ):
            return RestoreDecision("audit_only", RestoreStatus.PATH_NOT_APPROVED)
        content, _file_stat = self._read_once(lexical_path)
        return self._classify_content(content, validator)

    def _classify_content(self, content: bytes, validator: str | None) -> RestoreDecision:
        if len(content) > self._max_bytes:
            return RestoreDecision("audit_only", RestoreStatus.SIZE_LIMIT_EXCEEDED)
        if contains_sensitive_data(content.decode(errors="replace")):
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
        lexical_path = self._lexical_absolute(path)
        content, file_stat = self._read_once(lexical_path)
        validator = self._validators.get(execution_domain_id)
        approved = self._approved_paths.get(execution_domain_id, ())
        if not user_approved:
            decision = RestoreDecision("audit_only", RestoreStatus.USER_APPROVAL_REQUIRED)
        elif self._path_is_unsafe(lexical_path) or not any(
            lexical_path == self._lexical_absolute(item) for item in approved
        ):
            decision = RestoreDecision("audit_only", RestoreStatus.PATH_NOT_APPROVED)
        else:
            decision = self._classify_content(content, validator)
        digest = hashlib.sha256(content).hexdigest()
        entry = {
            "domain": execution_domain_id,
            "logical_path": str(lexical_path),
            "classification": decision.mode,
            "blob_sha256": None,
            "size": len(content),
            "mode": oct(stat.S_IMODE(file_stat.st_mode)),
            "uid": getattr(file_stat, "st_uid", None),
            "gid": getattr(file_stat, "st_gid", None),
            "validator": decision.validator,
            "sha256": digest,
            "status": decision.status.value,
        }
        blobs: dict[str, bytes] = {}
        if decision.mode == "restorable":
            entry["blob_sha256"] = digest
            blobs[digest] = content
        return {"format_version": 3, "manifest": [entry], "blobs": blobs}

    @staticmethod
    def _lexical_absolute(path: Path) -> Path:
        return path if path.is_absolute() else Path.cwd() / path

    @staticmethod
    def _path_is_unsafe(path: Path) -> bool:
        if ".." in path.parts:
            return True
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            try:
                if current.is_symlink():
                    return True
            except OSError:
                return True
        return False

    @staticmethod
    def _read_once(path: Path) -> tuple[bytes, os.stat_result]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                content = source.read()
            file_stat = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size != len(content):
            raise OSError("RECOVERY_TARGET_CHANGED")
        return content, file_stat
