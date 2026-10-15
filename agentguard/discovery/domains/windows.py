"""On-demand Windows-to-WSL discovery orchestration."""

from __future__ import annotations

import os
import platform
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from agentguard.recovery.contracts import RecoveryOperationResult, RecoveryRequest

from ..capabilities import CapabilityAssessment, CapabilityStatus, DomainCapabilities
from ..command_runner import (
    PROBE_MODULE_MISSING_EXIT,
    CommandId,
    WslCommandRunner,
    validate_distro_name,
)
from ..errors import DiscoveryError, DiscoveryErrorCode
from ..models import (
    DiscoverySnapshot,
    ExecutionDomainDescriptor,
    ExecutionDomainKind,
    ProbeEvidence,
)
from ..probes.wsl_probe import (
    NormalizedWslProbe,
    WslProbeProtocolError,
    normalize_wsl_probe,
)
from .wsl import (
    WslDistribution,
    WslDistroState,
    WslWorkspaceLocation,
    distro_domain_id,
    parse_wsl_list,
)


@dataclass(frozen=True)
class WindowsWslListResult:
    status: CapabilityStatus
    distros: tuple[WslDistribution, ...] = ()
    evidence: tuple[ProbeEvidence, ...] = ()
    errors: tuple[DiscoveryError, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class WindowsWslProbeResult:
    snapshot: DiscoverySnapshot
    distribution: WslDistribution | None = None
    workspace: WslWorkspaceLocation | None = None
    warnings: tuple[str, ...] = ()

    @property
    def status(self) -> CapabilityStatus:
        return self.snapshot.status


class WindowsAdapter:
    """List WSL distributions and probe one explicitly selected target."""

    def __init__(
        self,
        *,
        runner: WslCommandRunner | None = None,
        platform_system: Callable[[], str] | None = None,
        platform_release: Callable[[], str] | None = None,
        platform_version: Callable[[], str] | None = None,
        os_name: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._runner = runner or WslCommandRunner()
        self._platform_system = platform_system or platform.system
        self._platform_release = platform_release or platform.release
        self._platform_version = platform_version or platform.version
        self._os_name = os.name if os_name is None else os_name
        self._clock = clock or (lambda: datetime.now(UTC))

    def capabilities(self) -> DomainCapabilities:
        status = CapabilityStatus.UNKNOWN if self._is_windows() else CapabilityStatus.UNSUPPORTED
        reason = "ON_DEMAND_NOT_ASSESSED" if self._is_windows() else "WINDOWS_ONLY"
        return DomainCapabilities(
            {
                "discovery_lite": CapabilityAssessment(status=status, reason_code=reason),
                "discovery_full": CapabilityAssessment(status=status, reason_code=reason),
                "recovery_capable": CapabilityAssessment(
                    status=CapabilityStatus.UNSUPPORTED,
                    reason_code="WINDOWS_RECOVERY_OUT_OF_SCOPE_P6",
                ),
            }
        )

    def snapshot(self, request: RecoveryRequest) -> RecoveryOperationResult:
        return self._recovery_unsupported(request)

    def restore(self, request: RecoveryRequest) -> RecoveryOperationResult:
        return self._recovery_unsupported(request)

    def verify(self, request: RecoveryRequest) -> RecoveryOperationResult:
        return self._recovery_unsupported(request)

    def test_restore(self, request: RecoveryRequest) -> RecoveryOperationResult:
        return RecoveryOperationResult(
            operation=request.operation,
            status=CapabilityStatus.UNSUPPORTED,
            reason_code="WINDOWS_RECOVERY_OUT_OF_SCOPE_P7",
            execution_domain_id=request.execution_domain_id,
            checkpoint_id=request.checkpoint_id,
        )

    @staticmethod
    def _recovery_unsupported(request: RecoveryRequest) -> RecoveryOperationResult:
        return RecoveryOperationResult(
            operation=request.operation,
            status=CapabilityStatus.UNSUPPORTED,
            reason_code="WINDOWS_RECOVERY_OUT_OF_SCOPE_P6",
            execution_domain_id=request.execution_domain_id,
            checkpoint_id=request.checkpoint_id,
        )

    def list_distros(self) -> WindowsWslListResult:
        observed = self._observed_at()
        platform_evidence = self._platform_evidence(observed)
        if not self._is_windows():
            error = self._error(
                DiscoveryErrorCode.UNSUPPORTED,
                "Windows-to-WSL discovery is unsupported on this platform",
                "WINDOWS_ONLY",
            )
            return WindowsWslListResult(
                status=CapabilityStatus.UNSUPPORTED,
                evidence=(platform_evidence,),
                errors=(error,),
            )

        execution = self._runner.run(CommandId.WSL_LIST_VERBOSE)
        command_evidence = self._command_evidence(
            execution,
            observed,
            evidence_id="windows:wsl-command",
            fact_type="wsl.command_result",
        )
        if not execution.succeeded:
            status, error = self._map_list_failure(execution.status, execution.error)
            return WindowsWslListResult(
                status=status,
                evidence=(platform_evidence, command_evidence),
                errors=((error,) if error else ()),
                warnings=execution.warnings,
            )

        parsed = parse_wsl_list(execution.stdout)
        list_evidence = ProbeEvidence(
            evidence_id="windows:wsl-list",
            collector="windows_adapter",
            source=CommandId.WSL_LIST_VERBOSE.value,
            observed_at=observed,
            fact_type="wsl.distributions",
            value={
                "count": len(parsed.distros),
                "running": sum(item.state is WslDistroState.RUNNING for item in parsed.distros),
                "stopped": sum(item.state is WslDistroState.STOPPED for item in parsed.distros),
            },
            reliability="HIGH",
            confidence=0.95,
            status=CapabilityStatus.AVAILABLE,
            sanitized=True,
        )
        return WindowsWslListResult(
            status=(CapabilityStatus.DEGRADED if parsed.warnings else CapabilityStatus.AVAILABLE),
            distros=parsed.distros,
            evidence=(platform_evidence, command_evidence, list_evidence),
            warnings=parsed.warnings,
        )

    def discover(self) -> DiscoverySnapshot:
        result = self.list_distros()
        children = tuple(self._listed_domain(item) for item in result.distros)
        root = self._windows_domain(
            children=children,
            evidence_ids=tuple(item.evidence_id for item in result.evidence),
            wsl_status=result.status,
            error=result.errors[0] if result.errors else None,
        ) if self._is_windows() else None
        return self._snapshot(
            status=result.status,
            domains=((root,) if root else ()),
            evidence=result.evidence,
            errors=result.errors,
        )

    def probe_distro(
        self,
        name: str,
        *,
        allow_start: bool = False,
        allow_workspace: bool = False,
    ) -> WindowsWslProbeResult:
        selected_name = validate_distro_name(name)
        listed = self.list_distros()
        if listed.status not in {CapabilityStatus.AVAILABLE, CapabilityStatus.DEGRADED}:
            return WindowsWslProbeResult(
                snapshot=self._snapshot_from_list_failure(listed),
                warnings=listed.warnings,
            )

        selected = next((item for item in listed.distros if item.name == selected_name), None)
        if selected is None:
            error = self._error(
                DiscoveryErrorCode.NOT_PRESENT,
                "The selected WSL distribution is not present",
                "DISTRO_NOT_PRESENT",
            )
            root = self._windows_domain(
                children=(),
                evidence_ids=tuple(item.evidence_id for item in listed.evidence),
                wsl_status=CapabilityStatus.AVAILABLE,
            )
            return WindowsWslProbeResult(
                snapshot=self._snapshot(
                    status=CapabilityStatus.NOT_PRESENT,
                    domains=(root,),
                    evidence=listed.evidence,
                    errors=(error,),
                ),
                warnings=listed.warnings,
            )

        if selected.state is not WslDistroState.RUNNING and not allow_start:
            reason = (
                "STOPPED_NOT_PROBED"
                if selected.state is WslDistroState.STOPPED
                else "UNKNOWN_STATE_NOT_PROBED"
            )
            child = self._listed_domain(selected, full_reason=reason)
            root = self._windows_domain(
                children=(child,),
                evidence_ids=tuple(item.evidence_id for item in listed.evidence),
                wsl_status=CapabilityStatus.AVAILABLE,
            )
            warnings = tuple(dict.fromkeys((*listed.warnings, reason)))
            return WindowsWslProbeResult(
                snapshot=self._snapshot(
                    status=CapabilityStatus.DEGRADED,
                    domains=(root,),
                    evidence=listed.evidence,
                ),
                distribution=selected,
                warnings=warnings,
            )

        warnings = list(listed.warnings)
        if selected.state is not WslDistroState.RUNNING:
            warnings.append("PROBE_MAY_START_DISTRO")
        execution = self._runner.run(
            CommandId.WSL_PROBE_DISTRO,
            distro=selected.name,
            include_workspace=allow_workspace,
        )
        if not execution.succeeded:
            return self._probe_execution_failure(selected, listed, execution, warnings)

        try:
            normalized = normalize_wsl_probe(
                execution.stdout,
                expected_distro=selected.name,
                allow_workspace=allow_workspace,
            )
        except WslProbeProtocolError as exc:
            child = self._listed_domain(
                selected,
                full_status=exc.status,
                full_reason=str(exc.error.details.get("reason_code", "PROBE_PROTOCOL_ERROR")),
                full_error=exc.error,
            )
            root = self._windows_domain(
                children=(child,),
                evidence_ids=tuple(item.evidence_id for item in listed.evidence),
                wsl_status=CapabilityStatus.AVAILABLE,
            )
            return WindowsWslProbeResult(
                snapshot=self._snapshot(
                    status=exc.status,
                    domains=(root,),
                    evidence=listed.evidence,
                    errors=(exc.error,),
                ),
                distribution=selected,
                warnings=tuple(dict.fromkeys(warnings)),
            )

        return self._normalized_probe_result(selected, listed, normalized, warnings)

    def _normalized_probe_result(
        self,
        selected: WslDistribution,
        listed: WindowsWslListResult,
        normalized: NormalizedWslProbe,
        warnings: list[str],
    ) -> WindowsWslProbeResult:
        list_evidence_ids = tuple(item.evidence_id for item in listed.evidence)
        probe_evidence_ids = tuple(item.evidence_id for item in normalized.evidence)
        normalized_full = normalized.domain.capabilities.get("discovery_full")
        child = ExecutionDomainDescriptor(
            domain_id=normalized.domain.domain_id,
            kind=normalized.domain.kind,
            label=normalized.domain.label,
            capabilities=DomainCapabilities(
                {
                    "discovery_lite": CapabilityAssessment(
                        status=CapabilityStatus.AVAILABLE,
                        reason_code="WSL_LIST_AVAILABLE",
                        evidence_ids=("windows:wsl-list",),
                        confidence=0.95,
                    ),
                    "discovery_full": normalized_full,
                    "recovery_capable": CapabilityAssessment(
                        status=CapabilityStatus.UNKNOWN,
                        reason_code="NOT_ASSESSED_P2B",
                    ),
                }
            ),
            children=normalized.domain.children,
            evidence_ids=tuple(dict.fromkeys((*list_evidence_ids, *probe_evidence_ids))),
            confidence=normalized.domain.confidence,
        )
        root = self._windows_domain(
            children=(child,),
            evidence_ids=list_evidence_ids,
            wsl_status=CapabilityStatus.AVAILABLE,
        )
        final_warnings = tuple(dict.fromkeys((*warnings, *normalized.warnings)))
        status = normalized.status
        if listed.status is CapabilityStatus.DEGRADED:
            status = CapabilityStatus.DEGRADED if status is CapabilityStatus.AVAILABLE else status
        return WindowsWslProbeResult(
            snapshot=self._snapshot(
                status=status,
                domains=(root,),
                evidence=(*listed.evidence, *normalized.evidence),
                errors=normalized.errors,
                observed_at=normalized.observed_at,
            ),
            distribution=selected,
            workspace=normalized.workspace,
            warnings=final_warnings,
        )

    def _probe_execution_failure(self, selected, listed, execution, warnings):
        command_evidence = self._command_evidence(
            execution,
            self._observed_at(),
            evidence_id="windows:wsl-probe-command",
            fact_type="wsl.probe_command_result",
        )
        if execution.returncode in {127, PROBE_MODULE_MISSING_EXIT}:
            status = CapabilityStatus.NOT_PRESENT
            reason = "PROBE_ENVIRONMENT_NOT_PRESENT"
            error = self._error(
                DiscoveryErrorCode.NOT_PRESENT,
                "The supported WSL probe environment is not present",
                reason,
            )
        else:
            status, error = self._map_list_failure(execution.status, execution.error)
            reason = (
                "PROBE_TIMEOUT"
                if execution.error and execution.error.code is DiscoveryErrorCode.TIMEOUT
                else "PROBE_UNREACHABLE"
                if status is CapabilityStatus.UNREACHABLE
                else "PROBE_EXECUTION_FAILED"
            )
        child = self._listed_domain(
            selected,
            full_status=status,
            full_reason=reason,
            full_error=error,
            full_evidence_ids=(command_evidence.evidence_id,),
        )
        root = self._windows_domain(
            children=(child,),
            evidence_ids=(
                *tuple(item.evidence_id for item in listed.evidence),
                command_evidence.evidence_id,
            ),
            wsl_status=CapabilityStatus.AVAILABLE,
        )
        snapshot_status = CapabilityStatus.DEGRADED if status is CapabilityStatus.NOT_PRESENT else status
        return WindowsWslProbeResult(
            snapshot=self._snapshot(
                status=snapshot_status,
                domains=(root,),
                evidence=(*listed.evidence, command_evidence),
                errors=((error,) if error else ()),
            ),
            distribution=selected,
            warnings=tuple(dict.fromkeys((*warnings, *execution.warnings))),
        )

    def _snapshot_from_list_failure(self, listed: WindowsWslListResult) -> DiscoverySnapshot:
        root = self._windows_domain(
            children=(),
            evidence_ids=tuple(item.evidence_id for item in listed.evidence),
            wsl_status=listed.status,
            error=listed.errors[0] if listed.errors else None,
        ) if self._is_windows() else None
        return self._snapshot(
            status=listed.status,
            domains=((root,) if root else ()),
            evidence=listed.evidence,
            errors=listed.errors,
        )

    def _listed_domain(
        self,
        distro: WslDistribution,
        *,
        full_status: CapabilityStatus = CapabilityStatus.UNKNOWN,
        full_reason: str = "FULL_PROBE_NOT_RUN",
        full_error: DiscoveryError | None = None,
        full_evidence_ids: tuple[str, ...] = (),
    ) -> ExecutionDomainDescriptor:
        return ExecutionDomainDescriptor(
            domain_id=distro_domain_id(distro.name),
            kind=ExecutionDomainKind.WSL,
            label=f"{distro.generation} distribution {distro.name} ({distro.state.value})",
            capabilities=DomainCapabilities(
                {
                    "discovery_lite": CapabilityAssessment(
                        status=CapabilityStatus.AVAILABLE,
                        reason_code="WSL_LIST_AVAILABLE",
                        evidence_ids=("windows:wsl-list",),
                        confidence=0.95,
                    ),
                    "discovery_full": CapabilityAssessment(
                        status=full_status,
                        reason_code=full_reason,
                        evidence_ids=full_evidence_ids,
                        error=full_error,
                    ),
                    "recovery_capable": CapabilityAssessment(
                        status=CapabilityStatus.UNKNOWN,
                        reason_code="NOT_ASSESSED_P2B",
                    ),
                }
            ),
            evidence_ids=("windows:wsl-list",),
            confidence=0.95,
        )

    def _windows_domain(self, *, children, evidence_ids, wsl_status, error=None):
        return ExecutionDomainDescriptor(
            domain_id="windows-current",
            kind=ExecutionDomainKind.WINDOWS,
            label="Windows host",
            capabilities=DomainCapabilities(
                {
                    "wsl_cli": CapabilityAssessment(
                        status=wsl_status,
                        reason_code="WSL_LIST_RESULT",
                        evidence_ids=tuple(
                            item
                            for item in evidence_ids
                            if item in {"windows:wsl-command", "windows:wsl-list"}
                        ),
                        confidence=0.95 if wsl_status is CapabilityStatus.AVAILABLE else None,
                        error=error,
                    ),
                    "host_docker_daemon": CapabilityAssessment(
                        status=CapabilityStatus.UNKNOWN,
                        reason_code="NOT_ASSESSED_P2B",
                    ),
                    "security_posture": CapabilityAssessment(
                        status=CapabilityStatus.UNKNOWN,
                        reason_code="NOT_ASSESSED_P2B",
                    ),
                    "recovery_capable": CapabilityAssessment(
                        status=CapabilityStatus.UNKNOWN,
                        reason_code="NOT_ASSESSED_P2B",
                    ),
                }
            ),
            children=tuple(children),
            evidence_ids=tuple(evidence_ids),
            confidence=0.95,
        )

    def _platform_evidence(self, observed_at: datetime) -> ProbeEvidence:
        is_windows = self._is_windows()
        return ProbeEvidence(
            evidence_id="windows:platform",
            collector="windows_adapter",
            source="platform",
            observed_at=observed_at,
            fact_type="platform.current",
            value={
                "os_name": self._os_name,
                "system": self._platform_system(),
                "release": self._platform_release(),
                "version": self._platform_version(),
            },
            reliability="HIGH",
            confidence=0.95,
            status=CapabilityStatus.AVAILABLE if is_windows else CapabilityStatus.UNSUPPORTED,
            sanitized=True,
        )

    @staticmethod
    def _command_evidence(execution, observed_at, *, evidence_id, fact_type):
        return ProbeEvidence(
            evidence_id=evidence_id,
            collector="windows_adapter",
            source=execution.command_id.value,
            observed_at=observed_at,
            fact_type=fact_type,
            value={
                "command_id": execution.command_id.value,
                "returncode": execution.returncode,
                "truncated": execution.truncated,
                "warnings": list(execution.warnings),
            },
            reliability="HIGH",
            confidence=0.95,
            status=execution.status,
            error=execution.error,
            sanitized=True,
        )

    def _map_list_failure(self, status, error):
        if error and error.code is DiscoveryErrorCode.COLLECTOR_FAILURE:
            return CapabilityStatus.UNREACHABLE, self._error(
                DiscoveryErrorCode.UNREACHABLE,
                "The WSL subsystem exists but could not be reached",
                "WSL_CLI_NONZERO",
            )
        return status, error

    @staticmethod
    def _error(code: DiscoveryErrorCode, message: str, reason: str) -> DiscoveryError:
        return DiscoveryError(
            code=code,
            message=message,
            collector="windows_adapter",
            source="WSL_DISCOVERY",
            retryable=code in {DiscoveryErrorCode.TIMEOUT, DiscoveryErrorCode.UNREACHABLE},
            details={"reason_code": reason},
        )

    def _snapshot(self, *, status, domains, evidence, errors=(), observed_at=None):
        observed = observed_at or self._observed_at()
        return DiscoverySnapshot(
            snapshot_id=f"windows-wsl-{observed.isoformat()}",
            observed_at=observed,
            status=status,
            domains=tuple(domains),
            evidence=tuple(evidence),
            errors=tuple(errors),
        )

    def _observed_at(self) -> datetime:
        observed = self._clock()
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return observed.astimezone(UTC)

    def _is_windows(self) -> bool:
        return self._os_name == "nt" and self._platform_system().lower() == "windows"


__all__ = ["WindowsAdapter", "WindowsWslListResult", "WindowsWslProbeResult"]
