"""Durable, revision-checked preferences for server-discovered observation sources."""

from datetime import UTC, datetime

from agentguard.storage.audit import append_event
from agentguard.storage.db import StateDB


class SourceSettings:
    def __init__(self, database_path):
        self.database_path = database_path

    def read(self):
        database = StateDB(self.database_path)
        try:
            database.connect()
            return {
                source_id: {"enabled": bool(enabled), "revision": revision}
                for source_id, enabled, revision in database._conn.execute(
                    "SELECT source_id, enabled, revision FROM discovery_source_settings"
                )
            }
        finally:
            database.close()

    def set_enabled(self, source_id: str, enabled: bool, expected_revision: int):
        database = StateDB(self.database_path)
        try:
            database.connect()
            with database.transaction() as connection:
                row = connection.execute(
                    "SELECT revision FROM discovery_source_settings WHERE source_id=?", (source_id,)
                ).fetchone()
                revision = row[0] if row else 0
                if revision != expected_revision:
                    raise ValueError("DISCOVERY_SOURCE_REVISION_CONFLICT")
                revision += 1
                connection.execute(
                    """INSERT INTO discovery_source_settings VALUES (?, ?, ?, ?)
                       ON CONFLICT(source_id) DO UPDATE SET enabled=excluded.enabled,
                       revision=excluded.revision, updated_at=excluded.updated_at""",
                    (source_id, int(enabled), revision, datetime.now(UTC).isoformat()),
                )
                append_event(connection, "discovery.source.configure", "core", "success",
                             {"source_id": source_id, "enabled": enabled, "revision": revision})
            return {"source_id": source_id, "enabled": enabled, "revision": revision}
        finally:
            database.close()
