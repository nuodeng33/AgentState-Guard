"""Formal SQLite schema migration system.

Each migration is a named step with forward and backward functions.
Migrations run in transactions; failure triggers automatic rollback.
"""

import sqlite3
import textwrap
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _iter_statements(script: str) -> Iterator[str]:
    """Yield complete SQLite statements without splitting quoted SQL text."""
    buffer = ""
    for line in textwrap.dedent(script).splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                yield statement
            buffer = ""
    statement = buffer.strip()
    if statement:
        yield statement


class Migration:
    """A single schema migration step."""

    def __init__(self, version: int, name: str, forward: str, backward: str | None = None):
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
        self._migrations: dict[int, Migration] = {}
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

        self.register(Migration(3, "R4 evidence ledger", """
            CREATE TABLE evidence_ledger_events (
                sequence                  INTEGER PRIMARY KEY,
                event_id                  TEXT UNIQUE NOT NULL,
                schema_version            INTEGER NOT NULL,
                recorded_at                TEXT NOT NULL,
                observed_at                TEXT,
                event_family              TEXT NOT NULL,
                event_type                TEXT NOT NULL,
                source                    TEXT NOT NULL,
                result                    TEXT NOT NULL,
                execution_domain_id       TEXT,
                supervision_session_id    TEXT,
                transaction_id            TEXT,
                checkpoint_id             TEXT,
                subject_ref               TEXT,
                evidence_refs_json        TEXT NOT NULL,
                payload_safe_json         TEXT NOT NULL,
                payload_digest            TEXT NOT NULL,
                prev_hash                 TEXT NOT NULL,
                curr_hash                 TEXT NOT NULL
            );
            CREATE INDEX idx_evidence_ledger_event_id
                ON evidence_ledger_events(event_id);
            CREATE INDEX idx_evidence_ledger_family
                ON evidence_ledger_events(event_family);
            CREATE INDEX idx_evidence_ledger_type
                ON evidence_ledger_events(event_type);
            CREATE INDEX idx_evidence_ledger_recorded_at
                ON evidence_ledger_events(recorded_at);
            CREATE INDEX idx_evidence_ledger_domain
                ON evidence_ledger_events(execution_domain_id);
            CREATE INDEX idx_evidence_ledger_session
                ON evidence_ledger_events(supervision_session_id);
            CREATE INDEX idx_evidence_ledger_transaction
                ON evidence_ledger_events(transaction_id);
            CREATE INDEX idx_evidence_ledger_checkpoint
                ON evidence_ledger_events(checkpoint_id);
            CREATE INDEX idx_evidence_ledger_subject
                ON evidence_ledger_events(subject_ref);
            CREATE TRIGGER evidence_ledger_events_no_update
            BEFORE UPDATE ON evidence_ledger_events
            BEGIN
                SELECT RAISE(ABORT, 'EVIDENCE_LEDGER_APPEND_ONLY');
            END;
            CREATE TRIGGER evidence_ledger_events_no_delete
            BEFORE DELETE ON evidence_ledger_events
            BEGIN
                SELECT RAISE(ABORT, 'EVIDENCE_LEDGER_APPEND_ONLY');
            END;
        """, """
            DROP TRIGGER IF EXISTS evidence_ledger_events_no_delete;
            DROP TRIGGER IF EXISTS evidence_ledger_events_no_update;
            DROP TABLE IF EXISTS evidence_ledger_events;
        """))

        self.register(Migration(4, "R4 supervision sessions", """
            CREATE TABLE supervision_sessions (
                supervision_session_id       TEXT PRIMARY KEY,
                status                       TEXT NOT NULL,
                declared_intent_digest        TEXT NOT NULL,
                decision                      TEXT NOT NULL,
                requires_checkpoint           INTEGER NOT NULL,
                requires_manual_approval      INTEGER NOT NULL,
                created_at                    TEXT NOT NULL,
                updated_at                    TEXT NOT NULL
            );
            CREATE INDEX idx_supervision_sessions_status
                ON supervision_sessions(status);
        """, """
            DROP TABLE IF EXISTS supervision_sessions;
        """))

    def register(self, migration: Migration) -> None:
        self._migrations[migration.version] = migration

    def current_version(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return row[0] if row and row[0] else 0

    def pending(self, conn: sqlite3.Connection) -> list[Migration]:
        current = self.current_version(conn)
        return [m for v, m in sorted(self._migrations.items()) if v > current]

    def _execute_script(self, conn: sqlite3.Connection, script: str) -> None:
        for statement in _iter_statements(script):
            conn.execute(statement)

    def migrate(self, conn: sqlite3.Connection, target: int | None = None) -> dict[str, Any]:
        """Run pending migrations up to target (or all)."""
        current = self.current_version(conn)
        results = {"applied": [], "errors": [], "rolled_back": []}

        pending = [m for v, m in sorted(self._migrations.items())
                   if v > current and (target is None or v <= target)]

        for migration in pending:
            try:
                start = datetime.now(UTC)
                conn.execute("BEGIN IMMEDIATE")
                self._execute_script(conn, migration.forward_sql)
                # Record migration
                import hashlib
                checksum = hashlib.sha256(migration.forward_sql.encode()).hexdigest()[:16]
                duration = int((datetime.now(UTC) - start).total_seconds() * 1000)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations (version, name, applied_at, checksum, duration_ms) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (migration.version, migration.name, start.isoformat(), checksum, duration),
                )
                conn.commit()
                results["applied"].append(migration.description)
            except sqlite3.Error as exc:
                conn.rollback()
                results["errors"].append(f"{migration.description}: {exc}")
                break

        return results

    def rollback(self, conn: sqlite3.Connection, target_version: int) -> dict[str, Any]:
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
                conn.execute("BEGIN IMMEDIATE")
                self._execute_script(conn, migration.backward_sql)
                conn.execute("DELETE FROM schema_migrations WHERE version = ?",
                             (migration.version,))
                conn.commit()
                results["rolled_back"].append(migration.description)
            except sqlite3.Error as exc:
                conn.rollback()
                results["errors"].append(f"{migration.description}: {exc}")
                break

        return results
