"""Pure, offline CloudCLI classification over normalized discovery facts."""

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
_OBSERVED_FACT = "cloudcli_runtime_observed"
_OBSERVED_COLLECTORS = frozenset({"cloudcli-runtime-probe"})
_DEFAULT_DEPLOYMENT_BINDING = "default"
_CONTROLLED_PROVENANCE = object()


class CloudCliMarkerKind(str, Enum):
    INSTALLATION_MARKER = "INSTALLATION_MARKER"
    SERVICE_MARKER = "SERVICE_MARKER"
    PID_MARKER = "PID_MARKER"
    PROCESS_MARKER = "PROCESS_MARKER"
    VERSION_MARKER = "VERSION_MARKER"
    WORKSPACE_ROOT_MARKER = "WORKSPACE_ROOT_MARKER"
    RUNTIME_MARKER = "RUNTIME_MARKER"


class CloudCliMarkerSource(str, Enum):
    FIXED_HOST_PATH = "FIXED_HOST_PATH"
    FIXED_CONTAINER_PATH = "FIXED_CONTAINER_PATH"
    FIXED_PACKAGE_METADATA = "FIXED_PACKAGE_METADATA"
    HOST_PROBE = "HOST_PROBE"


class CloudCliMarkerVerification(str, Enum):
    DECLARED = "DECLARED"
    STRUCTURED = "STRUCTURED"
    VERIFIED = "VERIFIED"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class CloudCliDetectionLevel(str, Enum):
    TRACE_ONLY = "TRACE_ONLY"
    STALE_RUNTIME_TRACE = "STALE_RUNTIME_TRACE"
    PROCESS_CANDIDATE = "PROCESS_CANDIDATE"
    CONFIRMED_RUNNING = "CONFIRMED_RUNNING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class _MarkerProfile:
    kind: CloudCliMarkerKind
    source: CloudCliMarkerSource
    collector: str
    strong_static_anchor: bool = False


_MARKER_PROFILES = {
    "cloudcli.package": _MarkerProfile(
        CloudCliMarkerKind.INSTALLATION_MARKER,
        CloudCliMarkerSource.FIXED_PACKAGE_METADATA,
        "cloudcli-package-metadata-probe",
        True,
    ),
    "cloudcli.service": _MarkerProfile(
        CloudCliMarkerKind.SERVICE_MARKER,
        CloudCliMarkerSource.FIXED_CONTAINER_PATH,
        "cloudcli-fixed-container-probe",
        True,
    ),
    "cloudcli.install-manifest": _MarkerProfile(
        CloudCliMarkerKind.INSTALLATION_MARKER,
        CloudCliMarkerSource.FIXED_CONTAINER_PATH,
        "cloudcli-install-manifest-probe",
        True,
    ),
    "cloudcli.version": _MarkerProfile(
        CloudCliMarkerKind.VERSION_MARKER,
        CloudCliMarkerSource.FIXED_PACKAGE_METADATA,
        "cloudcli-package-metadata-probe",
        True,
    ),
    "cloudcli.pid": _MarkerProfile(
        CloudCliMarkerKind.PID_MARKER,
        CloudCliMarkerSource.FIXED_CONTAINER_PATH,
        "cloudcli-fixed-container-probe",
    ),
    "cloudcli.workspace": _MarkerProfile(
        CloudCliMarkerKind.WORKSPACE_ROOT_MARKER,
        CloudCliMarkerSource.FIXED_CONTAINER_PATH,
        "cloudcli-fixed-container-probe",
    ),
    "cloudcli.windows.start-script": _MarkerProfile(
        CloudCliMarkerKind.SERVICE_MARKER,
        CloudCliMarkerSource.FIXED_HOST_PATH,
        "cloudcli-windows-script-probe",
    ),
    "cloudcli.windows.stop-script": _MarkerProfile(
        CloudCliMarkerKind.SERVICE_MARKER,
        CloudCliMarkerSource.FIXED_HOST_PATH,
        "cloudcli-windows-script-probe",
    ),
    "cloudcli.windows.status-script": _MarkerProfile(
        CloudCliMarkerKind.SERVICE_MARKER,
        CloudCliMarkerSource.FIXED_HOST_PATH,
        "cloudcli-windows-script-probe",
    ),
    "cloudcli.runtime.observed": _MarkerProfile(
        CloudCliMarkerKind.RUNTIME_MARKER,
        CloudCliMarkerSource.HOST_PROBE,
        "cloudcli-runtime-probe",
    ),
}


