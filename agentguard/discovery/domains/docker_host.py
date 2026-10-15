"""Bounded, read-only process observations through the user's local Docker CLI."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from ..capabilities import CapabilityStatus
from ..probes.container_processes import (
    process_probe_source,
    MAX_PROBE_BYTES,
    ContainerProcessProtocolError,
    parse_container_processes,
)

_CONTEXT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
_CONTAINER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_LOCAL_PIPE = re.compile(r"npipe:////\./pipe/[A-Za-z0-9_.-]{1,128}\Z", re.IGNORECASE)
_PS_COMMAND = ("docker", "ps", "--no-trunc", "--format", "{{json .}}")
_MAX_TARGETS = 256


@dataclass(frozen=True)
class DockerTarget:
    container_id: str
    name: str
    status: str


@dataclass(frozen=True)
class DockerTargetListResult:
    status: CapabilityStatus
    reason_code: str
    targets: tuple[DockerTarget, ...] = ()


@dataclass(frozen=True)
class DomainProcessObservation:
    target: DockerTarget
    observed_at: datetime
    processes: tuple[dict[str, int | str], ...] = field(default=(), repr=False)
    status: CapabilityStatus = CapabilityStatus.UNKNOWN
    reason_code: str = "DOCKER_PROCESS_NOT_OBSERVED"
    boot_id: str | None = None


@dataclass(frozen=True)
class DockerCommandResult:
    returncode: int
    stdout: bytes = b""
    timed_out: bool = False
    output_limited: bool = False


class DockerCommandExecutor(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        max_output_bytes: int,
        env: Mapping[str, str],
    ) -> DockerCommandResult: ...


class _SubprocessExecutor:
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        max_output_bytes: int,
        env: Mapping[str, str],
    ) -> DockerCommandResult:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            env=dict(env),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output: list[bytes] = []

        def read_bounded() -> None:
            assert process.stdout is not None
            try:
                output.append(process.stdout.read(max_output_bytes + 1))
            except OSError:
                output.append(b"")

        reader = threading.Thread(target=read_bounded, daemon=True)
        reader.start()
        reader.join(timeout)
        if reader.is_alive():
            self._stop(process)
            reader.join(1)
            return DockerCommandResult(-1, timed_out=True)
        captured = output[0] if output else b""
        if len(captured) > max_output_bytes:
            self._stop(process)
            return DockerCommandResult(-1, output_limited=True)
        try:
            returncode = process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            self._stop(process)
            return DockerCommandResult(-1, timed_out=True)
        return DockerCommandResult(returncode, captured)

    @staticmethod
    def _stop(process: subprocess.Popen[bytes]) -> None:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


class DockerHostProbe:
    """Enumerate a local Docker context and inspect only targets it returned."""

    def __init__(
        self,
        *,
        executor: DockerCommandExecutor | None = None,
        clock: Callable[[], datetime] | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._executor = executor if executor is not None else _SubprocessExecutor()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._environment = dict(os.environ if environment is None else environment)
        self._bound_targets: dict[str, DockerTarget] = {}
        self._bound_endpoint: str | None = None
        self._lock = threading.Lock()

    def list_targets(self) -> DockerTargetListResult:
        status, reason, endpoint, env = self._local_context()
        if endpoint is None or env is None:
            with self._lock:
                self._bound_targets.clear()
                self._bound_endpoint = None
            return DockerTargetListResult(status, reason)
        result = self._query_targets(env)
        with self._lock:
            self._bound_targets = {
                target.container_id: target for target in result.targets
            } if result.status is CapabilityStatus.AVAILABLE else {}
            self._bound_endpoint = endpoint if result.status is CapabilityStatus.AVAILABLE else None
        return result

    def probe_target(self, target: DockerTarget) -> DomainProcessObservation:
        observed_at = self._clock()
        with self._lock:
            bound = (
                isinstance(target, DockerTarget)
                and self._bound_targets.get(target.container_id) is target
            )
            original_endpoint = self._bound_endpoint
        if not bound or original_endpoint is None:
            return DomainProcessObservation(
                target, observed_at, status=CapabilityStatus.ERROR,
                reason_code="DOCKER_TARGET_NOT_BOUND",
            )
        status, reason, endpoint, env = self._local_context()
        if endpoint is None or env is None:
            return DomainProcessObservation(
                target, observed_at, status=status, reason_code=reason,
            )
        if endpoint != original_endpoint:
            return DomainProcessObservation(
                target, observed_at, status=CapabilityStatus.ERROR,
                reason_code="DOCKER_CONTEXT_CHANGED",
            )
        refreshed = self._query_targets(env)
        if refreshed.status is not CapabilityStatus.AVAILABLE:
            return DomainProcessObservation(
                target, observed_at, status=refreshed.status,
                reason_code=refreshed.reason_code,
            )
        if not any(
            item.container_id == target.container_id and item.name == target.name
            for item in refreshed.targets
        ):
            return DomainProcessObservation(
                target, observed_at, status=CapabilityStatus.NOT_PRESENT,
                reason_code="DOCKER_TARGET_CHANGED",
            )
        command = (
            "docker", "exec", target.container_id, "python3", "-I", "-B", "-c",
            process_probe_source(),
        )
        result, status, reason = self._execute(
            command, env=env, timeout=10, max_output_bytes=MAX_PROBE_BYTES,
            failure_code="DOCKER_PROCESS_PROBE_FAILED",
        )
        if result is None:
            return DomainProcessObservation(
                target, observed_at, status=status, reason_code=reason,
            )
        try:
            normalized = parse_container_processes(result.stdout)
        except ContainerProcessProtocolError:
            return DomainProcessObservation(
                target, observed_at, status=CapabilityStatus.ERROR,
                reason_code="DOCKER_PROCESS_PROTOCOL_INVALID",
            )
        return DomainProcessObservation(
            target=target,
            observed_at=observed_at,
            processes=normalized.processes,
            status=(
                CapabilityStatus.DEGRADED
                if normalized.partial_reason is not None else CapabilityStatus.AVAILABLE
            ),
            reason_code=(
                "DOCKER_" + normalized.partial_reason
                if normalized.partial_reason is not None else "DOCKER_PROCESS_OBSERVED"
            ),
            boot_id=normalized.boot_id,
        )

    def session_transport(self, target: DockerTarget) -> tuple[str, dict[str, str]] | None:
        """Return a pinned local CLI transport for a current server-owned target.

        This is not a general command endpoint. Its only consumer issues the
        fixed Kimi initialize/session-list exchange, without prompts or tools.
        """
        with self._lock:
            if self._bound_targets.get(target.container_id) is not target:
                return None
            expected_endpoint = self._bound_endpoint
        _status, _reason, endpoint, env = self._local_context()
        if endpoint is None or endpoint != expected_endpoint or env is None:
            return None
        listing = self._query_targets(env)
        if listing.status is not CapabilityStatus.AVAILABLE or not any(
            item.container_id == target.container_id and item.name == target.name
            for item in listing.targets
        ):
            return None
        executable = shutil.which("docker")
        return (executable, env) if executable is not None else None

    def _local_context(
        self,
    ) -> tuple[CapabilityStatus, str, str | None, dict[str, str] | None]:
        requested_host = self._environment.get("DOCKER_HOST", "")
        requested_context = self._environment.get("DOCKER_CONTEXT", "")
        if requested_host and not _is_local_endpoint(requested_host):
            return CapabilityStatus.UNSUPPORTED, "DOCKER_REMOTE_ENDPOINT_REJECTED", None, None
        if requested_context and _CONTEXT_NAME.fullmatch(requested_context) is None:
            return CapabilityStatus.UNSUPPORTED, "DOCKER_CONTEXT_INVALID", None, None
        shown, status, reason = self._execute(
            ("docker", "context", "show"), env=self._environment,
            timeout=4, max_output_bytes=512, failure_code="DOCKER_CONTEXT_UNAVAILABLE",
        )
        if shown is None:
            return status, reason, None, None
        try:
            name = shown.stdout.decode("ascii").strip()
        except UnicodeDecodeError:
            name = ""
        if _CONTEXT_NAME.fullmatch(name) is None or (
            requested_context and name != requested_context
        ):
            return CapabilityStatus.ERROR, "DOCKER_CONTEXT_INVALID", None, None
        inspected, status, reason = self._execute(
            ("docker", "context", "inspect", name, "--format", "{{json .Endpoints.docker.Host}}"),
            env=self._environment, timeout=4, max_output_bytes=4096,
            failure_code="DOCKER_CONTEXT_UNAVAILABLE",
        )
        if inspected is None:
            return status, reason, None, None
        try:
            endpoint = json.loads(inspected.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            endpoint = None
        if not isinstance(endpoint, str):
            return CapabilityStatus.ERROR, "DOCKER_CONTEXT_INVALID", None, None
        if not _is_local_endpoint(endpoint):
            return CapabilityStatus.UNSUPPORTED, "DOCKER_REMOTE_ENDPOINT_REJECTED", None, None
        pinned_endpoint = requested_host or endpoint
        env = dict(self._environment)
        env.pop("DOCKER_CONTEXT", None)
        env["DOCKER_HOST"] = pinned_endpoint
        return CapabilityStatus.AVAILABLE, "DOCKER_CONTEXT_LOCAL", pinned_endpoint, env

    def _query_targets(self, env: Mapping[str, str]) -> DockerTargetListResult:
        result, status, reason = self._execute(
            _PS_COMMAND, env=env, timeout=6, max_output_bytes=128 * 1024,
            failure_code="DOCKER_TARGET_LIST_FAILED",
        )
        if result is None:
            return DockerTargetListResult(status, reason)
        try:
            lines = result.stdout.decode("utf-8").splitlines()
            if len(lines) > _MAX_TARGETS:
                raise ValueError
            targets: list[DockerTarget] = []
            seen: set[str] = set()
            for line in lines:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise TypeError
                container_id = item.get("ID")
                name = item.get("Names")
                state = item.get("Status")
                if (
                    not isinstance(container_id, str)
                    or _CONTAINER_ID.fullmatch(container_id) is None
                    or container_id in seen
                    or not isinstance(name, str)
                    or _CONTAINER_NAME.fullmatch(name) is None
                    or not _safe_status(state)
                    or item.get("State", "running") != "running"
                ):
                    raise ValueError
                seen.add(container_id)
                targets.append(DockerTarget(container_id, name, state))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return DockerTargetListResult(
                CapabilityStatus.ERROR, "DOCKER_TARGET_LIST_INVALID",
            )
        if not targets:
            return DockerTargetListResult(
                CapabilityStatus.NOT_PRESENT, "DOCKER_TARGETS_EMPTY",
            )
        return DockerTargetListResult(
            CapabilityStatus.AVAILABLE, "DOCKER_TARGETS_LISTED", tuple(targets),
        )

    def _execute(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        timeout: float,
        max_output_bytes: int,
        failure_code: str,
    ) -> tuple[DockerCommandResult | None, CapabilityStatus, str]:
        try:
            result = self._executor.run(
                argv, timeout=timeout, max_output_bytes=max_output_bytes, env=env,
            )
        except FileNotFoundError:
            return None, CapabilityStatus.UNREACHABLE, "DOCKER_CLI_UNAVAILABLE"
        except PermissionError:
            return None, CapabilityStatus.PERMISSION_DENIED, "DOCKER_CLI_PERMISSION_DENIED"
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            return None, CapabilityStatus.UNREACHABLE, failure_code
        if not isinstance(result, DockerCommandResult) or not isinstance(result.stdout, bytes):
            return None, CapabilityStatus.ERROR, "DOCKER_CLI_RESULT_INVALID"
        if result.timed_out:
            return None, CapabilityStatus.UNREACHABLE, "DOCKER_CLI_TIMEOUT"
        if result.output_limited or len(result.stdout) > max_output_bytes:
            return None, CapabilityStatus.ERROR, "DOCKER_OUTPUT_LIMIT_REACHED"
        if result.returncode != 0:
            return None, CapabilityStatus.UNREACHABLE, failure_code
        return result, CapabilityStatus.AVAILABLE, "DOCKER_COMMAND_COMPLETE"


def _is_local_endpoint(endpoint: str) -> bool:
    if _LOCAL_PIPE.fullmatch(endpoint) is not None:
        return True
    if not endpoint.startswith("unix:///"):
        return False
    path = endpoint.removeprefix("unix://")
    return bool(
        len(path) <= 256
        and path.startswith("/")
        and "//" not in path
        and all(part not in {".", ".."} for part in path.split("/"))
        and re.fullmatch(r"/[A-Za-z0-9_./-]+", path) is not None
    )


def _safe_status(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and 0 < len(value) <= 128
        and all(ord(char) >= 32 and ord(char) != 127 for char in value)
    )


__all__ = [
    "DockerCommandExecutor", "DockerCommandResult", "DockerHostProbe", "DockerTarget",
    "DockerTargetListResult", "DomainProcessObservation",
]
