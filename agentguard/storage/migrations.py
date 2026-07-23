"""Formal SQLite schema migration system.

Each migration is a named step with forward and backward functions.
Migrations run in transactions; failure triggers automatic rollback.
"""

import sqlite3
import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class Migration:
    """A single schema migration step."""

    def __init__(self, version: int, name: str, forward: str, backward: Optional[str] = None):
        self.version = version
        self.name = name
        self.forward_sql = textwrap.dedent(forward).strip()
        self.backward_sql = textwrap.dedent(backward).strip() if backward else None

    @property
    def description(self) -> str:
        return f"v{self.version:04d}: {self.name}"


class MigrationEngine:
    """Manages schema migrations with forward/backward support."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._migrations: Dict[int, Migration] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(Migration(1, "Initial schema", """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     INTEGER PRIMARY KEY,
                name        TEXT NOT NULL,
                applied_at  TEXT NOT NULL,
                checksum    TEXT NOT NULL,
                duration_ms INTEGER
            );

            CREATE TABLE IF NOT EXISTS checkpoints (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                label          TEXT NOT NULL,
                created_at     TEXT NOT NULL,
                snapshot_path  TEXT NOT NULL,
                hash_sha256    TEXT NOT NULL,
                file_count     INTEGER DEFAULT 0,
                version_info   TEXT,
                git_branch     TEXT,
                git_commit     TEXT,
                notes          TEXT
            );

            CREATE TABLE IF NOT EXISTS restore_log (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                checkpoint_id  INTEGER,
                file_path      TEXT NOT NULL,
                restored_at    TEXT NOT NULL,
                hash_before    TEXT,
                hash_after     TEXT,
                status         TEXT NOT NULL,
                FOREIGN KEY (checkpoint_id) REFERENCES checkpoints(id)
            );

            CREATE INDEX IF NOT EXISTS idx_checkpoints_created
                ON checkpoints(created_at DESC);
        """, """
            DROP TABLE IF EXISTS restore_log;
            DROP TABLE IF EXISTS checkpoints;
        """))

        self.register(Migration(2, "Blob store and audit", """
            CREATE TABLE IF NOT EXISTS blobs (
                sha256          TEXT PRIMARY KEY,
                size_bytes      INTEGER NOT NULL,
                compressed_size INTEGER NOT NULL,
                ref_count       INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT NOT NULL,
                verified_at     TEXT
            );

            CREATE TABLE IF NOT EXISTS snapshot_files (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                checkpoint_id   INTEGER NOT NULL,
                file_path       TEXT NOT NULL,
                mode            TEXT NOT NULL CHECK(mode IN ('audit_only','restorable','legacy_audit_only')),
                sha256          TEXT,
                size_bytes      INTEGER,
                blob_sha256     TEXT,
                content_gz_hex  TEXT,
                mtime           REAL,
                mode_oct        TEXT,
                has_sensitive   INTEGER,
                FOREIGN KEY (checkpoint_id) REFERENCES checkpoints(id),
                FOREIGN KEY (blob_sha256) REFERENCES blobs(sha256)
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT NOT NULL,
                event_type      TEXT NOT NULL,
                source          TEXT NOT NULL,
                txn_id          INTEGER,
                checkpoint_id   INTEGER,
                norm_path       TEXT,
                result          TEXT NOT NULL CHECK(result IN ('success','failure','cancelled')),
                prev_hash       TEXT NOT NULL,
                curr_hash       TEXT NOT NULL,
                details_safe    TEXT
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                status          TEXT NOT NULL DEFAULT 'planned'
                    CHECK(status IN ('planned','preflight_failed','ready','applying',
                                     'verifying','committed','rollback_required',
                                     'rolling_back','rolled_back','rollback_failed',
                                     'partially_recoverable','cancelled')),
                label           TEXT,
                created_at      TEXT NOT NULL,
                completed_at    TEXT,
                rollback_coverage REAL,
                rollback_reason TEXT,
                pre_checkpoint  INTEGER,
                post_checkpoint INTEGER,
                FOREIGN KEY (pre_checkpoint) REFERENCES checkpoints(id),
                FOREIGN KEY (post_checkpoint) REFERENCES checkpoints(id)
            );

            CREATE TABLE IF NOT EXISTS transaction_steps (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                txn_id          INTEGER NOT NULL,
                step_order      INTEGER NOT NULL,
                action          TEXT NOT NULL,
                file_path       TEXT,
                status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','applying','applied','verifying',
                                     'verified','failed','rolled_back')),
                message         TEXT,
                FOREIGN KEY (txn_id) REFERENCES transactions(id)
            );

            CREATE TABLE IF NOT EXISTS host_observations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at     TEXT NOT NULL,
                schema_version  INTEGER DEFAULT 1,
                source          TEXT NOT NULL DEFAULT 'stdin',
                data_json       TEXT NOT NULL,
                hash_sha256     TEXT
            );
        """, """
            DROP TABLE IF EXISTS host_observations;
            DROP TABLE IF EXISTS transaction_steps;
            DROP TABLE IF EXISTS transactions;
            DROP TABLE IF EXISTS audit_events;
            DROP TABLE IF EXISTS snapshot_files;
            DROP TABLE IF EXISTS blobs;
        """))

    def register(self, migration: Migration) -> None:
        self._migrations[migration.version] = migration

    def current_version(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return row[0] if row and row[0] else 0

    def pending(self, conn: sqlite3.Connection) -> List[Migration]:
        current = self.current_version(conn)
        return [m for v, m in sorted(self._migrations.items()) if v > current]

    def migrate(self, conn: sqlite3.Connection, target: Optional[int] = None) -> Dict[str, Any]:
        """Run pending migrations up to target (or all)."""
        current = self.current_version(conn)
        results = {"applied": [], "errors": [], "rolled_back": []}

        pending = [m for v, m in sorted(self._migrations.items())
                   if v > current and (target is None or v <= target)]

        for migration in pending:
            try:
                start = datetime.now(timezone.utc)
                conn.execute("BEGIN")
                conn.executescript(migration.forward_sql)
                # Record migration
                import hashlib
                checksum = hashlib.sha256(migration.forward_sql.encode()).hexdigest()[:16]
                duration = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations (version, name, applied_at, checksum, duration_ms) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (migration.version, migration.name, start.isoformat(), checksum, duration),
                )
                conn.commit()
                results["applied"].append(migration.description)
            except Exception as e:
                conn.rollback()
                results["errors"].append(f"{migration.description}: {e}")
                break

        return results

    def rollback(self, conn: sqlite3.Connection, target_version: int) -> Dict[str, Any]:
        """Roll back migrations down to target_version."""
        current = self.current_version(conn)
        results = {"rolled_back": [], "errors": []}

        to_rollback = sorted(
            [m for v, m in self._migrations.items()
             if v > target_version and v <= current and m.backward_sql],
            key=lambda m: -m.version,
        )

        for migration in to_rollback:
            try:
                conn.execute("BEGIN")
                conn.executescript(migration.backward_sql)
                conn.execute("DELETE FROM schema_migrations WHERE version = ?",
                             (migration.version,))
                conn.commit()
                results["rolled_back"].append(migration.description)
            except Exception as e:
                conn.rollback()
                results["errors"].append(f"{migration.description}: {e}")
                break

        return results
