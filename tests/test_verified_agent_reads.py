"""Display snapshots are nonblocking, bounded, and never grant write authority."""
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from agentguard.api import r4_projection
from agentguard.api.server import create_app
from agentguard.api.verified_agents import VerifiedAgentReads
from agentguard.evidence import discovery_adapter
from agentguard.storage.db import StateDB
from tests.test_work_api import _record_agent, _request, _work

AGENT = "external-agent-" + "a" * 24


def _database(root):
    database = StateDB(root / "state.db")
    database.connect()
    database.close()


def _wait(service, predicate, view="agents"):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = service.read(view)
        if predicate(result):
            return result
        time.sleep(0.01)
    raise AssertionError(service.read(view))


def test_http_cold_reads_do_not_wait_for_full_verification(tmp_path, monkeypatch):
    _database(tmp_path)
    _record_agent(tmp_path, "initial", AGENT)
    entered, release = threading.Event(), threading.Event()
    original = r4_projection.verify_ledger
    calls = []

    def slow(connection, **kwargs):
        if connection.execute("PRAGMA database_list").fetchone()[2] != str(tmp_path / "state.db"):
            return original(connection, **kwargs)
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return original(connection, **kwargs)

    monkeypatch.setattr(r4_projection, "verify_ledger", slow)
    monkeypatch.setattr(discovery_adapter, "verify_ledger_snapshot", slow)
    app = create_app(state_db_path=tmp_path / "state.db", config={"base_dir": str(tmp_path)})
    client = TestClient(app)
    client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
    service = getattr(app.state, "verified_agents", None)
    try:
        work_id = _work(client)
        started = time.monotonic()
        first = client.get("/api/v1/agents").json()
        assert time.monotonic() - started < 1
        assert entered.wait(1)
        assert first["verification"]["state"] == "PENDING"
        assert first["verification"]["verified_at"] is None
        assert first["verification"]["checked_at"] is None
        for view in ("runtime", "supervision", "changes", "recovery", "agents"):
            assert client.get("/api/v1/" + view).json()["verification"]["state"] == "PENDING"
        observed = client.get(f"/api/v1/works/{work_id}/agents").json()
        assert observed["verification"]["state"] == "PENDING"
        assert len(calls) == 1
        release.set()
        ready = _wait(service, lambda body: bool(body["items"]))
        assert ready["items"][0]["agent_id"] == AGENT
        assert ready["verification"]["state"] == "VERIFIED"
        _wait(service, lambda body: body["verification"]["state"] == "VERIFIED", "recovery")
        assert len(calls) == 1  # all fast views share one complete historical check
    finally:
        release.set()
        if service is not None:
            service.close()


def test_missing_database_recovers_automatically_and_binds_exact_source(tmp_path):
    selected, other = tmp_path / "selected", tmp_path / "other"
    selected.mkdir()
    other.mkdir()
    _database(other)
    _record_agent(other, "other-source", AGENT)
    service = VerifiedAgentReads(selected / "state.db", interval=0.02)
    try:
        error = _wait(service, lambda body: body["verification"]["state"] == "ERROR")
        assert error["items"] == []
        assert error["verification"]["verified_at"] is None
        assert error["verification"]["checked_at"] is not None
        _database(selected)
        empty = _wait(service, lambda body: body["verification"]["state"] == "VERIFIED")
        assert empty["items"] == []
        _record_agent(selected, "selected-source", AGENT)
        assert _wait(service, lambda body: bool(body["items"]))["items"][0]["agent_id"] == AGENT
    finally:
        service.close()
    assert not service._thread.is_alive()


def test_invalid_history_clears_snapshot_and_does_not_authorize_mutation(tmp_path):
    app = create_app(state_db_path=tmp_path / "state.db", config={"base_dir": str(tmp_path)})
    client = TestClient(app)
    client.headers["X-Session-Token"] = client.get("/api/session").json()["token"]
    service = app.state.verified_agents
    try:
        work_id = _work(client)
        _record_agent(tmp_path, "first", AGENT)
        _record_agent(tmp_path, "second", AGENT)
        good = _wait(service, lambda body: bool(body["items"]))
        linked = client.post(f"/api/v1/works/{work_id}/agents", json=_request(expected_revision=1, agent_id=AGENT))
        assert linked.status_code == 201
        with sqlite3.connect(tmp_path / "state.db") as connection:
            connection.execute("DROP TRIGGER evidence_ledger_events_no_update")
            connection.execute("UPDATE evidence_ledger_events SET result='tampered' WHERE sequence=(SELECT MIN(sequence) FROM evidence_ledger_events)")
        # UI's last success is deliberately still present: the mutation must
        # reject based on the current full validator, not the fast cache.
        assert service.read()["verification"]["state"] == "VERIFIED"
        rejected = client.post(f"/api/v1/works/{work_id}/agents", json=_request(expected_revision=2, agent_id=AGENT))
        assert rejected.status_code == 409
        assert rejected.json()["reason_code"] == "WORK_AGENT_EVIDENCE_INVALID"
        service.request_refresh()
        invalid = _wait(service, lambda body: body["verification"]["state"] == "INVALID")
        assert invalid["items"] == []
        assert invalid["verification"]["verified_at"] == good["verification"]["verified_at"]
        observed = client.get(f"/api/v1/works/{work_id}/agents").json()
        assert observed["verification"]["state"] == "INVALID"
        assert observed["items"][0]["association"] == "USER_LINKED"
        assert observed["items"][0]["presence"] != "CURRENT"
        assert observed["items"][0]["lifecycle"] == "UNKNOWN"
    finally:
        service.close()


