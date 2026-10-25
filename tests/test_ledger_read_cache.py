"""Exact raw-row read verification cache; no ledger writes or validation shortcuts."""
from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from agentguard.evidence import ledger
from agentguard.evidence.models import EventType
from agentguard.storage.db import StateDB
from tests.test_evidence_ledger import _event


@pytest.fixture
def database(tmp_path):
    value = StateDB(tmp_path / "state.db")
    value.connect()
    with value.transaction():
        for index in range(3):
            ledger.EvidenceLedger().append(value._conn, _event(event_id=f"event-{index}"))
    try:
        yield value
    finally:
        value.close()


def disable_triggers(connection):
    for name, in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='evidence_ledger_events'").fetchall():
        connection.execute(f'DROP TRIGGER "{name}"')
    connection.commit()


def counted(monkeypatch):
    ledger._READ_VALIDATED.clear()
    calls = []
    original = ledger._verify_ledger_rows

    def verify(rows):
        calls.append(len(rows))
        return original(rows)

    monkeypatch.setattr(ledger, "_verify_ledger_rows", verify)
    return calls


def test_identical_rows_share_only_success_and_return_fresh_lists(database, monkeypatch):
    calls = counted(monkeypatch)
    first = ledger.verify_ledger_snapshot(database._conn)
    first.append("caller-local")
    assert ledger.verify_ledger_snapshot(database._conn) == []
    assert calls == [3]
    # The original writer/public verification path still runs the full validator.
    assert ledger.verify_ledger(database._conn) == []
    assert calls == [3, 3]


@pytest.mark.parametrize("mutation", [
    "UPDATE evidence_ledger_events SET payload_safe_json='{ \"runtime_kind\":\"python\" }' WHERE sequence=1",
    "UPDATE evidence_ledger_events SET evidence_refs_json='[ \"evidence-1\" ]' WHERE sequence=1",
    "UPDATE evidence_ledger_events SET source='old-row-tamper' WHERE sequence=1",
    "UPDATE evidence_ledger_events SET curr_hash='tampered' WHERE sequence=1",
    "UPDATE evidence_ledger_events SET source=CAST(source AS BLOB) WHERE sequence=1",
    "DELETE FROM evidence_ledger_events WHERE sequence=1",
])
def test_old_row_changes_with_unchanged_head_always_invalidate(database, monkeypatch, mutation):
    calls = counted(monkeypatch)
    assert ledger.verify_ledger_snapshot(database._conn) == []
    head = database._conn.execute("SELECT sequence,curr_hash FROM evidence_ledger_events ORDER BY sequence DESC LIMIT 1").fetchone()
    disable_triggers(database._conn)
    database._conn.execute(mutation)
    database._conn.commit()
    assert database._conn.execute("SELECT sequence,curr_hash FROM evidence_ledger_events ORDER BY sequence DESC LIMIT 1").fetchone() == head
    expected = ledger.verify_ledger(database._conn)
    assert expected
    before = len(calls)
    assert ledger.verify_ledger_snapshot(database._conn) == expected
    assert ledger.verify_ledger_snapshot(database._conn) == expected
    assert len(calls) == before + 2  # Invalid results never enter the cache.


def test_legitimately_rebuilt_old_row_and_descendant_hashes_are_revalidated(database, monkeypatch):
    calls = counted(monkeypatch)
    assert ledger.verify_ledger_snapshot(database._conn) == []
    replacement = sqlite3.connect(":memory:")
    database._conn.backup(replacement)
    disable_triggers(replacement)
    replacement.execute("DELETE FROM evidence_ledger_events")
    for index in range(3):
        ledger.EvidenceLedger().append(replacement, _event(event_id=f"event-{index}", payload_safe={"runtime_kind": "changed"} if index == 0 else None))
    replacement.commit()
    assert ledger.verify_ledger_snapshot(replacement) == []
    assert calls == [3, 3]
    replacement.close()


