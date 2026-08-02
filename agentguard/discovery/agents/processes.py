"""Injected, read-only process fact collection without a concrete OS backend."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from ..capabilities import CapabilityStatus, EvidenceReliability
from ..errors import DiscoveryError, DiscoveryErrorCode
from ..models import ProbeEvidence
from .models import (
    ExecutableIdentityKind,
    ProcessFact,
    ProcessRelationship,
    ProcessState,
    ProcessWarningCode,
    WorkspaceCandidate,
    WorkspaceSource,
    make_process_instance_id,
    normalize_process_create_time,
)


@runtime_checkable
class ProcessHandle(Protocol):
    """Only the metadata calls P3A permits a backend to expose."""

    pid: int

    def parent_pid(self) -> int | None: ...

    def executable_basename(self) -> str | None: ...

    def create_time(self) -> datetime | float: ...

    def cwd(self) -> str | None: ...

    def fixed_boolean_facts(self) -> Mapping[str, bool]: ...


@runtime_checkable
class ProcessBackend(Protocol):
    """Abstract enumeration backend; no psutil dependency is declared in P3A."""

    def iter_processes(self) -> Iterable[ProcessHandle]: ...


@dataclass(frozen=True)
class ProcessCollectionResult:
    facts: tuple[ProcessFact, ...] = ()
    relationships: tuple[ProcessRelationship, ...] = ()
    workspace_candidates: tuple[WorkspaceCandidate, ...] = ()
    evidence: tuple[ProbeEvidence, ...] = ()
    errors: tuple[DiscoveryError, ...] = ()
    status: CapabilityStatus = CapabilityStatus.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "facts": [item.to_dict() for item in self.facts],
            "relationships": [item.to_dict() for item in self.relationships],
            "workspace_candidates": [item.to_dict() for item in self.workspace_candidates],
            "evidence": [item.to_dict() for item in self.evidence],
            "errors": [item.to_dict() for item in self.errors],
            "status": self.status.value,
        }


class ProcessCollector:
    """Best-effort metadata collector over an explicitly injected backend."""

    def __init__(
        self,
        *,
        backend: ProcessBackend,
        execution_domain_id: str,
        collector: str,
        clock: Callable[[], datetime],
        home_path: str | None = None,
        allowed_fixed_fact_names: tuple[str, ...] = (),
    ) -> None:
        self._backend = backend
        self._execution_domain_id = execution_domain_id
        self._collector = collector
        self._clock = clock
        self._home_path = home_path
        self._allowed_fixed_fact_names = frozenset(
            str(item) for item in allowed_fixed_fact_names
        )

    def collect(self) -> ProcessCollectionResult:
        observed_at = _as_utc(self._clock())
        try:
            handles = tuple(self._backend.iter_processes())
        except NotImplementedError:
            error = _collector_error(
                DiscoveryErrorCode.UNSUPPORTED,
                self._collector,
                "PROCESS_ENUMERATION_UNSUPPORTED",
            )
            return ProcessCollectionResult(errors=(error,), status=CapabilityStatus.UNSUPPORTED)
        except PermissionError:
            error = _collector_error(
                DiscoveryErrorCode.PERMISSION_DENIED,
                self._collector,
                "PROCESS_ENUMERATION_PERMISSION_DENIED",
            )
            return ProcessCollectionResult(
                errors=(error,),
                status=CapabilityStatus.PERMISSION_DENIED,
            )
        except Exception:  # noqa: BLE001 - enumeration failures are isolated
            error = _collector_error(
                DiscoveryErrorCode.COLLECTOR_FAILURE,
                self._collector,
                "PROCESS_ENUMERATION_FAILED",
            )
            return ProcessCollectionResult(errors=(error,), status=CapabilityStatus.ERROR)

        facts: list[ProcessFact] = []
        evidence: list[ProbeEvidence] = []
        errors: list[DiscoveryError] = []
        workspace_candidates: list[WorkspaceCandidate] = []
        for handle in handles:
            fact, record, item_errors, item_workspaces = self._collect_handle(
                handle,
                observed_at,
            )
            facts.append(fact)
            evidence.append(record)
            errors.extend(item_errors)
            workspace_candidates.extend(item_workspaces)

        relationships = build_process_relationships(tuple(facts))
        status = (
            CapabilityStatus.DEGRADED
            if errors or any(item.access_status is not CapabilityStatus.AVAILABLE for item in facts)
            else CapabilityStatus.AVAILABLE
        )
        return ProcessCollectionResult(
            facts=tuple(facts),
            relationships=relationships,
            workspace_candidates=tuple(workspace_candidates),
            evidence=tuple(evidence),
            errors=tuple(errors),
            status=status,
        )

    def _collect_handle(
        self,
        handle: ProcessHandle,
        observed_at: datetime,
    ) -> tuple[
        ProcessFact,
        ProbeEvidence,
        tuple[DiscoveryError, ...],
        tuple[WorkspaceCandidate, ...],
    ]:
        pid = _safe_pid(getattr(handle, "pid", -1))
        try:
            create_time = normalize_process_create_time(handle.create_time())
        except ProcessLookupError:
            error = _process_error(
                DiscoveryErrorCode.NOT_PRESENT,
                self._collector,
                "PROCESS_EXITED",
            )
            return (*self._unavailable_fact(
                pid=pid,
                observed_at=observed_at,
                state=ProcessState.EXITED,
                access=CapabilityStatus.NOT_PRESENT,
                warning=ProcessWarningCode.PROCESS_EXITED,
                error=error,
            ), ())
        except PermissionError:
            error = _process_error(
                DiscoveryErrorCode.PERMISSION_DENIED,
                self._collector,
                "PROCESS_METADATA_PERMISSION_DENIED",
            )
            return (*self._unavailable_fact(
                pid=pid,
                observed_at=observed_at,
                state=ProcessState.UNKNOWN,
                access=CapabilityStatus.PERMISSION_DENIED,
                warning=ProcessWarningCode.PARTIAL_VISIBILITY,
                error=error,
            ), ())
        except NotImplementedError:
            error = _process_error(
                DiscoveryErrorCode.UNSUPPORTED,
                self._collector,
                "PROCESS_METADATA_UNSUPPORTED",
            )
            return (*self._unavailable_fact(
                pid=pid,
                observed_at=observed_at,
                state=ProcessState.UNKNOWN,
                access=CapabilityStatus.UNSUPPORTED,
                warning=ProcessWarningCode.PARTIAL_VISIBILITY,
                error=error,
            ), ())
        except Exception:  # noqa: BLE001 - one process must not stop collection
            error = _process_error(
                DiscoveryErrorCode.COLLECTOR_FAILURE,
                self._collector,
                "PROCESS_METADATA_FAILED",
            )
            return (*self._unavailable_fact(
                pid=pid,
                observed_at=observed_at,
                state=ProcessState.UNKNOWN,
                access=CapabilityStatus.ERROR,
                warning=ProcessWarningCode.PARTIAL_VISIBILITY,
                error=error,
            ), ())

        errors: list[DiscoveryError] = []
        parent_pid = self._optional_value(handle.parent_pid, errors)
        basename = self._optional_value(handle.executable_basename, errors)
        fixed_facts = self._optional_value(handle.fixed_boolean_facts, errors)
        safe_basename = _basename(basename) if isinstance(basename, str) else None
        safe_identity = (
            f"sha256:{hashlib.sha256(safe_basename.casefold().encode()).hexdigest()}"
            if safe_basename is not None
            else None
        )
        identity_kind = (
            ExecutableIdentityKind.BASENAME_SHA256
            if safe_identity is not None
            else ExecutableIdentityKind.UNKNOWN
        )
        raw_fixed = fixed_facts if isinstance(fixed_facts, Mapping) else {}
        safe_fixed = {
            str(name): bool(value)
            for name, value in raw_fixed.items()
            if str(name) in self._allowed_fixed_fact_names
        }
        rejected_fixed_facts = len(safe_fixed) != len(raw_fixed)
        if rejected_fixed_facts:
            errors.append(
                _process_error(
                    DiscoveryErrorCode.INVALID_DATA,
                    self._collector,
                    "UNAPPROVED_FIXED_FACT_REJECTED",
                )
            )
        instance_id = make_process_instance_id(
            execution_domain_id=self._execution_domain_id,
            pid=pid,
            create_time=create_time,
            collector=self._collector,
        )
        evidence_id = f"{self._collector}:{instance_id}"
        workspace_candidates = self._collect_workspace_candidate(
            handle,
            evidence_id,
            errors,
        )
        exited_during_read = any(
            error.code is DiscoveryErrorCode.NOT_PRESENT for error in errors
        )
        access = (
            CapabilityStatus.NOT_PRESENT
            if exited_during_read
            else CapabilityStatus.DEGRADED
            if errors
            else CapabilityStatus.AVAILABLE
        )
        warning_values: list[ProcessWarningCode] = []
        if errors:
            warning_values.append(ProcessWarningCode.PARTIAL_VISIBILITY)
        if rejected_fixed_facts:
            warning_values.append(ProcessWarningCode.SENSITIVE_INPUT_REJECTED)
        if exited_during_read:
            warning_values.append(ProcessWarningCode.PROCESS_EXITED)
        warnings = tuple(dict.fromkeys(warning_values))
        fact = ProcessFact(
            process_instance_id=instance_id,
            pid=pid,
            parent_pid=int(parent_pid) if parent_pid is not None else None,
            executable_basename=safe_basename,
            executable_identity_digest=safe_identity,
            executable_identity_kind=identity_kind,
            executable_identity_verified=False,
            create_time=create_time,
            execution_domain_id=self._execution_domain_id,
            current_state=(
                ProcessState.EXITED if exited_during_read else ProcessState.RUNNING
            ),
            evidence_refs=(evidence_id,),
            access_status=access,
            sanitized=True,
            warnings=warnings,
            fixed_facts=safe_fixed,
            supported_fixed_fact_names=tuple(sorted(safe_fixed)),
            collector=self._collector,
        )
        record = _process_evidence(fact, observed_at, errors[0] if errors else None)
        return fact, record, tuple(errors), workspace_candidates

    def _collect_workspace_candidate(
        self,
        handle: ProcessHandle,
        evidence_id: str,
        errors: list[DiscoveryError],
    ) -> tuple[WorkspaceCandidate, ...]:
        from .workspaces import (
            unavailable_workspace_candidate,
            workspace_candidate_from_path,
        )

        try:
            cwd = handle.cwd()
        except ProcessLookupError:
            status = CapabilityStatus.NOT_PRESENT
            reason = "PROCESS_EXITED"
            code = DiscoveryErrorCode.NOT_PRESENT
        except PermissionError:
            status = CapabilityStatus.PERMISSION_DENIED
            reason = "PROCESS_CWD_PERMISSION_DENIED"
            code = DiscoveryErrorCode.PERMISSION_DENIED
        except NotImplementedError:
            status = CapabilityStatus.UNSUPPORTED
            reason = "PROCESS_CWD_UNSUPPORTED"
            code = DiscoveryErrorCode.UNSUPPORTED
        except Exception:  # noqa: BLE001 - cwd failure remains one bounded fact
            status = CapabilityStatus.ERROR
            reason = "PROCESS_CWD_FAILED"
            code = DiscoveryErrorCode.COLLECTOR_FAILURE
        else:
            if cwd is None:
                return (
                    unavailable_workspace_candidate(
                        source=WorkspaceSource.PROCESS_CWD,
                        execution_domain_id=self._execution_domain_id,
                        evidence_refs=(evidence_id,),
                        access_status=CapabilityStatus.UNKNOWN,
                    ),
                )
            return (
                workspace_candidate_from_path(
                    path=str(cwd),
                    source=WorkspaceSource.PROCESS_CWD,
                    execution_domain_id=self._execution_domain_id,
                    evidence_refs=(evidence_id,),
                    home_path=self._home_path,
                ),
            )
        errors.append(_process_error(code, self._collector, reason))
        return (
            unavailable_workspace_candidate(
                source=WorkspaceSource.PROCESS_CWD,
                execution_domain_id=self._execution_domain_id,
                evidence_refs=(evidence_id,),
                access_status=status,
            ),
        )

    def _optional_value(self, reader: Callable[[], Any], errors: list[DiscoveryError]) -> Any:
        try:
            return reader()
        except ProcessLookupError:
            errors.append(
                _process_error(
                    DiscoveryErrorCode.NOT_PRESENT,
                    self._collector,
                    "PROCESS_EXITED",
                )
            )
        except PermissionError:
            errors.append(
                _process_error(
                    DiscoveryErrorCode.PERMISSION_DENIED,
                    self._collector,
                    "PROCESS_FIELD_PERMISSION_DENIED",
                )
            )
        except NotImplementedError:
            errors.append(
                _process_error(
                    DiscoveryErrorCode.UNSUPPORTED,
                    self._collector,
                    "PROCESS_FIELD_UNSUPPORTED",
                )
            )
        except Exception:  # noqa: BLE001 - one optional field cannot stop collection
            errors.append(
                _process_error(
                    DiscoveryErrorCode.COLLECTOR_FAILURE,
                    self._collector,
                    "PROCESS_FIELD_FAILED",
                )
            )
        return None

    def _unavailable_fact(
        self,
        *,
        pid: int,
        observed_at: datetime,
        state: ProcessState,
        access: CapabilityStatus,
        warning: ProcessWarningCode,
        error: DiscoveryError,
    ) -> tuple[ProcessFact, ProbeEvidence, tuple[DiscoveryError, ...]]:
        instance_id = _process_event_id(
            self._execution_domain_id,
            pid,
            observed_at,
            self._collector,
            warning.value,
        )
        evidence_id = f"{self._collector}:{instance_id}"
        fact = ProcessFact(
            process_instance_id=instance_id,
            pid=pid,
            parent_pid=None,
            executable_basename=None,
            executable_identity_digest=None,
            executable_identity_kind=ExecutableIdentityKind.UNKNOWN,
            executable_identity_verified=False,
            create_time=None,
            execution_domain_id=self._execution_domain_id,
            current_state=state,
            evidence_refs=(evidence_id,),
            access_status=access,
            sanitized=True,
            warnings=(warning,),
            collector=self._collector,
        )
        return fact, _process_evidence(fact, observed_at, error), (error,)


def build_process_relationships(
    facts: tuple[ProcessFact, ...],
) -> tuple[ProcessRelationship, ...]:
    """Relate same-domain runtime instances without inferring control or trust."""

    candidates: dict[tuple[str, int], list[ProcessFact]] = {}
    for fact in facts:
        if fact.current_state is ProcessState.RUNNING:
            candidates.setdefault((fact.execution_domain_id, fact.pid), []).append(fact)

    parent_links: dict[str, str] = {}
    fact_by_id = {item.process_instance_id: item for item in facts}
    for child in facts:
        if child.parent_pid is None or child.create_time is None:
            continue
        possible = [
            parent
            for parent in candidates.get(
                (child.execution_domain_id, child.parent_pid),
                (),
            )
            if parent.process_instance_id != child.process_instance_id
            and parent.create_time is not None
            and parent.create_time <= child.create_time
        ]
        if possible:
            parent = max(possible, key=lambda item: item.create_time or datetime.min.replace(tzinfo=UTC))
            parent_links[child.process_instance_id] = parent.process_instance_id

    cycle_nodes = _cycle_nodes(parent_links)
    for node in cycle_nodes:
        parent_links.pop(node, None)

    children: dict[str, list[str]] = {}
    for child_id, parent_id in parent_links.items():
        children.setdefault(parent_id, []).append(child_id)

    relationships: list[ProcessRelationship] = []
    for fact in facts:
        instance_id = fact.process_instance_id
        requested_parent = fact.parent_pid is not None
        parent_id = parent_links.get(instance_id)
        cycle = instance_id in cycle_nodes
        unavailable = requested_parent and parent_id is None and not cycle
        warnings: tuple[ProcessWarningCode, ...] = ()
        if cycle:
            warnings = (ProcessWarningCode.PARENT_CYCLE,)
        elif unavailable:
            warnings = (ProcessWarningCode.PARENT_UNAVAILABLE,)
        evidence_refs = list(fact.evidence_refs)
        if parent_id is not None:
            evidence_refs.extend(fact_by_id[parent_id].evidence_refs)
        relationships.append(
            ProcessRelationship(
                process_instance_id=instance_id,
                parent_instance_id=parent_id,
                child_instance_ids=tuple(sorted(children.get(instance_id, ()))),
                orphaned=cycle or unavailable,
                parent_unavailable=unavailable,
                evidence_refs=tuple(dict.fromkeys(evidence_refs)),
                warnings=warnings,
            )
        )
    return tuple(relationships)


def _cycle_nodes(parent_links: Mapping[str, str]) -> set[str]:
    cycles: set[str] = set()
    for start in parent_links:
        path: list[str] = []
        positions: dict[str, int] = {}
        current: str | None = start
        while current is not None and current in parent_links:
            if current in positions:
                cycles.update(path[positions[current] :])
                break
            positions[current] = len(path)
            path.append(current)
            current = parent_links.get(current)
    return cycles


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("process timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _safe_pid(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _basename(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1]


def _process_event_id(
    domain_id: str,
    pid: int,
    observed_at: datetime,
    collector: str,
    reason: str,
) -> str:
    material = "\x1f".join(
        (domain_id, str(pid), observed_at.isoformat(), collector, reason)
    ).encode("utf-8")
    return f"process-event-{hashlib.sha256(material).hexdigest()[:24]}"


def _collector_error(
    code: DiscoveryErrorCode,
    collector: str,
    reason_code: str,
) -> DiscoveryError:
    return DiscoveryError(
        code=code,
        message="Process enumeration did not complete",
        collector=collector,
        source="local-process-table",
        details={"reason_code": reason_code},
    )


def _process_error(
    code: DiscoveryErrorCode,
    collector: str,
    reason_code: str,
) -> DiscoveryError:
    return DiscoveryError(
        code=code,
        message="Process metadata was not fully available",
        collector=collector,
        source="local-process-table",
        details={"reason_code": reason_code},
    )


def _process_evidence(
    fact: ProcessFact,
    observed_at: datetime,
    error: DiscoveryError | None,
) -> ProbeEvidence:
    return ProbeEvidence(
        evidence_id=fact.evidence_refs[0],
        collector=fact.collector,
        source="local-process-table",
        observed_at=observed_at,
        fact_type="process.metadata",
        value={
            "process_instance_id": fact.process_instance_id,
            "pid": fact.pid,
            "parent_pid": fact.parent_pid,
            "executable_basename": fact.executable_basename,
            "executable_identity_digest": fact.executable_identity_digest,
            "executable_identity_kind": fact.executable_identity_kind.value,
            "executable_identity_verified": fact.executable_identity_verified,
            "current_state": fact.current_state.value,
            "fixed_facts": dict(fact.fixed_facts),
        },
        reliability=(
            EvidenceReliability.MEDIUM
            if fact.access_status is CapabilityStatus.AVAILABLE
            else EvidenceReliability.LOW
        ),
        confidence=0.8 if fact.access_status is CapabilityStatus.AVAILABLE else 0.3,
        status=fact.access_status,
        error=error,
        sanitized=True,
    )


__all__ = [
    "ProcessBackend",
    "ProcessCollectionResult",
    "ProcessCollector",
    "ProcessHandle",
    "build_process_relationships",
]
