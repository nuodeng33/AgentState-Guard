"""Authoritative R4 P5 AI supervision over stored sessions and ledger evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from agentguard.evidence.canonical import canonical_json
from agentguard.evidence.ledger import EvidenceLedger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.policy.models import Decision
from agentguard.storage.db import StateDB

PROMPT_VERSION = "R4-P5-1"
_ALLOWED_DECISIONS = frozenset(decision.value for decision in Decision)
_ALLOWED_SEVERITIES = frozenset({"LOW", "MEDIUM", "HIGH", "UNKNOWN"})


@dataclass(frozen=True)
class AIAssessment:
    """Strict P5 output; never includes free-form root-cause fields."""

    decision: str
    severity: str
    summary: str
    evidence_refs: tuple[str, ...]
    uncertainties: tuple[str, ...]
    required_checks: tuple[str, ...]
    requires_checkpoint: bool
    requires_manual_approval: bool


class AssessmentProvider(Protocol):
    model: str

    def assess(self, authority_package: dict) -> AIAssessment: ...


class AISupervisor:
    """Assess one stored supervision session using authoritative, safe evidence only."""

    def __init__(self, database: StateDB, provider: AssessmentProvider) -> None:
        self._database = database
        self._provider = provider
        self._ledger = EvidenceLedger()
        self._cache: dict[str, AIAssessment] = {}

    def assess(self, supervision_session_id: str) -> AIAssessment:
        """Assess a session ID; callers cannot supply arbitrary AI context."""
        package = self._authority_package(supervision_session_id)
        local_decision = package["policy_decision"]
        if local_decision == Decision.BLOCK.value:
            return self._local_assessment(Decision.BLOCK.value, package)
        key = self._cache_key(package)
        if key in self._cache:
            return self._cache[key]
        try:
            assessment = self._validated(self._provider.assess(package))
        except Exception:  # noqa: BLE001 - provider implementation boundary fails closed.
            return self._fallback(package)
        assessment = self._constrain(assessment, package)
        now = datetime.now(UTC)
        with self._database.transaction() as connection:
            self._ledger.append(
                connection,
                EvidenceEvent(
                    schema_version=1,
                    event_id=f"ai-assessment-{uuid4()}",
                    recorded_at=now,
                    observed_at=None,
                    event_family=EventFamily.SUPERVISION,
                    event_type=EventType.AI_ASSESSED,
                    source="r4-ai-supervisor",
                    result=assessment.decision,
                    execution_domain_id=package["execution_domain_id"],
                    supervision_session_id=supervision_session_id,
                    transaction_id=None,
                    checkpoint_id=None,
                    subject_ref=supervision_session_id,
                    evidence_refs=assessment.evidence_refs,
                    payload_safe={
                        "cache_key": key,
                        "decision": assessment.decision,
                        "severity": assessment.severity,
                        "prompt_version": PROMPT_VERSION,
                        "policy_version": package["policy_version"],
                    },
                ),
            )
        self._cache[key] = assessment
        return assessment

    def _authority_package(self, session_id: str) -> dict:
        connection = self._database._conn
        if connection is None:
            raise RuntimeError("Database is not connected")
        row = connection.execute(
            """SELECT decision, requires_checkpoint, requires_manual_approval
               FROM supervision_sessions WHERE supervision_session_id = ?""",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError("SUPERVISION_SESSION_NOT_FOUND")
        evidence_rows = connection.execute(
            """SELECT event_id, event_type, result, execution_domain_id, evidence_refs_json,
                      payload_digest
               FROM evidence_ledger_events
               WHERE supervision_session_id = ? AND event_type != 'AI_ASSESSED' ORDER BY sequence""",
            (session_id,),
        ).fetchall()
        refs = {session_id}
        events = []
        domain = None
        for event_id, event_type, result, event_domain, refs_json, digest in evidence_rows:
            event_refs = tuple(json.loads(refs_json))
            refs.update(event_refs)
            domain = domain or event_domain
            events.append(
                {
                    "event_id": event_id,
                    "event_type": event_type,
                    "result": result,
                    "evidence_refs": event_refs,
                    "payload_digest": digest,
                }
            )
        return {
            "supervision_session_id": session_id,
            "policy_decision": row[0],
            "requires_checkpoint": bool(row[1]),
            "requires_manual_approval": bool(row[2]),
            "policy_version": "P4-LOCAL-1",
            "execution_domain_id": domain,
            "evidence_refs": tuple(sorted(refs)),
            "ledger_events": tuple(events),
        }

    def _cache_key(self, package: dict) -> str:
        ledger_events = [
            {**event, "evidence_refs": list(event["evidence_refs"])}
            for event in package["ledger_events"]
        ]
        evidence_digest = hashlib.sha256(canonical_json(ledger_events).encode()).hexdigest()
        model = getattr(self._provider, "model", "unknown")
        authority = {
            "evidence_digest": evidence_digest,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "policy_version": package["policy_version"],
        }
        return hashlib.sha256(canonical_json(authority).encode()).hexdigest()

    def _validated(self, value: object) -> AIAssessment:
        if not isinstance(value, AIAssessment):
            raise TypeError("AI_RESPONSE_INVALID")
        if value.decision not in _ALLOWED_DECISIONS or value.severity not in _ALLOWED_SEVERITIES:
            raise ValueError("AI_RESPONSE_INVALID")
        if not value.summary or len(value.summary) > 500:
            raise ValueError("AI_RESPONSE_INVALID")
        if not all(isinstance(item, str) and item for item in value.evidence_refs):
            raise ValueError("AI_RESPONSE_INVALID")
        return value

    def _constrain(self, assessment: AIAssessment, package: dict) -> AIAssessment:
        if package["policy_decision"] == Decision.REVIEW.value:
            return AIAssessment(
                decision=Decision.REVIEW.value,
                severity=assessment.severity,
                summary=assessment.summary,
                evidence_refs=assessment.evidence_refs,
                uncertainties=assessment.uncertainties,
                required_checks=assessment.required_checks,
                requires_checkpoint=assessment.requires_checkpoint or package["requires_checkpoint"],
                requires_manual_approval=True,
            )
        return assessment

    def _fallback(self, package: dict) -> AIAssessment:
        decision = Decision.REVIEW.value if package["policy_decision"] == Decision.REVIEW.value else Decision.UNKNOWN.value
        return self._local_assessment(decision, package)

    def _local_assessment(self, decision: str, package: dict) -> AIAssessment:
        return AIAssessment(
            decision=decision,
            severity="HIGH" if decision == Decision.BLOCK.value else "UNKNOWN",
            summary="AI assessment unavailable; deterministic local policy remains authoritative.",
            evidence_refs=package["evidence_refs"],
            uncertainties=("AI_ASSESSMENT_UNAVAILABLE",),
            required_checks=("human_review",) if decision == Decision.REVIEW.value else ("ai_assessment",),
            requires_checkpoint=package["requires_checkpoint"],
            requires_manual_approval=decision == Decision.REVIEW.value or package["requires_manual_approval"],
        )
