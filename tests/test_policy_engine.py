"""Tests for deterministic local P4 policy decisions."""

from __future__ import annotations

from agentguard.policy.engine import evaluate
from agentguard.policy.models import Decision, PolicyInput


def _input(**overrides):
    values = {
        "intent_kind": "discovery",
        "effect_kind": "read_metadata",
        "target_refs": ("runtime-1",),
        "execution_domain_id": "linux-container",
        "declared_scope": ("agentguard/discovery",),
        "requested_capabilities": (),
        "network_effect": False,
        "privilege_effect": False,
        "destructive_effect": False,
        "secret_access": False,
        "checkpoint_status": "not_required",
        "recovery_coverage": 1.0,
        "evidence_refs": ("evidence-1",),
    }
    values.update(overrides)
    return PolicyInput(**values)


def test_read_only_discovery_is_explicitly_allowed():
    decision = evaluate(_input())

    assert decision.decision is Decision.ALLOW
    assert decision.matched_rule_ids == ("P4-ALLOW-001",)


def test_secret_access_blocks_even_when_allow_rule_matches():
    decision = evaluate(_input(secret_access=True))

    assert decision.decision is Decision.BLOCK
    assert "P4-BLOCK-002" in decision.matched_rule_ids


def test_missing_evidence_is_unknown_without_implicit_allow():
    decision = evaluate(_input(evidence_refs=()))

    assert decision.decision is Decision.UNKNOWN
    assert "P4-UNKNOWN-001" in decision.matched_rule_ids


def test_provider_configuration_requires_manual_review():
    decision = evaluate(
        _input(intent_kind="change", effect_kind="provider_config_change")
    )

    assert decision.decision is Decision.REVIEW
    assert decision.requires_manual_approval is True
    assert "P4-REVIEW-001" in decision.matched_rule_ids
