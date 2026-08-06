"""Offline R4-P4 supervision closure tests."""

from __future__ import annotations

import socket
import urllib.request

import pytest

from agentguard.evidence.ledger import verify_ledger
from agentguard.policy.engine import evaluate
from agentguard.policy.models import PolicyInput
from agentguard.storage.db import StateDB
from agentguard.supervision.service import SupervisionService


def _input(**overrides) -> PolicyInput:
    values = {
        "intent_kind": "discovery",
        "effect_kind": "read_metadata",
        "target_refs": ("runtime-1",),
        "execution_domain_id": "local",
        "declared_scope": ("runtime-1",),
        "requested_capabilities": (),
        "network_effect": False,
        "privilege_effect": False,
        "destructive_effect": False,
        "secret_access": False,
        "evidence_refs": ("evidence-1",),
    }
    values.update(overrides)
    return PolicyInput(**values)


def test_offline_allow_session_completes_and_ledger_survives_reconnect(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("network"))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("network"))
    path = tmp_path / "state.db"
    database = StateDB(path)
    database.connect()
    service = SupervisionService(database)
    session = service.create("discovery", evaluate(_input()))
    assert service.activate(session.supervision_session_id).status == "ACTIVE"
    assert service.complete(session.supervision_session_id).status == "COMPLETED"
    database.close()

    reopened = StateDB(path)
    reopened.connect()
    try:
        assert SupervisionService(reopened)._read(session.supervision_session_id).status == "COMPLETED"
        assert verify_ledger(reopened._conn) == []
    finally:
        reopened.close()


def test_review_needs_approval_and_checkpoint(tmp_path):
    database = StateDB(tmp_path / "state.db")
    database.connect()
    try:
        service = SupervisionService(database)
        decision = evaluate(_input(intent_kind="change", effect_kind="provider_config_change"))
        session = service.create("provider_change", decision)
        assert service.activate(session.supervision_session_id).status == "AWAITING_APPROVAL"
        assert service.approve(session.supervision_session_id).status == "APPROVED"
        assert service.activate(session.supervision_session_id).status == "APPROVED"
    finally:
        database.close()


def test_block_and_unknown_do_not_activate_or_leak_synthetic_secret(tmp_path):
    database = StateDB(tmp_path / "state.db")
    secret = "synthetic-closure-secret"
    database.connect()
    try:
        service = SupervisionService(database)
        blocked = service.create(f"read {secret}", evaluate(_input(secret_access=True, target_refs=(secret,))))
        assert service.approve(blocked.supervision_session_id).status == "REJECTED"
        assert service.activate(blocked.supervision_session_id).status == "REJECTED"
        unknown = service.create("unknown", evaluate(_input(execution_domain_id=None)))
        assert unknown.status == "EVALUATED"
        assert service.activate(unknown.supervision_session_id).status == "EVALUATED"
        assert secret not in "\n".join(database._conn.iterdump())
    finally:
        database.close()
