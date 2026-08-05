"""Tests for the append-only R4 Evidence Ledger."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from agentguard.evidence.canonical import canonical_json, payload_digest
from agentguard.evidence.ledger import EvidenceLedger, verify_ledger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.evidence.privacy import EvidencePrivacyError
from agentguard.storage.migrations import MigrationEngine


def _connection(tmp_path):
    path = tmp_path / "ledger.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT, checksum TEXT, duration_ms INTEGER)"
    )
    connection.commit()
    MigrationEngine(path).migrate(connection)
    return connection


def _event(*, payload_safe=None, evidence_refs=("evidence-1",), event_id="event-1"):
    return EvidenceEvent(
        schema_version=1,
        event_id=event_id,
        recorded_at=datetime(2026, 8, 5, tzinfo=UTC),
        observed_at=datetime(2026, 8, 4, tzinfo=UTC),
        event_family=EventFamily.DISCOVERY,
        event_type=EventType.RUNTIME_DETECTED,
        source="discovery-adapter",
        result="observed",
        execution_domain_id="linux-container",
        supervision_session_id=None,
        transaction_id=None,
        checkpoint_id=None,
        subject_ref="runtime-1",
        evidence_refs=evidence_refs,
        payload_safe=payload_safe or {"runtime_kind": "python"},
    )


def test_canonical_payload_is_stable_across_key_and_reference_order():
    assert canonical_json({"b": True, "a": [2, 1]}) == canonical_json(
        {"a": [2, 1], "b": True}
    )
    assert payload_digest({"b": True, "a": [2, 1]}) == payload_digest(
        {"a": [2, 1], "b": True}
    )


def test_sensitive_payload_is_rejected_without_echoing_value():
    with pytest.raises(EvidencePrivacyError) as raised:
        _event(payload_safe={"access_token": "secret-value"})

    assert "secret-value" not in str(raised.value)


def test_append_and_verify_chain_binds_all_authoritative_fields(tmp_path):
    connection = _connection(tmp_path)
    ledger = EvidenceLedger()
    try:
        first = ledger.append(connection, _event(event_id="event-1"))
        second = ledger.append(connection, _event(event_id="event-2"))
        connection.commit()

        assert first.sequence == 1
        assert second.sequence == 2
        assert verify_ledger(connection) == []

        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(
                "UPDATE evidence_ledger_events SET subject_ref = 'tampered' WHERE sequence = 2"
            )
    finally:
        connection.close()


def test_database_rejects_update_and_delete(tmp_path):
    connection = _connection(tmp_path)
    ledger = EvidenceLedger()
    try:
        ledger.append(connection, _event())
        connection.commit()
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("DELETE FROM evidence_ledger_events WHERE sequence = 1")
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(
                "UPDATE evidence_ledger_events SET source = 'changed' WHERE sequence = 1"
            )
    finally:
        connection.close()
