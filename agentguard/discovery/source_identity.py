"""One source identity contract, including verified legacy Docker aliases."""

import hashlib
import json
import re
from datetime import UTC, datetime

from agentguard.storage.audit import append_event


def docker_source_id(container_id: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
        raise ValueError("DISCOVERY_CONTAINER_ID_INVALID")
    return "docker-" + hashlib.sha256(("docker\x1f" + container_id).encode()).hexdigest()[:24]


def legacy_docker_source_id(container_id: str) -> str:
    docker_source_id(container_id)
    return "docker-" + hashlib.sha256(container_id.encode()).hexdigest()[:24]


def resolve_source(connection, source_id: str) -> str:
    row = connection.execute("SELECT metadata_json FROM protection_sources WHERE id=?", (source_id,)).fetchone()
    if row:
        return json.loads(row[0]).get("canonical_source_id", source_id)
    return source_id


def reconcile_source_alias(connection, container_id: str) -> None:
    """Call only with a complete identity returned by a verified local daemon."""
    canonical, legacy = docker_source_id(container_id), legacy_docker_source_id(container_id)
    old = connection.execute("SELECT metadata_json FROM protection_sources WHERE id=?", (legacy,)).fetchone()
    if old and json.loads(old[0]).get("canonical_source_id") == canonical:
        return
    timestamp = datetime.now(UTC).isoformat()
    settings = connection.execute(
        "SELECT source_id,enabled,revision FROM discovery_source_settings WHERE source_id IN (?,?)",
        (legacy, canonical),
    ).fetchall()
    if any(row[0] == legacy for row in settings):
        enabled = all(bool(row[1]) for row in settings)
        revision = max(row[2] for row in settings) + 1
        connection.execute("INSERT INTO discovery_source_settings VALUES (?,?,?,?) "
            "ON CONFLICT(source_id) DO UPDATE SET enabled=excluded.enabled,revision=excluded.revision,updated_at=excluded.updated_at",
            (canonical, int(enabled), revision, timestamp))
        connection.execute("DELETE FROM discovery_source_settings WHERE source_id=?", (legacy,))
        append_event(connection, "discovery.source.identity", "core", "success",
                     {"source_id": canonical, "legacy_source_id": legacy, "revision": revision})
    # Legacy sessions retain their IDs and their unresolved store namespace.
    connection.execute("UPDATE protection_sessions SET source_id=? WHERE source_id=?", (canonical, legacy))
    metadata = json.loads(old[0]) if old else {"source_id": legacy}
    metadata["canonical_source_id"] = canonical
    connection.execute("INSERT INTO protection_sources VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET metadata_json=excluded.metadata_json",
                       (legacy, json.dumps(metadata, separators=(",", ":")), timestamp))
