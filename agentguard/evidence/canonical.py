"""Canonical JSON helpers for Evidence Ledger authority fields."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .privacy import validate_safe_json

_SHA256_DIGEST = re.compile(r"[0-9a-f]{64}")
_MAX_DIGEST_TREE_CHILDREN = 64
_MAX_DIGEST_TREE_DEPTH = 8


def canonical_json(value: Any) -> str:
    """Encode a validated JSON-compatible value in one stable representation."""
    validate_safe_json(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_json_unbounded(value: Any) -> str:
    """Encode non-Ledger content-addressed data without Ledger payload limits."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def encode_bounded_digest_tree(digests: tuple[str, ...]) -> list[Any]:
    """Encode every digest into privacy-bounded recursive list nodes."""
    if any(
        not isinstance(digest, str) or _SHA256_DIGEST.fullmatch(digest) is None
        for digest in digests
    ):
        raise ValueError("DIGEST_TREE_INVALID")
    payload: list[Any] = list(digests)
    while len(payload) > _MAX_DIGEST_TREE_CHILDREN:
        payload = [
            payload[index : index + _MAX_DIGEST_TREE_CHILDREN]
            for index in range(0, len(payload), _MAX_DIGEST_TREE_CHILDREN)
        ]
    return payload


def flatten_bounded_digest_tree(
    value: object,
    *,
    _depth: int = 0,
) -> tuple[str, ...] | None:
    """Decode a bounded digest tree, rejecting malformed or oversized nodes."""
    if isinstance(value, str):
        return (value,) if _SHA256_DIGEST.fullmatch(value) is not None else None
    if (
        not isinstance(value, list)
        or len(value) > _MAX_DIGEST_TREE_CHILDREN
        or _depth >= _MAX_DIGEST_TREE_DEPTH
    ):
        return None
    flattened: list[str] = []
    for item in value:
        child = flatten_bounded_digest_tree(item, _depth=_depth + 1)
        if child is None:
            return None
        flattened.extend(child)
    return tuple(flattened)


def payload_digest(payload_safe: dict[str, Any]) -> str:
    """Return the SHA-256 digest of canonical payload JSON."""
    return hashlib.sha256(canonical_json(payload_safe).encode("utf-8")).hexdigest()
