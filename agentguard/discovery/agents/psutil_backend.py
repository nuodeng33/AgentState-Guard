"""Strictly read-only psutil backend for bounded local process discovery."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import PureWindowsPath
from typing import Any, TypeVar

from agentguard.core.host_tools import windows_bounded_product_identity

try:
    import psutil as _psutil
except ModuleNotFoundError as exc:
    if exc.name != "psutil":
        raise
    _psutil = None

from .launcher_identity import (
    is_package_identity_anchor,
    is_package_wrapper_anchor,
)
from .processes import (
    ProcessAccessDeniedError,
    ProcessBackend,
    ProcessBackendUnavailableError,
    ProcessCollectorFailure,
    ProcessHandle,
    ProcessZombieError,
)

_PROCESS_ITER_ATTRS = ("pid", "ppid", "name", "create_time", "status")
_DEFAULT_PSUTIL = object()
_T = TypeVar("_T")
# Rust std::fs::canonicalize() style verbatim prefix on process image paths.
_VERBATIM_PREFIX = "\\\\?\\"


class PsutilProcessHandle(ProcessHandle):
    """Expose only metadata operations approved by the P3A contract."""

    def __init__(
        self,
        pid: int,
        *,
        psutil_module: Any = _DEFAULT_PSUTIL,
    ) -> None:
        self._psutil = _psutil if psutil_module is _DEFAULT_PSUTIL else psutil_module
        self.pid = int(pid)

    def parent_pid(self) -> int | None:
        return int(self._read(lambda process: process.ppid()))

    def executable_basename(self) -> str | None:
        value = self._read(lambda process: process.name())
        if value is None:
            return None
        return str(value).replace("\\", "/").rsplit("/", 1)[-1]

    def create_time(self) -> float:
        return float(self._read(lambda process: process.create_time()))

    def cwd(self) -> str | None:
        value = self._read(lambda process: process.cwd())
        return None if value is None else str(value)

    def fixed_boolean_facts(self) -> Mapping[str, bool]:
        status = self._read(lambda process: process.status())
        if status == getattr(self._psutil, "STATUS_ZOMBIE", object()):
            raise ProcessZombieError from None
        if not bool(self._read(lambda process: process.is_running())):
            raise ProcessLookupError from None
        return {}

    def _new_process(self) -> Any:
        try:
            return self._psutil.Process(self.pid)
        except Exception as exc:  # noqa: BLE001 - translated below
            self._raise_bounded(exc)

    def _read(
        self,
        reader: Callable[[Any], _T],
    ) -> _T:
        try:
            return reader(self._new_process())
        except Exception as exc:  # noqa: BLE001 - translated below
            self._raise_bounded(exc)

    def _raise_bounded(self, exc: Exception) -> None:
        if isinstance(exc, self._psutil.NoSuchProcess):
            raise ProcessLookupError from None
        if isinstance(exc, self._psutil.ZombieProcess):
            raise ProcessZombieError from None
        if isinstance(exc, self._psutil.AccessDenied):
            raise ProcessAccessDeniedError from None
        if isinstance(exc, NotImplementedError):
            raise NotImplementedError from None
        raise ProcessCollectorFailure from None


class PsutilProcessBackend(ProcessBackend):
    """Enumerate processes visible to the current security context."""

    def __init__(self, *, psutil_module: Any = _DEFAULT_PSUTIL) -> None:
        self._psutil = _psutil if psutil_module is _DEFAULT_PSUTIL else psutil_module

    def iter_processes(self) -> Iterable[ProcessHandle]:
        if self._psutil is None:
            raise ProcessBackendUnavailableError from None
        try:
            processes = iter(
                self._psutil.process_iter(
                    attrs=_PROCESS_ITER_ATTRS,
                    ad_value=None,
                )
            )
        except Exception as exc:  # noqa: BLE001 - enumeration boundary
            self._raise_bounded(exc)

        candidate_pids: set[int] = set()
        while True:
            try:
                process = next(processes)
            except StopIteration:
                break
            except (self._psutil.NoSuchProcess, self._psutil.ZombieProcess):
                continue
            except Exception as exc:  # noqa: BLE001 - enumeration boundary
                self._raise_bounded(exc)
            try:
                candidate_pids.add(int(process.pid))
            except (self._psutil.NoSuchProcess, self._psutil.ZombieProcess):
                continue
            except Exception as exc:  # noqa: BLE001 - enumeration boundary
                self._raise_bounded(exc)
        return tuple(
            PsutilProcessHandle(pid, psutil_module=self._psutil)
            for pid in sorted(candidate_pids)
        )

    def bounded_launcher_anchors(self, pid: int) -> tuple[str, ...]:
        """Best-effort bounded script anchors for launcher identity.

        Although psutil's cmdline is consulted internally as the only OS
        source of script paths, this method returns only bounded, on-disk
        script anchors — never the raw command line, never flags, never
        arguments. Anything that cannot be reduced to a bounded on-disk
        anchor yields no anchors, so identity resolution stays UNKNOWN
        instead of being guessed.
        """
        if self._psutil is None:
            return ()
        try:
            lines = self._psutil.Process(int(pid)).cmdline()
        except Exception:  # noqa: BLE001 - fail closed, identity stays UNKNOWN
            return ()
        if not isinstance(lines, (list, tuple)):
            return ()
        anchors: list[str] = []
        for value in lines:
            if not isinstance(value, str):
                continue
            candidate = value.strip().strip('"')
            if not candidate or candidate.startswith("-"):
                continue
            candidate = candidate.removeprefix(_VERBATIM_PREFIX)
            if is_package_identity_anchor(candidate) or is_package_wrapper_anchor(
                candidate
            ):
                anchors.append(candidate)
                if len(anchors) >= 2:
                    break
        return tuple(anchors)

    def bounded_product_identity(self, pid: int) -> tuple[str, str] | None:
        """Reduce an exact registered PE image to identity plus install root."""
        if self._psutil is None:
            return None
        try:
            process = self._psutil.Process(int(pid))
            executable = process.exe()
        except Exception:  # noqa: BLE001 - identity remains UNKNOWN
            return None
        if not isinstance(executable, str) or not executable:
            return None
        identity = windows_bounded_product_identity(executable)
        if identity is None:
            return None
        if identity in {"PI", "ZCODE"}:
            try:
                parent_pid = int(process.ppid())
                parent_executable = (
                    self._psutil.Process(parent_pid).exe() if parent_pid > 0 else None
                )
            except Exception:  # noqa: BLE001 - parent may have already exited
                if identity == "PI":
                    return None
                parent_executable = None
            if isinstance(parent_executable, str) and parent_executable:
                image = str(
                    PureWindowsPath(executable.removeprefix(_VERBATIM_PREFIX))
                ).casefold()
                parent_image = str(
                    PureWindowsPath(parent_executable.removeprefix(_VERBATIM_PREFIX))
                ).casefold()
                if parent_image == image:
                    return None
        return identity, str(PureWindowsPath(executable).parent)

    def _raise_bounded(self, exc: Exception) -> None:
        if isinstance(exc, self._psutil.NoSuchProcess):
            raise ProcessLookupError from None
        if isinstance(exc, self._psutil.ZombieProcess):
            raise ProcessZombieError from None
        if isinstance(exc, self._psutil.AccessDenied):
            raise ProcessAccessDeniedError from None
        if isinstance(exc, NotImplementedError):
            raise NotImplementedError from None
        raise ProcessCollectorFailure from None


__all__ = [
    "PsutilProcessBackend",
    "PsutilProcessHandle",
]
