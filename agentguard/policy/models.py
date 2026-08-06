"""Structured inputs and outputs for deterministic local policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    ALLOW = "ALLOW"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PolicyInput:
    intent_kind: str
    effect_kind: str
    target_refs: tuple[str, ...]
    execution_domain_id: str | None
    declared_scope: tuple[str, ...]
    requested_capabilities: tuple[str, ...]
    network_effect: bool
    privilege_effect: bool
    destructive_effect: bool
    secret_access: bool
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in self.evidence_refs):
            raise ValueError("POLICY_EVIDENCE_INVALID")


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    severity: str
    matched_rule_ids: tuple[str, ...]
    summary_code: str
    evidence_refs: tuple[str, ...]
    uncertainties: tuple[str, ...]
    required_checks: tuple[str, ...]
    requires_checkpoint: bool
    requires_manual_approval: bool
    policy_version: str = "P4-LOCAL-1"
