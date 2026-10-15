"""Tests for deterministic local P4 policy decisions."""

from __future__ import annotations

from agentguard.policy.engine import evaluate
from agentguard.policy.models import Decision, PolicyInput
from agentguard.recovery.coverage import RecoveryCoverageFacts, RecoveryCoverageStatus


def _input(**overrides):
    values = {
        "intent_kind": "discovery",
        "effect_kind": "read_metadata",
        "target_refs": ("runtime-1",),
        "execution_domain_id": "linux-container",
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
        _input(intent_kind="change", effect_kind="provider_config_change"),
        recovery_facts=_coverage(
            RecoveryCoverageStatus.COMPLETE,
            authorized=1,
            intact=1,
        ),
    )

    assert decision.decision is Decision.REVIEW
    assert decision.requires_manual_approval is True
    assert "P4-REVIEW-001" in decision.matched_rule_ids


def test_scope_drift_and_unknown_upload_are_blocked():
    scope_drift = evaluate(_input(target_refs=("outside-scope",)))
    upload = evaluate(_input(intent_kind="upload", effect_kind="remote_upload"))

    assert scope_drift.decision is Decision.BLOCK
    assert scope_drift.summary_code == "SCOPE_DRIFT_BLOCKED"
    assert upload.decision is Decision.BLOCK
    assert upload.summary_code == "UNKNOWN_REMOTE_UPLOAD_BLOCKED"


def test_configuration_dependency_ci_and_incomplete_scope_require_review():
    configuration = evaluate(_input(intent_kind="change", effect_kind="dependency_lock_change"))
    incomplete_scope = evaluate(_input(declared_scope=()))

    assert configuration.decision is Decision.REVIEW
    assert configuration.requires_manual_approval is True
    assert incomplete_scope.decision is Decision.REVIEW
    assert incomplete_scope.summary_code == "DECLARED_SCOPE_INCOMPLETE"


def _coverage(status, *, authorized=0, intact=0):
    return RecoveryCoverageFacts(
        status=status,
        checkpoint_id=None,
        requested_targets=1,
        authorized_snapshot_targets=authorized,
        intact_manifest_blob_targets=intact,
        test_restore_verified_targets=None,
        test_restore_status="NOT_RUN_P6",
        reason_code="TEST_RECOVERY_FACT",
        evidence_refs=(),
    )


def test_missing_checkpoint_requires_review_for_change():
    decision = evaluate(
        _input(intent_kind="change", effect_kind="provider_config_change"),
        recovery_facts=_coverage(RecoveryCoverageStatus.MISSING),
    )

    assert decision.decision is Decision.REVIEW
    assert decision.requires_checkpoint is True
    assert decision.requires_manual_approval is True


def test_insufficient_authoritative_coverage_requires_review():
    decision = evaluate(
        _input(intent_kind="change", effect_kind="provider_config_change"),
        recovery_facts=_coverage(RecoveryCoverageStatus.INSUFFICIENT),
    )

    assert decision.decision is Decision.REVIEW
    assert decision.summary_code == "RECOVERY_COVERAGE_REVIEW"


def test_unreachable_or_insufficient_recovery_evidence_is_unknown():
    unreachable = evaluate(
        _input(intent_kind="change", effect_kind="provider_config_change"),
        recovery_facts=_coverage(RecoveryCoverageStatus.UNREACHABLE),
    )
    insufficient = evaluate(
        _input(intent_kind="change", effect_kind="provider_config_change"),
        recovery_facts=_coverage(RecoveryCoverageStatus.EVIDENCE_INSUFFICIENT),
    )

    assert unreachable.decision is Decision.UNKNOWN
    assert insufficient.decision is Decision.UNKNOWN
    assert unreachable.summary_code == "RECOVERY_EVIDENCE_UNAVAILABLE"


def test_unreachable_execution_domain_is_unknown():
    decision = evaluate(_input(execution_domain_id="unreachable"))

    assert decision.decision is Decision.UNKNOWN
    assert decision.summary_code == "EXECUTION_DOMAIN_UNREACHABLE"
