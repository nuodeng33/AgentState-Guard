"""Fixed-operation UAC bridge for the product-owned Windows firewall helper."""

from __future__ import annotations

import ctypes
import ipaddress
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .network import is_rfc1918_ipv4

_HELPER_ENV = "ASG_DESKTOP_EXECUTABLE"
_EXIT_REASONS = {
    10: "DEVICE_FIREWALL_SCOPE_INVALID",
    20: "DEVICE_FIREWALL_ELEVATION_UNAVAILABLE",
    21: "DEVICE_FIREWALL_SCOPE_CHANGED",
    30: "DEVICE_FIREWALL_MUTATION_FAILED",
}


class ElevationLaunchError(OSError):
    def __init__(self, error_code: int) -> None:
        super().__init__(error_code, "Windows elevation launch failed")
        self.error_code = error_code


@dataclass(frozen=True)
class ElevationResult:
    ok: bool
    reason_code: str
    exit_code: int | None = None


class WindowsFirewallElevationRunner:
    """Invoke only the installed app's fixed firewall helper mode."""

    def __init__(
        self,
        *,
        helper_path: Path | None = None,
        sidecar_path: Path | None = None,
        launcher: Callable[[Path, tuple[str, ...]], int] | None = None,
    ) -> None:
        configured = helper_path or (
            Path(value) if (value := os.environ.get(_HELPER_ENV)) else None
        )
        self._helper_path = configured
        self._sidecar_path = sidecar_path or Path(sys.executable)
        self._launcher = launcher or _shell_execute_elevated

    def apply(self, address: str, prefix_length: int) -> ElevationResult:
        return self._run("apply", address, prefix_length)

    def remove(self, address: str, prefix_length: int) -> ElevationResult:
        return self._run("remove", address, prefix_length)

    def _run(
        self,
        operation: str,
        address: str,
        prefix_length: int,
    ) -> ElevationResult:
        if operation not in {"apply", "remove"} or not _valid_scope(
            address, prefix_length
        ):
            return ElevationResult(False, "DEVICE_FIREWALL_SCOPE_INVALID")
        helper = self._validated_helper_path()
        if helper is None:
            return ElevationResult(False, "DEVICE_FIREWALL_ELEVATION_UNAVAILABLE")
        arguments = (
            "--asg-firewall-helper",
            operation,
            "--address",
            address,
            "--prefix",
            str(prefix_length),
        )
        try:
            exit_code = self._launcher(helper, arguments)
        except ElevationLaunchError as exc:
            reason = (
                "DEVICE_FIREWALL_ELEVATION_DECLINED"
                if exc.error_code == 1223
                else "DEVICE_FIREWALL_ELEVATION_FAILED"
            )
            return ElevationResult(False, reason, exc.error_code)
        except OSError:
            return ElevationResult(False, "DEVICE_FIREWALL_ELEVATION_FAILED")
        if exit_code == 0:
            reason = (
                "DEVICE_FIREWALL_APPLIED"
                if operation == "apply"
                else "DEVICE_FIREWALL_REMOVED"
            )
            return ElevationResult(True, reason, 0)
        return ElevationResult(
            False,
            _EXIT_REASONS.get(exit_code, "DEVICE_FIREWALL_ELEVATION_FAILED"),
            exit_code,
        )

    def _validated_helper_path(self) -> Path | None:
        if self._helper_path is None:
            return None
        try:
            helper = self._helper_path.resolve(strict=True)
            sidecar = self._sidecar_path.resolve(strict=True)
            helper_info = self._helper_path.lstat()
        except OSError:
            return None
        if (
            not helper.is_file()
            or self._helper_path.is_symlink()
            or helper_info.st_size <= 0
            or helper.suffix.casefold() != ".exe"
            or helper.parent != sidecar.parent
        ):
            return None
        return helper


def _valid_scope(address: str, prefix_length: int) -> bool:
    if (
        isinstance(prefix_length, bool)
        or not isinstance(prefix_length, int)
        or not 1 <= prefix_length <= 30
        or not is_rfc1918_ipv4(address)
    ):
        return False
    try:
        network = ipaddress.ip_network(
            f"{address}/{prefix_length}", strict=False
        )
    except ValueError:
        return False
    return isinstance(network, ipaddress.IPv4Network)


def _shell_execute_elevated(helper: Path, arguments: tuple[str, ...]) -> int:
    if os.name != "nt":
        raise ElevationLaunchError(2)
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    parameters = subprocess.list2cmdline(list(arguments))
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = str(helper)
    info.lpParameters = parameters
    info.lpDirectory = str(helper.parent)
    info.nShow = 0
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        raise ElevationLaunchError(ctypes.get_last_error())
    if not info.hProcess:
        raise ElevationLaunchError(6)
    try:
        wait = kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        if wait != 0:
            raise ElevationLaunchError(int(wait))
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code)):
            raise ElevationLaunchError(ctypes.get_last_error())
        return int(exit_code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


__all__ = [
    "ElevationLaunchError",
    "ElevationResult",
    "WindowsFirewallElevationRunner",
]
