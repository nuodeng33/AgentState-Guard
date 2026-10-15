"""Restricted subprocess boundary for allowlisted WSL discovery commands."""

from __future__ import annotations

import ntpath
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .capabilities import CapabilityStatus
from .errors import DiscoveryError, DiscoveryErrorCode


class CommandId(str, Enum):
    WSL_STATUS = "WSL_STATUS"
    WSL_LIST_QUIET = "WSL_LIST_QUIET"
    WSL_LIST_VERBOSE = "WSL_LIST_VERBOSE"
    WSL_PROBE_DISTRO = "WSL_PROBE_DISTRO"


_FIXED_COMMANDS = {
    CommandId.WSL_STATUS: ("wsl.exe", "--status"),
    CommandId.WSL_LIST_QUIET: ("wsl.exe", "--list", "--quiet"),
    CommandId.WSL_LIST_VERBOSE: ("wsl.exe", "--list", "--verbose"),
}
_DISTRO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,127}\Z")
_MAX_CONFIGURED_OUTPUT = 1024 * 1024
PROBE_MODULE_MISSING_EXIT = 42
_WSL_ACCESS_DENIED_CODE = "Wsl/EnumerateDistros/Service/E_ACCESSDENIED"
_WSL_ACCESS_DENIED_REASON = "WSL_E_ACCESSDENIED"
_WSL_ACCESS_DENIED_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_./-]){re.escape(_WSL_ACCESS_DENIED_CODE)}(?![A-Za-z0-9_./-])"
)
_PROBE_MODULE = "agentguard.discovery.probes.wsl_probe"
_PROBE_BOOTSTRAP = f"""import importlib.util
import runpy
import sys

module = {_PROBE_MODULE!r}
try:
    spec = importlib.util.find_spec(module)
except ModuleNotFoundError:
    spec = None
if spec is None:
    raise SystemExit({PROBE_MODULE_MISSING_EXIT})
sys.argv = [module, *sys.argv[1:]]
runpy.run_module(module, run_name="__main__")
"""


def validate_distro_name(name: object) -> str:
    """Accept a WSL list result as one argv value, never as command text."""

    if not isinstance(name, str) or not _DISTRO_RE.fullmatch(name):
        raise ValueError("distribution name is invalid")
    if name != name.strip() or any(ord(character) < 32 for character in name):
        raise ValueError("distribution name is invalid")
    return name


def _validated_wsl_executable(value: object) -> str:
    if not isinstance(value, str) or not ntpath.isabs(value):
        raise ValueError("wsl_executable must be an absolute Windows path")
    normalized = ntpath.normpath(value)
    if ntpath.basename(normalized).lower() != "wsl.exe":
        raise ValueError("wsl_executable must identify wsl.exe")
    if any(ord(character) < 32 for character in normalized):
        raise ValueError("wsl_executable contains a control character")
    return normalized


def _default_wsl_executable() -> str:
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    return _validated_wsl_executable(ntpath.join(system_root, "System32", "wsl.exe"))


