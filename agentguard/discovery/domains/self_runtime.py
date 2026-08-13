"""Offline, low-privilege discovery of the current execution domain."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import stat
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Protocol

from agentguard.recovery.contracts import (
    RecoveryOperationResult,
    RecoveryRequest,
)
from agentguard.recovery.manifest import validate_snapshot_v3
from agentguard.recovery.policy import RestorePolicy

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


def _sandbox_relative_path(logical_path: str, *, reason_code: str) -> Path:
    """Map an absolute POSIX or drive-qualified Windows path under a sandbox."""
    windows_path = PureWindowsPath(logical_path)
    if windows_path.is_absolute():
        if not re.fullmatch(r"[A-Za-z]:", windows_path.drive):
            raise ValueError(reason_code)
        name = windows_path.name
        parts = (
            "windows",
            windows_path.drive[0].casefold(),
            hashlib.sha256(logical_path.casefold().encode("utf-8")).hexdigest()[:24],
            name,
        )
    else:
        posix_path = PurePosixPath(logical_path)
        if not posix_path.is_absolute():
            raise ValueError(reason_code)
        name = posix_path.name
        parts = (
            "posix",
            hashlib.sha256(logical_path.encode("utf-8")).hexdigest()[:24],
            name,
        )
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(reason_code)
    return Path(*parts)


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
        recovery_policy: RestorePolicy | None = None,
    ) -> None:
        self._source = source or _SystemProbeSource()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._recovery_policy = recovery_policy or RestorePolicy()

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

    def snapshot(self, request: RecoveryRequest) -> RecoveryOperationResult:
        """Capture one approved local target into the P6 artifact contract."""
        if request.target_path is None or not request.target_path.is_file():
            return self._recovery_result(
                request,
                CapabilityStatus.NOT_PRESENT,
                "RECOVERY_TARGET_NOT_PRESENT",
            )
        try:
            artifact = self._recovery_policy.snapshot_v3(
                request.target_path,
                request.execution_domain_id,
                user_approved=request.user_approved,
            )
        except PermissionError:
            return self._recovery_result(
                request,
                CapabilityStatus.PERMISSION_DENIED,
                "RECOVERY_PERMISSION_DENIED",
            )
        except (OSError, ValueError):
            return self._recovery_result(
                request,
                CapabilityStatus.UNREACHABLE,
                "RECOVERY_DOMAIN_UNREACHABLE",
            )
        valid, reason_code, digest = validate_snapshot_v3(
            artifact,
            expected_domain=request.execution_domain_id,
        )
        if not valid:
            return self._recovery_result(request, CapabilityStatus.ERROR, reason_code)
        return self._recovery_result(
            request,
            CapabilityStatus.AVAILABLE,
            reason_code,
            manifest_digest=digest,
            artifact=artifact,
        )

    def restore(self, request: RecoveryRequest) -> RecoveryOperationResult:
        """Refuse real restores until a later phase defines test-restore semantics."""
        return self._recovery_result(
            request,
            CapabilityStatus.UNSUPPORTED,
            "REAL_RESTORE_OUT_OF_SCOPE_P6",
        )

    def verify(self, request: RecoveryRequest) -> RecoveryOperationResult:
        """Verify a supplied P6 artifact without touching the local target."""
        valid, reason_code, digest = validate_snapshot_v3(
            request.artifact,
            expected_domain=request.execution_domain_id,
        )
        return self._recovery_result(
            request,
            CapabilityStatus.AVAILABLE if valid else CapabilityStatus.ERROR,
            reason_code,
            manifest_digest=digest,
        )

    def test_restore(self, request: RecoveryRequest) -> RecoveryOperationResult:
        """Materialize a validated Snapshot V3 into a fresh isolated sandbox."""
        artifact = request.artifact
        valid, reason_code, digest = validate_snapshot_v3(
            artifact,
            expected_domain=request.execution_domain_id,
        )
        if not valid:
            return self._recovery_result(request, CapabilityStatus.ERROR, reason_code)
        entries = [
            entry for entry in artifact["manifest"] if entry["classification"] == "restorable"
        ]
        if not entries:
            return self._recovery_result(
                request,
                CapabilityStatus.ERROR,
                "TEST_RESTORE_NO_RESTORABLE_ENTRIES",
                manifest_digest=digest,
            )
        sandbox_root = request.sandbox_path
        if sandbox_root is None or not sandbox_root.is_dir():
            return self._recovery_result(
                request,
                CapabilityStatus.ERROR,
                "TEST_RESTORE_SANDBOX_UNAVAILABLE",
            )
        sandbox = Path(
            tempfile.mkdtemp(prefix=".agentguard-test-restore-", dir=sandbox_root)
        )
        verified = 0
        try:
            for entry in entries:
                logical_path = entry["logical_path"]
                relative = _sandbox_relative_path(
                    logical_path,
                    reason_code="TEST_RESTORE_PATH_INVALID",
                )
                destination = (sandbox / relative).resolve()
                if sandbox not in destination.parents:
                    raise ValueError("TEST_RESTORE_PATH_INVALID")
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.is_symlink() or destination.exists() and not destination.is_file():
                    raise ValueError("TEST_RESTORE_PATH_INVALID")
                content = artifact["blobs"][entry["blob_sha256"]]
                temporary = destination.with_name(f".{destination.name}.tmp")
                expected_mode = stat.S_IMODE(int(entry["mode"], 8))
                with temporary.open("wb") as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                    if hasattr(os, "fchmod"):
                        os.fchmod(output.fileno(), expected_mode)
                    else:
                        os.chmod(temporary, expected_mode)
                os.replace(temporary, destination)
                actual = destination.read_bytes()
                current = destination.stat()
                if (
                    len(actual) != entry["size"]
                    or __import__("hashlib").sha256(actual).hexdigest() != entry["sha256"]
                    or stat.S_IMODE(current.st_mode) != expected_mode
                ):
                    raise ValueError("TEST_RESTORE_CONTENT_INVALID")
                if entry["validator"] != "toml-parse":
                    raise ValueError("TEST_RESTORE_VALIDATOR_UNDEFINED")
                tomllib.loads(actual.decode("utf-8"))
                verified += 1
        except PermissionError:
            shutil.rmtree(sandbox, ignore_errors=True)
            return self._recovery_result(request, CapabilityStatus.PERMISSION_DENIED, "RECOVERY_PERMISSION_DENIED")
        except (OSError, ValueError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            reason = str(error) if str(error).startswith("TEST_RESTORE_") else "TEST_RESTORE_VALIDATION_FAILED"
            shutil.rmtree(sandbox, ignore_errors=True)
            return self._recovery_result(request, CapabilityStatus.ERROR, reason)
        return self._recovery_result(
            request,
            CapabilityStatus.AVAILABLE,
            "TEST_RESTORE_VERIFIED",
            manifest_digest=digest,
            details={"sandbox_path": str(sandbox), "verified_targets": verified},
        )

    def drill_restore(self, request: RecoveryRequest) -> RecoveryOperationResult:
        """Prove CAS recovery after controlled drift inside a service-owned drill root."""
        artifact = request.artifact
        valid, reason_code, digest = validate_snapshot_v3(
            artifact,
            expected_domain=request.execution_domain_id,
        )
        if not valid:
            return self._recovery_result(request, CapabilityStatus.ERROR, reason_code)
        root = request.drill_root
        if root is None or not root.is_dir() or root.is_symlink():
            return self._recovery_result(request, CapabilityStatus.ERROR, "DRILL_TARGET_UNAVAILABLE")
        try:
            resolved_root = root.resolve(strict=True)
            if resolved_root != root.absolute() or not root.is_dir():
                raise ValueError("DRILL_TARGET_UNSAFE")
            entries = [
                entry for entry in artifact["manifest"] if entry["classification"] == "restorable"
            ]
            if not entries:
                raise ValueError("TEST_RESTORE_NO_RESTORABLE_ENTRIES")
            verified = 0
            targets: list[str] = []
            for entry in entries:
                logical_path = entry["logical_path"]
                relative = _sandbox_relative_path(
                    logical_path,
                    reason_code="DRILL_TARGET_UNSAFE",
                )
                target = root.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                resolved_parent = target.parent.resolve(strict=True)
                if resolved_root != resolved_parent and resolved_root not in resolved_parent.parents:
                    raise ValueError("DRILL_TARGET_UNSAFE")
                if target.exists() or target.is_symlink():
                    raise ValueError("DRILL_TARGET_UNSAFE")
                content = artifact["blobs"][entry["blob_sha256"]]
                self._write_atomic(target, content, int(entry["mode"], 8))
                baseline = self._read_regular(target)
                if baseline != content:
                    raise ValueError("DRILL_TARGET_WRITE_INVALID")
                drift = self._drift_bytes(content)
                self._write_atomic(target, drift, int(entry["mode"], 8))
                drifted = self._read_regular(target)
                if drifted == content or hashlib.sha256(drifted).hexdigest() == entry["sha256"]:
                    raise ValueError("DRILL_DRIFT_NOT_ESTABLISHED")
                self._write_atomic(target, content, int(entry["mode"], 8))
                recovered = self._read_regular(target)
                current = target.stat(follow_symlinks=False)
                if (
                    recovered != content
                    or hashlib.sha256(recovered).hexdigest() != entry["sha256"]
                    or len(recovered) != entry["size"]
                    or stat.S_IMODE(current.st_mode) != stat.S_IMODE(int(entry["mode"], 8))
                    or entry["domain"] != request.execution_domain_id
                ):
                    raise ValueError("DRILL_RECOVERY_VALIDATION_FAILED")
                if entry["validator"] != "toml-parse":
                    raise ValueError("TEST_RESTORE_VALIDATOR_UNDEFINED")
                tomllib.loads(recovered.decode("utf-8"))
                targets.append(str(target.relative_to(resolved_root)))
                verified += 1
        except PermissionError:
            return self._recovery_result(request, CapabilityStatus.PERMISSION_DENIED, "RECOVERY_PERMISSION_DENIED")
        except (OSError, ValueError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            reason = str(error)
            if not reason.startswith(("DRILL_", "TEST_RESTORE_")):
                reason = "DRILL_RECOVERY_VALIDATION_FAILED"
            return self._recovery_result(request, CapabilityStatus.ERROR, reason, manifest_digest=digest)
        return self._recovery_result(
            request,
            CapabilityStatus.AVAILABLE,
            "DRILL_VERIFIED_R3",
            manifest_digest=digest,
            details={
                "drift_established": True,
                "managed_target_root": str(resolved_root),
                "target_refs": targets,
                "verified_targets": verified,
            },
        )

    @staticmethod
    def _drift_bytes(content: bytes) -> bytes:
        return content + b"# agentguard-r3-controlled-drift\n"

    @staticmethod
    def _read_regular(path: Path) -> bytes:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            current = os.fstat(descriptor)
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                raise ValueError("DRILL_TARGET_UNSAFE")
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                content = source.read()
            if current.st_size != len(content):
                raise ValueError("DRILL_TARGET_CHANGED")
            return content
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_atomic(path: Path, content: bytes, mode: int) -> None:
        if path.is_symlink() or path.exists() and not path.is_file():
            raise ValueError("DRILL_TARGET_UNSAFE")
        temporary = path.with_name(f".{path.name}.agentguard-r3.tmp")
        if temporary.exists() or temporary.is_symlink():
            raise ValueError("DRILL_TARGET_UNSAFE")
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                mode,
            )
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
                if hasattr(os, "fchmod"):
                    os.fchmod(output.fileno(), mode)
                else:
                    os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _recovery_result(
        request: RecoveryRequest,
        status: CapabilityStatus,
        reason_code: str,
        *,
        manifest_digest: str | None = None,
        artifact: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> RecoveryOperationResult:
        return RecoveryOperationResult(
            operation=request.operation,
            status=status,
            reason_code=reason_code,
            execution_domain_id=request.execution_domain_id,
            checkpoint_id=request.checkpoint_id,
            manifest_digest=manifest_digest,
            artifact=artifact,
            details=details or {},
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