@dataclass(frozen=True)
class CloudCliMarker:
    marker_id: str
    kind: CloudCliMarkerKind
    present: bool
    source: CloudCliMarkerSource
    evidence_ref: str
    sanitized: bool
    verification: CloudCliMarkerVerification
    execution_domain_id: str
    collector: str = ""
    deployment_binding_id: str = _DEFAULT_DEPLOYMENT_BINDING
    pid: int | None = None
    reason_code: str | None = None
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
        if not self.evidence_ref or len(self.evidence_ref) > 256:
            raise ValueError("marker evidence_ref must be bounded")
        if not self.execution_domain_id or len(self.execution_domain_id) > 256:
            raise ValueError("marker execution domain must be bounded")
        if self.collector and re.fullmatch(
            r"[a-z0-9][a-z0-9._-]{0,127}",
            self.collector,
        ) is None:
            raise ValueError("marker collector must be a stable identifier")
        if re.fullmatch(
            r"[a-z0-9][a-z0-9._-]{0,127}",
            self.deployment_binding_id,
        ) is None:
            raise ValueError("deployment_binding_id must be sanitized and stable")
        if not self.sanitized:
            raise ValueError("CloudCLI markers must be sanitized")
        try:
            kind = (
                self.kind
                if isinstance(self.kind, CloudCliMarkerKind)
                else CloudCliMarkerKind(str(self.kind))
            )
            source = (
                self.source
                if isinstance(self.source, CloudCliMarkerSource)
                else CloudCliMarkerSource(str(self.source))
            )
            verification = (
                self.verification
                if isinstance(self.verification, CloudCliMarkerVerification)
                else CloudCliMarkerVerification(str(self.verification))
            )
        except ValueError as exc:
            raise ValueError("CloudCLI marker enum value is invalid") from exc
        if self.pid is not None and (isinstance(self.pid, bool) or self.pid <= 0):
            raise ValueError("CloudCLI marker PID must be a positive integer")
        if self.version is not None and re.fullmatch(
            r"[0-9A-Za-z.+-]{1,64}",
            self.version,
        ) is None:
            raise ValueError("CloudCLI version must be normalized and bounded")
        if self.reason_code is not None and re.fullmatch(
            r"[A-Z][A-Z0-9_]{0,127}",
            self.reason_code,
        ) is None:
            raise ValueError("CloudCLI marker reason code must be stable")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "verification", verification)

    def to_dict(self) -> dict[str, Any]:
        return {
            "marker_id": self.marker_id,
            "kind": self.kind.value,
            "present": self.present,
            "source": self.source.value,
            "evidence_ref": self.evidence_ref,
            "sanitized": self.sanitized,
            "verification": self.verification.value,
            "execution_domain_id": self.execution_domain_id,
            "collector": self.collector,
            "deployment_binding_id": self.deployment_binding_id,
            "pid": self.pid,
            "reason_code": self.reason_code,
            "version": self.version,
        }


@dataclass(frozen=True)
class CloudCliDiscoveryResult:
    classification: AgentClassification
    detection_level: CloudCliDetectionLevel
    status: CapabilityStatus
    confirmed: bool
    execution_domain_id: str
    deployment_binding_id: str
    markers: tuple[CloudCliMarker, ...]
    evidence_refs: tuple[str, ...]
    single_instance_per_domain: bool = True
    reason_codes: tuple[str, ...] = ()
    workspace_candidates: tuple[WorkspaceCandidate, ...] = ()
    sanitized: bool = True
    warnings: tuple[str, ...] = ()
    adapter_id: str = "cloudcli"

    def __post_init__(self) -> None:
        if not self.sanitized:
            raise ValueError("CloudCLI results must be sanitized")

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "classification": self.classification.to_dict(),
            "detection_level": self.detection_level.value,
            "status": self.status.value,
            "confirmed": self.confirmed,
            "execution_domain_id": self.execution_domain_id,
            "deployment_binding_id": self.deployment_binding_id,
            "single_instance_per_domain": self.single_instance_per_domain,
            "markers": [item.to_dict() for item in self.markers],
            "evidence_refs": list(self.evidence_refs),
            "reason_codes": list(self.reason_codes),
            "workspace_candidates": [
                item.to_dict() for item in self.workspace_candidates
            ],
            "sanitized": self.sanitized,
            "warnings": list(self.warnings),
        }