def test_fingerprint_covers_every_column_types_lengths_and_row_order():
    original = tuple("field" for _ in range(19))
    key = ledger._ledger_fingerprint([original])
    for column in range(19):
        for value in (None, 1, 1.0, b"field", "other"):
            changed = list(original); changed[column] = value
            assert ledger._ledger_fingerprint([tuple(changed)]) != key
    left = (*original[:-2], "ab", "c")
    right = (*original[:-2], "a", "bc")
    assert ledger._ledger_fingerprint([left]) != ledger._ledger_fingerprint([right])
    assert ledger._ledger_fingerprint([left, right]) != ledger._ledger_fingerprint([right, left])
    assert ledger._ledger_fingerprint([(*original[:-1], 0.0)]) != ledger._ledger_fingerprint([(*original[:-1], -0.0)])
    assert ledger._ledger_fingerprint([(*original[:-1], object())]) is None


def test_success_cache_is_bounded_at_64_generations(database, monkeypatch):
    calls = counted(monkeypatch)
    for index in range(65):
        with database.transaction():
            ledger.EvidenceLedger().append(database._conn, _event(event_id=f"append-{index}"))
        assert ledger.verify_ledger_snapshot(database._conn) == []
    assert len(calls) == 65
    assert len(ledger._READ_VALIDATED) == 64
    assert all(isinstance(key, bytes) and len(key) == 32 and value is None for key, value in ledger._READ_VALIDATED.items())


def test_concurrent_identical_snapshot_has_one_full_validation(database, monkeypatch):
    calls = counted(monkeypatch)
    barrier = threading.Barrier(2)
    def read():
        connection = sqlite3.connect(database.db_path)
        try:
            barrier.wait(timeout=2)
            return ledger.verify_ledger_snapshot(connection)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda unused: read(), range(2)))
    assert results == [[], []]
    assert calls == [3]


def test_projection_freshness_is_not_cached(database, tmp_path):
    from agentguard.api.r4_projection import R4ReadProjectionService
    from agentguard.storage.snapshots import SnapshotStore

    observed = datetime(2026, 10, 25, tzinfo=UTC)
    with database.transaction():
        ledger.EvidenceLedger().append(database._conn, replace(_event(event_id="runtime-current"),
            event_type=EventType.RUNTIME_DETECTED, observed_at=observed, result="available",
            payload_safe={"snapshot_id": "current-snapshot", "fact_type": "runtime.metadata", "value": {"runtime_kind": "SELF_RUNTIME"}}))
    for clock, expected in [(observed, "FRESH"), (observed.replace(hour=1), "STALE")]:
        value = R4ReadProjectionService(database, SnapshotStore(tmp_path / "snapshots"), clock=lambda value=clock: value).runtime()
        assert value["items"][0]["freshness"] == expected


@pytest.mark.parametrize("time_value", ["2026-08-05T00:00:00", "invalid-time", "2026-08-05T01:00:00+01:00"])
def test_noncanonical_or_naive_time_keeps_legacy_validation_without_cache(database, monkeypatch, time_value):
    calls = counted(monkeypatch)
    disable_triggers(database._conn)
    database._conn.execute("UPDATE evidence_ledger_events SET recorded_at=? WHERE sequence=1", (time_value,))
    database._conn.commit()
    expected = ledger.verify_ledger(database._conn)
    assert ledger.verify_ledger_snapshot(database._conn) == expected
    assert ledger.verify_ledger_snapshot(database._conn) == expected
    assert calls == [3, 3, 3]
    assert not ledger._READ_VALIDATED


def test_uncacheable_values_use_full_validator_without_reusing_success(database, monkeypatch):
    calls = counted(monkeypatch)
    monkeypatch.setattr(ledger, "_ledger_fingerprint", lambda rows: None)
    assert ledger.verify_ledger_snapshot(database._conn) == []
    assert ledger.verify_ledger_snapshot(database._conn) == []
    assert calls == [3, 3]
    assert not ledger._READ_VALIDATED


