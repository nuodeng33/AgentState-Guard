"""Strict JSON protocol for a one-shot, read-only WSL probe."""

from __future__ import annotations

import json
import os
import platform
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ..capabilities import CapabilityAssessment, CapabilityStatus, DomainCapabilities
from ..command_runner import validate_distro_name
from ..domains.wsl import WslWorkspaceLocation, distro_domain_id, workspace_location
from ..errors import (
    DiscoveryError,
    DiscoveryErrorCode,
    redact_text,
    sanitize_json_value,
)
from ..models import ExecutionDomainDescriptor, ExecutionDomainKind, ProbeEvidence

PROBE_SCHEMA_VERSION = "1.0"
PROBE_VERSION = "1.0"
_MAX_PROTOCOL_CHARS = 64 * 1024
_WARNING_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_URL_RE = re.compile(r"(?:https?|wss?)://", re.IGNORECASE)
_FORBIDDEN_KEYS = (
    "api_key",
    "apikey",
    "authorization",
    "command_line",
    "commandline",
    "environment",
    "password",
    "private_key",
    "remote_url",
    "secret",
    "token",
)


class _ProbeSource(Protocol):
    def kernel_system(self) -> str: ...

    def kernel_release(self) -> str: ...

    def kernel_version(self) -> str: ...

    def uid(self) -> int: ...

    def gid(self) -> int: ...

    def cwd(self) -> str: ...

    def path_exists(self, path: str) -> bool: ...

    def read_bytes(self, path: str) -> bytes: ...


class _SystemProbeSource:
    def kernel_system(self) -> str:
        return platform.system()

    def kernel_release(self) -> str:
        return platform.release()

    def kernel_version(self) -> str:
        return platform.version()

    def uid(self) -> int:
        return os.getuid()

    def gid(self) -> int:
        return os.getgid()

    def cwd(self) -> str:
        return os.getcwd()

    def path_exists(self, path: str) -> bool:
        return Path(path).exists()

    def read_bytes(self, path: str) -> bytes:
        return Path(path).read_bytes()


class WslProbeProtocolError(ValueError):
    def __init__(self, status: CapabilityStatus, error: DiscoveryError) -> None:
        super().__init__(error.message)
        self.status = status
        self.error = error


@dataclass(frozen=True)
class NormalizedWslProbe:
    schema_version: str
    probe_version: str
    distro: str
    observed_at: datetime
    status: CapabilityStatus
    domain: ExecutionDomainDescriptor
    kernel: Mapping[str, str]
    identity: Mapping[str, int]
    evidence: tuple[ProbeEvidence, ...]
    errors: tuple[DiscoveryError, ...]
    sanitized: bool
    warnings: tuple[str, ...] = ()
    workspace: WslWorkspaceLocation | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "probe_version": self.probe_version,
            "distro": self.distro,
            "observed_at": self.observed_at.astimezone(UTC).isoformat(),
            "status": self.status.value,
            "domain": self.domain.to_dict(),
            "kernel": dict(self.kernel),
            "identity": dict(self.identity),
            "evidence": [item.to_dict() for item in self.evidence],
            "errors": [item.to_dict() for item in self.errors],
            "sanitized": self.sanitized,
            "warnings": list(self.warnings),
            "workspace": (
                {
                    "domain": "WSL",
                    "distro": self.workspace.distro,
                    "native_path": self.workspace.native_path,
                    "windows_display_path": self.workspace.windows_display_path,
                    "windows_display_only": self.workspace.windows_display_only,
                }
                if self.workspace
                else None
            ),
        }


def _protocol_error(
    status: CapabilityStatus,
    code: DiscoveryErrorCode,
    reason_code: str,
    message: str,
) -> WslProbeProtocolError:
    return WslProbeProtocolError(
        status,
        DiscoveryError(
            code=code,
            message=message,
            collector="wsl_probe_protocol",
            source="WSL_PROBE_DISTRO",
            retryable=False,
            details={"reason_code": reason_code},
        ),
    )


