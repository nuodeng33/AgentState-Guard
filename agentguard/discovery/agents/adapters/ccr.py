"""Pure, offline Claude Code Router classification over normalized facts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ...capabilities import AgentLifecycleStatus, CapabilityStatus
from ..models import (
    AgentCandidateType,
    AgentClassification,
    AgentRole,
    ProcessFact,
    ProcessRelationship,
    ProcessState,
    WorkspaceCandidate,
)

_PID_MAX_BYTES = 64
_CCR_PACKAGE_NAME = "@musistudio/claude-code-router"
_CCR_OBSERVED_FACT = "ccr_runtime_observed"
_CCR_OBSERVED_COLLECTORS = frozenset({"ccr-runtime-probe"})
_CLOUDCLI_OBSERVED_FACT = "cloudcli_runtime_observed"
_DEFAULT_DEPLOYMENT_BINDING = "default"
_CONTROLLED_PROVENANCE = object()


class CcrMarkerKind(str, Enum):
    INSTALLATION_MARKER = "INSTALLATION_MARKER"
    PACKAGE_MARKER = "PACKAGE_MARKER"
    SERVICE_MARKER = "SERVICE_MARKER"
    PID_MARKER = "PID_MARKER"
    PROCESS_MARKER = "PROCESS_MARKER"
    VERSION_MARKER = "VERSION_MARKER"
    CONFIG_MARKER = "CONFIG_MARKER"
    RUNTIME_MARKER = "RUNTIME_MARKER"


class CcrMarkerSource(str, Enum):
    FIXED_CONTAINER_BINARY = "FIXED_CONTAINER_BINARY"
    FIXED_CONTAINER_PATH = "FIXED_CONTAINER_PATH"
    FIXED_PACKAGE_METADATA = "FIXED_PACKAGE_METADATA"
    HOST_PROBE = "HOST_PROBE"


class CcrMarkerVerification(str, Enum):
    DECLARED = "DECLARED"
    STRUCTURED = "STRUCTURED"
    VERIFIED = "VERIFIED"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class CcrDetectionLevel(str, Enum):
    TRACE_ONLY = "TRACE_ONLY"
    STALE_RUNTIME_TRACE = "STALE_RUNTIME_TRACE"
    PROCESS_CANDIDATE = "PROCESS_CANDIDATE"
    CONFIRMED_RUNNING = "CONFIRMED_RUNNING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class _MarkerProfile:
    marker_kind: CcrMarkerKind
    source_kind: CcrMarkerSource
    collector_id: str
    strong_static_anchor: bool = False


_MARKER_PROFILES = {
    "ccr.installation": _MarkerProfile(
        CcrMarkerKind.INSTALLATION_MARKER,
        CcrMarkerSource.FIXED_CONTAINER_BINARY,
        "ccr-fixed-container-probe",
        True,
    ),
    "ccr.package": _MarkerProfile(
        CcrMarkerKind.PACKAGE_MARKER,
        CcrMarkerSource.FIXED_PACKAGE_METADATA,
        "ccr-package-metadata-probe",
        True,
    ),
    "ccr.service": _MarkerProfile(
        CcrMarkerKind.SERVICE_MARKER,
        CcrMarkerSource.FIXED_CONTAINER_PATH,
        "ccr-service-metadata-probe",
        True,
    ),
    "ccr.pid": _MarkerProfile(
        CcrMarkerKind.PID_MARKER,
        CcrMarkerSource.FIXED_CONTAINER_PATH,
        "ccr-fixed-container-probe",
    ),
    "ccr.process": _MarkerProfile(
        CcrMarkerKind.PROCESS_MARKER,
        CcrMarkerSource.HOST_PROBE,
        "ccr-runtime-probe",
    ),
    "ccr.version": _MarkerProfile(
        CcrMarkerKind.VERSION_MARKER,
        CcrMarkerSource.FIXED_PACKAGE_METADATA,
        "ccr-package-metadata-probe",
        True,
    ),
    "ccr.config": _MarkerProfile(
        CcrMarkerKind.CONFIG_MARKER,
        CcrMarkerSource.FIXED_CONTAINER_PATH,
        "ccr-config-presence-probe",
    ),
    "ccr.runtime": _MarkerProfile(
        CcrMarkerKind.RUNTIME_MARKER,
        CcrMarkerSource.HOST_PROBE,
        "ccr-runtime-probe",
    ),
}


@dataclass(frozen=True)
class CcrMarker:
    marker_id: str
    marker_kind: CcrMarkerKind
    present: bool
    source_kind: CcrMarkerSource
    collector_id: str
    evidence_ref: str
    verification: CcrMarkerVerification
    execution_domain_id: str
    deployment_binding_id: str
    sanitized: bool
    warnings: tuple[str, ...] = ()
    pid: int | None = None
    reason_code: str | None = None
    package_name: str | None = None
    version: str | None = None
    _provenance: object | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", self.marker_id) is None:
            raise ValueError("marker_id must be a stable identifier")
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", self.collector_id) is None:
            raise ValueError("collector_id must be a stable identifier")
        if re.fullmatch(
            r"[a-z0-9][a-z0-9._-]{0,127}",
            self.deployment_binding_id,
        ) is None:
            raise ValueError("deployment_binding_id must be sanitized and stable")
        if not self.execution_domain_id or len(self.execution_domain_id) > 256:
            raise ValueError("execution domain must be bounded")
        if not self.evidence_ref or len(self.evidence_ref) > 256:
            raise ValueError("evidence_ref must be bounded")
        if not self.sanitized:
            raise ValueError("CCR markers must be sanitized")
        try:
            marker_kind = (
                self.marker_kind
                if isinstance(self.marker_kind, CcrMarkerKind)
                else CcrMarkerKind(str(self.marker_kind))
            )
            source_kind = (
                self.source_kind
                if isinstance(self.source_kind, CcrMarkerSource)
                else CcrMarkerSource(str(self.source_kind))
            )
            verification = (
                self.verification
                if isinstance(self.verification, CcrMarkerVerification)
                else CcrMarkerVerification(str(self.verification))
            )
        except ValueError as exc:
            raise ValueError("CCR marker enum value is invalid") from exc
        if self.pid is not None and (isinstance(self.pid, bool) or self.pid <= 0):
            raise ValueError("CCR PID must be a positive integer")
        if self.package_name is not None and self.package_name != _CCR_PACKAGE_NAME:
            raise ValueError("CCR package_name must be the exact fixed identity")
        if self.version is not None and re.fullmatch(
            r"[0-9A-Za-z.+-]{1,64}",
            self.version,
        ) is None:
            raise ValueError("CCR version must be normalized and bounded")
        if self.reason_code is not None and re.fullmatch(
            r"[A-Z][A-Z0-9_]{0,127}",
            self.reason_code,
        ) is None:
            raise ValueError("CCR reason code must be stable")
        warnings = tuple(str(item) for item in self.warnings)
        if any(
            re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", item) is None
            for item in warnings
        ):
            raise ValueError("CCR warnings must contain stable codes")
        object.__setattr__(self, "marker_kind", marker_kind)
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "verification", verification)
        object.__setattr__(self, "warnings", warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "marker_id": self.marker_id,
            "marker_kind": self.marker_kind.value,
            "present": self.present,
            "source_kind": self.source_kind.value,
            "collector_id": self.collector_id,
            "evidence_ref": self.evidence_ref,
            "verification": self.verification.value,
            "execution_domain_id": self.execution_domain_id,
            "deployment_binding_id": self.deployment_binding_id,
            "sanitized": self.sanitized,
            "warnings": list(self.warnings),
            "pid": self.pid,
            "reason_code": self.reason_code,
            "package_name": self.package_name,
            "version": self.version,
        }


@dataclass(frozen=True)
class CcrDiscoveryResult:
    classification: AgentClassification
    detection_level: CcrDetectionLevel
    status: CapabilityStatus
    confirmed: bool
    execution_domain_id: str
    deployment_binding_id: str
    deployment_trace_id: str
    markers: tuple[CcrMarker, ...]
    evidence_refs: tuple[str, ...]
    reason_codes: tuple[str, ...] = ()
    workspace_candidates: tuple[WorkspaceCandidate, ...] = ()
    sanitized: bool = True
    warnings: tuple[str, ...] = ()
    adapter_id: str = "ccr"

    def __post_init__(self) -> None:
        if not self.sanitized:
            raise ValueError("CCR results must be sanitized")

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "classification": self.classification.to_dict(),
            "detection_level": self.detection_level.value,
            "status": self.status.value,
            "confirmed": self.confirmed,
            "execution_domain_id": self.execution_domain_id,
            "deployment_binding_id": self.deployment_binding_id,
            "deployment_trace_id": self.deployment_trace_id,
            "markers": [item.to_dict() for item in self.markers],
            "evidence_refs": list(self.evidence_refs),
            "reason_codes": list(self.reason_codes),
            "workspace_candidates": [
                item.to_dict() for item in self.workspace_candidates
            ],
            "sanitized": self.sanitized,
            "warnings": list(self.warnings),
        }


def create_verified_ccr_marker(
    *,
    marker_id: str,
    execution_domain_id: str,
    evidence_ref: str,
    present: bool = True,
    deployment_binding_id: str = _DEFAULT_DEPLOYMENT_BINDING,
    package_name: str | None = None,
    version: str | None = None,
) -> CcrMarker:
    """Create a CCR marker from the fixed collector and identity table."""

    if marker_id not in _MARKER_PROFILES or marker_id == "ccr.pid":
        raise ValueError("marker_id is not available through this fixed collector")
    if marker_id != "ccr.package" and package_name is not None:
        raise ValueError("package_name is only valid for the fixed package marker")
    verification = CcrMarkerVerification.VERIFIED
    reason_code = None
    normalized_package = None
    normalized_version = None
    if marker_id == "ccr.package":
        if package_name == _CCR_PACKAGE_NAME:
            normalized_package = _CCR_PACKAGE_NAME
        else:
            verification = CcrMarkerVerification.INVALID
            reason_code = "CCR_PACKAGE_NAME_MISMATCH"
    if marker_id == "ccr.version":
        if version is None:
            verification = CcrMarkerVerification.UNKNOWN
        elif re.fullmatch(r"[0-9A-Za-z.+-]{1,64}", version) is not None:
            normalized_version = version
        else:
            verification = CcrMarkerVerification.INVALID
            reason_code = "CCR_VERSION_INVALID"
    elif version is not None:
        raise ValueError("version is only valid for the fixed version marker")
    return _controlled_marker(
        marker_id=marker_id,
        present=present,
        evidence_ref=evidence_ref,
        verification=verification,
        execution_domain_id=execution_domain_id,
        deployment_binding_id=deployment_binding_id,
        reason_code=reason_code,
        package_name=normalized_package,
        version=normalized_version,
    )


def parse_ccr_pid_marker(
    *,
    raw: bytes,
    execution_domain_id: str,
    evidence_ref: str,
    deployment_binding_id: str = _DEFAULT_DEPLOYMENT_BINDING,
    is_symlink: bool = False,
) -> CcrMarker:
    """Parse already-bounded bytes from an allowlisted CCR PID marker."""

    if is_symlink:
        return _invalid_pid_marker(
            execution_domain_id,
            evidence_ref,
            deployment_binding_id,
            "CCR_PID_SYMLINK_UNSUPPORTED",
        )
    if len(raw) > _PID_MAX_BYTES:
        return _invalid_pid_marker(
            execution_domain_id,
            evidence_ref,
            deployment_binding_id,
            "CCR_PID_OUTPUT_TOO_LARGE",
        )
    if re.fullmatch(rb"[1-9][0-9]*(?:\n)?", raw) is None:
        return _invalid_pid_marker(
            execution_domain_id,
            evidence_ref,
            deployment_binding_id,
            "CCR_PID_INVALID",
        )
    return _controlled_marker(
        marker_id="ccr.pid",
        present=True,
        evidence_ref=evidence_ref,
        verification=CcrMarkerVerification.STRUCTURED,
        execution_domain_id=execution_domain_id,
        deployment_binding_id=deployment_binding_id,
        pid=int(raw.rstrip(b"\n")),
    )


def _invalid_pid_marker(
    execution_domain_id: str,
    evidence_ref: str,
    deployment_binding_id: str,
    reason_code: str,
) -> CcrMarker:
    return _controlled_marker(
        marker_id="ccr.pid",
        present=True,
        evidence_ref=evidence_ref,
        verification=CcrMarkerVerification.INVALID,
        execution_domain_id=execution_domain_id,
        deployment_binding_id=deployment_binding_id,
        reason_code=reason_code,
    )


def _controlled_marker(
    *,
    marker_id: str,
    present: bool,
    evidence_ref: str,
    verification: CcrMarkerVerification,
    execution_domain_id: str,
    deployment_binding_id: str,
    pid: int | None = None,
    reason_code: str | None = None,
    package_name: str | None = None,
    version: str | None = None,
) -> CcrMarker:
    profile = _MARKER_PROFILES[marker_id]
    marker = CcrMarker(
        marker_id=marker_id,
        marker_kind=profile.marker_kind,
        present=present,
        source_kind=profile.source_kind,
        collector_id=profile.collector_id,
        evidence_ref=evidence_ref,
        verification=verification,
        execution_domain_id=execution_domain_id,
        deployment_binding_id=deployment_binding_id,
        sanitized=True,
        pid=pid,
        reason_code=reason_code,
        package_name=package_name,
        version=version,
    )
    object.__setattr__(marker, "_provenance", _CONTROLLED_PROVENANCE)
    return marker


class CcrAdapter:
    """Classify CCR without process, filesystem, configuration, or network I/O."""

    adapter_id = "ccr"
    role = AgentRole.MODEL_ROUTER

    def __init__(
        self,
        *,
        markers: tuple[CcrMarker, ...] = (),
        workspace_candidates: tuple[WorkspaceCandidate, ...] = (),
        relationships: tuple[ProcessRelationship, ...] = (),
    ) -> None:
        if any(not isinstance(item, CcrMarker) for item in markers):
            raise TypeError("CCR Adapter requires CcrMarker inputs")
        self._markers = tuple(sorted(markers, key=_marker_key))
        self._workspace_candidates = tuple(
            sorted(workspace_candidates, key=lambda item: item.candidate_id)
        )
        self._relationships = tuple(
            sorted(relationships, key=lambda item: item.process_instance_id)
        )

    def discover(
        self,
        facts: tuple[ProcessFact, ...],
    ) -> tuple[AgentClassification, ...]:
        return tuple(item.classification for item in self.assess(facts))

    def assess(
        self,
        facts: tuple[ProcessFact, ...],
    ) -> tuple[CcrDiscoveryResult, ...]:
        if any(not isinstance(item, ProcessFact) for item in facts):
            raise TypeError("CCR Adapter requires ProcessFact inputs")
        present_markers = tuple(item for item in self._markers if item.present)
        if not present_markers:
            return ()
        facts = tuple(
            sorted(
                facts,
                key=lambda item: (
                    item.execution_domain_id,
                    item.pid,
                    item.process_instance_id,
                ),
            )
        )
        results: list[CcrDiscoveryResult] = []
        domains = sorted({item.execution_domain_id for item in present_markers})
        for domain in domains:
            domain_markers = tuple(
                item
                for item in present_markers
                if item.execution_domain_id == domain
            )
            bindings = sorted(
                {item.deployment_binding_id for item in domain_markers}
            )
            deployment_conflict = len(bindings) > 1
            domain_evidence = _marker_evidence(domain_markers)
            for binding in bindings:
                bound_markers = tuple(
                    item
                    for item in domain_markers
                    if item.deployment_binding_id == binding
                )
                pid_markers = tuple(
                    item
                    for item in bound_markers
                    if item.marker_kind is CcrMarkerKind.PID_MARKER
                )
                if not pid_markers:
                    results.append(
                        self._trace_result(
                            domain,
                            binding,
                            bound_markers,
                            deployment_conflict=deployment_conflict,
                            conflict_evidence_refs=(
                                domain_evidence if deployment_conflict else ()
                            ),
                        )
                    )
                    continue
                process_conflict = self._has_multiple_bound_processes(
                    pid_markers,
                    facts,
                    domain,
                )
                for pid_marker in pid_markers:
                    results.append(
                        self._process_result(
                            domain,
                            binding,
                            bound_markers,
                            pid_marker,
                            facts,
                            global_conflict=(deployment_conflict or process_conflict),
                            conflict_evidence_refs=(
                                domain_evidence if deployment_conflict else ()
                            ),
                        )
                    )
        return tuple(
            sorted(
                results,
                key=lambda item: (
                    item.deployment_trace_id,
                    item.classification.candidate_id,
                ),
            )
        )

    def _trace_result(
        self,
        domain: str,
        binding: str,
        markers: tuple[CcrMarker, ...],
        *,
        deployment_conflict: bool,
        conflict_evidence_refs: tuple[str, ...],
    ) -> CcrDiscoveryResult:
        reasons = set(_marker_reason_codes(markers))
        invalid = any(
            item.verification is CcrMarkerVerification.INVALID
            for item in markers
        )
        if deployment_conflict:
            reasons.add("CCR_DEPLOYMENT_CONFLICT")
        if invalid:
            reasons.add("CCR_SIGNATURE_CONFLICT")
        if not reasons:
            reasons.add("CCR_EVIDENCE_INSUFFICIENT")
        config_only = all(
            item.marker_kind is CcrMarkerKind.CONFIG_MARKER for item in markers
        )
        evidence = tuple(
            sorted({*_marker_evidence(markers), *conflict_evidence_refs})
        )
        trace_id = _trace_id(domain, binding)
        uncertainties = ["CCR_RUNNING_PROCESS_UNCONFIRMED"]
        if not any(
            item.marker_kind is CcrMarkerKind.VERSION_MARKER
            and _is_controlled_marker(item)
            and item.verification is CcrMarkerVerification.VERIFIED
            for item in markers
        ):
            uncertainties.append("CCR_VERSION_REQUIRED")
        classification = AgentClassification(
            candidate_id=trace_id,
            candidate_type=AgentCandidateType.EXECUTABLE,
            role=AgentRole.MODEL_ROUTER,
            lifecycle=AgentLifecycleStatus.DETECTED,
            confidence=0.2 if config_only else 0.45,
            evidence_refs=evidence,
            uncertainties=tuple(uncertainties),
            required_checks=("CCR_PROCESS_BINDING_REQUIRED",),
            process_instance_id=None,
        )
        reason_codes = tuple(sorted(reasons))
        return CcrDiscoveryResult(
            classification=classification,
            detection_level=CcrDetectionLevel.TRACE_ONLY,
            status=CapabilityStatus.DEGRADED,
            confirmed=False,
            execution_domain_id=domain,
            deployment_binding_id=binding,
            deployment_trace_id=trace_id,
            markers=markers,
            evidence_refs=evidence,
            reason_codes=reason_codes,
            workspace_candidates=self._domain_workspaces(domain),
            warnings=reason_codes,
        )

    def _process_result(
        self,
        domain: str,
        binding: str,
        markers: tuple[CcrMarker, ...],
        pid_marker: CcrMarker,
        facts: tuple[ProcessFact, ...],
        *,
        global_conflict: bool,
        conflict_evidence_refs: tuple[str, ...],
    ) -> CcrDiscoveryResult:
        same_domain = tuple(
            item
            for item in facts
            if item.execution_domain_id == domain and item.pid == pid_marker.pid
        )
        other_domain = tuple(
            item
            for item in facts
            if item.execution_domain_id != domain and item.pid == pid_marker.pid
        )
        if not same_domain and other_domain:
            return self._bounded_process_result(
                domain,
                binding,
                markers,
                None,
                CcrDetectionLevel.UNKNOWN,
                AgentLifecycleStatus.UNKNOWN,
                0.1,
                CapabilityStatus.DEGRADED,
                ("CCR_DOMAIN_MISMATCH",),
                extra_evidence_refs=tuple(
                    ref for item in other_domain for ref in item.evidence_refs
                ),
            )
        if not same_domain:
            return self._bounded_process_result(
                domain,
                binding,
                markers,
                None,
                CcrDetectionLevel.STALE_RUNTIME_TRACE,
                AgentLifecycleStatus.DETECTED,
                0.15,
                CapabilityStatus.DEGRADED,
                ("CCR_STALE_PID",),
            )
        if len(same_domain) != 1:
            return self._bounded_process_result(
                domain,
                binding,
                markers,
                None,
                CcrDetectionLevel.UNKNOWN,
                AgentLifecycleStatus.UNKNOWN,
                0.1,
                CapabilityStatus.DEGRADED,
                ("CCR_SIGNATURE_CONFLICT",),
                extra_evidence_refs=tuple(
                    ref for item in same_domain for ref in item.evidence_refs
                ),
            )
        fact = same_domain[0]
        if fact.current_state is ProcessState.EXITED or (
            fact.access_status is CapabilityStatus.NOT_PRESENT
        ):
            return self._bounded_process_result(
                domain,
                binding,
                markers,
                fact,
                CcrDetectionLevel.STALE_RUNTIME_TRACE,
                AgentLifecycleStatus.DETECTED,
                0.15,
                CapabilityStatus.DEGRADED,
                ("CCR_STALE_PID",),
            )
        invalid = any(
            item.verification is CcrMarkerVerification.INVALID
            for item in markers
        )
        if global_conflict or invalid:
            reason = (
                "CCR_DEPLOYMENT_CONFLICT"
                if global_conflict
                else "CCR_SIGNATURE_CONFLICT"
            )
            return self._bounded_process_result(
                domain,
                binding,
                markers,
                fact,
                CcrDetectionLevel.UNKNOWN,
                AgentLifecycleStatus.UNKNOWN,
                0.1,
                CapabilityStatus.DEGRADED,
                (reason,),
                extra_evidence_refs=tuple(
                    {
                        *conflict_evidence_refs,
                        *(
                            ref
                            for item in facts
                            if item.execution_domain_id == domain
                            and item.pid
                            in {
                                marker.pid
                                for marker in markers
                                if marker.pid is not None
                            }
                            for ref in item.evidence_refs
                        ),
                    }
                ),
            )
        marker_reasons = _marker_reason_codes(markers)
        if marker_reasons:
            return self._bounded_process_result(
                domain,
                binding,
                markers,
                fact,
                CcrDetectionLevel.PROCESS_CANDIDATE,
                AgentLifecycleStatus.DETECTED,
                0.2,
                CapabilityStatus.DEGRADED,
                marker_reasons,
            )
        if _is_shared_cloudcli_process(fact):
            return self._bounded_process_result(
                domain,
                binding,
                markers,
                fact,
                CcrDetectionLevel.UNKNOWN,
                AgentLifecycleStatus.UNKNOWN,
                0.1,
                CapabilityStatus.DEGRADED,
                ("CCR_PROCESS_SHARED_UNRESOLVED",),
            )
        static_evidence = tuple(
            item
            for item in markers
            if _is_strong_static_anchor(item)
            and item.verification is CcrMarkerVerification.VERIFIED
        )
        accessible = fact.access_status in {
            CapabilityStatus.AVAILABLE,
            CapabilityStatus.DEGRADED,
        }
        if (
            not _is_controlled_marker(pid_marker)
            or not static_evidence
            or not accessible
            or fact.current_state is not ProcessState.RUNNING
        ):
            return self._bounded_process_result(
                domain,
                binding,
                markers,
                fact,
                CcrDetectionLevel.PROCESS_CANDIDATE,
                AgentLifecycleStatus.DETECTED,
                0.25,
                CapabilityStatus.DEGRADED,
                ("CCR_EVIDENCE_INSUFFICIENT",),
            )
        observed = (
            fact.fixed_facts.get(_CCR_OBSERVED_FACT) is True
            and _CCR_OBSERVED_FACT in fact.supported_fixed_fact_names
            and fact.collector in _CCR_OBSERVED_COLLECTORS
        )
        return self._bounded_process_result(
            domain,
            binding,
            markers,
            fact,
            CcrDetectionLevel.CONFIRMED_RUNNING,
            (
                AgentLifecycleStatus.OBSERVED
                if observed
                else AgentLifecycleStatus.RUNNING
            ),
            0.9,
            CapabilityStatus.AVAILABLE,
            (),
            confirmed=True,
        )

    def _bounded_process_result(
        self,
        domain: str,
        binding: str,
        markers: tuple[CcrMarker, ...],
        fact: ProcessFact | None,
        detection_level: CcrDetectionLevel,
        lifecycle: AgentLifecycleStatus,
        confidence: float,
        status: CapabilityStatus,
        reasons: tuple[str, ...],
        *,
        confirmed: bool = False,
        extra_evidence_refs: tuple[str, ...] = (),
    ) -> CcrDiscoveryResult:
        evidence = tuple(
            sorted(
                {
                    *_marker_evidence(markers),
                    *(fact.evidence_refs if fact is not None else ()),
                    *extra_evidence_refs,
                }
            )
        )
        trace_id = _trace_id(domain, binding)
        candidate_id = (
            _process_candidate_id(domain, binding, fact.process_instance_id)
            if fact is not None
            else trace_id
        )
        uncertainties: list[str] = []
        if not any(
            item.marker_kind is CcrMarkerKind.VERSION_MARKER
            and _is_controlled_marker(item)
            and item.verification is CcrMarkerVerification.VERIFIED
            for item in markers
        ):
            uncertainties.append("CCR_VERSION_REQUIRED")
        if not confirmed:
            uncertainties.append("CCR_IDENTITY_UNCONFIRMED")
        required_checks = (
            () if confirmed else ("CCR_PROCESS_INSTANCE_BINDING_REQUIRED",)
        )
        classification = AgentClassification(
            candidate_id=candidate_id,
            candidate_type=(
                AgentCandidateType.PROCESS
                if fact is not None
                else AgentCandidateType.EXECUTABLE
            ),
            role=AgentRole.MODEL_ROUTER,
            lifecycle=lifecycle,
            confidence=confidence,
            evidence_refs=evidence,
            uncertainties=tuple(uncertainties),
            required_checks=required_checks,
            process_instance_id=(
                fact.process_instance_id if fact is not None else None
            ),
        )
        reason_codes = tuple(sorted(set(reasons)))
        return CcrDiscoveryResult(
            classification=classification,
            detection_level=detection_level,
            status=status,
            confirmed=confirmed,
            execution_domain_id=domain,
            deployment_binding_id=binding,
            deployment_trace_id=trace_id,
            markers=markers,
            evidence_refs=evidence,
            reason_codes=reason_codes,
            workspace_candidates=self._process_workspaces(domain, fact),
            warnings=reason_codes,
        )

    def _domain_workspaces(
        self,
        domain: str,
    ) -> tuple[WorkspaceCandidate, ...]:
        return tuple(
            item
            for item in self._workspace_candidates
            if item.execution_domain_id == domain
        )

    def _process_workspaces(
        self,
        domain: str,
        fact: ProcessFact | None,
    ) -> tuple[WorkspaceCandidate, ...]:
        if fact is None:
            return self._domain_workspaces(domain)
        evidence = set(fact.evidence_refs)
        return tuple(
            item
            for item in self._domain_workspaces(domain)
            if evidence.intersection(item.evidence_refs)
        )

    @staticmethod
    def _has_multiple_bound_processes(
        pid_markers: tuple[CcrMarker, ...],
        facts: tuple[ProcessFact, ...],
        domain: str,
    ) -> bool:
        bound_ids = {
            fact.process_instance_id
            for marker in pid_markers
            for fact in facts
            if marker.pid == fact.pid
            and fact.execution_domain_id == domain
            and fact.current_state is ProcessState.RUNNING
        }
        return len(bound_ids) > 1


def _is_controlled_marker(marker: CcrMarker) -> bool:
    profile = _MARKER_PROFILES.get(marker.marker_id)
    return bool(
        profile is not None
        and marker.marker_kind is profile.marker_kind
        and marker.source_kind is profile.source_kind
        and marker.collector_id == profile.collector_id
        and marker._provenance is _CONTROLLED_PROVENANCE
    )


def _is_strong_static_anchor(marker: CcrMarker) -> bool:
    profile = _MARKER_PROFILES.get(marker.marker_id)
    return bool(
        profile is not None
        and profile.strong_static_anchor
        and _is_controlled_marker(marker)
    )


def _marker_reason_codes(markers: tuple[CcrMarker, ...]) -> tuple[str, ...]:
    reasons: set[str] = set()
    for marker in markers:
        profile = _MARKER_PROFILES.get(marker.marker_id)
        if profile is None:
            continue
        if (
            marker.marker_kind is not profile.marker_kind
            or marker.source_kind is not profile.source_kind
        ):
            reasons.add("CCR_MARKER_SOURCE_MISMATCH")
        elif marker.collector_id != profile.collector_id:
            reasons.add("CCR_MARKER_COLLECTOR_UNTRUSTED")
        elif marker._provenance is not _CONTROLLED_PROVENANCE:
            reasons.add("CCR_MARKER_PROVENANCE_UNVERIFIED")
    return tuple(sorted(reasons))


def _is_shared_cloudcli_process(fact: ProcessFact) -> bool:
    return bool(
        fact.fixed_facts.get(_CLOUDCLI_OBSERVED_FACT) is True
        and _CLOUDCLI_OBSERVED_FACT in fact.supported_fixed_fact_names
        and fact.collector == "cloudcli-runtime-probe"
    )


def _marker_key(marker: CcrMarker) -> tuple[str, str, str, str]:
    return (
        marker.execution_domain_id,
        marker.deployment_binding_id,
        marker.marker_kind.value,
        marker.marker_id,
    )


def _marker_evidence(markers: tuple[CcrMarker, ...]) -> tuple[str, ...]:
    return tuple(sorted({item.evidence_ref for item in markers}))


def _trace_id(domain: str, binding: str) -> str:
    material = f"ccr\x1f{domain}\x1f{binding}".encode()
    digest = hashlib.sha256(material).hexdigest()[:24]
    return f"ccr-trace-{digest}"


def _process_candidate_id(
    domain: str,
    binding: str,
    process_instance_id: str,
) -> str:
    material = f"ccr\x1f{domain}\x1f{binding}\x1f{process_instance_id}".encode()
    digest = hashlib.sha256(material).hexdigest()[:24]
    return f"ccr-process-{digest}"


__all__ = [
    "CcrAdapter",
    "CcrDetectionLevel",
    "CcrDiscoveryResult",
    "CcrMarker",
    "CcrMarkerKind",
    "CcrMarkerSource",
    "CcrMarkerVerification",
    "create_verified_ccr_marker",
    "parse_ccr_pid_marker",
]
