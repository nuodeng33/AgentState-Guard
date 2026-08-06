"""Pure deterministic local policy evaluation."""

from __future__ import annotations

from agentguard.recovery.coverage import RecoveryCoverageFacts, RecoveryCoverageStatus

from .models import Decision, PolicyDecision, PolicyInput


def _decision(
    decision: Decision,
    rule_ids: tuple[str, ...],
    *,
    summary_code: str,
    evidence_refs: tuple[str, ...],
    requires_checkpoint: bool = False,
    requires_manual_approval: bool = False,
    uncertainties: tuple[str, ...] = (),
) -> PolicyDecision:
    severity = {
        Decision.BLOCK: "HIGH",
        Decision.REVIEW: "MEDIUM",
        Decision.UNKNOWN: "UNKNOWN",
        Decision.ALLOW: "LOW",
    }[decision]
    return PolicyDecision(
        decision=decision,
        severity=severity,
        matched_rule_ids=rule_ids,
        summary_code=summary_code,
        evidence_refs=tuple(sorted(set(evidence_refs))),
        uncertainties=uncertainties,
        required_checks=("evidence_refs",) if decision is Decision.UNKNOWN else (),
        requires_checkpoint=requires_checkpoint,
        requires_manual_approval=requires_manual_approval,
    )


def evaluate(
    policy_input: PolicyInput,
    *,
    recovery_facts: RecoveryCoverageFacts | None = None,
) -> PolicyDecision:
    """Evaluate structured local facts with fixed BLOCK > REVIEW > UNKNOWN > ALLOW precedence."""
    if policy_input.secret_access:
        return _decision(
            Decision.BLOCK,
            ("P4-BLOCK-002",),
            summary_code="SECRET_ACCESS_BLOCKED",
            evidence_refs=policy_input.evidence_refs,
        )
    if policy_input.destructive_effect:
        return _decision(
            Decision.BLOCK,
            ("P4-BLOCK-004",),
            summary_code="DESTRUCTIVE_EFFECT_BLOCKED",
            evidence_refs=policy_input.evidence_refs,
        )
    if policy_input.privilege_effect:
        return _decision(
            Decision.BLOCK,
            ("P4-BLOCK-003",),
            summary_code="PRIVILEGE_EFFECT_BLOCKED",
            evidence_refs=policy_input.evidence_refs,
        )
    if not policy_input.evidence_refs or not policy_input.execution_domain_id:
        return _decision(
            Decision.UNKNOWN,
            ("P4-UNKNOWN-001",),
            summary_code="EVIDENCE_INSUFFICIENT",
            evidence_refs=policy_input.evidence_refs,
            uncertainties=("EVIDENCE_INSUFFICIENT",),
        )
    if policy_input.execution_domain_id == "unreachable":
        return _decision(
            Decision.UNKNOWN,
            ("P4-UNKNOWN-002",),
            summary_code="EXECUTION_DOMAIN_UNREACHABLE",
            evidence_refs=policy_input.evidence_refs,
            uncertainties=("EXECUTION_DOMAIN_UNREACHABLE",),
        )
    if not policy_input.declared_scope:
        return _decision(
            Decision.REVIEW,
            ("P4-REVIEW-005",),
            summary_code="DECLARED_SCOPE_INCOMPLETE",
            evidence_refs=policy_input.evidence_refs,
            requires_manual_approval=True,
        )
    if not set(policy_input.target_refs).issubset(policy_input.declared_scope):
        return _decision(
            Decision.BLOCK,
            ("P4-BLOCK-005",),
            summary_code="SCOPE_DRIFT_BLOCKED",
            evidence_refs=policy_input.evidence_refs,
        )
    if policy_input.intent_kind == "upload" and policy_input.effect_kind == "remote_upload":
        return _decision(
            Decision.BLOCK,
            ("P4-BLOCK-006",),
            summary_code="UNKNOWN_REMOTE_UPLOAD_BLOCKED",
            evidence_refs=policy_input.evidence_refs,
        )
    if policy_input.intent_kind == "change":
        recovery_refs = tuple(
            sorted(
                set(policy_input.evidence_refs)
                | set(recovery_facts.evidence_refs if recovery_facts else ())
            )
        )
        if recovery_facts is None or recovery_facts.status is RecoveryCoverageStatus.MISSING:
            return _decision(
                Decision.REVIEW,
                ("P6-REVIEW-001",),
                summary_code="RECOVERY_CHECKPOINT_REVIEW",
                evidence_refs=recovery_refs,
                requires_checkpoint=True,
                requires_manual_approval=True,
            )
        if recovery_facts.status in {
            RecoveryCoverageStatus.UNREACHABLE,
            RecoveryCoverageStatus.EVIDENCE_INSUFFICIENT,
        }:
            return _decision(
                Decision.UNKNOWN,
                ("P6-UNKNOWN-001",),
                summary_code="RECOVERY_EVIDENCE_UNAVAILABLE",
                evidence_refs=recovery_refs,
                uncertainties=(recovery_facts.reason_code,),
            )
        if (
            recovery_facts.status is RecoveryCoverageStatus.INSUFFICIENT
            or recovery_facts.authorized_snapshot_coverage != 1.0
            or recovery_facts.manifest_blob_coverage != 1.0
        ):
            return _decision(
                Decision.REVIEW,
                ("P6-REVIEW-002",),
                summary_code="RECOVERY_COVERAGE_REVIEW",
                evidence_refs=recovery_refs,
                requires_manual_approval=True,
            )
    if policy_input.intent_kind == "change" and policy_input.effect_kind in {
        "provider_config_change",
        "permission_config_change",
        "dependency_lock_change",
        "ci_change",
    }:
        return _decision(
            Decision.REVIEW,
            ("P4-REVIEW-001",),
            summary_code="PROVIDER_CONFIGURATION_REVIEW",
            evidence_refs=policy_input.evidence_refs,
            requires_manual_approval=True,
        )
    if policy_input.network_effect:
        return _decision(
            Decision.REVIEW,
            ("P4-REVIEW-003",),
            summary_code="NETWORK_EFFECT_REVIEW",
            evidence_refs=policy_input.evidence_refs,
            requires_manual_approval=True,
        )
    if policy_input.intent_kind == "discovery" and policy_input.effect_kind == "read_metadata":
        return _decision(
            Decision.ALLOW,
            ("P4-ALLOW-001",),
            summary_code="OFFLINE_DISCOVERY_ALLOWED",
            evidence_refs=policy_input.evidence_refs,
        )
    return _decision(
        Decision.UNKNOWN,
        ("P4-UNKNOWN-001",),
        summary_code="NO_EXPLICIT_RULE",
        evidence_refs=policy_input.evidence_refs,
        uncertainties=("NO_EXPLICIT_RULE",),
    )
