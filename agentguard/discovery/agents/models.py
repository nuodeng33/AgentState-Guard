"""Pure R4-P3A process, Agent candidate, and workspace candidate models."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

from ..capabilities import (
    CapabilityStatus,
    _enum_or_unknown,
    validate_confidence,
)
from ..errors import redact_text


class AgentRole(str, Enum):
    EXECUTION_AGENT = "EXECUTION_AGENT"
    AGENT_HOST = "AGENT_HOST"
    MODEL_ROUTER = "MODEL_ROUTER"
    TOOL_PROCESS = "TOOL_PROCESS"
    UNKNOWN = "UNKNOWN"


class ExecutableIdentityKind(str, Enum):
    BASENAME_SHA256 = "BASENAME_SHA256"
    UNKNOWN = "UNKNOWN"


class ProcessState(str, Enum):
    RUNNING = "RUNNING"
    EXITED = "EXITED"
    UNKNOWN = "UNKNOWN"


class ProcessWarningCode(str, Enum):
    PROCESS_EXITED = "PROCESS_EXITED"
    PARENT_UNAVAILABLE = "PARENT_UNAVAILABLE"
    PARENT_CYCLE = "PARENT_CYCLE"
    PARTIAL_VISIBILITY = "PARTIAL_VISIBILITY"
    CWD_UNAVAILABLE = "CWD_UNAVAILABLE"
    PATH_REDACTED = "PATH_REDACTED"
    WSL_DISPLAY_PATH = "WSL_DISPLAY_PATH"
    SENSITIVE_INPUT_REJECTED = "SENSITIVE_INPUT_REJECTED"
    UNKNOWN = "UNKNOWN"


class WorkspaceSource(str, Enum):
    PROCESS_CWD = "PROCESS_CWD"
    KNOWN_LOGICAL_PATH = "KNOWN_LOGICAL_PATH"
    GIT_ROOT_CANDIDATE = "GIT_ROOT_CANDIDATE"
    HOST_PROBE_MAPPING = "HOST_PROBE_MAPPING"
    USER_INPUT = "USER_INPUT"
    UNKNOWN = "UNKNOWN"


class WorkspacePathKind(str, Enum):
    NATIVE = "NATIVE"
    DISPLAY_ONLY = "DISPLAY_ONLY"
    REDACTED = "REDACTED"
    UNKNOWN = "UNKNOWN"


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("process time must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("process time must be timezone-aware")
    return value.astimezone(UTC)


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def normalize_process_create_time(value: datetime | float) -> datetime:
    """Normalize backend timestamps to one UTC microsecond representation."""

    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("process create_time must be datetime or epoch seconds")
    try:
        seconds = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("process create_time is invalid") from exc
    if not seconds.is_finite() or seconds < 0:
        raise ValueError("process create_time must be finite and non-negative")
    microseconds = int(
        (seconds * Decimal(1_000_000)).to_integral_value(rounding=ROUND_HALF_EVEN)
    )
    try:
        return _EPOCH + timedelta(microseconds=microseconds)
    except OverflowError as exc:
        raise ValueError("process create_time is out of range") from exc


def _epoch_microseconds(value: datetime | float) -> int:
    normalized = normalize_process_create_time(value)
    delta = normalized - _EPOCH
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _parse_time(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return _as_utc(datetime.fromisoformat(normalized))


def _time_text(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None


def _tuple_text(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(item) for item in values)


def make_process_instance_id(
    *,
    execution_domain_id: str,
    pid: int,
    create_time: datetime | float,
    collector: str,
) -> str:
    """Derive an opaque runtime-instance ID that is not based on PID alone."""

    created_microseconds = _epoch_microseconds(create_time)
    material = "\x1f".join(
        (
            str(execution_domain_id),
            str(int(pid)),
            str(created_microseconds),
            str(collector),
        )
    ).encode("utf-8")
    return f"process-{hashlib.sha256(material).hexdigest()[:24]}"


@dataclass(frozen=True)
class ProcessFact:
    CURRENT_SCHEMA_VERSION: ClassVar[str] = "1.0"

    process_instance_id: str
    pid: int
    parent_pid: int | None
    executable_basename: str | None
    executable_identity_digest: str | None
    executable_identity_kind: ExecutableIdentityKind
    executable_identity_verified: bool
    create_time: datetime | None
    execution_domain_id: str
    current_state: ProcessState = ProcessState.UNKNOWN
    evidence_refs: tuple[str, ...] = ()
    access_status: CapabilityStatus = CapabilityStatus.UNKNOWN
    sanitized: bool = True
    warnings: tuple[ProcessWarningCode, ...] = ()
    fixed_facts: Mapping[str, bool] = field(default_factory=dict)
    supported_fixed_fact_names: tuple[str, ...] = ()
    collector: str = ""
    schema_version: str = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        state = _enum_or_unknown(ProcessState, self.current_state)
        access = _enum_or_unknown(CapabilityStatus, self.access_status)
        create_time = _as_utc(self.create_time) if self.create_time is not None else None
        basename = self.executable_basename
        if basename is not None and ("/" in basename or "\\" in basename):
            raise ValueError("executable_basename must contain a basename only")
        identity = self.executable_identity_digest
        if identity is not None and not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            identity.casefold(),
        ):
            raise ValueError("executable identity must be a SHA-256 digest summary")
        identity_kind = _enum_or_unknown(
            ExecutableIdentityKind,
            self.executable_identity_kind,
        )
        if identity is not None and identity_kind is ExecutableIdentityKind.UNKNOWN:
            raise ValueError("executable identity kind must describe the digest input")
        if identity is None and identity_kind is not ExecutableIdentityKind.UNKNOWN:
            raise ValueError("executable identity kind requires a digest")
        if self.executable_identity_verified:
            raise ValueError("P3A executable identity cannot be verified")
        if state is ProcessState.RUNNING and create_time is None:
            raise ValueError("RUNNING ProcessFact requires create_time")
        if not self.sanitized:
            raise ValueError("ProcessFact must be sanitized")
        if self.pid < 0 or (self.parent_pid is not None and self.parent_pid < 0):
            raise ValueError("process identifiers must not be negative")
        fixed_facts = {str(name): bool(value) for name, value in self.fixed_facts.items()}
        supported_fixed_fact_names = tuple(
            str(name) for name in self.supported_fixed_fact_names
        )
        if not set(supported_fixed_fact_names).issubset(fixed_facts):
            raise ValueError("supported fixed facts must reference collected boolean facts")
        for name in fixed_facts:
            normalized = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
            if any(
                marker in normalized
                for marker in (
                    "api_key",
                    "apikey",
                    "command_line",
                    "commandline",
                    "environment",
                    "password",
                    "private_key",
                    "remote_url",
                    "secret",
                    "token",
                )
            ):
                raise ValueError("sensitive fixed fact name is not permitted")
        object.__setattr__(self, "current_state", state)
        object.__setattr__(self, "access_status", access)
        object.__setattr__(self, "create_time", create_time)
        object.__setattr__(
            self,
            "executable_identity_digest",
            identity.casefold() if identity is not None else None,
        )
        object.__setattr__(self, "executable_identity_kind", identity_kind)
        object.__setattr__(self, "executable_identity_verified", False)
        object.__setattr__(self, "evidence_refs", tuple(str(item) for item in self.evidence_refs))
        object.__setattr__(
            self,
            "warnings",
            tuple(_enum_or_unknown(ProcessWarningCode, item) for item in self.warnings),
        )
        object.__setattr__(self, "fixed_facts", fixed_facts)
        object.__setattr__(
            self,
            "supported_fixed_fact_names",
            supported_fixed_fact_names,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "process_instance_id": self.process_instance_id,
            "pid": self.pid,
            "parent_pid": self.parent_pid,
            "executable_basename": self.executable_basename,
            "executable_identity_digest": self.executable_identity_digest,
            "executable_identity_kind": self.executable_identity_kind.value,
            "executable_identity_verified": self.executable_identity_verified,
            "create_time": _time_text(self.create_time),
            "execution_domain_id": self.execution_domain_id,
            "current_state": self.current_state.value,
            "evidence_refs": list(self.evidence_refs),
            "access_status": self.access_status.value,
            "sanitized": self.sanitized,
            "warnings": [item.value for item in self.warnings],
            "fixed_facts": dict(sorted(self.fixed_facts.items())),
            "supported_fixed_fact_names": list(self.supported_fixed_fact_names),
            "collector": self.collector,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProcessFact:
        fixed = data.get("fixed_facts")
        return cls(
            schema_version=str(data.get("schema_version", cls.CURRENT_SCHEMA_VERSION)),
            process_instance_id=str(data.get("process_instance_id", "unknown-process")),
            pid=int(data.get("pid", 0)),
            parent_pid=(int(data["parent_pid"]) if data.get("parent_pid") is not None else None),
            executable_basename=(
                str(data["executable_basename"])
                if data.get("executable_basename") is not None
                else None
            ),
            executable_identity_digest=(
                str(data["executable_identity_digest"])
                if data.get("executable_identity_digest") is not None
                else None
            ),
            executable_identity_kind=_enum_or_unknown(
                ExecutableIdentityKind,
                data.get("executable_identity_kind", "UNKNOWN"),
            ),
            executable_identity_verified=bool(
                data.get("executable_identity_verified", False)
            ),
            create_time=_parse_time(data.get("create_time")),
            execution_domain_id=str(data.get("execution_domain_id", "unknown-domain")),
            current_state=_enum_or_unknown(ProcessState, data.get("current_state", "UNKNOWN")),
            evidence_refs=_tuple_text(data.get("evidence_refs")),
            access_status=_enum_or_unknown(
                CapabilityStatus,
                data.get("access_status", "UNKNOWN"),
            ),
            sanitized=bool(data.get("sanitized", True)),
            warnings=tuple(
                _enum_or_unknown(ProcessWarningCode, item)
                for item in _tuple_text(data.get("warnings"))
            ),
            fixed_facts=fixed if isinstance(fixed, Mapping) else {},
            supported_fixed_fact_names=_tuple_text(
                data.get("supported_fixed_fact_names")
            ),
            collector=str(data.get("collector", "")),
        )


@dataclass(frozen=True)
class ProcessRelationship:
    process_instance_id: str
    parent_instance_id: str | None = None
    child_instance_ids: tuple[str, ...] = ()
    orphaned: bool = False
    parent_unavailable: bool = False
    evidence_refs: tuple[str, ...] = ()
    warnings: tuple[ProcessWarningCode, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "process_instance_id": self.process_instance_id,
            "parent_instance_id": self.parent_instance_id,
            "child_instance_ids": list(self.child_instance_ids),
            "orphaned": self.orphaned,
            "parent_unavailable": self.parent_unavailable,
            "evidence_refs": list(self.evidence_refs),
            "warnings": [item.value for item in self.warnings],
        }


@dataclass(frozen=True)
class WorkspaceCandidate:
    candidate_id: str
    source: WorkspaceSource
    execution_domain_id: str
    path_hint: str | None
    path_kind: WorkspacePathKind
    access_status: CapabilityStatus
    evidence_refs: tuple[str, ...]
    confidence: float | None = None
    sanitized: bool = True
    warnings: tuple[ProcessWarningCode, ...] = ()
    git_root_candidate: bool = False
    is_final_binding: bool = False

    def __post_init__(self) -> None:
        if self.is_final_binding:
            raise ValueError("WorkspaceCandidate cannot be a final binding")
        if not self.sanitized:
            raise ValueError("WorkspaceCandidate must be sanitized")
        if not self.evidence_refs:
            raise ValueError("WorkspaceCandidate requires evidence refs")
        raw_path = self.path_hint or ""
        if (
            re.match(r"^/home/[^/]+(?:/|$)", raw_path)
            or re.match(
                r"^[a-z]:[\\/]users[\\/][^\\/]+(?:[\\/]|$)",
                raw_path,
                re.IGNORECASE,
            )
            or re.match(
                r"^\\\\wsl(?:\$|\.localhost)\\[^\\]+\\home\\[^\\]+",
                raw_path,
                re.IGNORECASE,
            )
        ):
            raise ValueError("workspace user-home paths must be minimized")
        object.__setattr__(self, "source", _enum_or_unknown(WorkspaceSource, self.source))
        object.__setattr__(self, "path_kind", _enum_or_unknown(WorkspacePathKind, self.path_kind))
        object.__setattr__(self, "access_status", _enum_or_unknown(CapabilityStatus, self.access_status))
        object.__setattr__(self, "path_hint", redact_text(self.path_hint) if self.path_hint else None)
        object.__setattr__(self, "evidence_refs", tuple(str(item) for item in self.evidence_refs))
        object.__setattr__(self, "confidence", validate_confidence(self.confidence))
        object.__setattr__(
            self,
            "warnings",
            tuple(_enum_or_unknown(ProcessWarningCode, item) for item in self.warnings),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source": self.source.value,
            "execution_domain_id": self.execution_domain_id,
            "path_hint": self.path_hint,
            "path_kind": self.path_kind.value,
            "access_status": self.access_status.value,
            "evidence_refs": list(self.evidence_refs),
            "confidence": self.confidence,
            "sanitized": self.sanitized,
            "warnings": [item.value for item in self.warnings],
            "git_root_candidate": self.git_root_candidate,
            "is_final_binding": self.is_final_binding,
        }


@dataclass(frozen=True)
class ProcessWorkspaceAuthority:
    """Exact process CWD retained only inside the local authority pipeline."""

    process_instance_id: str
    candidate_id: str
    execution_domain_id: str
    cwd: Path = field(repr=False)
    evidence_refs: tuple[str, ...] = ()
    agent_id: str | None = None

    def __post_init__(self) -> None:
        if not self.process_instance_id or not self.candidate_id:
            raise ValueError("workspace authority requires opaque process and candidate IDs")
        if not self.execution_domain_id:
            raise ValueError("workspace authority requires an execution domain")
        if not self.evidence_refs:
            raise ValueError("workspace authority requires evidence refs")
        object.__setattr__(self, "cwd", Path(self.cwd))
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(str(item) for item in self.evidence_refs),
        )


__all__ = [
    "AgentRole",
    "ExecutableIdentityKind",
    "ProcessFact",
    "ProcessRelationship",
    "ProcessState",
    "ProcessWarningCode",
    "ProcessWorkspaceAuthority",
    "WorkspaceCandidate",
    "WorkspacePathKind",
    "WorkspaceSource",
    "make_process_instance_id",
    "normalize_process_create_time",
]
