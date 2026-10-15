"""Pure merge policy for SelfRuntime and imported Host Probe facts."""

from __future__ import annotations

from collections.abc import Iterable

from ..capabilities import (
    CapabilityAssessment,
    CapabilityStatus,
    DomainCapabilities,
)
from ..errors import DiscoveryError, DiscoveryErrorCode
from ..models import DiscoverySnapshot, ExecutionDomainDescriptor, ExecutionDomainKind
from .models import HostFreshness, HostImportResult


def merge_host_probe_snapshot(
    local: DiscoverySnapshot,
    imported: HostImportResult,
) -> DiscoverySnapshot:
    """Merge by stable domain ID while preserving local direct evidence."""

    errors = list(local.errors)
    evidence = list(local.evidence)
    envelope = imported.envelope
    if envelope is None:
        if imported.error is not None:
            errors.append(imported.error)
        return _snapshot(
            local,
            domains=local.domains,
            evidence=evidence,
            errors=errors,
            status=_degraded(local.status),
            suffix="invalid",
        )

    evidence = _unique_evidence((*evidence, *envelope.evidence))
    errors.extend(envelope.errors)
    if imported.freshness is HostFreshness.EXPIRED:
        errors.append(_merge_error("HOST_PROBE_EXPIRED"))
        return _snapshot(
            local,
            domains=local.domains,
            evidence=evidence,
            errors=errors,
            status=_degraded(local.status),
            suffix=f"{envelope.probe_id}:expired",
            observed_at=max(local.observed_at, envelope.observed_at),
        )
    if imported.freshness is HostFreshness.INVALID:
        if imported.error is not None:
            errors.append(imported.error)
        return _snapshot(
            local,
            domains=local.domains,
            evidence=evidence,
            errors=errors,
            status=_degraded(local.status),
            suffix=f"{envelope.probe_id}:invalid",
        )

    host_domain = _attach_envelope_capabilities(
        envelope.host,
        envelope.capabilities,
    )
    forced_degraded = bool(envelope.errors)
    if imported.freshness is HostFreshness.STALE:
        host_domain = _downgrade_stale_domain(host_domain)
        errors.append(_merge_error("HOST_PROBE_STALE"))
        forced_degraded = True

    merged_domains, conflicts = _merge_domain_list(local.domains, host_domain)
    for reason_code in conflicts:
        errors.append(_merge_error(reason_code))
    if conflicts:
        forced_degraded = True
    status = _degraded(local.status) if forced_degraded else local.status
    return _snapshot(
        local,
        domains=merged_domains,
        evidence=evidence,
        errors=errors,
        status=status,
        suffix=f"{envelope.probe_id}:{imported.freshness.value.lower()}",
        observed_at=max(local.observed_at, envelope.observed_at),
    )


def _merge_domain_list(
    local_domains: tuple[ExecutionDomainDescriptor, ...],
    host_domain: ExecutionDomainDescriptor,
) -> tuple[tuple[ExecutionDomainDescriptor, ...], tuple[str, ...]]:
    merged = list(local_domains)
    for index, local_domain in enumerate(merged):
        if local_domain.domain_id == host_domain.domain_id:
            domain, conflicts = _merge_domain(local_domain, host_domain)
            merged[index] = domain
            return tuple(merged), conflicts
    merged.append(host_domain)
    return tuple(merged), ()


def _merge_domain(
    local: ExecutionDomainDescriptor,
    host: ExecutionDomainDescriptor,
) -> tuple[ExecutionDomainDescriptor, tuple[str, ...]]:
    conflicts: list[str] = []
    if local.kind is ExecutionDomainKind.UNKNOWN:
        kind = host.kind
    elif host.kind in {ExecutionDomainKind.UNKNOWN, local.kind}:
        kind = local.kind
    else:
        kind = local.kind
        conflicts.append("HOST_PROBE_DOMAIN_CONFLICT")

    capabilities, capability_conflict = _merge_capabilities(
        local.capabilities,
        host.capabilities,
    )
    if capability_conflict:
        conflicts.append("HOST_PROBE_CAPABILITY_CONFLICT")

    host_children = {item.domain_id: item for item in host.children}
    children: list[ExecutionDomainDescriptor] = []
    for local_child in local.children:
        host_child = host_children.pop(local_child.domain_id, None)
        if host_child is None:
            children.append(local_child)
            continue
        merged_child, child_conflicts = _merge_domain(local_child, host_child)
        children.append(merged_child)
        conflicts.extend(child_conflicts)
    children.extend(host_children.values())

    return (
        ExecutionDomainDescriptor(
            domain_id=local.domain_id,
            kind=kind,
            label=local.label or host.label,
            capabilities=capabilities,
            children=tuple(children),
            evidence_ids=_unique_strings((*local.evidence_ids, *host.evidence_ids)),
            confidence=(
                local.confidence
                if local.confidence is not None
                else host.confidence
            ),
        ),
        tuple(dict.fromkeys(conflicts)),
    )