def _untrusted_warnings(value: Any) -> set[str]:
    warnings: set[str] = set()
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(raw_key).lower()).strip("_")
            if any(marker in normalized for marker in _FORBIDDEN_KEYS):
                warnings.add("SENSITIVE_FIELD_REJECTED")
            warnings.update(_untrusted_warnings(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            warnings.update(_untrusted_warnings(item))
    elif isinstance(value, str) and _URL_RE.search(value):
        warnings.add("REMOTE_URL_REJECTED")
    return warnings


def _safe_text(value: object, warnings: set[str]) -> str:
    if not isinstance(value, str):
        raise TypeError("protocol text field has an invalid type")
    lowered = value.lower()
    if any(marker in lowered for marker in _FORBIDDEN_KEYS):
        warnings.add("SENSITIVE_FIELD_REJECTED")
        return "[REDACTED]"
    if _URL_RE.search(value):
        warnings.add("REMOTE_URL_REJECTED")
        return "[REDACTED]"
    return redact_text(value)


def _normalize_untrusted_error(value: Mapping[str, Any]) -> DiscoveryError:
    """Keep machine semantics while discarding untrusted diagnostic prose."""

    try:
        code = DiscoveryErrorCode(str(value.get("code", "UNKNOWN")))
    except ValueError:
        code = DiscoveryErrorCode.UNKNOWN
    return DiscoveryError(
        code=code,
        message="The WSL probe reported a structured discovery failure",
        collector="wsl_probe",
        source="fixed_probe",
        retryable=value.get("retryable") is True,
        details={"reported_code": code.value},
    )


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("observed_at is invalid")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observed_at is invalid")
    return parsed.astimezone(UTC)


def _capability_status(value: object) -> CapabilityStatus:
    try:
        return CapabilityStatus(str(value))
    except ValueError:
        return CapabilityStatus.UNKNOWN


def _container_domain(
    data: Mapping[str, Any],
    evidence_ids: tuple[str, ...],
) -> ExecutionDomainDescriptor | None:
    container_data = data.get("container")
    if not isinstance(container_data, Mapping) or not bool(container_data.get("detected")):
        return None
    runtime = str(container_data.get("runtime", "UNKNOWN")).upper()
    label = (
        "Docker container"
        if runtime == "DOCKER"
        else "containerd container"
        if runtime == "CONTAINERD"
        else "Podman container"
        if runtime == "PODMAN"
        else "Container (runtime unknown)"
    )
    requested_ids = container_data.get("evidence_ids", ())
    container_ids = tuple(
        item for item in requested_ids if isinstance(item, str) and item in evidence_ids
    )
    if not container_ids:
        container_ids = evidence_ids
    confidence = 0.9 if container_ids else 0.8
    return ExecutionDomainDescriptor(
        domain_id="wsl-current-container",
        kind=ExecutionDomainKind.CONTAINER,
        label=label,
        capabilities=DomainCapabilities(
            {
                "self_visible": CapabilityAssessment(
                    status=CapabilityStatus.AVAILABLE,
                    reason_code="WSL_PROBE_CONTAINER_FACTS",
                    evidence_ids=container_ids,
                    confidence=confidence,
                ),
                "host_docker_daemon": CapabilityAssessment(
                    status=CapabilityStatus.UNKNOWN,
                    reason_code="NOT_PROBED_OFFLINE",
                    evidence_ids=container_ids,
                ),
            }
        ),
        evidence_ids=container_ids,
        confidence=confidence,
    )


def normalize_wsl_probe(
    payload_text: str,
    *,
    expected_distro: str,
    allow_workspace: bool = False,
) -> NormalizedWslProbe:
    """Validate and normalize untrusted WSL stdout without executing its contents."""

    validated_distro = validate_distro_name(expected_distro)
    if not isinstance(payload_text, str) or len(payload_text) > _MAX_PROTOCOL_CHARS:
        raise _protocol_error(
            CapabilityStatus.ERROR,
            DiscoveryErrorCode.INVALID_DATA,
            "PROTOCOL_SIZE_INVALID",
            "The WSL probe payload size was invalid",
        )
    try:
        raw = json.loads(payload_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise _protocol_error(
            CapabilityStatus.ERROR,
            DiscoveryErrorCode.INVALID_DATA,
            "MALFORMED_JSON",
            "The WSL probe returned malformed JSON",
        ) from exc
    if not isinstance(raw, Mapping):
        raise _protocol_error(
            CapabilityStatus.ERROR,
            DiscoveryErrorCode.INVALID_DATA,
            "INVALID_ROOT_TYPE",
            "The WSL probe payload root was invalid",
        )
    if raw.get("schema_version") != PROBE_SCHEMA_VERSION:
        raise _protocol_error(
            CapabilityStatus.UNSUPPORTED,
            DiscoveryErrorCode.UNSUPPORTED,
            "SCHEMA_MISMATCH",
            "The WSL probe schema version is unsupported",
        )

    warnings = _untrusted_warnings(raw)
    try:
        probe_version = _safe_text(raw.get("probe_version"), warnings)
        distro = validate_distro_name(raw.get("distro"))
        if distro != validated_distro:
            raise ValueError("distribution identity mismatch")
        observed_at = _parse_time(raw.get("observed_at"))
        execution_data = raw.get("execution_domain")
        kernel_data = raw.get("kernel")
        identity_data = raw.get("identity")
        capability_data = raw.get("capabilities")
        evidence_data = raw.get("evidence")
        errors_data = raw.get("errors")
        if not all(
            isinstance(item, Mapping)
            for item in (execution_data, kernel_data, identity_data, capability_data)
        ):
            raise TypeError("protocol mapping field has an invalid type")
        if not isinstance(evidence_data, list) or not isinstance(errors_data, list):
            raise TypeError("protocol sequence field has an invalid type")
    except (TypeError, ValueError) as exc:
        raise _protocol_error(
            CapabilityStatus.ERROR,
            DiscoveryErrorCode.INVALID_DATA,
            "INVALID_FIELD_TYPE",
            "The WSL probe payload contained an invalid field",
        ) from exc

    kernel = {
        key: _safe_text(kernel_data.get(key, ""), warnings)
        for key in ("system", "release", "version")
    }
    try:
        uid = identity_data.get("uid")
        gid = identity_data.get("gid")
        if not isinstance(uid, int) or isinstance(uid, bool) or uid < 0:
            raise ValueError("uid is invalid")
        if not isinstance(gid, int) or isinstance(gid, bool) or gid < 0:
            raise ValueError("gid is invalid")
        identity = {"uid": uid, "gid": gid}
    except ValueError as exc:
        raise _protocol_error(
            CapabilityStatus.ERROR,
            DiscoveryErrorCode.INVALID_DATA,
            "INVALID_IDENTITY",
            "The WSL probe identity summary was invalid",
        ) from exc

    evidence: list[ProbeEvidence] = []
    for item in evidence_data:
        if not isinstance(item, Mapping):
            warnings.add("INVALID_EVIDENCE_REJECTED")
            continue
        try:
            evidence.append(ProbeEvidence.from_dict(item))
        except (TypeError, ValueError):
            warnings.add("INVALID_EVIDENCE_REJECTED")
    errors = tuple(
        _normalize_untrusted_error(item) for item in errors_data if isinstance(item, Mapping)
    )
    evidence_ids = tuple(item.evidence_id for item in evidence)

    kind_value = str(execution_data.get("kind", "UNKNOWN"))
    try:
        kind = ExecutionDomainKind(kind_value)
    except ValueError:
        kind = ExecutionDomainKind.UNKNOWN
    generation = str(execution_data.get("generation", "UNKNOWN")).upper()
    if generation not in {"WSL1", "WSL2"}:
        generation = "UNKNOWN"

    lite_status = _capability_status(capability_data.get("DISCOVERY_LITE", "UNKNOWN"))
    requested_full = _capability_status(capability_data.get("DISCOVERY_FULL", "UNKNOWN"))
    full_status = (
        requested_full
        if kind is ExecutionDomainKind.WSL
        else CapabilityStatus.UNKNOWN
    )
    confidence = 0.9 if evidence_ids and kind is ExecutionDomainKind.WSL else 0.75
    container = _container_domain(execution_data, evidence_ids)
    capabilities = DomainCapabilities(
        {
            "discovery_lite": CapabilityAssessment(
                status=lite_status,
                reason_code="WSL_LIST_AVAILABLE",
                evidence_ids=evidence_ids,
                confidence=confidence if lite_status is CapabilityStatus.AVAILABLE else None,
            ),
            "discovery_full": CapabilityAssessment(
                status=full_status,
                reason_code=(
                    "ONE_SHOT_PROBE_AVAILABLE"
                    if full_status is CapabilityStatus.AVAILABLE
                    else "PROBE_RESULT_UNKNOWN"
                ),
                evidence_ids=evidence_ids,
                confidence=confidence if full_status is CapabilityStatus.AVAILABLE else None,
            ),
            "recovery_capable": CapabilityAssessment(
                status=CapabilityStatus.UNKNOWN,
                reason_code="NOT_ASSESSED_P2B",
            ),
        }
    )
    domain = ExecutionDomainDescriptor(
        domain_id=distro_domain_id(distro),
        kind=kind,
        label=f"{generation} distribution {distro}",
        capabilities=capabilities,
        children=((container,) if container else ()),
        evidence_ids=evidence_ids,
        confidence=confidence,
    )

    workspace = None
    if raw.get("cwd") is not None:
        if not allow_workspace:
            warnings.add("UNREQUESTED_CWD_REJECTED")
        else:
            try:
                workspace = workspace_location(distro, raw.get("cwd"))
            except ValueError:
                warnings.add("INVALID_CWD_REJECTED")

    for item in raw.get("warnings", ()) if isinstance(raw.get("warnings", ()), list) else ():
        if isinstance(item, str) and _WARNING_RE.fullmatch(item):
            warnings.add(item)
        else:
            warnings.add("INVALID_WARNING_REJECTED")
    sanitized = raw.get("sanitized") is True
    if not sanitized:
        warnings.add("PROBE_NOT_SANITIZED")

    unknown_capability = any(
        status is CapabilityStatus.UNKNOWN
        for status in (lite_status, full_status)
    )
    status = (
        CapabilityStatus.DEGRADED
        if errors
        or kind is ExecutionDomainKind.UNKNOWN
        or unknown_capability
        or not sanitized
        or "INVALID_EVIDENCE_REJECTED" in warnings
        else CapabilityStatus.AVAILABLE
    )
    return NormalizedWslProbe(
        schema_version=PROBE_SCHEMA_VERSION,
        probe_version=probe_version,
        distro=distro,
        observed_at=observed_at,
        status=status,
        domain=domain,
        kernel=sanitize_json_value(kernel),
        identity=identity,
        evidence=tuple(evidence),
        errors=errors,
        sanitized=sanitized,
        warnings=tuple(sorted(warnings)),
        workspace=workspace,
    )


def _probe_evidence(
    evidence_id: str,
    observed_at: str,
    fact_type: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "collector": "wsl_probe",
        "source": "fixed_probe",
        "observed_at": observed_at,
        "fact_type": fact_type,
        "value": sanitize_json_value(value),
        "reliability": "HIGH",
        "confidence": 0.9,
        "status": "AVAILABLE",
        "sanitized": True,
    }


def build_probe_document(
    distro: str,
    *,
    include_workspace: bool = False,
    source: _ProbeSource | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Collect fixed local facts for one process and return JSON-compatible data."""

    validated_distro = validate_distro_name(distro)
    probe_source = source or _SystemProbeSource()
    observed = (clock or (lambda: datetime.now(UTC)))().astimezone(UTC)
    observed_text = observed.isoformat()
    warnings: set[str] = set()
    system = _safe_text(probe_source.kernel_system(), warnings)
    release = _safe_text(probe_source.kernel_release(), warnings)
    version = _safe_text(probe_source.kernel_version(), warnings)
    lowered_release = release.lower()
    generation = (
        "WSL2"
        if "wsl2" in lowered_release or "microsoft-standard" in lowered_release
        else "WSL1"
        if "microsoft" in lowered_release and re.search(r"(?:^|\s)4\.4\.", lowered_release)
        else "UNKNOWN"
    )
    identity = {"uid": int(probe_source.uid()), "gid": int(probe_source.gid())}
    evidence = [
        _probe_evidence(
            "wsl-probe:kernel",
            observed_text,
            "wsl.kernel",
            {"system": system, "release": release},
        ),
        _probe_evidence(
            "wsl-probe:identity",
            observed_text,
            "wsl.identity",
            identity,
        ),
    ]
    errors: list[dict[str, Any]] = []
    container_runtime = "UNKNOWN"
    container_detected = False
    try:
        if probe_source.path_exists("/run/.containerenv"):
            container_detected = True
            container_runtime = "PODMAN"
        elif probe_source.path_exists("/.dockerenv"):
            container_detected = True
            container_runtime = "DOCKER"
        cgroup = probe_source.read_bytes("/proc/self/cgroup").decode("utf-8", errors="strict").lower()
        if "libpod" in cgroup or "podman" in cgroup:
            container_detected = True
            container_runtime = "PODMAN"
        elif "docker" in cgroup:
            container_detected = True
            container_runtime = "DOCKER"
        elif "containerd" in cgroup or "kubepods" in cgroup:
            container_detected = True
            container_runtime = "CONTAINERD"
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError):
        errors.append(
            DiscoveryError(
                code=DiscoveryErrorCode.COLLECTOR_FAILURE,
                message="An optional container fact could not be read",
                collector="wsl_probe",
                source="fixed_probe",
                retryable=False,
            ).to_dict()
        )
    execution_domain: dict[str, Any] = {"kind": "WSL", "generation": generation}
    if container_detected:
        evidence.append(
            _probe_evidence(
                "wsl-probe:container",
                observed_text,
                "container.current",
                {"detected": True, "runtime": container_runtime},
            )
        )
        execution_domain["container"] = {
            "detected": True,
            "runtime": container_runtime,
            "evidence_ids": ["wsl-probe:container"],
        }
    document: dict[str, Any] = {
        "schema_version": PROBE_SCHEMA_VERSION,
        "probe_version": PROBE_VERSION,
        "distro": validated_distro,
        "observed_at": observed_text,
        "execution_domain": execution_domain,
        "kernel": {"system": system, "release": release, "version": version},
        "identity": identity,
        "capabilities": {
            "DISCOVERY_LITE": "AVAILABLE",
            "DISCOVERY_FULL": "AVAILABLE",
            "RECOVERY_CAPABLE": "UNKNOWN",
        },
        "evidence": evidence,
        "errors": errors,
        "sanitized": True,
        "warnings": sorted(warnings),
    }
    if include_workspace:
        document["cwd"] = _safe_text(probe_source.cwd(), warnings)
        document["warnings"] = sorted(warnings)
    return document


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in {1, 2} or (len(args) == 2 and args[1] != "--workspace"):
        sys.stderr.write("WSL probe requires a distribution and an optional fixed flag\n")
        return 2
    try:
        document = build_probe_document(args[0], include_workspace=len(args) == 2)
        sys.stdout.write(json.dumps(document, separators=(",", ":"), allow_nan=False))
    except (TypeError, ValueError, OSError):
        sys.stderr.write("WSL probe failed to collect fixed local facts\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PROBE_SCHEMA_VERSION",
    "PROBE_VERSION",
    "NormalizedWslProbe",
    "WslProbeProtocolError",
    "build_probe_document",
    "main",
    "normalize_wsl_probe",
]