def parse_cloudcli_pid_marker(
    *,
    raw: bytes,
    execution_domain_id: str,
    evidence_ref: str,
    deployment_binding_id: str = _DEFAULT_DEPLOYMENT_BINDING,
    is_symlink: bool = False,
) -> CloudCliMarker:
    """Parse already-bounded bytes from the one fixed CloudCLI PID marker."""

    if is_symlink:
        return _invalid_pid_marker(
            execution_domain_id,
            evidence_ref,
            "CLOUDCLI_PID_SYMLINK_UNSUPPORTED",
            deployment_binding_id,
        )
    if len(raw) > _PID_MAX_BYTES:
        return _invalid_pid_marker(
            execution_domain_id,
            evidence_ref,
            "CLOUDCLI_PID_OUTPUT_TOO_LARGE",
            deployment_binding_id,
        )
    if re.fullmatch(rb"[1-9][0-9]*(?:\n)?", raw) is None:
        return _invalid_pid_marker(
            execution_domain_id,
            evidence_ref,
            "CLOUDCLI_PID_INVALID",
            deployment_binding_id,
        )
    return _controlled_marker(
        marker_id="cloudcli.pid",
        present=True,
        evidence_ref=evidence_ref,
        verification=CloudCliMarkerVerification.STRUCTURED,
        execution_domain_id=execution_domain_id,
        deployment_binding_id=deployment_binding_id,
        pid=int(raw.rstrip(b"\n")),
    )


def _invalid_pid_marker(
    execution_domain_id: str,
    evidence_ref: str,
    reason_code: str,
    deployment_binding_id: str,
) -> CloudCliMarker:
    return _controlled_marker(
        marker_id="cloudcli.pid",
        present=True,
        evidence_ref=evidence_ref,
        verification=CloudCliMarkerVerification.INVALID,
        execution_domain_id=execution_domain_id,
        deployment_binding_id=deployment_binding_id,
        reason_code=reason_code,
    )


def create_verified_cloudcli_marker(
    *,
    marker_id: str,
    execution_domain_id: str,
    evidence_ref: str,
    present: bool = True,
    deployment_binding_id: str = _DEFAULT_DEPLOYMENT_BINDING,
    version: str | None = None,
) -> CloudCliMarker:
    """Create one marker from the fixed, internal CloudCLI collector table."""

    if marker_id not in _MARKER_PROFILES or marker_id == "cloudcli.pid":
        raise ValueError("marker_id is not available through this fixed collector")
    return _controlled_marker(
        marker_id=marker_id,
        present=present,
        evidence_ref=evidence_ref,
        verification=CloudCliMarkerVerification.VERIFIED,
        execution_domain_id=execution_domain_id,
        deployment_binding_id=deployment_binding_id,
        version=version,
    )


def _controlled_marker(
    *,
    marker_id: str,
    present: bool,
    evidence_ref: str,
    verification: CloudCliMarkerVerification,
    execution_domain_id: str,
    deployment_binding_id: str,
    pid: int | None = None,
    reason_code: str | None = None,
    version: str | None = None,
) -> CloudCliMarker:
    profile = _MARKER_PROFILES[marker_id]
    marker = CloudCliMarker(
        marker_id=marker_id,
        kind=profile.kind,
        present=present,
        source=profile.source,
        collector=profile.collector,
        evidence_ref=evidence_ref,
        sanitized=True,
        verification=verification,
        execution_domain_id=execution_domain_id,
        deployment_binding_id=deployment_binding_id,
        pid=pid,
        reason_code=reason_code,
        version=version,
    )
    object.__setattr__(marker, "_provenance", _CONTROLLED_PROVENANCE)
    return marker


