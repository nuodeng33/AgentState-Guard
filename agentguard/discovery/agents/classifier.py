"""Pure, product-neutral classification from bounded process facts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field

from ..capabilities import AgentLifecycleStatus
from .models import (
    AgentCandidateType,
    AgentClassification,
    AgentRole,
    ProcessFact,
    ProcessState,
)


@dataclass(frozen=True)
class AgentSignatureRule:
    """Explicit exact-match rule using only low-risk structured facts."""

    rule_id: str
    role: AgentRole
    executable_basenames: tuple[str, ...]
    signature_evidence_ref: str
    execution_domain_ids: tuple[str, ...] = ()
    required_flags: Mapping[str, bool] = field(default_factory=dict)
    observed_flag: str | None = None
    uncertainties: tuple[str, ...] = ("PRODUCT_IDENTITY_UNVERIFIED",)
    required_checks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.rule_id or not self.signature_evidence_ref:
            raise ValueError("signature rule identity and evidence are required")
        names = tuple(str(item).casefold() for item in self.executable_basenames)
        if not names or any("/" in item or "\\" in item for item in names):
            raise ValueError("signature executable names must be basenames")
        object.__setattr__(self, "role", AgentRole(self.role))
        object.__setattr__(self, "executable_basenames", names)
        object.__setattr__(
            self,
            "execution_domain_ids",
            tuple(str(item) for item in self.execution_domain_ids),
        )
        object.__setattr__(
            self,
            "required_flags",
            {str(name): bool(value) for name, value in self.required_flags.items()},
        )
        object.__setattr__(self, "uncertainties", tuple(str(item) for item in self.uncertainties))
        object.__setattr__(self, "required_checks", tuple(str(item) for item in self.required_checks))


def classify_process(
    fact: ProcessFact,
    rules: tuple[AgentSignatureRule, ...],
) -> AgentClassification:
    """Classify one ProcessFact without I/O or lifecycle escalation."""

    rule = _matching_rule(
        basename=fact.executable_basename,
        execution_domain_id=fact.execution_domain_id,
        fixed_facts=fact.fixed_facts,
        rules=rules,
    )
    if fact.current_state is ProcessState.RUNNING:
        lifecycle = AgentLifecycleStatus.RUNNING
    else:
        lifecycle = AgentLifecycleStatus.UNKNOWN

    if fact.executable_basename is None:
        return AgentClassification(
            candidate_id=_candidate_id(
                AgentCandidateType.PROCESS,
                fact.execution_domain_id,
                fact.process_instance_id,
                "unknown-executable",
            ),
            candidate_type=AgentCandidateType.PROCESS,
            role=AgentRole.UNKNOWN,
            lifecycle=lifecycle,
            confidence=None,
            evidence_refs=fact.evidence_refs,
            uncertainties=("EXECUTABLE_IDENTITY_UNAVAILABLE",),
            required_checks=("EXECUTABLE_BASENAME_REQUIRED",),
            process_instance_id=fact.process_instance_id,
        )

    if rule is None:
        return AgentClassification(
            candidate_id=_candidate_id(
                AgentCandidateType.PROCESS,
                fact.execution_domain_id,
                fact.process_instance_id,
                "unmatched",
            ),
            candidate_type=AgentCandidateType.PROCESS,
            role=AgentRole.TOOL_PROCESS,
            lifecycle=lifecycle,
            confidence=0.5,
            evidence_refs=fact.evidence_refs,
            uncertainties=("AGENT_ROLE_UNCLASSIFIED",),
            required_checks=("EXPLICIT_SIGNATURE_REQUIRED",),
            process_instance_id=fact.process_instance_id,
        )

    required_checks = list(rule.required_checks)
    if (
        lifecycle is AgentLifecycleStatus.RUNNING
        and rule.observed_flag is not None
        and fact.fixed_facts.get(rule.observed_flag) is True
        and rule.observed_flag in fact.supported_fixed_fact_names
    ):
        lifecycle = AgentLifecycleStatus.OBSERVED
    elif rule.observed_flag is not None:
        required_checks.append("SUPPORTED_RUNTIME_EVIDENCE_REQUIRED")

    return AgentClassification(
        candidate_id=_candidate_id(
            AgentCandidateType.PROCESS,
            fact.execution_domain_id,
            fact.process_instance_id,
            rule.rule_id,
        ),
        candidate_type=AgentCandidateType.PROCESS,
        role=rule.role,
        lifecycle=lifecycle,
        confidence=0.9 if lifecycle is AgentLifecycleStatus.OBSERVED else 0.8,
        evidence_refs=tuple(dict.fromkeys((*fact.evidence_refs, rule.signature_evidence_ref))),
        uncertainties=rule.uncertainties,
        required_checks=tuple(dict.fromkeys(required_checks)),
        process_instance_id=fact.process_instance_id,
    )


def classify_executable(
    *,
    executable_basename: str,
    execution_domain_id: str,
    evidence_refs: tuple[str, ...],
    rules: tuple[AgentSignatureRule, ...],
) -> AgentClassification:
    """Classify executable presence as DETECTED, never as a running process."""

    basename = executable_basename.replace("\\", "/").rsplit("/", 1)[-1]
    rule = _matching_rule(
        basename=basename,
        execution_domain_id=execution_domain_id,
        fixed_facts={},
        rules=rules,
    )
    role = rule.role if rule else AgentRole.TOOL_PROCESS
    rule_id = rule.rule_id if rule else "unmatched"
    signature_refs = (rule.signature_evidence_ref,) if rule else ()
    uncertainties = rule.uncertainties if rule else ("AGENT_ROLE_UNCLASSIFIED",)
    required_checks = rule.required_checks if rule else ("EXPLICIT_SIGNATURE_REQUIRED",)
    return AgentClassification(
        candidate_id=_candidate_id(
            AgentCandidateType.EXECUTABLE,
            execution_domain_id,
            basename.casefold(),
            rule_id,
        ),
        candidate_type=AgentCandidateType.EXECUTABLE,
        role=role,
        lifecycle=AgentLifecycleStatus.DETECTED,
        confidence=0.7 if rule else 0.4,
        evidence_refs=tuple(dict.fromkeys((*evidence_refs, *signature_refs))),
        uncertainties=uncertainties,
        required_checks=required_checks,
    )


def _matching_rule(
    *,
    basename: str | None,
    execution_domain_id: str,
    fixed_facts: Mapping[str, bool],
    rules: tuple[AgentSignatureRule, ...],
) -> AgentSignatureRule | None:
    if basename is None:
        return None
    normalized = basename.casefold()
    for rule in rules:
        if normalized not in rule.executable_basenames:
            continue
        if rule.execution_domain_ids and execution_domain_id not in rule.execution_domain_ids:
            continue
        if any(fixed_facts.get(name) is not expected for name, expected in rule.required_flags.items()):
            continue
        return rule
    return None


def _candidate_id(
    candidate_type: AgentCandidateType,
    domain_id: str,
    subject: str,
    rule_id: str,
) -> str:
    material = (
        f"{candidate_type.value}\x1f{domain_id}\x1f{subject}\x1f{rule_id}"
    ).encode()
    return f"agent-candidate-{hashlib.sha256(material).hexdigest()[:24]}"


__all__ = [
    "AgentSignatureRule",
    "classify_executable",
    "classify_process",
]
