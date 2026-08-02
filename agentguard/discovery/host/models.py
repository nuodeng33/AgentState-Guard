"""Pure Host Probe import models and freshness semantics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, ClassVar

from ..capabilities import DomainCapabilities
from ..errors import DiscoveryError
from ..models import ExecutionDomainDescriptor, ProbeEvidence


class HostFreshness(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    INVALID = "INVALID"
    NOT_PRESENT = "NOT_PRESENT"


class HostSourceKind(str, Enum):
    HOST_PROBE = "HOST_PROBE"
    LEGACY = "LEGACY"
    UNKNOWN = "UNKNOWN"


class HostTrustLevel(str, Enum):
    SOURCE_BOUND = "SOURCE_BOUND"
    UNVERIFIED = "UNVERIFIED"
    UNKNOWN = "UNKNOWN"


class HostWarningCode(str, Enum):
    SENSITIVE_FIELD_REJECTED = "SENSITIVE_FIELD_REJECTED"
    REMOTE_URL_REJECTED = "REMOTE_URL_REJECTED"
    UNKNOWN_FIELD_IGNORED = "UNKNOWN_FIELD_IGNORED"
    UNKNOWN_ENUM_DOWNGRADED = "UNKNOWN_ENUM_DOWNGRADED"
    UNSUPPORTED_CAPABILITY_IGNORED = "UNSUPPORTED_CAPABILITY_IGNORED"
    INPUT_SANITIZED = "INPUT_SANITIZED"
    SOURCE_BINDING_UNVERIFIED = "SOURCE_BINDING_UNVERIFIED"
    LEGACY_INPUT_DEGRADED = "LEGACY_INPUT_DEGRADED"
    UNKNOWN = "UNKNOWN"


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("Host Probe timestamps must be datetime values")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Host Probe timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _parse_time(value: object) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)
    if not isinstance(value, str) or not value:
        raise ValueError("Host Probe timestamp is missing")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return _as_utc(datetime.fromisoformat(normalized))


def _time_text(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _enum_or_unknown(enum_type: type[Enum], value: object) -> Enum:
    try:
        return value if isinstance(value, enum_type) else enum_type(str(value))
    except ValueError:
        return enum_type.UNKNOWN


@dataclass(frozen=True)
class HostProbeWarning:
    code: HostWarningCode
    field_category: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "code",
            _enum_or_unknown(HostWarningCode, self.code),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "field_category": self.field_category,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> HostProbeWarning:
        return cls(
            code=_enum_or_unknown(HostWarningCode, data.get("code", "UNKNOWN")),
            field_category=(
                str(data["field_category"])
                if data.get("field_category") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class HostProbeEnvelope:
    CURRENT_SCHEMA_VERSION: ClassVar[str] = "1.0"

    schema_version: str
    probe_version: str
    probe_id: str
    source_domain_id: str
    source_kind: HostSourceKind
    issued_at: datetime
    observed_at: datetime
    expires_at: datetime
    ttl_seconds: int
    sequence: int
    host: ExecutionDomainDescriptor
    capabilities: DomainCapabilities = field(default_factory=DomainCapabilities)
    evidence: tuple[ProbeEvidence, ...] = ()
    errors: tuple[DiscoveryError, ...] = ()
    sanitized: bool = True
    warnings: tuple[HostProbeWarning, ...] = ()
    source_binding_id: str | None = None
    trust_level: HostTrustLevel = HostTrustLevel.UNVERIFIED
    imported: bool = True
    self_visible: bool = False
    source_authenticated: bool = False
    imported_evidence_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "issued_at", _as_utc(self.issued_at))
        object.__setattr__(self, "observed_at", _as_utc(self.observed_at))
        object.__setattr__(self, "expires_at", _as_utc(self.expires_at))
        object.__setattr__(
            self,
            "source_kind",
            _enum_or_unknown(HostSourceKind, self.source_kind),
        )
        object.__setattr__(
            self,
            "trust_level",
            _enum_or_unknown(HostTrustLevel, self.trust_level),
        )
        object.__setattr__(self, "capabilities", _capabilities(self.capabilities))
        object.__setattr__(
            self,
            "host",
            ExecutionDomainDescriptor.from_dict(self.host)
            if isinstance(self.host, Mapping)
            else self.host,
        )
        object.__setattr__(
            self,
            "evidence",
            tuple(
                ProbeEvidence.from_dict(item) if isinstance(item, Mapping) else item
                for item in self.evidence
            ),
        )
        object.__setattr__(
            self,
            "errors",
            tuple(
                DiscoveryError.from_dict(item) if isinstance(item, Mapping) else item
                for item in self.errors
            ),
        )
        object.__setattr__(
            self,
            "warnings",
            tuple(
                HostProbeWarning.from_dict(item) if isinstance(item, Mapping) else item
                for item in self.warnings
            ),
        )
        if self.ttl_seconds <= 0:
            raise ValueError("Host Probe ttl_seconds must be positive")
        if self.sequence < 0:
            raise ValueError("Host Probe sequence must not be negative")
        if self.observed_at > self.issued_at:
            raise ValueError("Host Probe observed_at must not be later than issued_at")
        expected_expiry = self.observed_at + timedelta(seconds=self.ttl_seconds)
        if self.expires_at != expected_expiry:
            raise ValueError(
                "Host Probe expires_at must equal observed_at plus ttl_seconds"
            )
        if not self.imported or self.self_visible or self.source_authenticated:
            raise ValueError("Host Probe provenance flags are fail-closed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "probe_version": self.probe_version,
            "probe_id": self.probe_id,
            "source_domain_id": self.source_domain_id,
            "source_kind": self.source_kind.value,
            "source_binding_id": self.source_binding_id,
            "trust_level": self.trust_level.value,
            "source_authenticated": self.source_authenticated,
            "issued_at": _time_text(self.issued_at),
            "observed_at": _time_text(self.observed_at),
            "expires_at": _time_text(self.expires_at),
            "ttl_seconds": self.ttl_seconds,
            "sequence": self.sequence,
            "host": self.host.to_dict(),
            "capabilities": self.capabilities.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
            "errors": [item.to_dict() for item in self.errors],
            "sanitized": self.sanitized,
            "warnings": [item.to_dict() for item in self.warnings],
            "imported": self.imported,
            "self_visible": self.self_visible,
            "imported_evidence_id": self.imported_evidence_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> HostProbeEnvelope:
        return cls(
            schema_version=str(data.get("schema_version", "")),
            probe_version=str(data.get("probe_version", "")),
            probe_id=str(data.get("probe_id", "")),
            source_domain_id=str(data.get("source_domain_id", "")),
            source_kind=_enum_or_unknown(
                HostSourceKind,
                data.get("source_kind", "UNKNOWN"),
            ),
            source_binding_id=(
                str(data["source_binding_id"])
                if data.get("source_binding_id") is not None
                else None
            ),
            trust_level=_enum_or_unknown(
                HostTrustLevel,
                data.get("trust_level", "UNKNOWN"),
            ),
            source_authenticated=bool(data.get("source_authenticated", False)),
            issued_at=_parse_time(data.get("issued_at")),
            observed_at=_parse_time(data.get("observed_at")),
            expires_at=_parse_time(data.get("expires_at")),
            ttl_seconds=int(data.get("ttl_seconds", 0)),
            sequence=int(data.get("sequence", -1)),
            host=ExecutionDomainDescriptor.from_dict(_mapping(data.get("host"))),
            capabilities=DomainCapabilities.from_dict(_mapping(data.get("capabilities"))),
            evidence=tuple(
                ProbeEvidence.from_dict(item)
                for item in _sequence(data.get("evidence"))
                if isinstance(item, Mapping)
            ),
            errors=tuple(
                DiscoveryError.from_dict(item)
                for item in _sequence(data.get("errors"))
                if isinstance(item, Mapping)
            ),
            sanitized=bool(data.get("sanitized", False)),
            warnings=tuple(
                HostProbeWarning.from_dict(item)
                for item in _sequence(data.get("warnings"))
                if isinstance(item, Mapping)
            ),
            imported=bool(data.get("imported", False)),
            self_visible=bool(data.get("self_visible", True)),
            imported_evidence_id=str(data.get("imported_evidence_id", "")),
        )


@dataclass(frozen=True)
class HostImportResult:
    freshness: HostFreshness
    envelope: HostProbeEnvelope | None = None
    error: DiscoveryError | None = None
    warnings: tuple[HostProbeWarning, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "freshness": self.freshness.value,
            "envelope": self.envelope.to_dict() if self.envelope else None,
            "error": self.error.to_dict() if self.error else None,
            "warnings": [item.to_dict() for item in self.warnings],
        }


def evaluate_freshness(
    envelope: HostProbeEnvelope,
    now: datetime,
    *,
    stale_grace_seconds: int,
) -> HostFreshness:
    current = _as_utc(now)
    if current <= envelope.expires_at:
        return HostFreshness.FRESH
    if current <= envelope.expires_at + timedelta(seconds=stale_grace_seconds):
        return HostFreshness.STALE
    return HostFreshness.EXPIRED


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> tuple[Any, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return ()


def _capabilities(value: object) -> DomainCapabilities:
    if isinstance(value, DomainCapabilities):
        return value
    if isinstance(value, Mapping):
        return DomainCapabilities.from_dict(value)
    return DomainCapabilities()


__all__ = [
    "HostFreshness",
    "HostImportResult",
    "HostProbeEnvelope",
    "HostProbeWarning",
    "HostSourceKind",
    "HostTrustLevel",
    "HostWarningCode",
    "evaluate_freshness",
]
