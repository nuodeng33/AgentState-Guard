"""Tests for transactionally ledger-backed supervision sessions."""

from __future__ import annotations

import sqlite3

import pytest

from agentguard.evidence.ledger import verify_ledger
from agentguard.policy.models import Decision, PolicyDecision
from agentguard.storage.db import StateDB
from agentguard.supervision.service import SupervisionService


def _decision(decision: Decision, *, checkpoint: bool = False) -> PolicyDecision:
    return PolicyDecision(
        decision=decision,
        severity="HIGH" if decision is Decision.BLOCK else "LOW",
        matched_rule_ids=("test-rule",),
        summary_code="TEST",
        evidence_refs=("evidence-1",),
        uncertainties=(),
        required_checks=(),
        requires_checkpoint=checkpoint,
        requires_manual_approval=decision is Decision.REVIEW,
    )


def _service(tmp_path):
    database = StateDB(tmp_path / "state.db")
    database.connect()
    return database, SupervisionService(database)


def test_review_session_requires_explicit_approval_before_active(tmp_path):
    database, service = _service(tmp_path)
    try:
        session = service.create("read_metadata", _decision(Decision.REVIEW))
        assert session.status == "AWAITING_APPROVAL"
        assert service.activate(session.supervision_session_id).status == "AWAITING_APPROVAL"
        assert service.approve(session.supervision_session_id).status == "APPROVED"
        assert service.activate(session.supervision_session_id).status == "ACTIVE"
        assert verify_ledger(database._conn) == []
    finally:
        database.close()


def test_blocked_session_cannot_be_approved_or_activated(tmp_path):
    database, service = _service(tmp_path)
    try:
        session = service.create("secret_read", _decision(Decision.BLOCK))
        assert session.status == "REJECTED"
        assert service.approve(session.supervision_session_id).status == "REJECTED"
        assert service.activate(session.supervision_session_id).status == "REJECTED"
    finally:
        database.close()


def test_checkpoint_requirement_blocks_activation_until_checkpoint_is_valid(tmp_path):
    database, service = _service(tmp_path)
    try:
        session = service.create("read_metadata", _decision(Decision.ALLOW, checkpoint=True))
        assert service.activate(session.supervision_session_id).status == "EVALUATED"
    finally:
        database.close()


def test_ledger_failure_rolls_back_session_creation(tmp_path, monkeypatch):
    database, service = _service(tmp_path)
    try:
        def fail_append(*args, **kwargs):
            raise sqlite3.IntegrityError("forced")

        monkeypatch.setattr("agentguard.supervision.service.EvidenceLedger.append", fail_append)
        with pytest.raises(sqlite3.IntegrityError):
            service.create("read_metadata", _decision(Decision.ALLOW))

        assert database._conn.execute("SELECT COUNT(*) FROM supervision_sessions").fetchone() == (0,)
        assert database._conn.execute("SELECT COUNT(*) FROM evidence_ledger_events").fetchone() == (0,)
        assert not database._conn.in_transaction
    finally:
        database.close()


def test_terminal_session_cannot_be_revived_and_completion_is_ledgered(tmp_path):
    database, service = _service(tmp_path)
    try:
        session = service.create("read_metadata", _decision(Decision.ALLOW))
        active = service.activate(session.supervision_session_id)
        completed = service.complete(active.supervision_session_id)
        assert completed.status == "COMPLETED"
        assert service.activate(completed.supervision_session_id).status == "COMPLETED"
        types = [row[0] for row in database._conn.execute("SELECT event_type FROM evidence_ledger_events")]
        assert "SESSION_COMPLETED" in types
    finally:
        database.close()


def test_sensitive_declared_intent_is_not_stored_or_raised(tmp_path):
    database, service = _service(tmp_path)
    secret = "synthetic-secret-value"
    try:
        session = service.create(f"read {secret}", _decision(Decision.ALLOW))
        raw = " ".join(str(item) for row in database._conn.iterdump() for item in (row,))
        assert secret not in raw
        assert session.status == "EVALUATED"
    finally:
        database.close()