def test_runtime_reads_only_the_snapshot_whose_rows_were_validated(database, tmp_path, monkeypatch):
    from agentguard.api import r4_projection
    from agentguard.storage.snapshots import SnapshotStore

    first = replace(_event(event_id="runtime-snapshot-first"), event_type=EventType.RUNTIME_DETECTED,
        result="available", payload_safe={"snapshot_id": "first-snapshot", "fact_type": "runtime.metadata",
                                         "value": {"runtime_kind": "FIRST"}})
    second = replace(first, event_id="runtime-snapshot-second", payload_safe={"snapshot_id": "second-snapshot",
        "fact_type": "runtime.metadata", "value": {"runtime_kind": "SECOND"}})
    with database.transaction():
        ledger.EvidenceLedger().append(database._conn, first)
    writer = StateDB(database.db_path)
    writer.connect()
    verify = r4_projection.verify_ledger
    committed = []

    def append_after_check(connection):
        problems = verify(connection)
        if not committed:
            with writer.transaction():
                ledger.EvidenceLedger().append(writer._conn, second)
            committed.append(True)
        return problems

    monkeypatch.setattr(r4_projection, "verify_ledger", append_after_check)
    try:
        projection = r4_projection.R4ReadProjectionService(database, SnapshotStore(tmp_path / "snapshots"))
        assert projection.runtime()["items"][0]["runtime_type"] == "FIRST"
        assert projection.runtime()["items"][0]["runtime_type"] == "SECOND"
        assert not database._conn.in_transaction
    finally:
        writer.close()


def test_cache_hit_still_reads_every_raw_authority_column(database, monkeypatch):
    calls = counted(monkeypatch)
    queries = []
    database._conn.set_trace_callback(queries.append)
    try:
        assert ledger.verify_ledger_snapshot(database._conn) == []
        assert ledger.verify_ledger_snapshot(database._conn) == []
    finally:
        database._conn.set_trace_callback(None)
    reads = [query for query in queries if "FROM evidence_ledger_events ORDER BY sequence ASC" in query]
    assert len(reads) == 2
    assert all("payload_safe_json" in query and "curr_hash" in query and "subject_ref" in query for query in reads)
    assert calls == [3]


def test_public_binding_authority_entry_always_fully_validates_even_when_read_cache_is_warm(database, monkeypatch):
    from agentguard.evidence import discovery_adapter

    assert ledger.verify_ledger_snapshot(database._conn) == []
    calls = []

    def full(connection):
        calls.append(connection)
        return ledger.verify_ledger(connection)

    monkeypatch.setattr(discovery_adapter, "verify_ledger", full)
    for _ in range(2):
        assert discovery_adapter.resolve_verified_workspace_binding(database._conn)["reason_code"] == "WORKSPACE_BINDING_MISSING"
    assert calls == [database._conn, database._conn]
    disable_triggers(database._conn)
    database._conn.execute("UPDATE evidence_ledger_events SET source='old-row-change' WHERE sequence=1")
    database._conn.commit()
    assert discovery_adapter.resolve_verified_workspace_binding(database._conn)["reason_code"] == "WORKSPACE_LEDGER_INVALID"
    assert calls == [database._conn] * 3


def test_attachment_writer_fully_validates_a_warm_read_cache_and_rejects_old_row_tampering(tmp_path, monkeypatch):
    from agentguard.work import service as work_service
    from tests.test_work_api import _client, _record_agent, _request, _work

    client = _client(tmp_path)
    first_work, second_work = _work(client), _work(client)
    agent_id = "external-agent-" + "c" * 24
    _record_agent(tmp_path, "attach-warm-cache", agent_id)
    assert client.get("/api/v1/agents").status_code == 200
    calls = []

    def full(connection):
        assert connection.in_transaction
        calls.append(connection)
        return ledger.verify_ledger(connection)

    monkeypatch.setattr(work_service, "verify_ledger", full)
    attached = client.post(f"/api/v1/works/{first_work}/agents", json=_request(expected_revision=1, agent_id=agent_id))
    assert attached.status_code == 201, attached.text
    assert len(calls) == 1
    connection = sqlite3.connect(tmp_path / "state.db")
    try:
        disable_triggers(connection)
        connection.execute("UPDATE evidence_ledger_events SET source='old-row-tamper' WHERE sequence=1")
        connection.commit()
    finally:
        connection.close()
    refused = client.post(f"/api/v1/works/{second_work}/agents", json=_request(expected_revision=1, agent_id=agent_id))
    assert refused.status_code == 409, refused.text
    assert refused.json()["reason_code"] == "WORK_AGENT_EVIDENCE_INVALID"
    assert len(calls) == 2