def _merge_capabilities(
    local: DomainCapabilities,
    host: DomainCapabilities,
) -> tuple[DomainCapabilities, bool]:
    assessments = dict(local.assessments)
    conflict = False
    for name, host_assessment in host.assessments.items():
        local_assessment = assessments.get(name)
        if local_assessment is None:
            assessments[name] = host_assessment
            continue
        refs = _unique_strings(
            (*local_assessment.evidence_ids, *host_assessment.evidence_ids)
        )
        if local_assessment.status is host_assessment.status:
            assessments[name] = _assessment_with_refs(local_assessment, refs)
            continue
        if (
            local_assessment.status is CapabilityStatus.UNKNOWN
            and name != "self_visible"
        ):
            assessments[name] = _assessment_with_refs(host_assessment, refs)
            continue
        if host_assessment.status is CapabilityStatus.UNKNOWN:
            assessments[name] = _assessment_with_refs(local_assessment, refs)
            continue
        assessments[name] = _assessment_with_refs(local_assessment, refs)
        conflict = True
    return DomainCapabilities(assessments), conflict


def _assessment_with_refs(
    assessment: CapabilityAssessment,
    evidence_ids: tuple[str, ...],
) -> CapabilityAssessment:
    return CapabilityAssessment(
        status=assessment.status,
        reason_code=assessment.reason_code,
        evidence_ids=evidence_ids,
        confidence=assessment.confidence,
        error=assessment.error,
    )


def _attach_envelope_capabilities(
    host: ExecutionDomainDescriptor,
    capabilities: DomainCapabilities,
) -> ExecutionDomainDescriptor:
    combined = dict(host.capabilities.assessments)
    for name, assessment in capabilities.assessments.items():
        if name == "self_visible":
            continue
        combined[name] = assessment
    return ExecutionDomainDescriptor(
        domain_id=host.domain_id,
        kind=host.kind,
        label=host.label,
        capabilities=DomainCapabilities(combined),
        children=host.children,
        evidence_ids=host.evidence_ids,
        confidence=host.confidence,
    )


def _downgrade_stale_domain(
    domain: ExecutionDomainDescriptor,
) -> ExecutionDomainDescriptor:
    capabilities: dict[str, CapabilityAssessment] = {}
    for name, assessment in domain.capabilities.assessments.items():
        if assessment.status is CapabilityStatus.AVAILABLE:
            capabilities[name] = CapabilityAssessment(
                status=CapabilityStatus.UNKNOWN,
                reason_code="STALE_HOST_EVIDENCE",
                evidence_ids=assessment.evidence_ids,
                error=assessment.error,
            )
        else:
            capabilities[name] = assessment
    return ExecutionDomainDescriptor(
        domain_id=domain.domain_id,
        kind=domain.kind,
        label=domain.label,
        capabilities=DomainCapabilities(capabilities),
        children=tuple(_downgrade_stale_domain(item) for item in domain.children),
        evidence_ids=domain.evidence_ids,
        confidence=domain.confidence,
    )


def _snapshot(
    local: DiscoverySnapshot,
    *,
    domains: tuple[ExecutionDomainDescriptor, ...],
    evidence,
    errors,
    status: CapabilityStatus,
    suffix: str,
    observed_at=None,
) -> DiscoverySnapshot:
    return DiscoverySnapshot(
        schema_version=local.schema_version,
        snapshot_id=f"{local.snapshot_id}+host:{suffix}",
        observed_at=observed_at or local.observed_at,
        domains=domains,
        runtimes=local.runtimes,
        agents=local.agents,
        workspaces=local.workspaces,
        evidence=tuple(evidence),
        errors=tuple(errors),
        status=status,
    )


def _merge_error(reason_code: str) -> DiscoveryError:
    return DiscoveryError(
        code=DiscoveryErrorCode.INVALID_DATA,
        message="Host Probe facts were merged with restrictions",
        collector="host_probe_merge",
        source="HOST_PROBE",
        retryable=False,
        details={"reason_code": reason_code},
    )


def _degraded(status: CapabilityStatus) -> CapabilityStatus:
    if status is CapabilityStatus.ERROR:
        return CapabilityStatus.ERROR
    return CapabilityStatus.DEGRADED


def _unique_strings(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values))


def _unique_evidence(values):
    by_id = {}
    for item in values:
        by_id.setdefault(item.evidence_id, item)
    return list(by_id.values())


__all__ = ["merge_host_probe_snapshot"]
