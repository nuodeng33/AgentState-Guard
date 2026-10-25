"""One complete ledger verification per coherent Agent read snapshot."""
from __future__ import annotations

from dataclasses import replace

import pytest

from agentguard.api import r4_projection
from agentguard.api.r4_projection import R4ReadProjectionService
from agentguard.evidence import discovery_adapter
from agentguard.evidence.ledger import EvidenceLedger, verify_ledger
from agentguard.evidence.models import EventType
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore
from tests.test_authoritative_workspace_binding import OBSERVED_AT, _snapshot
from tests.test_evidence_ledger import _event


@pytest.fixture
def database(tmp_path):
    value = StateDB(tmp_path / "state.db")
    value.connect()
    try:
        yield value
    finally:
        value.close()


def record_agents(database, count=6):
    snapshot = _snapshot()
    agent = snapshot.agents[0]
    evidence = next(item for item in snapshot.evidence if item.fact_type == "agent.metadata")
    agents = tuple(replace(agent, agent_id=f"agent-{index}", evidence_ids=(f"agent-evidence-{index}",)) for index in range(count))
    snapshot = replace(snapshot, agents=agents,
                       workspaces=(replace(snapshot.workspaces[0], agent_ids=tuple(item.agent_id for item in agents)),),
                       evidence=tuple(item for item in snapshot.evidence if item.fact_type != "agent.metadata") +
                       tuple(replace(evidence, evidence_id=f"agent-evidence-{index}") for index in range(count)))
    discovery_adapter.record_discovery_snapshot(database, snapshot, recorded_at=OBSERVED_AT)
    return snapshot


def projector(database, tmp_path):
    return R4ReadProjectionService(database, SnapshotStore(tmp_path / "snapshots"), clock=lambda: OBSERVED_AT)


def test_six_agents_share_one_snapshot_check_and_recheck_each_request(database, tmp_path, monkeypatch):
    record_agents(database)
    calls = []

    def counted(connection):
        calls.append(connection)
        return verify_ledger(connection)

    monkeypatch.setattr(r4_projection, "verify_ledger", counted)
    monkeypatch.setattr(discovery_adapter, "verify_ledger_snapshot", counted)
    result = projector(database, tmp_path).agents()
    assert len(result["items"]) == 6
    assert all(item["workspace"]["status"] == "BOUND" for item in result["items"])
    assert calls == [database._conn]
    assert projector(database, tmp_path).agents() == result
    assert calls == [database._conn, database._conn]
    assert not database._conn.in_transaction


def test_writer_commit_during_verification_is_seen_only_by_next_snapshot(database, tmp_path, monkeypatch):
    snapshot = record_agents(database, 1)
    writer = StateDB(database.db_path)
    writer.connect()
    binding = next(event for event in discovery_adapter.discovery_events(snapshot, recorded_at=OBSERVED_AT)
                   if event.event_type == EventType.WORKSPACE_LINKED)
    committed = []

    def append_after_verification(connection):
        result = verify_ledger(connection)
        if not committed:
            with writer.transaction():
                EvidenceLedger().append(writer._conn, replace(binding, event_id="second-binding",
                    payload_safe={**binding.payload_safe, "binding_id": "second-binding"}))
            committed.append(True)
        return result

    monkeypatch.setattr(r4_projection, "verify_ledger", append_after_verification)
    monkeypatch.setattr(discovery_adapter, "verify_ledger_snapshot", append_after_verification)
    try:
        first = projector(database, tmp_path).agents()
        assert first["items"][0]["workspace"]["status"] == "BOUND"
        second = projector(database, tmp_path).agents()
        assert second["items"][0]["workspace"]["reason_code"] == "WORKSPACE_BINDING_CONFLICT"
        assert verify_ledger(database._conn) == []
    finally:
        writer.close()


def test_invalid_ledger_fails_closed_even_after_previous_valid_read(database, tmp_path):
    record_agents(database, 1)
    assert projector(database, tmp_path).agents()["items"][0]["workspace"]["status"] == "BOUND"
    # Isolated damage fixture; application append-only triggers stay intact in production.
    trigger = database._conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='evidence_ledger_events' AND sql LIKE '%UPDATE%'").fetchone()[0]
    database._conn.execute(f'DROP TRIGGER "{trigger}"')
    database._conn.execute("UPDATE evidence_ledger_events SET curr_hash=? WHERE sequence=1", ("f" * 64,))
    database._conn.commit()
    assert projector(database, tmp_path).agents()["reason_code"] == "R4_LEDGER_INVALID"
    assert projector(database, tmp_path).agents()["items"] == []
    assert discovery_adapter.resolve_verified_workspace_binding(database._conn)["reason_code"] == "WORKSPACE_LEDGER_INVALID"


def test_snapshot_scope_never_commits_outer_transaction_and_rejects_expired_resolver(database):
    record_agents(database, 1)
    outside = StateDB(database.db_path)
    outside.connect()
    try:
        before = outside._conn.execute("SELECT COUNT(*) FROM evidence_ledger_events").fetchone()[0]
        with database.transaction():
            EvidenceLedger().append(database._conn, _event(event_id="uncommitted-fixture"))
            with discovery_adapter.verified_workspace_bindings(database._conn) as resolve:
                assert resolve()["status"] == "BOUND"
            assert database._conn.in_transaction
            assert outside._conn.execute("SELECT COUNT(*) FROM evidence_ledger_events").fetchone()[0] == before
        assert outside._conn.execute("SELECT COUNT(*) FROM evidence_ledger_events").fetchone()[0] == before + 1
        with pytest.raises(RuntimeError, match="WORKSPACE_SNAPSHOT_CLOSED"):
            resolve()
    finally:
        outside.close()


def test_snapshot_scope_cleans_up_after_resolver_error(database):
    record_agents(database, 1)
    with pytest.raises(RuntimeError, match="fixture failure"), discovery_adapter.verified_workspace_bindings(database._conn) as resolve:
        assert resolve()["status"] == "BOUND"
        raise RuntimeError("fixture failure")
    assert not database._conn.in_transaction
    assert discovery_adapter.resolve_verified_workspace_binding(database._conn)["status"] == "BOUND"
