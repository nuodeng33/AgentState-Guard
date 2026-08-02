"""Offline, low-privilege discovery of the current execution domain."""

from __future__ import annotations

import os
import platform
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ..capabilities import (
    CapabilityAssessment,
    CapabilityStatus,
    DomainCapabilities,
    EvidenceReliability,
)
from ..errors import DiscoveryError, DiscoveryErrorCode
from ..models import DiscoverySnapshot, ProbeEvidence
from ..resolver import resolve_execution_domains

_COLLECTOR = "self_runtime"
_SENSITIVE_FIELD_MARKERS = (
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
_URL_RE = re.compile(r"(?:https?|wss?)://", re.IGNORECASE)
_NAMESPACE_RE = re.compile(r"^[a-z]+:\[(\d+)\]$", re.IGNORECASE)


class _ProbeSource(Protocol):
    def os_name(self) -> str: ...

    def platform_system(self) -> str: ...

    def platform_release(self) -> str: ...

    def platform_version(self) -> str: ...

    def environment_name_present(self, name: str) -> bool: ...

    def read_bytes(self, path: str) -> bytes: ...

    def readlink(self, path: str) -> str: ...


class _SystemProbeSource:
    def os_name(self) -> str:
        return os.name

    def platform_system(self) -> str:
        return platform.system()

    def platform_release(self) -> str:
        return platform.release()

    def platform_version(self) -> str:
        return platform.version()

    def environment_name_present(self, name: str) -> bool:
        return name in os.environ

    def read_bytes(self, path: str) -> bytes:
        return Path(path).read_bytes()

    def readlink(self, path: str) -> str:
        return os.readlink(path)


def _warnings_for_text(text: str) -> list[str]:
    normalized = text.lower()
    warnings = []
    if any(marker in normalized for marker in _SENSITIVE_FIELD_MARKERS):
        warnings.append("SENSITIVE_FIELD_REJECTED")
    if _URL_RE.search(text):
        warnings.append("REMOTE_URL_REJECTED")
    return warnings


def _sanitized_local_text(value: object) -> tuple[str, list[str]]:
    text = str(value)
    warnings = _warnings_for_text(text)
    return ("[REDACTED]" if warnings else text, warnings)


def _kernel_facts(text: str) -> Mapping[str, Any]:
    lowered = text.lower()
    warnings = _warnings_for_text(text)
    wsl2 = "wsl2" in lowered or "microsoft-standard" in lowered
    wsl1 = "microsoft" in lowered and bool(re.search(r"(?:^|\s)4\.4\.", lowered)) and not wsl2
    return {
        "microsoft": "microsoft" in lowered,
        "wsl": "microsoft" in lowered or "wsl" in lowered,
        "wsl1": wsl1,
        "wsl2": wsl2,
        "generation": "WSL2" if wsl2 else "WSL1" if wsl1 else "UNKNOWN",
        "warnings": warnings,
    }


def _cgroup_facts(text: str) -> Mapping[str, Any]:
    lowered = text.lower()
    docker = "docker" in lowered
    containerd = "containerd" in lowered or "kubepods" in lowered
    podman = "podman" in lowered or "libpod" in lowered
    return {
        "docker": docker,
        "containerd": containerd,
        "podman": podman,
        "container_hint": docker or containerd or podman,
        "warnings": _warnings_for_text(text),
    }


def _mount_facts(text: str) -> Mapping[str, Any]:
    lowered = text.lower()
    return {
        "docker": "/docker/" in lowered or "docker/containers" in lowered,
        "containerd": "containerd" in lowered,
        "podman": "podman" in lowered or "libpod" in lowered,
        "overlay": " - overlay " in lowered,
        "warnings": _warnings_for_text(text),
    }


def _status_facts(text: str) -> Mapping[str, Any]:
    fields: dict[str, Any] = {}
    warnings = _warnings_for_text(text)
    for line in text.splitlines():
        if ":" not in line:
            continue
        raw_key, raw_value = line.split(":", 1)
        normalized = re.sub(r"[^a-z0-9]+", "_", raw_key.lower()).strip("_")
        if any(marker in normalized for marker in _SENSITIVE_FIELD_MARKERS):
            if "SENSITIVE_FIELD_REJECTED" not in warnings:
                warnings.append("SENSITIVE_FIELD_REJECTED")
            continue
        value = raw_value.strip()
        if normalized in {"no_new_privs", "nonewprivs"}:
            fields["no_new_privs"] = int(value, 10)
        elif normalized == "seccomp":
            fields["seccomp"] = int(value, 10)
        elif normalized in {"cap_eff", "capeff"}:
            if not re.fullmatch(r"[0-9a-fA-F]+", value):
                raise ValueError("CapEff is not a hexadecimal capability mask")
            fields["cap_eff"] = value.lower()
    return {"fields": fields, "warnings": warnings}


class SelfRuntimeAdapter:
    """Collect only fixed, local facts visible to the current process."""

    def __init__(
        self,
        *,
        source: _ProbeSource | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._source = source or _SystemProbeSource()
        self._clock = clock or (lambda: datetime.now(UTC))

    def capabilities(self) -> DomainCapabilities:
        return DomainCapabilities(
            {
                "local_platform": CapabilityAssessment(
                    status=CapabilityStatus.AVAILABLE,
                    reason_code="STANDARD_LIBRARY_READ_ONLY",
                ),
                "local_files": CapabilityAssessment(
                    status=CapabilityStatus.AVAILABLE,
                    reason_code="FIXED_PATH_READ_ONLY",
                ),
                "network_probe": CapabilityAssessment(
                    status=CapabilityStatus.UNSUPPORTED,
                    reason_code="OFFLINE_BY_CONTRACT",
                ),
                "host_container_enumeration": CapabilityAssessment(
                    status=CapabilityStatus.UNSUPPORTED,
                    reason_code="OUT_OF_SCOPE",
                ),
            }
        )

    def discover(self) -> DiscoverySnapshot:
        observed_at = self._clock()
        evidence: list[ProbeEvidence] = []
        platform_value: Mapping[str, Any] | None = None
        try:
            os_name, os_name_warnings = _sanitized_local_text(self._source.os_name())
            system, system_warnings = _sanitized_local_text(self._source.platform_system())
            release, release_warnings = _sanitized_local_text(self._source.platform_release())
            version, version_warnings = _sanitized_local_text(self._source.platform_version())
            platform_value = {
                "os_name": os_name,
                "system": system,
                "release": release,
                "version": version,
                "warnings": list(
                    dict.fromkeys(
                        (
                            *os_name_warnings,
                            *system_warnings,
                            *release_warnings,
                            *version_warnings,
                        )
                    )
                ),
            }
            evidence.append(
                self._evidence(
                    "platform",
                    "standard_library",
                    observed_at,
                    "platform.identity",
                    platform_value,
                    CapabilityStatus.AVAILABLE,
                    EvidenceReliability.HIGH,
                    0.95,
                )
            )
        except Exception as exc:  # noqa: BLE001 - isolate this probe failure
            evidence.append(
                self._failure_evidence(
                    "platform",
                    "standard_library",
                    observed_at,
                    "platform.identity",
                    exc,
                )
            )

        system = str((platform_value or {}).get("system", "")).lower()
        os_name = str((platform_value or {}).get("os_name", "")).lower()
        linux_applicable = system == "linux" or os_name == "posix"
        if not linux_applicable:
            evidence.append(
                self._evidence(
                    "linux-files",
                    "/proc",
                    observed_at,
                    "probe.linux_files",
                    {"applicable": False, "warnings": []},
                    CapabilityStatus.UNSUPPORTED,
                    EvidenceReliability.HIGH,
                    0.95,
                )
            )
        else:
            evidence.append(self._probe_wsl_interop(observed_at))
            evidence.extend(
                (
                    self._probe_text(
                        "kernel-osrelease",
                        "/proc/sys/kernel/osrelease",
                        observed_at,
                        "kernel.osrelease",
                        _kernel_facts,
                    ),
                    self._probe_text(
                        "kernel-version",
                        "/proc/version",
                        observed_at,
                        "kernel.proc_version",
                        _kernel_facts,
                    ),
                    self._probe_marker(
                        "dockerenv",
                        "/.dockerenv",
                        observed_at,
                        "container.dockerenv",
                    ),
                    self._probe_marker(
                        "containerenv",
                        "/run/.containerenv",
                        observed_at,
                        "container.containerenv",
                    ),
                    self._probe_text(
                        "cgroup-init",
                        "/proc/1/cgroup",
                        observed_at,
                        "container.cgroup_init",
                        _cgroup_facts,
                    ),
                    self._probe_text(
                        "cgroup-self",
                        "/proc/self/cgroup",
                        observed_at,
                        "container.cgroup_self",
                        _cgroup_facts,
                    ),
                    self._probe_text(
                        "mountinfo",
                        "/proc/self/mountinfo",
                        observed_at,
                        "container.mountinfo",
                        _mount_facts,
                    ),
                    self._probe_text(
                        "security-status",
                        "/proc/self/status",
                        observed_at,
                        "process.security_status",
                        _status_facts,
                    ),
                )
            )
            for namespace in ("mnt", "pid", "user"):
                evidence.append(self._probe_namespace(namespace, observed_at))

        resolution = resolve_execution_domains(evidence)
        stamp = observed_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        return DiscoverySnapshot(
            snapshot_id=f"self-runtime-{stamp}",
            observed_at=observed_at,
            domains=resolution.domains,
            evidence=tuple(evidence),
            errors=resolution.errors,
            status=resolution.status,
        )

    def _probe_wsl_interop(self, observed_at: datetime) -> ProbeEvidence:
        try:
            present = self._source.environment_name_present("WSL_INTEROP")
            return self._evidence(
                "wsl-interop",
                "environment_name:WSL_INTEROP",
                observed_at,
                "wsl.interop_presence",
                {"present": present, "warnings": []},
                CapabilityStatus.AVAILABLE if present else CapabilityStatus.NOT_PRESENT,
                EvidenceReliability.HIGH,
                0.95,
            )
        except Exception as exc:  # noqa: BLE001 - isolate this probe failure
            return self._failure_evidence(
                "wsl-interop",
                "environment_name:WSL_INTEROP",
                observed_at,
                "wsl.interop_presence",
                exc,
            )

    def _probe_marker(
        self,
        name: str,
        path: str,
        observed_at: datetime,
        fact_type: str,
    ) -> ProbeEvidence:
        try:
            self._source.read_bytes(path)
            return self._evidence(
                name,
                path,
                observed_at,
                fact_type,
                {"present": True, "warnings": []},
                CapabilityStatus.AVAILABLE,
                EvidenceReliability.MEDIUM,
                0.7,
            )
        except FileNotFoundError:
            return self._not_present_evidence(name, path, observed_at, fact_type)
        except Exception as exc:  # noqa: BLE001 - isolate this probe failure
            return self._failure_evidence(name, path, observed_at, fact_type, exc)

    def _probe_text(
        self,
        name: str,
        path: str,
        observed_at: datetime,
        fact_type: str,
        parser: Callable[[str], Mapping[str, Any]],
    ) -> ProbeEvidence:
        try:
            raw = self._source.read_bytes(path)
            text = raw.decode("utf-8", errors="strict")
            value = parser(text)
            return self._evidence(
                name,
                path,
                observed_at,
                fact_type,
                value,
                CapabilityStatus.AVAILABLE,
                EvidenceReliability.MEDIUM,
                0.8,
            )
        except FileNotFoundError:
            return self._not_present_evidence(name, path, observed_at, fact_type)
        except Exception as exc:  # noqa: BLE001 - isolate this probe failure
            return self._failure_evidence(name, path, observed_at, fact_type, exc)

    def _probe_namespace(self, namespace: str, observed_at: datetime) -> ProbeEvidence:
        name = f"namespace-{namespace}"
        path = f"/proc/self/ns/{namespace}"
        fact_type = f"process.namespace.{namespace}"
        try:
            target = self._source.readlink(path)
            match = _NAMESPACE_RE.fullmatch(target)
            if match is None:
                raise ValueError("namespace identifier has an unsupported format")
            return self._evidence(
                name,
                path,
                observed_at,
                fact_type,
                {"namespace": namespace, "inode": match.group(1), "warnings": []},
                CapabilityStatus.AVAILABLE,
                EvidenceReliability.LOW,
                0.4,
            )
        except FileNotFoundError:
            return self._not_present_evidence(name, path, observed_at, fact_type)
        except Exception as exc:  # noqa: BLE001 - isolate this probe failure
            return self._failure_evidence(name, path, observed_at, fact_type, exc)

    def _not_present_evidence(
        self,
        name: str,
        source: str,
        observed_at: datetime,
        fact_type: str,
    ) -> ProbeEvidence:
        return self._evidence(
            name,
            source,
            observed_at,
            fact_type,
            {"present": False, "warnings": []},
            CapabilityStatus.NOT_PRESENT,
            EvidenceReliability.HIGH,
            0.95,
        )

    def _failure_evidence(
        self,
        name: str,
        source: str,
        observed_at: datetime,
        fact_type: str,
        error: Exception,
    ) -> ProbeEvidence:
        if isinstance(error, PermissionError):
            status = CapabilityStatus.PERMISSION_DENIED
            code = DiscoveryErrorCode.PERMISSION_DENIED
            message = "Permission denied while reading a local discovery source"
        elif isinstance(error, (UnicodeDecodeError, ValueError)):
            status = CapabilityStatus.ERROR
            code = DiscoveryErrorCode.INVALID_DATA
            message = "Local discovery source contained invalid data"
        else:
            status = CapabilityStatus.ERROR
            code = DiscoveryErrorCode.COLLECTOR_FAILURE
            message = "Local discovery probe failed"
        structured_error = DiscoveryError(
            code=code,
            message=message,
            collector=_COLLECTOR,
            source=source,
            retryable=False,
            details={"exception_type": type(error).__name__},
        )
        return self._evidence(
            name,
            source,
            observed_at,
            fact_type,
            {"warnings": [code.value]},
            status,
            EvidenceReliability.LOW,
            0.0,
            structured_error,
        )

    @staticmethod
    def _evidence(
        name: str,
        source: str,
        observed_at: datetime,
        fact_type: str,
        value: Mapping[str, Any],
        status: CapabilityStatus,
        reliability: EvidenceReliability,
        confidence: float,
        error: DiscoveryError | None = None,
    ) -> ProbeEvidence:
        return ProbeEvidence(
            evidence_id=f"self-runtime:{name}",
            collector=_COLLECTOR,
            source=source,
            observed_at=observed_at,
            fact_type=fact_type,
            value=value,
            reliability=reliability,
            confidence=confidence,
            status=status,
            error=error,
            sanitized=True,
        )


__all__ = ["SelfRuntimeAdapter"]
