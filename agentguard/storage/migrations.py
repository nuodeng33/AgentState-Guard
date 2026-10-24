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

    def __init__(
        self, version: int, name: str, forward: str, backward: str | None = None
    ):
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
        self.register(
            Migration(
                1,
                "Initial schema",
                """
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
        """,
                """
            DROP TABLE IF EXISTS restore_log;
            DROP TABLE IF EXISTS checkpoints;
        """,
            )
        )

        self.register(
            Migration(
                2,
                "Blob store and audit",
                """
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
        """,
                """
            DROP TABLE IF EXISTS host_observations;
            DROP TABLE IF EXISTS transaction_steps;
            DROP TABLE IF EXISTS transactions;
            DROP TABLE IF EXISTS audit_events;
            DROP TABLE IF EXISTS snapshot_files;
            DROP TABLE IF EXISTS blobs;
        """,
            )
        )

        self.register(
            Migration(
                3,
                "R4 evidence ledger",
                """
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
        """,
                """
            DROP TRIGGER IF EXISTS evidence_ledger_events_no_delete;
            DROP TRIGGER IF EXISTS evidence_ledger_events_no_update;
            DROP TABLE IF EXISTS evidence_ledger_events;
        """,
            )
        )

        self.register(
            Migration(
                4,
                "R4 supervision sessions",
                """
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
        """,
                """
            DROP TABLE IF EXISTS supervision_sessions;
        """,
            )
        )

        self.register(
            Migration(
                5,
                "R4 controlled recovery drills",
                """
            CREATE TABLE recovery_drills (
                drill_id TEXT PRIMARY KEY,
                checkpoint_id TEXT NOT NULL,
                execution_domain_id TEXT NOT NULL,
                manifest_digest TEXT NOT NULL,
                target_refs_digest TEXT NOT NULL,
                binding_digest TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK(status IN (
                    'AWAITING_APPROVAL', 'APPROVED', 'RUNNING', 'FAILED', 'VERIFIED_R3'
                )),
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE recovery_drill_approvals (
                approval_id TEXT PRIMARY KEY,
                drill_id TEXT NOT NULL UNIQUE REFERENCES recovery_drills(drill_id),
                binding_digest TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                consumed_at TEXT,
                consumed_by_run_id TEXT UNIQUE,
                CHECK((consumed_at IS NULL) = (consumed_by_run_id IS NULL))
            );
            CREATE TABLE trusted_baselines (
                baseline_id TEXT PRIMARY KEY,
                checkpoint_id TEXT NOT NULL,
                execution_domain_id TEXT NOT NULL,
                manifest_digest TEXT NOT NULL,
                target_refs_digest TEXT NOT NULL,
                recovery_evidence_digest TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(checkpoint_id, execution_domain_id, manifest_digest, target_refs_digest)
            );
            CREATE TABLE trusted_baseline_retirements (
                retirement_id TEXT PRIMARY KEY,
                baseline_id TEXT NOT NULL UNIQUE REFERENCES trusted_baselines(baseline_id),
                retired_at TEXT NOT NULL,
                reason_code TEXT NOT NULL
            );
            CREATE TRIGGER trusted_baselines_no_update
            BEFORE UPDATE ON trusted_baselines
            BEGIN SELECT RAISE(ABORT, 'TRUSTED_BASELINE_IMMUTABLE'); END;
            CREATE TRIGGER trusted_baselines_no_delete
            BEFORE DELETE ON trusted_baselines
            BEGIN SELECT RAISE(ABORT, 'TRUSTED_BASELINE_IMMUTABLE'); END;
        """,
                """
            DROP TRIGGER IF EXISTS trusted_baselines_no_delete;
            DROP TRIGGER IF EXISTS trusted_baselines_no_update;
            DROP TABLE IF EXISTS trusted_baseline_retirements;
            DROP TABLE IF EXISTS trusted_baselines;
            DROP TABLE IF EXISTS recovery_drill_approvals;
            DROP TABLE IF EXISTS recovery_drills;
        """,
            )
        )

        self.register(
            Migration(
                6,
                "Durable trusted baseline approvals",
                """
            CREATE TABLE trusted_baseline_candidates (
                candidate_id TEXT PRIMARY KEY,
                checkpoint_id TEXT NOT NULL,
                execution_domain_id TEXT NOT NULL,
                manifest_digest TEXT NOT NULL,
                target_refs_digest TEXT NOT NULL,
                recovery_evidence_digest TEXT NOT NULL,
                binding_digest TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK(status IN ('CANDIDATE', 'CONSUMED')),
                created_at TEXT NOT NULL
            );
            CREATE TABLE trusted_baseline_approvals (
                approval_id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL UNIQUE REFERENCES trusted_baseline_candidates(candidate_id),
                binding_digest TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                consumed_by_baseline_id TEXT UNIQUE,
                CHECK((consumed_at IS NULL) = (consumed_by_baseline_id IS NULL))
            );
        """,
                """
            DROP TABLE IF EXISTS trusted_baseline_approvals;
            DROP TABLE IF EXISTS trusted_baseline_candidates;
        """,
            )
        )

        self.register(
            Migration(
                7,
                "Bound recovery supervision authorizations",
                """
            CREATE TABLE recovery_drill_bindings (
                drill_id TEXT PRIMARY KEY REFERENCES recovery_drills(drill_id),
                supervision_session_id TEXT NOT NULL UNIQUE REFERENCES supervision_sessions(supervision_session_id),
                session_identity_digest TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                operation_kind TEXT NOT NULL CHECK(operation_kind = 'SELF_RUNTIME_R3_DRILL'),
                binding_digest TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            CREATE TABLE trusted_baseline_candidate_bindings (
                candidate_id TEXT PRIMARY KEY REFERENCES trusted_baseline_candidates(candidate_id),
                supervision_session_id TEXT NOT NULL UNIQUE REFERENCES supervision_sessions(supervision_session_id),
                session_identity_digest TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                drill_fingerprint TEXT NOT NULL,
                binding_digest TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            CREATE TABLE recovery_authorizations (
                authorization_id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL UNIQUE,
                supervision_session_id TEXT NOT NULL UNIQUE REFERENCES supervision_sessions(supervision_session_id),
                session_identity_digest TEXT NOT NULL,
                checkpoint_id TEXT NOT NULL,
                execution_domain_id TEXT NOT NULL,
                manifest_digest TEXT NOT NULL,
                operation_kind TEXT NOT NULL CHECK(operation_kind IN ('SELF_RUNTIME_R3_DRILL', 'TRUSTED_BASELINE_CONFIRM')),
                target_refs_digest TEXT NOT NULL,
                drill_fingerprint TEXT,
                policy_version TEXT NOT NULL,
                binding_digest TEXT NOT NULL UNIQUE,
                issued_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                nonce TEXT NOT NULL UNIQUE,
                consumed_at TEXT,
                consumed_by_ref TEXT UNIQUE,
                CHECK((consumed_at IS NULL) = (consumed_by_ref IS NULL))
            );
            CREATE INDEX idx_recovery_authorizations_subject
                ON recovery_authorizations(subject_id);
        """,
                """
            DROP TABLE IF EXISTS recovery_authorizations;
            DROP TABLE IF EXISTS trusted_baseline_candidate_bindings;
            DROP TABLE IF EXISTS recovery_drill_bindings;
        """,
            )
        )

        self.register(
            Migration(
                8,
                "Durable Device Link bindings",
                """
            CREATE TABLE device_link_bindings (
                device_uuid TEXT PRIMARY KEY,
                public_key_der BLOB NOT NULL,
                fingerprint TEXT NOT NULL,
                display_name TEXT NOT NULL,
                permissions TEXT NOT NULL,
                protocol_version INTEGER NOT NULL CHECK(protocol_version = 1),
                created_at TEXT NOT NULL,
                last_seen_at TEXT,
                revoked_at TEXT
            );
            CREATE UNIQUE INDEX idx_device_link_active_fingerprint
                ON device_link_bindings(fingerprint) WHERE revoked_at IS NULL;
        """,
                """
            DROP INDEX IF EXISTS idx_device_link_active_fingerprint;
            DROP TABLE IF EXISTS device_link_bindings;
        """,
            )
        )

        self.register(
            Migration(
                9,
                "Host-native workspace protection scope",
                """
            CREATE TABLE workspace_scope_observations (
                observation_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_id TEXT UNIQUE NOT NULL,
                discovery_snapshot_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'BOUND', 'NOT_OBSERVED', 'UNAVAILABLE'
                )),
                reason_code TEXT NOT NULL,
                workspace_id TEXT,
                execution_domain_id TEXT,
                root_path TEXT,
                root_digest TEXT,
                agent_ids_json TEXT NOT NULL,
                process_instance_ids_json TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                ledger_event_id TEXT UNIQUE NOT NULL,
                CHECK(
                    (status = 'BOUND' AND workspace_id IS NOT NULL
                     AND execution_domain_id IS NOT NULL
                     AND root_path IS NOT NULL AND root_digest IS NOT NULL)
                    OR
                    (status != 'BOUND' AND workspace_id IS NULL
                     AND execution_domain_id IS NULL
                     AND root_path IS NULL AND root_digest IS NULL)
                )
            );
            CREATE INDEX idx_workspace_scope_latest
                ON workspace_scope_observations(observation_sequence DESC);
            CREATE INDEX idx_workspace_scope_workspace
                ON workspace_scope_observations(workspace_id, observation_sequence DESC);
            CREATE TRIGGER workspace_scope_observations_no_update
            BEFORE UPDATE ON workspace_scope_observations
            BEGIN SELECT RAISE(ABORT, 'WORKSPACE_SCOPE_APPEND_ONLY'); END;
            CREATE TRIGGER workspace_scope_observations_no_delete
            BEFORE DELETE ON workspace_scope_observations
            BEGIN SELECT RAISE(ABORT, 'WORKSPACE_SCOPE_APPEND_ONLY'); END;

            CREATE TABLE workspace_quarantine_records (
                record_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                quarantine_id TEXT UNIQUE NOT NULL,
                workspace_id TEXT NOT NULL,
                checkpoint_id TEXT NOT NULL,
                relative_path_digest TEXT NOT NULL,
                quarantine_path TEXT NOT NULL,
                content_digest TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
                status TEXT NOT NULL CHECK(status IN ('QUARANTINED', 'RESTORED')),
                reason_code TEXT NOT NULL,
                created_at TEXT NOT NULL,
                ledger_event_id TEXT UNIQUE NOT NULL
            );
            CREATE INDEX idx_workspace_quarantine_workspace
                ON workspace_quarantine_records(workspace_id, record_sequence DESC);
            CREATE TRIGGER workspace_quarantine_records_no_update
            BEFORE UPDATE ON workspace_quarantine_records
            BEGIN SELECT RAISE(ABORT, 'WORKSPACE_QUARANTINE_APPEND_ONLY'); END;
            CREATE TRIGGER workspace_quarantine_records_no_delete
            BEFORE DELETE ON workspace_quarantine_records
            BEGIN SELECT RAISE(ABORT, 'WORKSPACE_QUARANTINE_APPEND_ONLY'); END;
        """,
                """
            DROP TRIGGER IF EXISTS workspace_quarantine_records_no_delete;
            DROP TRIGGER IF EXISTS workspace_quarantine_records_no_update;
            DROP INDEX IF EXISTS idx_workspace_quarantine_workspace;
            DROP TABLE IF EXISTS workspace_quarantine_records;
            DROP TRIGGER IF EXISTS workspace_scope_observations_no_delete;
            DROP TRIGGER IF EXISTS workspace_scope_observations_no_update;
            DROP INDEX IF EXISTS idx_workspace_scope_workspace;
            DROP INDEX IF EXISTS idx_workspace_scope_latest;
            DROP TABLE IF EXISTS workspace_scope_observations;
        """,
            )
        )

        self.register(Migration(
            10,
            "Local work and immutable requirements",
            """
            CREATE TABLE works (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                owner TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision > 0),
                requirement_version INTEGER NOT NULL CHECK(requirement_version > 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE work_requirements (
                work_id TEXT NOT NULL REFERENCES works(id),
                version INTEGER NOT NULL CHECK(version > 0),
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (work_id, version)
            );
            CREATE TRIGGER work_requirements_no_update BEFORE UPDATE ON work_requirements
            BEGIN SELECT RAISE(ABORT, 'WORK_REQUIREMENT_IMMUTABLE'); END;
            CREATE TRIGGER work_requirements_no_delete BEFORE DELETE ON work_requirements
            BEGIN SELECT RAISE(ABORT, 'WORK_REQUIREMENT_IMMUTABLE'); END;
            CREATE TABLE work_tasks (
                id TEXT PRIMARY KEY,
                work_id TEXT NOT NULL REFERENCES works(id),
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                assignee TEXT,
                dependencies_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX idx_work_tasks_work ON work_tasks(work_id, id);
            CREATE TABLE work_requests (
                request_id TEXT PRIMARY KEY,
                request_digest TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """,
            """
            DROP TABLE IF EXISTS work_requests;
            DROP TABLE IF EXISTS work_tasks;
            DROP TRIGGER IF EXISTS work_requirements_no_delete;
            DROP TRIGGER IF EXISTS work_requirements_no_update;
            DROP TABLE IF EXISTS work_requirements;
            DROP TABLE IF EXISTS works;
            """,
        ))

        self.register(Migration(11, "Text artifacts and saved workbench", """
            CREATE TABLE work_artifacts (
                id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
                latest_version INTEGER NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX idx_work_artifacts_work ON work_artifacts(work_id);
            CREATE TABLE work_artifact_versions (
                artifact_id TEXT NOT NULL REFERENCES work_artifacts(id),
                version INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
                requirement_version INTEGER NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(artifact_id, version)
            );
            CREATE TRIGGER work_artifact_versions_no_update BEFORE UPDATE ON work_artifact_versions
            BEGIN SELECT RAISE(ABORT, 'WORK_ARTIFACT_IMMUTABLE'); END;
            CREATE TRIGGER work_artifact_versions_no_delete BEFORE DELETE ON work_artifact_versions
            BEGIN SELECT RAISE(ABORT, 'WORK_ARTIFACT_IMMUTABLE'); END;
            CREATE TABLE work_views (
                work_id TEXT PRIMARY KEY REFERENCES works(id), revision INTEGER NOT NULL,
                state_json TEXT NOT NULL, updated_at TEXT NOT NULL
            );
        """, """
            DROP TABLE IF EXISTS work_views;
            DROP TRIGGER IF EXISTS work_artifact_versions_no_delete;
            DROP TRIGGER IF EXISTS work_artifact_versions_no_update;
            DROP TABLE IF EXISTS work_artifact_versions;
            DROP TABLE IF EXISTS work_artifacts;
        """))

        self.register(Migration(12, "Verified work Agent associations", """
            CREATE TABLE work_agent_links (
                work_id TEXT NOT NULL REFERENCES works(id),
                agent_id TEXT NOT NULL,
                detected_identity TEXT NOT NULL,
                role TEXT NOT NULL,
                execution_domain_id TEXT,
                observed_at TEXT NOT NULL,
                evidence_ref TEXT NOT NULL,
                linked_at TEXT NOT NULL,
                PRIMARY KEY(work_id, agent_id)
            );
            CREATE INDEX idx_work_agent_links_agent ON work_agent_links(agent_id);
        """, """
            DROP INDEX IF EXISTS idx_work_agent_links_agent;
            DROP TABLE IF EXISTS work_agent_links;
        """))

        self.register(Migration(13, "User-linked Agent task assignments", """
            CREATE TABLE work_agent_tasks (
                work_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                task_id TEXT NOT NULL REFERENCES work_tasks(id),
                PRIMARY KEY(work_id, agent_id, task_id),
                FOREIGN KEY(work_id, agent_id)
                    REFERENCES work_agent_links(work_id, agent_id)
            );
            CREATE INDEX idx_work_agent_tasks_task ON work_agent_tasks(work_id, task_id);
        """, """
            DROP INDEX IF EXISTS idx_work_agent_tasks_task;
            DROP TABLE IF EXISTS work_agent_tasks;
        """))

        self.register(Migration(14, "Discovery source preferences", """
            CREATE TABLE discovery_source_settings (
                source_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                revision INTEGER NOT NULL CHECK(revision > 0),
                updated_at TEXT NOT NULL
            );
        """, "DROP TABLE IF EXISTS discovery_source_settings;"))

        from .protection_schema import PROTECTION_DOWN_SQL, PROTECTION_SQL

        self.register(Migration(15, "Continuous protection and durable recovery", PROTECTION_SQL, PROTECTION_DOWN_SQL))

        from .protection_schema import (
            PROTECTION_OPTIMIZATION_DOWN_SQL,
            PROTECTION_OPTIMIZATION_SQL,
        )

        self.register(Migration(16, "Protection summaries and logical retention accounting",
            PROTECTION_OPTIMIZATION_SQL, PROTECTION_OPTIMIZATION_DOWN_SQL))

        from .discovery_schema import DISCOVERY_CATALOG_DOWN_SQL, DISCOVERY_CATALOG_SQL

        self.register(Migration(17, "Program registrations and isolated session stores",
                                DISCOVERY_CATALOG_SQL, DISCOVERY_CATALOG_DOWN_SQL))

        from .execution_schema import EXECUTION_DOWN_SQL, EXECUTION_SQL

        self.register(Migration(18, "Explicit Work sessions and durable provider runs",
                                EXECUTION_SQL, EXECUTION_DOWN_SQL))

        from .delivery_schema import DELIVERY_DOWN_SQL, DELIVERY_SQL

        self.register(Migration(19, "Version-bound Work submissions and decisions",
                                DELIVERY_SQL, DELIVERY_DOWN_SQL))

        from .work_extensions_schema import (
            WORK_EXTENSIONS_DOWN_SQL,
            WORK_EXTENSIONS_SQL,
        )

        self.register(Migration(20, "Pending instructions and Work agent appearance",
                                WORK_EXTENSIONS_SQL, WORK_EXTENSIONS_DOWN_SQL))

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

    def migrate(
        self, conn: sqlite3.Connection, target: int | None = None
    ) -> dict[str, Any]:
        """Run pending migrations up to target (or all)."""
        current = self.current_version(conn)
        results = {"applied": [], "errors": [], "rolled_back": []}

        pending = [
            m
            for v, m in sorted(self._migrations.items())
            if v > current and (target is None or v <= target)
        ]

        for migration in pending:
            try:
                start = datetime.now(UTC)
                conn.execute("BEGIN IMMEDIATE")
                # Another connection may have applied this version while we
                # waited for the write lock. Keep its immutable history intact.
                if conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ?",
                    (migration.version,),
                ).fetchone() is not None:
                    conn.rollback()
                    continue
                self._execute_script(conn, migration.forward_sql)
                # Record migration
                import hashlib

                checksum = hashlib.sha256(migration.forward_sql.encode()).hexdigest()[
                    :16
                ]
                duration = int((datetime.now(UTC) - start).total_seconds() * 1000)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at, checksum, duration_ms) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        migration.version,
                        migration.name,
                        start.isoformat(),
                        checksum,
                        duration,
                    ),
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
            [
                m
                for v, m in self._migrations.items()
                if v > target_version and v <= current and m.backward_sql
            ],
            key=lambda m: -m.version,
        )

        for migration in to_rollback:
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._execute_script(conn, migration.backward_sql)
                conn.execute(
                    "DELETE FROM schema_migrations WHERE version = ?",
                    (migration.version,),
                )
                conn.commit()
                results["rolled_back"].append(migration.description)
            except sqlite3.Error as exc:
                conn.rollback()
                results["errors"].append(f"{migration.description}: {exc}")
                break

        return results
