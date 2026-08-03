"""Strictly read-only psutil backend for bounded local process discovery."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, TypeVar

try:
    import psutil as _psutil
except ModuleNotFoundError as exc:
    if exc.name != "psutil":
        raise
    _psutil = None

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
