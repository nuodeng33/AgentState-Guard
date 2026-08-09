"""Contracts for authoritative R4 P5 AI supervision."""

from __future__ import annotations

import pytest

from agentguard.ai.provider import AIResult, OpenAICompatibleProvider, ProviderConfig
from agentguard.ai.supervisor import AIAssessment, AISupervisor
from agentguard.policy.models import Decision, PolicyDecision
from agentguard.storage.db import StateDB
from agentguard.supervision.service import SupervisionService


class _Provider:
    model = "test-model"

    def assess(self, authority_package):
        assert "declared_intent" not in authority_package
        return AIAssessment(
            decision="REVIEW",
            severity="MEDIUM",
            summary="Authoritative evidence requires review.",
            evidence_refs=tuple(authority_package["evidence_refs"]),
            uncertainties=(),
            required_checks=("human_review",),
            requires_checkpoint=False,
            requires_manual_approval=True,
        )


class _AllowProvider:
    model = "unsafe-test-model"

    def assess(self, authority_package):
        return AIAssessment(
            decision="ALLOW",
            severity="LOW",
            summary="Caller-controlled upgrade attempt.",
            evidence_refs=tuple(authority_package["evidence_refs"]),
            uncertainties=(),
            required_checks=(),
            requires_checkpoint=False,
            requires_manual_approval=False,
        )


def _decision(decision: Decision) -> PolicyDecision:
    return PolicyDecision(
        decision=decision,
        severity="HIGH" if decision is Decision.BLOCK else "LOW",
        matched_rule_ids=("test-rule",),
        summary_code="TEST",
        evidence_refs=("evidence-1",),
        uncertainties=(),
        required_checks=(),
        requires_checkpoint=False,
        requires_manual_approval=decision is Decision.REVIEW,
    )


def _service(tmp_path):
    database = StateDB(tmp_path / "state.db")
    database.connect()
    return database, SupervisionService(database)


def test_assessment_uses_authoritative_session_evidence_and_ledgers_result(tmp_path):
    database, sessions = _service(tmp_path)
    try:
        session = sessions.create("synthetic-secret-value", _decision(Decision.ALLOW))
        result = AISupervisor(database, _Provider()).assess(session.supervision_session_id)

        assert result.decision == "REVIEW"
        assert database._conn.execute(
            "SELECT COUNT(*) FROM evidence_ledger_events WHERE event_type = 'AI_ASSESSED'"
        ).fetchone() == (1,)
        assert "synthetic-secret-value" not in "\n".join(database._conn.iterdump())
    finally:
        database.close()


def test_policy_block_cannot_be_overridden_by_ai(tmp_path):
    database, sessions = _service(tmp_path)
    try:
        session = sessions.create("secret", _decision(Decision.BLOCK))
        result = AISupervisor(database, _Provider()).assess(session.supervision_session_id)

        assert result.decision == "BLOCK"
        assert result.requires_manual_approval is False
    finally:
        database.close()


def test_invalid_provider_response_degrades_without_ledger_write(tmp_path):
    database, sessions = _service(tmp_path)
    try:
        session = sessions.create("read", _decision(Decision.ALLOW))
        result = AISupervisor(database, object()).assess(session.supervision_session_id)

        assert result.decision == "UNKNOWN"
        assert database._conn.execute(
            "SELECT COUNT(*) FROM evidence_ledger_events WHERE event_type = 'AI_ASSESSED'"
        ).fetchone() == (0,)
    finally:
        database.close()


def test_assessment_cache_avoids_duplicate_provider_and_ledger_calls(tmp_path):
    database, sessions = _service(tmp_path)
    provider = _Provider()
    try:
        session = sessions.create("read", _decision(Decision.ALLOW))
        supervisor = AISupervisor(database, provider)
        first = supervisor.assess(session.supervision_session_id)
        second = supervisor.assess(session.supervision_session_id)

        assert second == first
        assert database._conn.execute(
            "SELECT COUNT(*) FROM evidence_ledger_events WHERE event_type = 'AI_ASSESSED'"
        ).fetchone() == (1,)
    finally:
        database.close()


def test_ai_cannot_upgrade_authoritative_unknown_policy(tmp_path):
    database, sessions = _service(tmp_path)
    try:
        session = sessions.create("read", _decision(Decision.UNKNOWN))
        result = AISupervisor(database, _AllowProvider()).assess(session.supervision_session_id)

        assert result.decision == "UNKNOWN"
        assert result.requires_manual_approval is False
        assert "RECOVERY_VERIFIED" not in {
            row[0]
            for row in database._conn.execute("SELECT event_type FROM evidence_ledger_events")
        }
    finally:
        database.close()


def test_openai_compatible_provider_maps_legacy_result_conservatively(monkeypatch):
    provider = OpenAICompatibleProvider(
        ProviderConfig(
            base_url="https://provider.invalid/v1",
            api_key="memory-only",
            model="review-model",
        )
    )
    monkeypatch.setattr(
        provider,
        "analyze",
        lambda _context: AIResult(
            status="attention",
            severity="critical",
            summary="Potential risk requires review.",
            possible_causes=["untrusted root cause"],
            recommended_checks=["untrusted command"],
            evidence=["untrusted evidence"],
        ),
    )

    assessment = provider.assess(
        {
            "policy_decision": "REVIEW",
            "evidence_refs": ("server-evidence",),
            "requires_checkpoint": True,
            "requires_manual_approval": True,
        }
    )

    assert provider.model == "review-model"
    assert assessment == AIAssessment(
        decision="REVIEW",
        severity="HIGH",
        summary="Potential risk requires review.",
        evidence_refs=("server-evidence",),
        uncertainties=(),
        required_checks=("human_review",),
        requires_checkpoint=True,
        requires_manual_approval=True,
    )


def test_openai_compatible_provider_error_is_unavailable_not_an_assessment(monkeypatch):
    provider = OpenAICompatibleProvider(
        ProviderConfig(base_url="https://provider.invalid/v1", model="review-model")
    )
    monkeypatch.setattr(
        provider,
        "analyze",
        lambda _context: AIResult(
            status="error",
            severity="low",
            summary="provider unavailable",
        ),
    )

    with pytest.raises(RuntimeError, match="AI_ASSESSMENT_UNAVAILABLE"):
        provider.assess(
            {
                "policy_decision": "REVIEW",
                "evidence_refs": ("server-evidence",),
                "requires_checkpoint": True,
                "requires_manual_approval": True,
            }
        )
