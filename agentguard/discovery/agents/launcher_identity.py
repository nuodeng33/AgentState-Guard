"""Bounded launcher identity for node-hosted Agents.

A generic runtime process (``node.exe``, npm shims) must never be guessed as
an Agent. Identity may only be established from bounded filesystem anchors:
a script path that lives in a ``node_modules`` tree and whose nearest
``package.json`` declares one of the known Agent package identities.

The raw process command line is never read here, never returned, never
logged, and never persisted. Callers pass only bounded candidate file paths
(for example a backend-provided script anchor). Anything unresolvable is
``None`` — UNKNOWN, never a guess.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_SCRIPT_SUFFIXES = frozenset({".js", ".cjs", ".mjs"})
_PACKAGE_NAME = re.compile(r"^@[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$")
_MAX_PATH_CHARS = 512
_MAX_PACKAGE_JSON_BYTES = 64 * 1024
_MAX_ANCESTOR_STEPS = 4

#: Known node-hosted Agent package identities → bounded product identity.
KNOWN_PACKAGE_IDENTITIES = {
    "@anthropic-ai/claude-code": "CLAUDE",
    "@openai/codex": "CODEX",
    "@moonshot-ai/kimi-code": "KIMI_CODE",
}


def is_package_identity_anchor(value: str | None) -> bool:
    """Whether *value* is a real, bounded script anchor worth resolving."""
    if not value or not isinstance(value, str):
        return False
    if len(value) > _MAX_PATH_CHARS:
        return False
    try:
        path = Path(value)
    except (OSError, ValueError):
        return False
    if path.suffix.casefold() not in _SCRIPT_SUFFIXES:
        return False
    if "node_modules" not in {part.casefold() for part in path.parts}:
        return False
    try:
        return path.is_file()
    except OSError:
        return False


def resolve_launcher_identity(candidates: object) -> str | None:
    """Resolve a bounded Agent identity from candidate anchor paths.

    *candidates* must be an iterable of filesystem paths (strings). For each
    bounded anchor the nearest ``package.json`` (within a few parent steps)
    is read; only when it declares a known Agent package identity is that
    bounded identity returned. Everything else — missing files, unreadable
    manifests, unknown or malformed package names — resolves to ``None``.
    """
    if not isinstance(candidates, (list, tuple)):
        return None
    for candidate in candidates:
        if not isinstance(candidate, str) or not is_package_identity_anchor(candidate):
            continue
        identity = _identity_from_anchor(Path(candidate))
        if identity is not None:
            return identity
    return None


def _identity_from_anchor(script: Path) -> str | None:
    directory = script.parent
    for _step in range(_MAX_ANCESTOR_STEPS + 1):
        manifest = directory / "package.json"
        try:
            if manifest.is_file():
                raw = manifest.read_bytes()
                if len(raw) > _MAX_PACKAGE_JSON_BYTES:
                    return None
                parsed = json.loads(raw.decode("utf-8", errors="strict"))
                if not isinstance(parsed, dict):
                    return None
                name = parsed.get("name")
                if not isinstance(name, str) or not _PACKAGE_NAME.fullmatch(name):
                    return None
                return KNOWN_PACKAGE_IDENTITIES.get(name)
        except (OSError, ValueError):
            return None
        parent = directory.parent
        if parent == directory:
            return None
        directory = parent
    return None


__all__ = [
    "KNOWN_PACKAGE_IDENTITIES",
    "is_package_identity_anchor",
    "resolve_launcher_identity",
]
