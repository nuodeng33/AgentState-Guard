"""Structured R4 Evidence Ledger event model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from .canonical import payload_digest
from .privacy import validate_evidence_refs, validate_safe_json


class EventFamily(str, Enum):
    DISCOVERY = "DISCOVERY"
    SUPERVISION = "SUPERVISION"
    CHANGE = "CHANGE"
    RECOVERY = "RECOVERY"


class EventType(str, Enum):
    RUNTIME_DETECTED = "RUNTIME_DETECTED"
    AGENT_DETECTED = "AGENT_DETECTED"
    WORKSPACE_LINKED = "WORKSPACE_LINKED"
    WORKSPACE_PROTECTION_BOUND = "WORKSPACE_PROTECTION_BOUND"
    CONTROLLED_TARGET_BOUND = "CONTROLLED_TARGET_BOUND"
    PROBE_UNREACHABLE = "PROBE_UNREACHABLE"
    SESSION_CREATED = "SESSION_CREATED"
    SESSION_ACTIVATED = "SESSION_ACTIVATED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    USER_APPROVED = "USER_APPROVED"
    USER_REJECTED = "USER_REJECTED"
    SESSION_COMPLETED = "SESSION_COMPLETED"
    SESSION_FAILED = "SESSION_FAILED"
    AI_ASSESSED = "AI_ASSESSED"
    DECLARED_SCOPE = "DECLARED_SCOPE"
    OBSERVED_CHANGE = "OBSERVED_CHANGE"
    SCOPE_DRIFT = "SCOPE_DRIFT"
    EXTERNAL_EFFECT_UNKNOWN = "EXTERNAL_EFFECT_UNKNOWN"
    CHECKPOINT_CREATED = "CHECKPOINT_CREATED"
    MANIFEST_VERIFIED = "MANIFEST_VERIFIED"
    TEST_RESTORE_STARTED = "TEST_RESTORE_STARTED"
    FILE_RESTORED = "FILE_RESTORED"
    VALIDATOR_PASSED = "VALIDATOR_PASSED"
    RESTORE_FAILED = "RESTORE_FAILED"
    RECOVERY_DRILL_PREPARED = "RECOVERY_DRILL_PREPARED"
    RECOVERY_DRILL_APPROVED = "RECOVERY_DRILL_APPROVED"
    RECOVERY_DRILL_STARTED = "RECOVERY_DRILL_STARTED"
    DRIFT_ESTABLISHED = "DRIFT_ESTABLISHED"
    RECOVERY_DRILL_COMPLETED = "RECOVERY_DRILL_COMPLETED"
    RECOVERY_DRILL_VERIFIED = "RECOVERY_DRILL_VERIFIED"
    TRUSTED_BASELINE_CREATED = "TRUSTED_BASELINE_CREATED"
    TRUSTED_BASELINE_RETIRED = "TRUSTED_BASELINE_RETIRED"


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("EVIDENCE_TIME_INVALID")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class EvidenceEvent:
    """Validated event before assignment of ledger sequence and chain hashes."""

    schema_version: int
    event_id: str
    recorded_at: datetime
    observed_at: datetime | None
    event_family: EventFamily
    event_type: EventType
    source: str
    result: str
    execution_domain_id: str | None
    supervision_session_id: str | None
    transaction_id: str | None
    checkpoint_id: str | None
    subject_ref: str | None
    evidence_refs: tuple[str, ...]
    payload_safe: dict[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != 1 or not self.event_id or not self.source or not self.result:
            raise ValueError("EVIDENCE_EVENT_INVALID")
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at))
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        object.__setattr__(self, "evidence_refs", validate_evidence_refs(self.evidence_refs))
        validate_safe_json(self.payload_safe)

    @property
    def payload_digest(self) -> str:
        return payload_digest(self.payload_safe)

    def authority_dict(self, *, sequence: int, prev_hash: str) -> dict[str, Any]:
        """Return every authority field used by the ledger chain hash."""
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "sequence": sequence,
            "recorded_at": self.recorded_at.isoformat(),
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "event_family": self.event_family.value,
            "event_type": self.event_type.value,
            "source": self.source,
            "result": self.result,
            "execution_domain_id": self.execution_domain_id,
            "supervision_session_id": self.supervision_session_id,
            "transaction_id": self.transaction_id,
            "checkpoint_id": self.checkpoint_id,
            "subject_ref": self.subject_ref,
            "evidence_refs": list(self.evidence_refs),
            "payload_safe": self.payload_safe,
            "payload_digest": self.payload_digest,
            "prev_hash": prev_hash,
        }
