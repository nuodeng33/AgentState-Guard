"""Canonical JSON helpers for Evidence Ledger authority fields."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .privacy import validate_safe_json


def canonical_json(value: Any) -> str:
    """Encode a validated JSON-compatible value in one stable representation."""
    validate_safe_json(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def payload_digest(payload_safe: dict[str, Any]) -> str:
    """Return the SHA-256 digest of canonical payload JSON."""
    return hashlib.sha256(canonical_json(payload_safe).encode("utf-8")).hexdigest()