def test_snapshot_ages_and_callers_cannot_modify_shared_state(tmp_path):
    _database(tmp_path)
    _record_agent(tmp_path, "initial", AGENT)
    now = [datetime.now(UTC)]
    service = VerifiedAgentReads(tmp_path / "state.db", clock=lambda: now[0])
    try:
        good = _wait(service, lambda body: bool(body["items"]))
        good["items"][0]["agent_id"] = "caller-mutation"
        now[0] += timedelta(seconds=121)
        stale = service.read()
        assert stale["verification"]["state"] == "STALE"
        assert stale["verification"]["verified_at"] == good["verification"]["verified_at"]
        assert stale["items"][0]["agent_id"] == AGENT
        assert stale["items"][0]["lifecycle"] == "UNKNOWN"
        assert stale["items"][0]["presence"] == "STALE"
        assert stale["items"][0]["management_capabilities"]["observe"] is False
        supervision = service.read("supervision")
        assert supervision["observed_agents"][0]["presence"] == "STALE"
        assert supervision["observed_agents"][0]["lifecycle"] == "UNKNOWN"
        assert supervision["status"] == "EMPTY"  # session state is independent
    finally:
        service.close()


def test_consistent_snapshot_race_and_shutdown_prevent_late_publication(tmp_path, monkeypatch):
    _database(tmp_path)
    _record_agent(tmp_path, "initial", AGENT)
    original = r4_projection.verify_ledger
    entered, release = threading.Event(), threading.Event()

    def concurrent_edit(connection, **kwargs):
        errors = original(connection, **kwargs)
        with sqlite3.connect(tmp_path / "state.db") as writer:
            writer.execute("DROP TRIGGER evidence_ledger_events_no_update")
            writer.execute("UPDATE evidence_ledger_events SET result='tampered'")
        entered.set()
        assert release.wait(5)
        return errors

    monkeypatch.setattr(r4_projection, "verify_ledger", concurrent_edit)
    service = VerifiedAgentReads(tmp_path / "state.db")
    try:
        service.start()
        assert entered.wait(2)
        assert service.read()["verification"]["verified_at"] is None
        release.set()
        # Projection still reads the same pre-edit SQLite snapshot that passed.
        good = _wait(service, lambda body: bool(body["items"]))
        assert good["items"][0]["lifecycle"] == "RUNNING"
        monkeypatch.setattr(r4_projection, "verify_ledger", original)
        service.request_refresh()
        _wait(service, lambda body: body["verification"]["state"] == "INVALID")
    finally:
        release.set()
        service.close()
    before = service.read()
    service.request_refresh()
    assert service.read() == before
    assert not service._thread.is_alive()


def test_slow_recovery_does_not_delay_fast_view_publication(tmp_path, monkeypatch):
    _database(tmp_path)
    _record_agent(tmp_path, "initial", AGENT)
    entered, release = threading.Event(), threading.Event()
    original = r4_projection.R4ReadProjectionService.recovery

    def slow_recovery(projector):
        entered.set()
        assert release.wait(5)
        return original(projector)

    monkeypatch.setattr(r4_projection.R4ReadProjectionService, "recovery", slow_recovery)
    service = VerifiedAgentReads(tmp_path / "state.db")
    try:
        service.start()
        assert entered.wait(2)
        assert service.read()["verification"]["state"] == "VERIFIED"
        assert service.read("recovery")["verification"]["state"] == "PENDING"
        fast_timestamp = service.read()["verification"]["verified_at"]
        release.set()
        _wait(service, lambda body: body["verification"]["state"] == "VERIFIED", "recovery")
        assert service.read()["verification"]["verified_at"] == fast_timestamp
    finally:
        release.set()
        service.close()
