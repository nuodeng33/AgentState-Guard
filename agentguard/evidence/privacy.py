"""Fail-closed privacy validation for Evidence Ledger payloads."""

from __future__ import annotations

import math
from typing import Any


class EvidencePrivacyError(ValueError):
    """Raised when an evidence payload violates the safe data contract."""


_SENSITIVE_PARTS = frozenset(
    {
        "api_key",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "passphrase",
        "private_key",
        "secret",
        "credential",
        "environment",
        "env",
        "command_line",
        "cmdline",
        "open_files",
        "net_connections",
        "provider",
        "remote_url",
    }
)
_MAX_DEPTH = 8
_MAX_FIELDS = 64
_MAX_STRING_LENGTH = 1024
_MAX_REFS = 32
_MAX_REF_LENGTH = 128


def _normalized_key(key: str) -> str:
    return key.casefold().replace("-", "_")


def validate_evidence_refs(evidence_refs: tuple[str, ...]) -> tuple[str, ...]:
    """Validate and normalize bounded evidence references."""
    if not isinstance(evidence_refs, tuple) or len(evidence_refs) > _MAX_REFS:
        raise EvidencePrivacyError("EVIDENCE_REFS_INVALID")
    if any(not isinstance(item, str) or not item or len(item) > _MAX_REF_LENGTH for item in evidence_refs):
        raise EvidencePrivacyError("EVIDENCE_REFS_INVALID")
    return tuple(sorted(set(evidence_refs)))


def validate_safe_json(value: Any, *, _depth: int = 0) -> None:
    """Reject non-JSON, sensitive, oversized, or deeply nested data."""
    if _depth > _MAX_DEPTH:
        raise EvidencePrivacyError("PAYLOAD_TOO_DEEP")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LENGTH:
            raise EvidencePrivacyError("PAYLOAD_STRING_TOO_LONG")
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EvidencePrivacyError("PAYLOAD_NUMBER_INVALID")
        return
    if isinstance(value, list):
        if len(value) > _MAX_FIELDS:
            raise EvidencePrivacyError("PAYLOAD_TOO_LARGE")
        for item in value:
            validate_safe_json(item, _depth=_depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > _MAX_FIELDS:
            raise EvidencePrivacyError("PAYLOAD_TOO_LARGE")
        for key, item in value.items():
            if not isinstance(key, str):
                raise EvidencePrivacyError("PAYLOAD_KEY_INVALID")
            if _normalized_key(key) in _SENSITIVE_PARTS:
                raise EvidencePrivacyError("PAYLOAD_SENSITIVE_FIELD")
            validate_safe_json(item, _depth=_depth + 1)
        return
    raise EvidencePrivacyError("PAYLOAD_TYPE_INVALID")
