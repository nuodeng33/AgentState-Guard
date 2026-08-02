"""Capability and Agent lifecycle semantics for Runtime Discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple

from .errors import DiscoveryError


class CapabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNREACHABLE = "UNREACHABLE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_PRESENT = "NOT_PRESENT"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


class AgentLifecycleStatus(str, Enum):
    """Distinct assertions; no value implies any later lifecycle level."""

    DETECTED = "DETECTED"
    RUNNING = "RUNNING"
    OBSERVED = "OBSERVED"
    INTEGRATED = "INTEGRATED"
    ENFORCED = "ENFORCED"
    UNKNOWN = "UNKNOWN"


class EvidenceReliability(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


def _enum_or_unknown(enum_type: type[Enum], value: object) -> Enum:
    try:
        return value if isinstance(value, enum_type) else enum_type(str(value))
    except ValueError:
        return enum_type.UNKNOWN


def validate_confidence(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    confidence = float(value)
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0.0 and 1.0")
    return confidence


@dataclass(frozen=True)
class CapabilityAssessment:
    """One capability conclusion backed by zero or more evidence records."""

    status: CapabilityStatus = CapabilityStatus.UNKNOWN
    reason_code: Optional[str] = None
    evidence_ids: Tuple[str, ...] = ()
    confidence: Optional[float] = None
    error: Optional[DiscoveryError] = None

    def __post_init__(self) -> None:
        status = _enum_or_unknown(CapabilityStatus, self.status)
        confidence = validate_confidence(self.confidence)
        evidence_ids = tuple(str(item) for item in self.evidence_ids)
        if status is CapabilityStatus.AVAILABLE and confidence is not None:
            if confidence > 0.8 and not evidence_ids:
                raise ValueError("high-confidence AVAILABLE requires evidence")
        error = self.error
        if isinstance(error, Mapping):
            error = DiscoveryError.from_dict(error)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "evidence_ids", evidence_ids)
        object.__setattr__(self, "error", error)

    @property
    def is_available(self) -> bool:
        """Only an explicit AVAILABLE state permits availability claims."""

        return self.status is CapabilityStatus.AVAILABLE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "reason_code": self.reason_code,
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
            "error": self.error.to_dict() if self.error else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CapabilityAssessment":
        error_data = data.get("error")
        return cls(
            status=_enum_or_unknown(CapabilityStatus, data.get("status", "UNKNOWN")),
            reason_code=str(data["reason_code"]) if data.get("reason_code") is not None else None,
            evidence_ids=tuple(data.get("evidence_ids", ())),
            confidence=data.get("confidence"),
            error=DiscoveryError.from_dict(error_data) if isinstance(error_data, Mapping) else None,
        )


@dataclass(frozen=True)
class DomainCapabilities:
    """Named capability conclusions for one execution domain."""

    assessments: Mapping[str, CapabilityAssessment] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized: Dict[str, CapabilityAssessment] = {}
        for name, assessment in self.assessments.items():
            key = str(name)
            if not key:
                raise ValueError("capability name must not be empty")
            normalized[key] = (
                CapabilityAssessment.from_dict(assessment)
                if isinstance(assessment, Mapping)
                else assessment
            )
            if not isinstance(normalized[key], CapabilityAssessment):
                raise TypeError("capability values must be CapabilityAssessment instances")
        object.__setattr__(self, "assessments", normalized)

    def get(self, name: str) -> CapabilityAssessment:
        return self.assessments.get(
            name,
            CapabilityAssessment(
                status=CapabilityStatus.UNKNOWN,
                reason_code="NOT_ASSESSED",
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            name: self.assessments[name].to_dict()
            for name in sorted(self.assessments)
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DomainCapabilities":
        return cls(
            {
                str(name): CapabilityAssessment.from_dict(value)
                for name, value in data.items()
                if isinstance(value, Mapping)
            }
        )
