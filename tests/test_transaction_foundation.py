"""Regression tests for composable SQLite transaction foundation."""

from __future__ import annotations

import sqlite3

import pytest

from agentguard.storage.db import StateDB
from agentguard.storage.migrations import Migration, MigrationEngine


def _database(tmp_path):
    database = StateDB(tmp_path / "state.db")
    database.connect()
    return database


def test_transaction_commits_all_writes_on_normal_exit(tmp_path):
    database = _database(tmp_path)
    try:
        with database.transaction() as connection:
            connection.execute("CREATE TABLE transaction_probe (value TEXT NOT NULL)")
            connection.execute("INSERT INTO transaction_probe VALUES ('committed')")

        assert database._conn.in_transaction is False
        assert database._conn.execute("SELECT value FROM transaction_probe").fetchone() == (
            "committed",
        )
    finally:
        database.close()


def test_transaction_rolls_back_all_writes_on_error(tmp_path):
    database = _database(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="stop"), database.transaction() as connection:
            connection.execute("CREATE TABLE rollback_probe (value TEXT NOT NULL)")
            connection.execute("INSERT INTO rollback_probe VALUES ('discarded')")
            raise RuntimeError("stop")

        assert database._conn.in_transaction is False
        assert database._conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'rollback_probe'"
        ).fetchone() is None
    finally:
        database.close()


def test_transaction_rolls_back_when_commit_itself_fails(tmp_path):
    class CommitFailConnection(sqlite3.Connection):
        fail_next_commit = False

        def commit(self):
            if self.fail_next_commit:
                self.fail_next_commit = False
                raise sqlite3.OperationalError("injected commit failure")
            return super().commit()

    database = _database(tmp_path)
    database._conn.close()
    database._conn = sqlite3.connect(
        str(database.db_path),
        factory=CommitFailConnection,
    )
    try:
        database._conn.fail_next_commit = True
        with (
            pytest.raises(sqlite3.OperationalError, match="injected commit failure"),
            database.transaction() as connection,
        ):
            connection.execute("CREATE TABLE commit_failure_probe (value TEXT NOT NULL)")
            connection.execute("INSERT INTO commit_failure_probe VALUES ('ghost')")

        assert database._conn.in_transaction is False
        assert database._conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'commit_failure_probe'"
        ).fetchone() is None
    finally:
        database.close()


def test_transaction_rejects_nested_scope_without_committing_outer_writes(tmp_path):
    database = _database(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="Nested transactions"), database.transaction() as connection:
            connection.execute("CREATE TABLE nested_probe (value TEXT NOT NULL)")
            with database.transaction():
                pass

        assert database._conn.in_transaction is False
        assert database._conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'nested_probe'"
        ).fetchone() is None
    finally:
        database.close()


def test_transaction_rejects_external_open_transaction(tmp_path):
    database = _database(tmp_path)
    try:
        database._conn.execute("BEGIN")
        with pytest.raises(
            RuntimeError, match="existing transaction"
        ), database.transaction():
            pass
        database._conn.rollback()
    finally:
        database.close()


def test_migration_failure_rolls_back_prior_ddl_and_migration_record(tmp_path):
    path = tmp_path / "migration.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT, checksum TEXT, duration_ms INTEGER)"
    )
    connection.commit()
    engine = MigrationEngine(path)
    engine.register(
        Migration(
            99,
            "Atomic failure probe",
            """
            CREATE TABLE partial_migration_probe (id INTEGER PRIMARY KEY);
            INSERT INTO missing_table VALUES (1);
            """,
        )
    )
    try:
        result = engine.migrate(connection)
        assert result["errors"]
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'partial_migration_probe'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT version FROM schema_migrations WHERE version = 99"
        ).fetchone() is None
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_migration_executes_trigger_with_internal_semicolons_atomically(tmp_path):
    path = tmp_path / "trigger.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT, checksum TEXT, duration_ms INTEGER)"
    )
    connection.commit()
    engine = MigrationEngine(path)
    engine.register(
        Migration(
            99,
            "Trigger statement probe",
            """
            CREATE TABLE source_values (value TEXT);
            CREATE TABLE copied_values (value TEXT);
            CREATE TRIGGER copy_insert AFTER INSERT ON source_values
            BEGIN
                INSERT INTO copied_values VALUES (NEW.value || ';marker');
            END;
            INSERT INTO source_values VALUES ('first;value');
            """,
        )
    )
    try:
        result = engine.migrate(connection)
        assert result["errors"] == []
        assert connection.execute("SELECT value FROM copied_values").fetchone() == (
            "first;value;marker",
        )
    finally:
        connection.close()
