"""Pure data descriptors for Runtime Discovery; no collectors or system access."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Dict, Mapping, Optional, Tuple

from .capabilities import (
    AgentLifecycleStatus,
    CapabilityStatus,
    DomainCapabilities,
    EvidenceReliability,
    _enum_or_unknown,
    validate_confidence,
)
from .errors import DiscoveryError, redact_text, sanitize_json_value


class ExecutionDomainKind(str, Enum):
    WINDOWS = "WINDOWS"
    LINUX = "LINUX"
    WSL = "WSL"
    CONTAINER = "CONTAINER"
    HOST_PROBE = "HOST_PROBE"
    UNKNOWN = "UNKNOWN"


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("observed_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_time(value: object) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str) and value:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        return _as_utc(datetime.fromisoformat(normalized))
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _time_text(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _validate_supported_conclusion(
    conclusion: bool,
    confidence: Optional[float],
    evidence_ids: Tuple[str, ...],
) -> None:
    if conclusion and confidence is not None and confidence > 0.8 and not evidence_ids:
        raise ValueError("high-confidence positive conclusion requires evidence")


@dataclass(frozen=True)
class ProbeEvidence:
    evidence_id: str
    collector: str
    source: str
    observed_at: datetime
    fact_type: str
    value: Any = None
    summary: Optional[str] = None
    reliability: EvidenceReliability = EvidenceReliability.UNKNOWN
    confidence: Optional[float] = None
    status: CapabilityStatus = CapabilityStatus.UNKNOWN
    error: Optional[DiscoveryError] = None
    sanitized: bool = False

    def __post_init__(self) -> None:
        error = self.error
        if isinstance(error, Mapping):
            error = DiscoveryError.from_dict(error)
        object.__setattr__(self, "observed_at", _as_utc(self.observed_at))
        object.__setattr__(self, "source", redact_text(self.source))
        object.__setattr__(self, "value", sanitize_json_value(self.value))
        object.__setattr__(
            self,
            "summary",
            redact_text(self.summary) if self.summary is not None else None,
        )
        object.__setattr__(
            self,
            "reliability",
            _enum_or_unknown(EvidenceReliability, self.reliability),
        )
        object.__setattr__(self, "confidence", validate_confidence(self.confidence))
        object.__setattr__(self, "status", _enum_or_unknown(CapabilityStatus, self.status))
        object.__setattr__(self, "error", error)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "collector": self.collector,
            "source": self.source,
            "observed_at": _time_text(self.observed_at),
            "fact_type": self.fact_type,
            "value": sanitize_json_value(self.value),
            "summary": self.summary,
            "reliability": self.reliability.value,
            "confidence": self.confidence,
            "status": self.status.value,
            "error": self.error.to_dict() if self.error else None,
            "sanitized": self.sanitized,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProbeEvidence":
        error_data = data.get("error")
        return cls(
            evidence_id=str(data.get("evidence_id", "unknown-evidence")),
            collector=str(data.get("collector", "unknown")),
            source=str(data.get("source", "unknown")),
            observed_at=_parse_time(data.get("observed_at")),
            fact_type=str(data.get("fact_type", "unknown")),
            value=data.get("value"),
            summary=str(data["summary"]) if data.get("summary") is not None else None,
            reliability=_enum_or_unknown(
                EvidenceReliability,
                data.get("reliability", "UNKNOWN"),
            ),
            confidence=data.get("confidence"),
            status=_enum_or_unknown(CapabilityStatus, data.get("status", "UNKNOWN")),
            error=DiscoveryError.from_dict(error_data) if isinstance(error_data, Mapping) else None,
            sanitized=bool(data.get("sanitized", False)),
        )


@dataclass(frozen=True)
class ExecutionDomainDescriptor:
    domain_id: str
    kind: ExecutionDomainKind = ExecutionDomainKind.UNKNOWN
    label: Optional[str] = None
    capabilities: DomainCapabilities = field(default_factory=DomainCapabilities)
    children: Tuple["ExecutionDomainDescriptor", ...] = ()
    evidence_ids: Tuple[str, ...] = ()
    confidence: Optional[float] = None

    def __post_init__(self) -> None:
        kind = _enum_or_unknown(ExecutionDomainKind, self.kind)
        evidence_ids = tuple(str(item) for item in self.evidence_ids)
        confidence = validate_confidence(self.confidence)
        capabilities = (
            DomainCapabilities.from_dict(self.capabilities)
            if isinstance(self.capabilities, Mapping)
            else self.capabilities
        )
        children = tuple(
            ExecutionDomainDescriptor.from_dict(child) if isinstance(child, Mapping) else child
            for child in self.children
        )
        _validate_supported_conclusion(
            kind is not ExecutionDomainKind.UNKNOWN,
            confidence,
            evidence_ids,
        )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "label", redact_text(self.label) if self.label else None)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "children", children)
        object.__setattr__(self, "evidence_ids", evidence_ids)
        object.__setattr__(self, "confidence", confidence)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain_id": self.domain_id,
            "kind": self.kind.value,
            "label": self.label,
            "capabilities": self.capabilities.to_dict(),
            "children": [child.to_dict() for child in self.children],
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionDomainDescriptor":
        capability_data = data.get("capabilities", {})
        return cls(
            domain_id=str(data.get("domain_id", "unknown-domain")),
            kind=_enum_or_unknown(ExecutionDomainKind, data.get("kind", "UNKNOWN")),
            label=str(data["label"]) if data.get("label") is not None else None,
            capabilities=DomainCapabilities.from_dict(capability_data)
            if isinstance(capability_data, Mapping)
            else DomainCapabilities(),
            children=tuple(
                cls.from_dict(child)
                for child in data.get("children", ())
                if isinstance(child, Mapping)
            ),
            evidence_ids=tuple(data.get("evidence_ids", ())),
            confidence=data.get("confidence"),
        )


@dataclass(frozen=True)
class RuntimeDescriptor:
    runtime_id: str
    runtime_type: str
    domain_id: str
    status: CapabilityStatus = CapabilityStatus.UNKNOWN
    label: Optional[str] = None
    version: Optional[str] = None
    capabilities: DomainCapabilities = field(default_factory=DomainCapabilities)
    evidence_ids: Tuple[str, ...] = ()
    confidence: Optional[float] = None

    def __post_init__(self) -> None:
        status = _enum_or_unknown(CapabilityStatus, self.status)
        evidence_ids = tuple(str(item) for item in self.evidence_ids)
        confidence = validate_confidence(self.confidence)
        capabilities = (
            DomainCapabilities.from_dict(self.capabilities)
            if isinstance(self.capabilities, Mapping)
            else self.capabilities
        )
        _validate_supported_conclusion(
            status is CapabilityStatus.AVAILABLE,
            confidence,
            evidence_ids,
        )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "label", redact_text(self.label) if self.label else None)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "evidence_ids", evidence_ids)
        object.__setattr__(self, "confidence", confidence)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "runtime_type": self.runtime_type,
            "domain_id": self.domain_id,
            "status": self.status.value,
            "label": self.label,
            "version": self.version,
            "capabilities": self.capabilities.to_dict(),
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RuntimeDescriptor":
        capability_data = data.get("capabilities", {})
        return cls(
            runtime_id=str(data.get("runtime_id", "unknown-runtime")),
            runtime_type=str(data.get("runtime_type", "UNKNOWN")),
            domain_id=str(data.get("domain_id", "unknown-domain")),
            status=_enum_or_unknown(CapabilityStatus, data.get("status", "UNKNOWN")),
            label=str(data["label"]) if data.get("label") is not None else None,
            version=str(data["version"]) if data.get("version") is not None else None,
            capabilities=DomainCapabilities.from_dict(capability_data)
            if isinstance(capability_data, Mapping)
            else DomainCapabilities(),
            evidence_ids=tuple(data.get("evidence_ids", ())),
            confidence=data.get("confidence"),
        )


@dataclass(frozen=True)
class AgentDescriptor:
    agent_id: str
    agent_type: str
    lifecycle: AgentLifecycleStatus
    domain_id: str
    runtime_id: Optional[str] = None
    label: Optional[str] = None
    workspace_ids: Tuple[str, ...] = ()
    evidence_ids: Tuple[str, ...] = ()
    confidence: Optional[float] = None

    def __post_init__(self) -> None:
        lifecycle = _enum_or_unknown(AgentLifecycleStatus, self.lifecycle)
        evidence_ids = tuple(str(item) for item in self.evidence_ids)
        confidence = validate_confidence(self.confidence)
        _validate_supported_conclusion(
            lifecycle is not AgentLifecycleStatus.UNKNOWN,
            confidence,
            evidence_ids,
        )
        object.__setattr__(self, "lifecycle", lifecycle)
        object.__setattr__(self, "label", redact_text(self.label) if self.label else None)
        object.__setattr__(self, "workspace_ids", tuple(str(item) for item in self.workspace_ids))
        object.__setattr__(self, "evidence_ids", evidence_ids)
        object.__setattr__(self, "confidence", confidence)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "lifecycle": self.lifecycle.value,
            "domain_id": self.domain_id,
            "runtime_id": self.runtime_id,
            "label": self.label,
            "workspace_ids": list(self.workspace_ids),
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentDescriptor":
        return cls(
            agent_id=str(data.get("agent_id", "unknown-agent")),
            agent_type=str(data.get("agent_type", "UNKNOWN")),
            lifecycle=_enum_or_unknown(
                AgentLifecycleStatus,
                data.get("lifecycle", "UNKNOWN"),
            ),
            domain_id=str(data.get("domain_id", "unknown-domain")),
            runtime_id=str(data["runtime_id"]) if data.get("runtime_id") is not None else None,
            label=str(data["label"]) if data.get("label") is not None else None,
            workspace_ids=tuple(data.get("workspace_ids", ())),
            evidence_ids=tuple(data.get("evidence_ids", ())),
            confidence=data.get("confidence"),
        )


@dataclass(frozen=True)
class WorkspaceDescriptor:
    workspace_id: str
    domain_id: str
    label: Optional[str] = None
    runtime_ids: Tuple[str, ...] = ()
    agent_ids: Tuple[str, ...] = ()
    evidence_ids: Tuple[str, ...] = ()
    confidence: Optional[float] = None

    def __post_init__(self) -> None:
        evidence_ids = tuple(str(item) for item in self.evidence_ids)
        confidence = validate_confidence(self.confidence)
        _validate_supported_conclusion(True, confidence, evidence_ids)
        object.__setattr__(self, "label", redact_text(self.label) if self.label else None)
        object.__setattr__(self, "runtime_ids", tuple(str(item) for item in self.runtime_ids))
        object.__setattr__(self, "agent_ids", tuple(str(item) for item in self.agent_ids))
        object.__setattr__(self, "evidence_ids", evidence_ids)
        object.__setattr__(self, "confidence", confidence)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "domain_id": self.domain_id,
            "label": self.label,
            "runtime_ids": list(self.runtime_ids),
            "agent_ids": list(self.agent_ids),
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkspaceDescriptor":
        return cls(
            workspace_id=str(data.get("workspace_id", "unknown-workspace")),
            domain_id=str(data.get("domain_id", "unknown-domain")),
            label=str(data["label"]) if data.get("label") is not None else None,
            runtime_ids=tuple(data.get("runtime_ids", ())),
            agent_ids=tuple(data.get("agent_ids", ())),
            evidence_ids=tuple(data.get("evidence_ids", ())),
            confidence=data.get("confidence"),
        )


@dataclass(frozen=True)
class DiscoverySnapshot:
    CURRENT_SCHEMA_VERSION: ClassVar[str] = "1.0"

    snapshot_id: str
    observed_at: datetime
    schema_version: str = CURRENT_SCHEMA_VERSION
    domains: Tuple[ExecutionDomainDescriptor, ...] = ()
    runtimes: Tuple[RuntimeDescriptor, ...] = ()
    agents: Tuple[AgentDescriptor, ...] = ()
    workspaces: Tuple[WorkspaceDescriptor, ...] = ()
    evidence: Tuple[ProbeEvidence, ...] = ()
    errors: Tuple[DiscoveryError, ...] = ()
    status: CapabilityStatus = CapabilityStatus.UNKNOWN

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _as_utc(self.observed_at))
        object.__setattr__(
            self,
            "domains",
            tuple(
                ExecutionDomainDescriptor.from_dict(item) if isinstance(item, Mapping) else item
                for item in self.domains
            ),
        )
        object.__setattr__(
            self,
            "runtimes",
            tuple(
                RuntimeDescriptor.from_dict(item) if isinstance(item, Mapping) else item
                for item in self.runtimes
            ),
        )
        object.__setattr__(
            self,
            "agents",
            tuple(
                AgentDescriptor.from_dict(item) if isinstance(item, Mapping) else item
                for item in self.agents
            ),
        )
        object.__setattr__(
            self,
            "workspaces",
            tuple(
                WorkspaceDescriptor.from_dict(item) if isinstance(item, Mapping) else item
                for item in self.workspaces
            ),
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
        object.__setattr__(self, "status", _enum_or_unknown(CapabilityStatus, self.status))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "observed_at": _time_text(self.observed_at),
            "status": self.status.value,
            "domains": [item.to_dict() for item in self.domains],
            "runtimes": [item.to_dict() for item in self.runtimes],
            "agents": [item.to_dict() for item in self.agents],
            "workspaces": [item.to_dict() for item in self.workspaces],
            "evidence": [item.to_dict() for item in self.evidence],
            "errors": [item.to_dict() for item in self.errors],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DiscoverySnapshot":
        return cls(
            schema_version=str(data.get("schema_version", cls.CURRENT_SCHEMA_VERSION)),
            snapshot_id=str(data.get("snapshot_id", "unknown-snapshot")),
            observed_at=_parse_time(data.get("observed_at")),
            status=_enum_or_unknown(CapabilityStatus, data.get("status", "UNKNOWN")),
            domains=tuple(
                ExecutionDomainDescriptor.from_dict(item)
                for item in data.get("domains", ())
                if isinstance(item, Mapping)
            ),
            runtimes=tuple(
                RuntimeDescriptor.from_dict(item)
                for item in data.get("runtimes", ())
                if isinstance(item, Mapping)
            ),
            agents=tuple(
                AgentDescriptor.from_dict(item)
                for item in data.get("agents", ())
                if isinstance(item, Mapping)
            ),
            workspaces=tuple(
                WorkspaceDescriptor.from_dict(item)
                for item in data.get("workspaces", ())
                if isinstance(item, Mapping)
            ),
            evidence=tuple(
                ProbeEvidence.from_dict(item)
                for item in data.get("evidence", ())
                if isinstance(item, Mapping)
            ),
            errors=tuple(
                DiscoveryError.from_dict(item)
                for item in data.get("errors", ())
                if isinstance(item, Mapping)
            ),
        )
