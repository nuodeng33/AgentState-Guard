"""Current-user-only permission proofs for Host-native workspace recovery."""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from agentguard.evidence.canonical import canonical_json

_MODE = re.compile(r"0o[0-7]+")
_WINDOWS_MUTABLE_ATTRIBUTES = 0x0001 | 0x0002 | 0x0004 | 0x0020 | 0x0100 | 0x2000


class PermissionCapabilityError(RuntimeError):
    """A permission fact cannot be proved using the current process token."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class PermissionProof:
    kind: str
    values: Mapping[str, str | int]

    def __post_init__(self) -> None:
        values = dict(self.values)
        if self.kind == "POSIX_MODE":
            if set(values) != {"mode"}:
                raise ValueError("WORKSPACE_PERMISSION_PROOF_INVALID")
            mode = values.get("mode")
            if (
                not isinstance(mode, str)
                or _MODE.fullmatch(mode) is None
                or oct(int(mode, 8)) != mode
            ):
                raise ValueError("WORKSPACE_PERMISSION_PROOF_INVALID")
        elif self.kind == "WINDOWS_ATTRIBUTES_DACL":
            if set(values) != {"attributes", "dacl_sddl"}:
                raise ValueError("WORKSPACE_PERMISSION_PROOF_INVALID")
            attributes = values.get("attributes")
            dacl = values.get("dacl_sddl")
            if (
                not isinstance(attributes, int)
                or isinstance(attributes, bool)
                or attributes < 0
                or attributes & ~_WINDOWS_MUTABLE_ATTRIBUTES
                or not isinstance(dacl, str)
                or not dacl.startswith("D:")
                or len(dacl) > 65_536
            ):
                raise ValueError("WORKSPACE_PERMISSION_PROOF_INVALID")
        else:
            raise ValueError("WORKSPACE_PERMISSION_PROOF_INVALID")
        object.__setattr__(self, "values", MappingProxyType(values))

    @property
    def digest(self) -> str:
        authority = {"kind": self.kind, "values": dict(self.values)}
        return hashlib.sha256(canonical_json(authority).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "values": dict(self.values),
            "digest": self.digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> PermissionProof:
        if not isinstance(value, dict) or set(value) != {"kind", "values", "digest"}:
            raise ValueError("WORKSPACE_PERMISSION_PROOF_INVALID")
        kind = value.get("kind")
        values = value.get("values")
        digest = value.get("digest")
        if not isinstance(kind, str) or not isinstance(values, dict) or not isinstance(
            digest, str
        ):
            raise ValueError("WORKSPACE_PERMISSION_PROOF_INVALID")  # noqa: TRY004
        proof = cls(kind=kind, values=values)
        if not hmac.compare_digest(proof.digest, digest):
            raise ValueError("WORKSPACE_PERMISSION_PROOF_INVALID")
        return proof


class PermissionBackend(Protocol):
    def capture(self, path: Path) -> PermissionProof: ...

    def apply(self, path: Path, proof: PermissionProof) -> None: ...

    def verify(self, path: Path, proof: PermissionProof) -> bool: ...


class PosixPermissionBackend:
    def capture(self, path: Path) -> PermissionProof:
        try:
            info = path.lstat()
        except PermissionError as exc:
            raise PermissionCapabilityError(
                "WORKSPACE_PERMISSION_CAPTURE_DENIED"
            ) from exc
        except OSError as exc:
            raise PermissionCapabilityError(
                "WORKSPACE_PERMISSION_CAPTURE_UNAVAILABLE"
            ) from exc
        if path.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise PermissionCapabilityError("WORKSPACE_PERMISSION_TARGET_UNSUPPORTED")
        return PermissionProof(
            kind="POSIX_MODE",
            values={"mode": oct(stat.S_IMODE(info.st_mode))},
        )

    def apply(self, path: Path, proof: PermissionProof) -> None:
        if proof.kind != "POSIX_MODE":
            raise PermissionCapabilityError("WORKSPACE_PERMISSION_PROOF_MISMATCH")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise PermissionCapabilityError(
                        "WORKSPACE_PERMISSION_TARGET_UNSUPPORTED"
                    )
                os.fchmod(descriptor, int(str(proof.values["mode"]), 8))
            finally:
                os.close(descriptor)
        except PermissionCapabilityError:
            raise
        except PermissionError as exc:
            raise PermissionCapabilityError("WORKSPACE_PERMISSION_APPLY_DENIED") from exc
        except OSError as exc:
            raise PermissionCapabilityError(
                "WORKSPACE_PERMISSION_APPLY_UNAVAILABLE"
            ) from exc

    def verify(self, path: Path, proof: PermissionProof) -> bool:
        try:
            return hmac.compare_digest(self.capture(path).digest, proof.digest)
        except PermissionCapabilityError as exc:
            raise PermissionCapabilityError(
                "WORKSPACE_PERMISSION_VERIFY_UNAVAILABLE"
            ) from exc


class _WindowsSecurityApi(Protocol):
    def get_file_attributes(self, path: Path) -> int: ...

    def set_file_attributes(self, path: Path, attributes: int) -> None: ...

    def get_dacl_sddl(self, path: Path) -> str: ...

    def set_dacl_sddl(self, path: Path, dacl_sddl: str) -> None: ...


class WindowsPermissionBackend:
    def __init__(self, *, api: _WindowsSecurityApi | None = None) -> None:
        self._api = api or _CtypesWindowsSecurityApi()

    def capture(self, path: Path) -> PermissionProof:
        try:
            attributes = self._api.get_file_attributes(path)
            dacl = self._api.get_dacl_sddl(path)
        except PermissionError as exc:
            raise PermissionCapabilityError(
                "WORKSPACE_PERMISSION_CAPTURE_DENIED"
            ) from exc
        except OSError as exc:
            reason = (
                "WORKSPACE_PERMISSION_CAPTURE_DENIED"
                if getattr(exc, "winerror", None) == 5
                else "WORKSPACE_PERMISSION_CAPTURE_UNAVAILABLE"
            )
            raise PermissionCapabilityError(reason) from exc
        return PermissionProof(
            kind="WINDOWS_ATTRIBUTES_DACL",
            values={
                "attributes": attributes & _WINDOWS_MUTABLE_ATTRIBUTES,
                "dacl_sddl": dacl,
            },
        )

    def apply(self, path: Path, proof: PermissionProof) -> None:
        if proof.kind != "WINDOWS_ATTRIBUTES_DACL":
            raise PermissionCapabilityError("WORKSPACE_PERMISSION_PROOF_MISMATCH")
        try:
            current = self._api.get_file_attributes(path)
            attributes = int(proof.values["attributes"])
            self._api.set_file_attributes(
                path,
                (current & ~_WINDOWS_MUTABLE_ATTRIBUTES) | attributes,
            )
            self._api.set_dacl_sddl(path, str(proof.values["dacl_sddl"]))
        except PermissionError as exc:
            raise PermissionCapabilityError("WORKSPACE_PERMISSION_APPLY_DENIED") from exc
        except OSError as exc:
            reason = (
                "WORKSPACE_PERMISSION_APPLY_DENIED"
                if getattr(exc, "winerror", None) == 5
                else "WORKSPACE_PERMISSION_APPLY_UNAVAILABLE"
            )
            raise PermissionCapabilityError(reason) from exc

    def verify(self, path: Path, proof: PermissionProof) -> bool:
        try:
            return hmac.compare_digest(self.capture(path).digest, proof.digest)
        except PermissionCapabilityError as exc:
            raise PermissionCapabilityError(
                "WORKSPACE_PERMISSION_VERIFY_UNAVAILABLE"
            ) from exc


class _CtypesWindowsSecurityApi:
    _SE_FILE_OBJECT = 1
    _DACL_SECURITY_INFORMATION = 0x00000004
    _SDDL_REVISION_1 = 1
    _INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF

    def __init__(self) -> None:
        if os.name != "nt":
            raise PermissionCapabilityError("WORKSPACE_PERMISSION_BACKEND_UNAVAILABLE")
        from ctypes import wintypes

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._kernel32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
        self._kernel32.GetFileAttributesW.restype = wintypes.DWORD
        self._kernel32.SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        self._kernel32.SetFileAttributesW.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p
        self._advapi32.GetNamedSecurityInfoW.argtypes = [
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
        self._advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_wchar_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = (
            wintypes.BOOL
        )
        self._advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
            wintypes.BOOL
        )
        self._advapi32.GetSecurityDescriptorDacl.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.BOOL),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.BOOL),
        ]
        self._advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
        self._advapi32.SetNamedSecurityInfoW.argtypes = [
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self._advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD

    def get_file_attributes(self, path: Path) -> int:
        value = self._kernel32.GetFileAttributesW(str(path))
        if value == self._INVALID_FILE_ATTRIBUTES:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(value)

    def set_file_attributes(self, path: Path, attributes: int) -> None:
        if not self._kernel32.SetFileAttributesW(str(path), attributes):
            raise ctypes.WinError(ctypes.get_last_error())

    def get_dacl_sddl(self, path: Path) -> str:
        descriptor = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        result = self._advapi32.GetNamedSecurityInfoW(
            str(path),
            self._SE_FILE_OBJECT,
            self._DACL_SECURITY_INFORMATION,
            None,
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if result:
            raise ctypes.WinError(result)
        text = ctypes.c_wchar_p()
        try:
            if not self._advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor,
                self._SDDL_REVISION_1,
                self._DACL_SECURITY_INFORMATION,
                ctypes.byref(text),
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            return str(text.value)
        finally:
            if text:
                self._kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
            if descriptor:
                self._kernel32.LocalFree(descriptor)

    def set_dacl_sddl(self, path: Path, dacl_sddl: str) -> None:
        descriptor = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        from ctypes import wintypes

        present = wintypes.BOOL()
        defaulted = wintypes.BOOL()
        if not self._advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            dacl_sddl,
            self._SDDL_REVISION_1,
            ctypes.byref(descriptor),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._advapi32.GetSecurityDescriptorDacl(
                descriptor,
                ctypes.byref(present),
                ctypes.byref(dacl),
                ctypes.byref(defaulted),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not present.value:
                raise OSError("WORKSPACE_PERMISSION_DACL_MISSING")
            result = self._advapi32.SetNamedSecurityInfoW(
                str(path),
                self._SE_FILE_OBJECT,
                self._DACL_SECURITY_INFORMATION,
                None,
                None,
                dacl,
                None,
            )
            if result:
                raise ctypes.WinError(result)
        finally:
            if descriptor:
                self._kernel32.LocalFree(descriptor)


def current_user_permission_backend() -> PermissionBackend:
    return WindowsPermissionBackend() if os.name == "nt" else PosixPermissionBackend()


__all__ = [
    "PermissionBackend",
    "PermissionCapabilityError",
    "PermissionProof",
    "PosixPermissionBackend",
    "WindowsPermissionBackend",
    "current_user_permission_backend",
]
