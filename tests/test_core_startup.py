"""Core must initialize storage before starting concurrent database workers."""

import asyncio
import sqlite3

import pytest

from agentguard.api.server import create_app
from agentguard.storage.db import StateDB


def start(app):
    async def startup():
        for handler in app.router.on_startup:
            await handler()

    asyncio.run(startup())


def test_storage_is_ready_and_closed_before_workers_start(tmp_path, monkeypatch):
    path = tmp_path / "fresh.db"
    app = create_app(state_db_path=path)
    events = []
    original_close = StateDB.close

    def closed(database):
        original_close(database)
        events.append("storage_closed")

    def worker_start(name):
        def check():
            assert events and events[0] == "storage_closed"
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
                assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
                assert connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone() == (20,)
                assert connection.execute("PRAGMA integrity_check").fetchone() == (
                    "ok",
                )
            events.append(name)

        return check

    monkeypatch.setattr(StateDB, "close", closed)
    monkeypatch.setattr(app.state.execution, "start", worker_start("execution"))
    monkeypatch.setattr(app.state.protection, "start", worker_start("protection"))
    start(app)
    assert events == ["storage_closed", "execution", "protection"]


@pytest.mark.parametrize(
    "failure_type", [OSError, RuntimeError, sqlite3.OperationalError, TypeError]
)
def test_storage_failure_preserves_degraded_http_contract(
    tmp_path, monkeypatch, caplog, failure_type
):
    app = create_app(state_db_path=tmp_path / "fresh.db")
    events = []
    original_close = StateDB.close

    def fail(database):
        database._conn = sqlite3.connect(database.db_path)
        raise failure_type("simulated storage initialization failure")

    def closed(database):
        original_close(database)
        assert database._conn is None
        events.append("storage_closed")

    monkeypatch.setattr(StateDB, "connect", fail)
    monkeypatch.setattr(StateDB, "close", closed)
    monkeypatch.setattr(
        app.state.execution, "start", lambda: events.append("execution")
    )
    monkeypatch.setattr(
        app.state.protection, "start", lambda: events.append("protection")
    )
    if failure_type is TypeError:
        with pytest.raises(TypeError, match="simulated storage initialization failure"):
            start(app)
        assert events == ["storage_closed"]
    else:
        start(app)
        assert events == ["storage_closed", "execution", "protection"]
        assert "CORE_DATABASE_INITIALIZATION_UNAVAILABLE" in caplog.text
        assert "simulated storage initialization failure" not in caplog.text


def test_execution_cleanup_failure_still_closes_protection(tmp_path, monkeypatch):
    app = create_app(state_db_path=tmp_path / "fresh.db")
    events = []

    def fail():
        events.append("execution")
        raise sqlite3.OperationalError("simulated cleanup failure")

    monkeypatch.setattr(app.state.execution, "close", fail)
    monkeypatch.setattr(
        app.state.protection, "close", lambda: events.append("protection")
    )
    with pytest.raises(sqlite3.OperationalError, match="simulated cleanup failure"):
        asyncio.run(app.router.on_shutdown[0]())
    assert events == ["execution", "protection"]
