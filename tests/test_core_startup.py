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


def test_slow_initial_discovery_does_not_block_app_factory(tmp_path):
    import threading

    from agentguard.discovery.product import unavailable_product_snapshot

    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    outcomes = []

    class Discovery:
        def discover(self):
            entered.set()
            assert release.wait(2)
            return unavailable_product_snapshot()

    class Domains:
        def sources(self):
            return []

    def factory():
        try:
            outcomes.append(create_app(state_db_path=tmp_path / "state.db",
                config={"base_dir": str(tmp_path)}, discovery_service=Discovery(),
                protection_domains=Domains(), product_startup_discovery=True))
        finally:
            returned.set()

    thread = threading.Thread(target=factory)
    thread.start()
    try:
        assert returned.wait(.5), "app factory waited for initial discovery I/O"
        assert not entered.is_set(), "discovery must belong to initialized worker lifecycle"
    finally:
        release.set()
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert len(outcomes) == 1


def test_source_initialization_precedes_pause_restore_observation_and_execution(tmp_path, monkeypatch):
    import threading

    import httpx

    from agentguard.discovery.catalog import DiscoveryCatalog
    from agentguard.discovery.managed import ManagedDiscoveryService
    from agentguard.recovery.protection import ProtectionService

    events = []
    preparing, release_prepare, observing, release_observe = (threading.Event() for _ in range(4))

    class Domains:
        def close(self):
            pass

    from types import SimpleNamespace
    managed = ManagedDiscoveryService(native=SimpleNamespace())
    monkeypatch.setattr(DiscoveryCatalog, "reconcile", lambda self: events.append("catalog"))
    monkeypatch.setattr(ProtectionService, "restore_disconnections", lambda self: events.append("pause_restored"))
    app = create_app(state_db_path=tmp_path / "state.db", config={"base_dir": str(tmp_path)},
                     discovery_service=managed, protection_domains=Domains(), product_startup_discovery=True)
    protection = app.state.protection
    prepare = protection.initialize_sources

    def initialize():
        preparing.set()
        assert release_prepare.wait(3)
        prepare()

    def discover():
        events.append("observe")
        observing.set()
        assert release_observe.wait(3)

    def execution_start():
        assert events[:2] == ["catalog", "pause_restored"]
        events.append("execution")
        app.state.execution.accepting = True

    protection.initialize_sources = initialize
    protection.discovery_refresh = discover
    protection.on_initialized = execution_start
    monkeypatch.setattr(app.state.execution, "close", lambda: None)

    async def exercise():
        for handler in app.router.on_startup:
            await handler()
        assert await asyncio.to_thread(preparing.wait, 1)
        assert not app.state.execution.accepting
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            token = (await client.get("/api/session")).json()["token"]
            headers = {"X-Session-Token": token}
            assert (await client.get("/api/health")).status_code == 200
            assert (await client.get("/api/v1/works", headers=headers)).status_code == 200
            # Writes remain unavailable until source safety initialization finishes.
            result = await client.post("/api/v1/works/00000000-0000-0000-0000-000000000001/sessions", headers=headers,
                json={"request_id": "00000000-0000-0000-0000-000000000002", "expected_revision": 1,
                      "application_id": "fixture", "store_id": "fixture", "scope_id": "fixture", "mode": "NEW", "read_only": True, "control_consent": True})
            assert result.status_code == 503
            assert result.json()["reason_code"] == "EXECUTION_RUNTIME_UNAVAILABLE"
            checkpoint = await client.post("/api/v1/recovery/checkpoints", headers=headers, json={})
            assert checkpoint.status_code == 503
            assert checkpoint.json()["reason_code"] == "RECOVERY_DISCOVERY_UNAVAILABLE"
            release_prepare.set()
            assert await asyncio.to_thread(observing.wait, 1)
            assert (await client.get("/api/health")).status_code == 200
            assert (await client.get("/api/v1/works", headers=headers)).status_code == 200
        assert events[:4] == ["catalog", "pause_restored", "execution", "observe"]

    try:
        asyncio.run(exercise())
    finally:
        release_prepare.set()
        release_observe.set()
        protection.close()


def test_closing_while_initializing_never_starts_execution_or_observer(tmp_path):
    import threading

    entered, release = threading.Event(), threading.Event()
    app = create_app(state_db_path=tmp_path / "state.db", config={"base_dir": str(tmp_path)},
                     discovery_service=object(), protection_domains=type("Domains", (), {"close": lambda self: None})(),
                     product_startup_discovery=True)
    protection = app.state.protection
    callbacks = []

    def prepare():
        entered.set()
        assert release.wait(3)

    protection.initialize_sources = prepare
    protection.on_initialized = lambda: callbacks.append("execution")
    protection.discovery_refresh = lambda: callbacks.append("observe")
    start(app)
    try:
        assert entered.wait(1)
        with protection.initialization_lock:
            protection.stop_event.set()
        release.set()
        protection.thread.join(timeout=2)
        assert not protection.thread.is_alive()
        assert callbacks == []
    finally:
        release.set()
        protection.close()


def test_failed_source_initialization_does_not_start_execution_or_discovery(tmp_path):
    import threading

    failed = threading.Event()
    app = create_app(state_db_path=tmp_path / "state.db", config={"base_dir": str(tmp_path)},
                     discovery_service=object(), protection_domains=type("Domains", (), {"close": lambda self: None})(),
                     product_startup_discovery=True)
    protection = app.state.protection
    callbacks = []

    def fail():
        failed.set()
        raise RuntimeError("fixture initialization unavailable")

    protection.initialize_sources = fail
    protection.on_initialized = lambda: callbacks.append("execution")
    protection.discovery_refresh = lambda: callbacks.append("observe")
    start(app)
    try:
        assert failed.wait(1)
        assert not app.state.execution.accepting
        assert callbacks == []
        assert protection.discovery_thread is None
    finally:
        protection.close()


def test_unavailable_discovery_never_marks_initial_authority_complete(tmp_path):
    import threading

    called = threading.Event()
    app = create_app(state_db_path=tmp_path / "state.db", config={"base_dir": str(tmp_path)},
                     discovery_service=object(), protection_domains=type("Domains", (), {"close": lambda self: None})(),
                     product_startup_discovery=True)
    protection = app.state.protection
    protection.on_initialized = lambda: None

    def unavailable():
        called.set()
        return {"status": "UNREACHABLE", "reason_code": "DISCOVERY_REFRESH_UNAVAILABLE", "snapshot_id": "fixture"}

    protection.discovery_refresh = unavailable
    start(app)
    try:
        assert called.wait(1)
        assert not protection.initial_discovery_complete.is_set()
    finally:
        protection.close()