class CloudCliAdapter:
    """Classify CloudCLI without performing process, file, or network I/O."""

    adapter_id = "cloudcli"
    role = AgentRole.AGENT_HOST

    def __init__(
        self,
        *,
        markers: tuple[CloudCliMarker, ...] = (),
        workspace_candidates: tuple[WorkspaceCandidate, ...] = (),
        relationships: tuple[ProcessRelationship, ...] = (),
    ) -> None:
        if any(not isinstance(item, CloudCliMarker) for item in markers):
            raise TypeError("CloudCLI Adapter requires CloudCliMarker inputs")
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
    ) -> tuple[CloudCliDiscoveryResult, ...]:
        if any(not isinstance(item, ProcessFact) for item in facts):
            raise TypeError("CloudCLI Adapter requires ProcessFact inputs")
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
        results: list[CloudCliDiscoveryResult] = []
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
            binding_conflict = len(bindings) > 1
            for binding in bindings:
                bound_markers = tuple(
                    item
                    for item in domain_markers
                    if item.deployment_binding_id == binding
                )
                pid_markers = tuple(
                    item
                    for item in bound_markers
                    if item.kind is CloudCliMarkerKind.PID_MARKER
                )
                if not pid_markers:
                    results.append(
                        self._trace_result(
                            domain,
                            binding,
                            bound_markers,
                            binding_conflict=binding_conflict,
                        )
                    )
                    continue
                global_conflict = binding_conflict or (
                    self._has_multiple_bound_processes(
                        pid_markers,
                        facts,
                        domain,
                    )
                )
                for pid_marker in pid_markers:
                    results.append(
                        self._process_result(
                            domain,
                            binding,
                            bound_markers,
                            pid_marker,
                            facts,
                            global_conflict=global_conflict,
                        )
                    )
        return tuple(
            sorted(
                results,
                key=lambda item: item.classification.candidate_id,
            )
        )

    def _trace_result(
        self,
        domain: str,
        binding: str,
        markers: tuple[CloudCliMarker, ...],
        *,
        binding_conflict: bool,
    ) -> CloudCliDiscoveryResult:
        workspace_only = all(
            item.kind is CloudCliMarkerKind.WORKSPACE_ROOT_MARKER
            for item in markers
        )
        marker_reasons = _marker_reason_codes(markers)
        invalid = any(
            item.verification is CloudCliMarkerVerification.INVALID
            for item in markers
        )
        reasons = set(marker_reasons)
        if invalid or binding_conflict:
            reasons.add("CLOUDCLI_SIGNATURE_CONFLICT")
        if not reasons:
            reasons.add("CLOUDCLI_EVIDENCE_INSUFFICIENT")
        reason_codes = tuple(sorted(reasons))
        evidence = _marker_evidence(markers)
        candidate_id = _candidate_id("trace", domain, binding)
        classification = AgentClassification(
            candidate_id=candidate_id,
            candidate_type=AgentCandidateType.EXECUTABLE,
            role=AgentRole.AGENT_HOST,
            lifecycle=AgentLifecycleStatus.DETECTED,
            confidence=0.3 if workspace_only else 0.45,
            evidence_refs=evidence,
            uncertainties=(
                "CLOUDCLI_RUNNING_PROCESS_UNCONFIRMED",
                "CLOUDCLI_VERSION_REQUIRED",
            ),
            required_checks=("CLOUDCLI_PROCESS_BINDING_REQUIRED",),
            process_instance_id=None,
        )
        return CloudCliDiscoveryResult(
            classification=classification,
            detection_level=CloudCliDetectionLevel.TRACE_ONLY,
            status=CapabilityStatus.DEGRADED,
            confirmed=False,
            execution_domain_id=domain,
            deployment_binding_id=binding,
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
        markers: tuple[CloudCliMarker, ...],
        pid_marker: CloudCliMarker,
        facts: tuple[ProcessFact, ...],
        *,
        global_conflict: bool,
    ) -> CloudCliDiscoveryResult:
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
                CloudCliDetectionLevel.UNKNOWN,
                AgentLifecycleStatus.UNKNOWN,
                0.1,
                CapabilityStatus.DEGRADED,
                ("CLOUDCLI_DOMAIN_MISMATCH",),
            )
        if not same_domain:
            return self._bounded_process_result(
                domain,
                binding,
                markers,
                None,
                CloudCliDetectionLevel.STALE_RUNTIME_TRACE,
                AgentLifecycleStatus.DETECTED,
                0.15,
                CapabilityStatus.DEGRADED,
                ("CLOUDCLI_STALE_PID",),
            )
        if len(same_domain) != 1:
            return self._bounded_process_result(
                domain,
                binding,
                markers,
                None,
                CloudCliDetectionLevel.UNKNOWN,
                AgentLifecycleStatus.UNKNOWN,
                0.1,
                CapabilityStatus.DEGRADED,
                ("CLOUDCLI_SIGNATURE_CONFLICT",),
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
                CloudCliDetectionLevel.STALE_RUNTIME_TRACE,
                AgentLifecycleStatus.DETECTED,
                0.15,
                CapabilityStatus.DEGRADED,
                ("CLOUDCLI_STALE_PID",),
            )
        invalid = any(
            item.verification is CloudCliMarkerVerification.INVALID
            for item in markers
        )
        marker_reasons = _marker_reason_codes(markers)
        if global_conflict or invalid:
            return self._bounded_process_result(
                domain,
                binding,
                markers,
                fact,
                CloudCliDetectionLevel.UNKNOWN,
                AgentLifecycleStatus.UNKNOWN,
                0.1,
                CapabilityStatus.DEGRADED,
                ("CLOUDCLI_SIGNATURE_CONFLICT",),
            )
        if marker_reasons:
            return self._bounded_process_result(
                domain,
                binding,
                markers,
                fact,
                CloudCliDetectionLevel.PROCESS_CANDIDATE,
                AgentLifecycleStatus.DETECTED,
                0.2,
                CapabilityStatus.DEGRADED,
                marker_reasons,
            )
        static_evidence = tuple(
            item
            for item in markers
            if _is_strong_static_anchor(item)
            and item.verification is CloudCliMarkerVerification.VERIFIED
        )
        bound = _is_controlled_marker(pid_marker)
        accessible = fact.access_status in {
            CapabilityStatus.AVAILABLE,
            CapabilityStatus.DEGRADED,
        }
        if (
            not bound
            or not static_evidence
            or not accessible
            or fact.current_state is not ProcessState.RUNNING
        ):
            return self._bounded_process_result(
                domain,
                binding,
                markers,
                fact,
                CloudCliDetectionLevel.PROCESS_CANDIDATE,
                AgentLifecycleStatus.DETECTED,
                0.25,
                CapabilityStatus.DEGRADED,
                ("CLOUDCLI_EVIDENCE_INSUFFICIENT",),
            )
        observed = (
            fact.fixed_facts.get(_OBSERVED_FACT) is True
            and _OBSERVED_FACT in fact.supported_fixed_fact_names
            and fact.collector in _OBSERVED_COLLECTORS
        )
        return self._bounded_process_result(
            domain,
            binding,
            markers,
            fact,
            CloudCliDetectionLevel.CONFIRMED_RUNNING,
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
        markers: tuple[CloudCliMarker, ...],
        fact: ProcessFact | None,
        detection_level: CloudCliDetectionLevel,
        lifecycle: AgentLifecycleStatus,
        confidence: float,
        status: CapabilityStatus,
        reasons: tuple[str, ...],
        *,
        confirmed: bool = False,
    ) -> CloudCliDiscoveryResult:
        evidence = tuple(
            sorted(
                {
                    *_marker_evidence(markers),
                    *(fact.evidence_refs if fact is not None else ()),
                }
            )
        )
        candidate_id = _candidate_id(
            "process" if fact is not None else "trace",
            domain,
            binding,
        )
        uncertainties: list[str] = []
        if not any(
            item.kind is CloudCliMarkerKind.VERSION_MARKER
            and _is_controlled_marker(item)
            and item.verification is CloudCliMarkerVerification.VERIFIED
            for item in markers
        ):
            uncertainties.append("CLOUDCLI_VERSION_REQUIRED")
        if not confirmed:
            uncertainties.append("CLOUDCLI_IDENTITY_UNCONFIRMED")
        required_checks = (
            ()
            if confirmed
            else ("CLOUDCLI_PROCESS_INSTANCE_BINDING_REQUIRED",)
        )
        classification = AgentClassification(
            candidate_id=candidate_id,
            candidate_type=(
                AgentCandidateType.PROCESS
                if fact is not None
                else AgentCandidateType.EXECUTABLE
            ),
            role=AgentRole.AGENT_HOST,
            lifecycle=lifecycle,
            confidence=confidence,
            evidence_refs=evidence,
            uncertainties=tuple(uncertainties),
            required_checks=required_checks,
            process_instance_id=(
                fact.process_instance_id if fact is not None else None
            ),
        )
        return CloudCliDiscoveryResult(
            classification=classification,
            detection_level=detection_level,
            status=status,
            confirmed=confirmed,
            execution_domain_id=domain,
            deployment_binding_id=binding,
            markers=markers,
            evidence_refs=evidence,
            reason_codes=tuple(sorted(set(reasons))),
            workspace_candidates=self._process_workspaces(domain, fact),
            warnings=tuple(sorted(set(reasons))),
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
        pid_markers: tuple[CloudCliMarker, ...],
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


def _is_controlled_marker(marker: CloudCliMarker) -> bool:
    profile = _MARKER_PROFILES.get(marker.marker_id)
    return bool(
        profile is not None
        and marker.kind is profile.kind
        and marker.source is profile.source
        and marker.collector == profile.collector
        and getattr(marker, "_provenance", None) is _CONTROLLED_PROVENANCE
    )


def _is_strong_static_anchor(marker: CloudCliMarker) -> bool:
    profile = _MARKER_PROFILES.get(marker.marker_id)
    return bool(
        profile is not None
        and profile.strong_static_anchor
        and _is_controlled_marker(marker)
    )


def _marker_reason_codes(
    markers: tuple[CloudCliMarker, ...],
) -> tuple[str, ...]:
    reasons: set[str] = set()
    for marker in markers:
        profile = _MARKER_PROFILES.get(marker.marker_id)
        if profile is None:
            continue
        if marker.kind is not profile.kind or marker.source is not profile.source:
            reasons.add("CLOUDCLI_MARKER_SOURCE_MISMATCH")
        elif (
            marker.collector != profile.collector
            or getattr(marker, "_provenance", None) is not _CONTROLLED_PROVENANCE
        ):
            reasons.add("CLOUDCLI_MARKER_PROVENANCE_UNVERIFIED")
    return tuple(sorted(reasons))


def _marker_key(marker: CloudCliMarker) -> tuple[str, str, str, str]:
    return (
        marker.execution_domain_id,
        marker.deployment_binding_id,
        marker.kind.value,
        marker.marker_id,
    )


def _marker_evidence(markers: tuple[CloudCliMarker, ...]) -> tuple[str, ...]:
    return tuple(sorted({item.evidence_ref for item in markers}))


def _candidate_id(kind: str, domain: str, identity: str) -> str:
    del kind
    material = f"cloudcli\x1f{domain}\x1f{identity}".encode()
    digest = hashlib.sha256(material).hexdigest()[:24]
    return f"cloudcli-trace-{digest}"


__all__ = [
    "CloudCliAdapter",
    "CloudCliDetectionLevel",
    "CloudCliDiscoveryResult",
    "CloudCliMarker",
    "CloudCliMarkerKind",
    "CloudCliMarkerSource",
    "CloudCliMarkerVerification",
    "create_verified_cloudcli_marker",
    "parse_cloudcli_pid_marker",
]