@dataclass(frozen=True)
class CommandExecution:
    command_id: CommandId
    status: CapabilityStatus
    stdout: str = ""
    returncode: int | None = None
    error: DiscoveryError | None = None
    warnings: tuple[str, ...] = ()
    truncated: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status is CapabilityStatus.AVAILABLE and self.returncode == 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize metadata only; raw protocol/list output remains transient."""

        return {
            "command_id": self.command_id.value,
            "status": self.status.value,
            "stdout_length": len(self.stdout),
            "returncode": self.returncode,
            "error": self.error.to_dict() if self.error else None,
            "warnings": list(self.warnings),
            "truncated": self.truncated,
        }


class WslCommandRunner:
    """Execute only fixed WSL discovery operations without a shell."""

    def __init__(
        self,
        *,
        executor: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
        timeout_seconds: float = 5.0,
        max_output_bytes: int = 64 * 1024,
        wsl_executable: str | None = None,
    ) -> None:
        if not 0 < float(timeout_seconds) <= 30:
            raise ValueError("timeout_seconds must be between 0 and 30")
        if not 0 < int(max_output_bytes) <= _MAX_CONFIGURED_OUTPUT:
            raise ValueError("max_output_bytes is outside the supported range")
        self._executor = executor or subprocess.run
        self._timeout_seconds = float(timeout_seconds)
        self._max_output_bytes = int(max_output_bytes)
        self._wsl_executable = _validated_wsl_executable(
            wsl_executable or _default_wsl_executable()
        )

    def run(
        self,
        command_id: CommandId | str,
        *,
        distro: str | None = None,
        include_workspace: bool = False,
    ) -> CommandExecution:
        try:
            selected = command_id if isinstance(command_id, CommandId) else CommandId(command_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("command_id is not allowlisted") from exc

        argv = self._argv(selected, distro, include_workspace)
        try:
            completed = self._executor(
                argv,
                capture_output=True,
                check=False,
                env={},
                shell=False,
                text=False,
                timeout=self._timeout_seconds,
            )
        except FileNotFoundError:
            return self._failure(
                selected,
                CapabilityStatus.NOT_PRESENT,
                DiscoveryErrorCode.NOT_PRESENT,
                "The WSL command interface is not present",
            )
        except PermissionError:
            return self._failure(
                selected,
                CapabilityStatus.PERMISSION_DENIED,
                DiscoveryErrorCode.PERMISSION_DENIED,
                "Permission was denied while invoking the WSL command interface",
            )
        except subprocess.TimeoutExpired:
            return self._failure(
                selected,
                CapabilityStatus.ERROR,
                DiscoveryErrorCode.TIMEOUT,
                "The allowlisted WSL command timed out",
            )
        except OSError:
            return self._failure(
                selected,
                CapabilityStatus.ERROR,
                DiscoveryErrorCode.COLLECTOR_FAILURE,
                "The allowlisted WSL command could not be executed",
            )

        stdout_bytes = completed.stdout if isinstance(completed.stdout, bytes) else b""
        stderr_bytes = completed.stderr if isinstance(completed.stderr, bytes) else b""
        if len(stdout_bytes) + len(stderr_bytes) > self._max_output_bytes:
            return self._failure(
                selected,
                CapabilityStatus.ERROR,
                DiscoveryErrorCode.INVALID_DATA,
                "The WSL command output exceeded the configured limit",
                returncode=completed.returncode,
                warnings=("OUTPUT_TOO_LARGE",),
                truncated=True,
                reason_code="OUTPUT_TOO_LARGE",
            )

        if completed.returncode != 0:
            if self._contains_access_denied_code(stdout_bytes, stderr_bytes):
                return self._failure(
                    selected,
                    CapabilityStatus.PERMISSION_DENIED,
                    DiscoveryErrorCode.PERMISSION_DENIED,
                    "Permission was denied by the WSL discovery service",
                    returncode=completed.returncode,
                    reason_code=_WSL_ACCESS_DENIED_REASON,
                )
            return self._failure(
                selected,
                CapabilityStatus.ERROR,
                DiscoveryErrorCode.COLLECTOR_FAILURE,
                "The allowlisted WSL command returned a non-zero status",
                returncode=completed.returncode,
            )

        try:
            stdout = self._decode(stdout_bytes)
        except UnicodeError:
            return self._failure(
                selected,
                CapabilityStatus.ERROR,
                DiscoveryErrorCode.INVALID_DATA,
                "The WSL command output encoding was invalid",
                returncode=completed.returncode,
            )
        return CommandExecution(
            command_id=selected,
            status=CapabilityStatus.AVAILABLE,
            stdout=stdout,
            returncode=completed.returncode,
        )

    def _argv(
        self,
        command_id: CommandId,
        distro: str | None,
        include_workspace: bool,
    ) -> list[str]:
        if command_id is CommandId.WSL_PROBE_DISTRO:
            validated = validate_distro_name(distro)
            argv = [
                self._wsl_executable,
                "--distribution",
                validated,
                "--exec",
                "python3",
                "-c",
                _PROBE_BOOTSTRAP,
                validated,
            ]
            if include_workspace:
                argv.append("--workspace")
            return argv
        if include_workspace:
            raise ValueError("workspace metadata is only valid for the distro probe")
        if distro is not None:
            raise ValueError("distribution name is only valid for the distro probe")
        return [self._wsl_executable, *_FIXED_COMMANDS[command_id][1:]]

    @staticmethod
    def _decode(output: bytes) -> str:
        if not output:
            return ""
        if output.startswith((b"\xff\xfe", b"\xfe\xff")):
            return output.decode("utf-16", errors="strict")
        if output.startswith(b"\xef\xbb\xbf"):
            return output.decode("utf-8-sig", errors="strict")
        odd_bytes = output[1::2]
        if odd_bytes and odd_bytes.count(0) * 2 >= len(odd_bytes):
            return output.decode("utf-16le", errors="strict")
        return output.decode("utf-8", errors="strict")

    @classmethod
    def _contains_access_denied_code(cls, *outputs: bytes) -> bool:
        for output in outputs:
            try:
                decoded = cls._decode(output)
            except UnicodeError:
                continue
            if _WSL_ACCESS_DENIED_PATTERN.search(decoded):
                return True
        return False

    @staticmethod
    def _failure(
        command_id: CommandId,
        status: CapabilityStatus,
        code: DiscoveryErrorCode,
        message: str,
        *,
        returncode: int | None = None,
        stdout: str = "",
        warnings: tuple[str, ...] = (),
        truncated: bool = False,
        reason_code: str | None = None,
    ) -> CommandExecution:
        return CommandExecution(
            command_id=command_id,
            status=status,
            stdout=stdout,
            returncode=returncode,
            error=DiscoveryError(
                code=code,
                message=message,
                collector="wsl_command_runner",
                source=command_id.value,
                retryable=code is DiscoveryErrorCode.TIMEOUT,
                details={"returncode": returncode, "reason_code": reason_code},
            ),
            warnings=warnings,
            truncated=truncated,
        )


__all__ = [
    "PROBE_MODULE_MISSING_EXIT",
    "CommandExecution",
    "CommandId",
    "WslCommandRunner",
    "validate_distro_name",
]
