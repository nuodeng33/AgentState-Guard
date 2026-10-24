"""Two fresh workers must recheck migration history after the write lock."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from agentguard.storage.migrations import Migration, MigrationEngine


def test_stale_initial_migration_list_cannot_reapply_schema_or_replace_history(
    tmp_path,
):
    path = tmp_path / "concurrent.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,
            name TEXT,applied_at TEXT,checksum TEXT,duration_ms INTEGER)""")
    barrier = threading.Barrier(2)

    def migrate(_):
        with sqlite3.connect(path, timeout=10) as conn:
            engine = MigrationEngine(path)
            engine._migrations = {}
            engine.register(
                Migration(
                    1,
                    "Immutable single application",
                    "CREATE TABLE singleton(id INTEGER PRIMARY KEY);\nINSERT INTO singleton VALUES(1);",
                )
            )
            original = engine.current_version
            initial = True

            def version(connection):
                nonlocal initial
                result = original(connection)
                if initial:
                    initial = False
                    barrier.wait(timeout=5)
                return result

            engine.current_version = version
            return engine.migrate(conn)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(migrate, range(2)))
    assert all(result["errors"] == [] for result in results), results
    assert sorted(len(result["applied"]) for result in results) == [0, 1]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT id FROM singleton").fetchall() == [(1,)]
        history = conn.execute("SELECT * FROM schema_migrations").fetchall()
        assert len(history) == 1


@pytest.mark.parametrize("initial_version", [0, 2, 19])
def test_concurrent_full_schema_preserves_seeded_history(tmp_path, initial_version):
    path = tmp_path / "full-schema.db"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE schema_migrations(
            version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT,
            checksum TEXT, duration_ms INTEGER)""")
        connection.commit()
        assert (
            MigrationEngine(path).migrate(connection, initial_version)["errors"] == []
        )
        if initial_version:
            connection.execute("""INSERT INTO checkpoints
                (label, created_at, snapshot_path, hash_sha256)
                VALUES ('old data', '2026-10-24', 'original', 'immutable-hash')""")
            connection.commit()
        old_history = connection.execute(
            "SELECT * FROM schema_migrations ORDER BY version"
        ).fetchall()
        old_checkpoints = (
            connection.execute("SELECT * FROM checkpoints").fetchall()
            if initial_version
            else []
        )

    barrier = threading.Barrier(2)

    def migrate(_):
        with sqlite3.connect(path, timeout=10) as connection:
            engine = MigrationEngine(path)
            original = engine.current_version

            def initial_version_read(connection):
                version = original(connection)
                barrier.wait(timeout=5)
                return version

            engine.current_version = initial_version_read
            return engine.migrate(connection)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(migrate, range(2)))
    assert all(result["errors"] == [] for result in results), results
    applied = [step for result in results for step in result["applied"]]
    assert len(applied) == len(set(applied)) == 20 - initial_version
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT * FROM schema_migrations WHERE version<=? ORDER BY version",
                (initial_version,),
            ).fetchall()
            == old_history
        )
        assert (
            connection.execute("SELECT * FROM checkpoints").fetchall()
            == old_checkpoints
        )
        assert connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone() == (20,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
