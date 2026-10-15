"""Strictly read-only psutil backend for bounded local process discovery."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, TypeVar

from ..application_probe import entry_from_argv

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

# The collector re-reads metadata around its PID-reuse checks. Do not fetch and
# discard the same expensive Windows metadata during enumeration.
_PROCESS_ITER_ATTRS = ("pid",)
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
        self._prefetched_create_time: float | None = None
        self._prefetched_time_pending = False
        self._parent_map: Mapping[int, int] | None = None

    def parent_pid(self) -> int | None:
        if (
            self._parent_map is not None
            and self._prefetched_create_time is not None
            and not self._prefetched_time_pending
            and self.pid in self._parent_map
        ):
            return int(self._parent_map[self.pid])
        return int(self._read(lambda process: process.ppid()))

    def executable_basename(self) -> str | None:
        value = self._read(lambda process: process.name())
        if value is None:
            return None
        return str(value).replace("\\", "/").rsplit("/", 1)[-1]

    def create_time(self) -> float:
        if self._prefetched_time_pending:
            self._prefetched_time_pending = False
            assert self._prefetched_create_time is not None
            return self._prefetched_create_time
        return float(self._read(lambda process: process.create_time()))

    def _prefetch_create_time(self) -> bool:
        try:
            self._prefetched_create_time = float(
                self._read(lambda process: process.create_time())
            )
        except (ProcessLookupError, ProcessZombieError, ProcessAccessDeniedError,
                ProcessCollectorFailure, NotImplementedError):
            return False
        self._prefetched_time_pending = True
        return True

    def cwd(self) -> str | None:
        value = self._read(lambda process: process.cwd())
        return None if value is None else str(value)

    def fixed_boolean_facts(self) -> Mapping[str, bool]:
        if getattr(self._psutil, "WINDOWS", False):
            # Windows status adds no fixed fact; the collector's final fresh
            # creation-time read still verifies exit and PID reuse.
            if not bool(self._read(lambda process: process.is_running())):
                raise ProcessLookupError from None
            return {}
        status = self._read(lambda process: process.status())
        if status == getattr(self._psutil, "STATUS_ZOMBIE", object()):
            raise ProcessZombieError from None
        if not bool(self._read(lambda process: process.is_running())):
            raise ProcessLookupError from None
        return {}

    def application_metadata(self) -> Mapping[str, str | None]:
        """Keep only executable/entry identity and a fixed Electron role."""
        try:
            executable = self._read(lambda process: process.exe())
        except (ProcessLookupError, ProcessAccessDeniedError, ProcessZombieError,
                ProcessCollectorFailure, NotImplementedError):
            executable = None
        path = str(executable) if executable else None
        basename = path.replace("\\", "/").rsplit("/", 1)[-1].casefold() if path else ""
        parsed = {"entry_path": None, "electron_role": None}
        if basename in {"node", "node.exe", "python", "python.exe", "python3", "python3.exe",
                        "zcode", "zcode.exe"}:
            try:
                arguments = self._read(lambda process: process.cmdline())
            except (ProcessLookupError, ProcessAccessDeniedError, ProcessZombieError,
                    ProcessCollectorFailure, NotImplementedError):
                arguments = ()
            parsed = entry_from_argv(path or basename, arguments)
            if parsed["entry_path"] is None and basename not in {"zcode", "zcode.exe"}:
                try:
                    parsed = entry_from_argv(path or basename, arguments, cwd=self.cwd())
                except (ProcessLookupError, ProcessAccessDeniedError, ProcessZombieError,
                        ProcessCollectorFailure, NotImplementedError):
                    pass
        return {"path": path, **parsed}

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
        handles = tuple(
            PsutilProcessHandle(pid, psutil_module=self._psutil)
            for pid in sorted(candidate_pids)
        )
        parent_map_reader = getattr(
            getattr(self._psutil, "_psplatform", None), "ppid_map", None
        )
        if getattr(self._psutil, "WINDOWS", False) and callable(parent_map_reader):
            prefetched = [handle for handle in handles if handle._prefetch_create_time()]
            try:
                parent_map = parent_map_reader()
            except Exception:  # noqa: BLE001 - optional optimization; individual reads remain
                parent_map = None
            if isinstance(parent_map, Mapping):
                parent_map = dict(parent_map)
                for handle in prefetched:
                    handle._parent_map = parent_map
        return handles

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
