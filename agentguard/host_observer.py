"""Bounded host-native activity observation over admitted Agent instances.

The pure engine in this module never performs admission. It accepts only
server-owned targets produced by Product Discovery, establishes a baseline,
and emits activity only for later process-tree changes or a verified workspace
watch. Raw argv, environment values, absolute paths, and file contents never
enter the Evidence Ledger.
"""

from __future__ import annotations

import hashlib
import os
import queue
import sqlite3
import struct
import threading
from collections.abc import Callable, Iterable
from ctypes import wintypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Protocol

from agentguard.evidence.ledger import EvidenceLedger, verify_ledger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.storage.db import StateDB

_WORKSPACE_ACTIONS = frozenset({"CREATE", "MODIFY", "RENAME", "DELETE"})
_MAX_TRACKED_PROCESSES = 4096


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("HOST_OBSERVER_TIME_INVALID")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class HostAgentObservationTarget:
    """Private authority linking one admitted Agent to one OS process instance."""

    agent_ref: str
    agent_event_id: str
    process_instance_id: str
    pid: int
    create_time: datetime
    execution_domain_id: str

    def __post_init__(self) -> None:
        if self.pid < 0:
            raise ValueError("HOST_OBSERVER_PID_INVALID")
        object.__setattr__(self, "create_time", _utc(self.create_time))


@dataclass(frozen=True)
class ObservedProcess:
    """Privacy-bounded process fact used only for tree-diff observation."""

    pid: int
    parent_pid: int | None
    create_time: datetime
    executable_basename: str | None = None

    def __post_init__(self) -> None:
        if self.pid < 0 or (self.parent_pid is not None and self.parent_pid < 0):
            raise ValueError("HOST_OBSERVER_PID_INVALID")
        basename = self.executable_basename
        if basename is not None:
            if "/" in basename or "\\" in basename:
                raise ValueError("HOST_OBSERVER_BASENAME_REQUIRED")
            basename = basename[:64]
        object.__setattr__(self, "create_time", _utc(self.create_time))
        object.__setattr__(self, "executable_basename", basename)

    @property
    def key(self) -> tuple[int, datetime]:
        return self.pid, self.create_time


@dataclass(frozen=True)
class VerifiedWorkspaceWatch:
    """Server-owned workspace authority safe to hand to a metadata watcher."""

    workspace_id: str
    execution_domain_id: str
    root_digest: str
    binding_event_id: str
    agent_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class HostWorkspaceObservationTarget:
    workspace_id: str
    execution_domain_id: str
    root_digest: str
    binding_event_id: str
    root_path: Path
    agent_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        root = Path(self.root_path)
        if not root.is_absolute():
            raise ValueError("HOST_OBSERVER_WORKSPACE_ROOT_INVALID")
        object.__setattr__(self, "root_path", root)

    def public_watch(self) -> VerifiedWorkspaceWatch:
        return VerifiedWorkspaceWatch(
            workspace_id=self.workspace_id,
            execution_domain_id=self.execution_domain_id,
            root_digest=self.root_digest,
            binding_event_id=self.binding_event_id,
            agent_refs=self.agent_refs,
        )


@dataclass(frozen=True)
class HostNativeObservationPlan:
    targets: tuple[HostAgentObservationTarget, ...] = ()
    workspace: HostWorkspaceObservationTarget | None = None


@dataclass(frozen=True)
class ObservedWorkspaceActivity:
    action: str
    relative_path: str

    def __post_init__(self) -> None:
        action = self.action.upper()
        if action not in _WORKSPACE_ACTIONS:
            raise ValueError("HOST_OBSERVER_WORKSPACE_ACTION_INVALID")
        path = self.relative_path.replace("\\", "/")
        logical = PurePath(path)
        if not path or logical.is_absolute() or ".." in logical.parts:
            raise ValueError("HOST_OBSERVER_WORKSPACE_REFERENCE_INVALID")
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "relative_path", path)


class HostObservationEngine:
    """Stateful diff engine; process existence alone is only a baseline."""

    def __init__(
        self,
        targets: Iterable[HostAgentObservationTarget] = (),
        *,
        workspace: VerifiedWorkspaceWatch | None = None,
    ) -> None:
        self._targets: dict[str, HostAgentObservationTarget] = {
            item.agent_ref: item for item in targets
        }
        self._workspace = workspace
        self._known: dict[str, dict[tuple[int, datetime], ObservedProcess]] = {}

    def update(
        self,
        targets: Iterable[HostAgentObservationTarget],
        *,
        workspace: VerifiedWorkspaceWatch | None,
    ) -> None:
        replacement = {item.agent_ref: item for item in targets}
        retained: dict[str, dict[tuple[int, datetime], ObservedProcess]] = {}
        for agent_ref, target in replacement.items():
            previous = self._targets.get(agent_ref)
            if previous == target and agent_ref in self._known:
                retained[agent_ref] = self._known[agent_ref]
        self._targets = replacement
        self._known = retained
        self._workspace = workspace

    def observe_processes(
        self,
        processes: Iterable[ObservedProcess],
        *,
        observed_at: datetime,
    ) -> tuple[EvidenceEvent, ...]:
        observed_at = _utc(observed_at)
        snapshot = tuple(processes)[:_MAX_TRACKED_PROCESSES]
        by_pid = {item.pid: item for item in snapshot}
        events: list[EvidenceEvent] = []
        for agent_ref, target in sorted(self._targets.items()):
            root = by_pid.get(target.pid)
            root_matches = root is not None and root.create_time == target.create_time
            visible = (
                self._descendants(root, snapshot)
                if root_matches and root is not None
                else {}
            )
            prior = self._known.get(agent_ref)
            if prior is None:
                self._known[agent_ref] = visible
                continue
            started = sorted(set(visible) - set(prior))
            exited = sorted(set(prior) - set(visible))
            for key in started:
                process = visible[key]
                events.append(
                    self._process_event(
                        target,
                        process,
                        EventType.PROCESS_STARTED,
                        "AGENT_CHILD_PROCESS_STARTED",
                        observed_at,
                    )
                )
            for key in exited:
                process = prior[key]
                is_root = key == (target.pid, target.create_time)
                events.append(
                    self._process_event(
                        target,
                        process,
                        EventType.PROCESS_EXITED,
                        (
                            "ADMITTED_AGENT_PROCESS_EXITED"
                            if is_root
                            else "AGENT_CHILD_PROCESS_EXITED"
                        ),
                        observed_at,
                    )
                )
            self._known[agent_ref] = visible
        return tuple(events)

    def observe_workspace_activity(
        self,
        activity: ObservedWorkspaceActivity,
        *,
        observed_at: datetime,
    ) -> EvidenceEvent | None:
        workspace = self._workspace
        if workspace is None:
            return None
        observed_at = _utc(observed_at)
        target_digest = hashlib.sha256(
            (
                f"workspace-activity-v1\x1f{workspace.workspace_id}\x1f"
                f"{activity.relative_path.casefold()}"
            ).encode()
        ).hexdigest()
        payload = {
            "action": activity.action,
            "agent_refs": sorted(set(workspace.agent_refs)),
            "attribution": "WORKSPACE_SCOPE_ACTIVITY_NOT_AGENT_CAUSAL",
            "reason_code": "WORKSPACE_ACTIVITY_OBSERVED",
            "target_ref_digest": target_digest,
            "workspace_id": workspace.workspace_id,
        }
        event_id = self._event_id(
            "workspace",
            workspace.workspace_id,
            target_digest,
            activity.action,
            observed_at.isoformat(),
        )
        return EvidenceEvent(
            schema_version=1,
            event_id=event_id,
            recorded_at=observed_at,
            observed_at=observed_at,
            event_family=EventFamily.CHANGE,
            event_type=EventType.WORKSPACE_ACTIVITY_OBSERVED,
            source="host-native-observer",
            result="OBSERVED",
            execution_domain_id=workspace.execution_domain_id,
            supervision_session_id=None,
            transaction_id=None,
            checkpoint_id=None,
            subject_ref=workspace.workspace_id,
            evidence_refs=(workspace.binding_event_id,),
            payload_safe=payload,
        )

    @staticmethod
    def _descendants(
        root: ObservedProcess,
        snapshot: tuple[ObservedProcess, ...],
    ) -> dict[tuple[int, datetime], ObservedProcess]:
        visible = {root.key: root}
        descendant_pids = {root.pid}
        pending = list(snapshot)
        for _pass in range(min(len(snapshot), _MAX_TRACKED_PROCESSES)):
            changed = False
            remainder: list[ObservedProcess] = []
            for item in pending:
                if item.pid == root.pid:
                    continue
                if item.parent_pid in descendant_pids:
                    visible[item.key] = item
                    descendant_pids.add(item.pid)
                    changed = True
                else:
                    remainder.append(item)
            if not changed:
                break
            pending = remainder
        return visible

    def _process_event(
        self,
        target: HostAgentObservationTarget,
        process: ObservedProcess,
        event_type: EventType,
        reason_code: str,
        observed_at: datetime,
    ) -> EvidenceEvent:
        process_ref = hashlib.sha256(
            (
                f"host-process-v1\x1f{target.agent_ref}\x1f{process.pid}\x1f"
                f"{process.create_time.isoformat()}"
            ).encode()
        ).hexdigest()
        payload = {
            "activity_kind": (
                "AGENT_PROCESS_LIFECYCLE"
                if process.pid == target.pid
                and process.create_time == target.create_time
                else "CHILD_PROCESS"
            ),
            "agent_ref": target.agent_ref,
            "executable_basename": process.executable_basename,
            "process_ref": process_ref,
            "reason_code": reason_code,
        }
        return EvidenceEvent(
            schema_version=1,
            event_id=self._event_id(
                event_type.value,
                target.agent_ref,
                process_ref,
                observed_at.isoformat(),
            ),
            recorded_at=observed_at,
            observed_at=observed_at,
            event_family=EventFamily.SUPERVISION,
            event_type=event_type,
            source="host-native-observer",
            result="OBSERVED",
            execution_domain_id=target.execution_domain_id,
            supervision_session_id=None,
            transaction_id=None,
            checkpoint_id=None,
            subject_ref=target.agent_ref,
            evidence_refs=(target.agent_event_id,),
            payload_safe=payload,
        )

    @staticmethod
    def _event_id(*parts: str) -> str:
        material = "\x1f".join(parts).encode("utf-8")
        return f"host-observer-{hashlib.sha256(material).hexdigest()[:32]}"


class HostProcessSource(Protocol):
    def snapshot(self) -> Iterable[ObservedProcess]: ...


class _PsutilHostProcessSource:
    """Read only the four bounded facts required for process-tree diffs."""

    def snapshot(self) -> tuple[ObservedProcess, ...]:
        import psutil

        processes: list[ObservedProcess] = []
        for item in psutil.process_iter(attrs=("pid", "ppid", "name", "create_time")):
            try:
                info = item.info
                created = datetime.fromtimestamp(float(info["create_time"]), tz=UTC)
                name = info.get("name")
                processes.append(
                    ObservedProcess(
                        pid=int(info["pid"]),
                        parent_pid=(
                            int(info["ppid"]) if info.get("ppid") is not None else None
                        ),
                        create_time=created,
                        executable_basename=(
                            os.path.basename(str(name))
                            if isinstance(name, str)
                            else None
                        ),
                    )
                )
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except (KeyError, TypeError, ValueError, OSError):
                continue
        return tuple(processes[:_MAX_TRACKED_PROCESSES])


def parse_windows_notify_buffer(raw: bytes) -> tuple[ObservedWorkspaceActivity, ...]:
    """Parse one FILE_NOTIFY_INFORMATION buffer and pair rename records."""

    records: list[tuple[int, str]] = []
    offset = 0
    while offset + 12 <= len(raw):
        next_offset, action, name_bytes = struct.unpack_from("<III", raw, offset)
        start = offset + 12
        end = start + name_bytes
        if name_bytes % 2 or end > len(raw):
            break
        try:
            name = raw[start:end].decode("utf-16-le")
        except UnicodeDecodeError:
            name = ""
        if name:
            records.append((action, name))
        if next_offset == 0:
            break
        if next_offset < 12 or offset + next_offset > len(raw):
            break
        offset += next_offset

    activities: list[ObservedWorkspaceActivity] = []
    index = 0
    while index < len(records):
        action, name = records[index]
        if action == 4 and index + 1 < len(records) and records[index + 1][0] == 5:
            activities.append(
                ObservedWorkspaceActivity(
                    action="RENAME", relative_path=records[index + 1][1]
                )
            )
            index += 2
            continue
        mapped = {1: "CREATE", 2: "DELETE", 3: "MODIFY", 5: "RENAME"}.get(action)
        if mapped is not None:
            activities.append(
                ObservedWorkspaceActivity(action=mapped, relative_path=name)
            )
        index += 1
    return tuple(activities)


class _WindowsDirectoryChangesSource:
    """Recursive ReadDirectoryChangesW source with a bounded metadata queue."""

    _FILE_LIST_DIRECTORY = 0x0001
    _SHARE = 0x00000001 | 0x00000002 | 0x00000004
    _OPEN_EXISTING = 3
    _BACKUP_SEMANTICS = 0x02000000
    _FILTER = 0x00000001 | 0x00000002 | 0x00000008 | 0x00000010 | 0x00000040

    def __init__(self, root: Path) -> None:
        import ctypes

        self._ctypes = ctypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateFileW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        self._kernel32.CreateFileW.restype = wintypes.HANDLE
        self._kernel32.ReadDirectoryChangesW.argtypes = (
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
            wintypes.LPVOID,
        )
        self._kernel32.ReadDirectoryChangesW.restype = wintypes.BOOL
        self._handle = self._kernel32.CreateFileW(
            str(root),
            self._FILE_LIST_DIRECTORY,
            self._SHARE,
            None,
            self._OPEN_EXISTING,
            self._BACKUP_SEMANTICS,
            None,
        )
        invalid = wintypes.HANDLE(-1).value
        if self._handle in (None, invalid):
            raise OSError("READ_DIRECTORY_CHANGES_OPEN_FAILED")
        self._events: queue.Queue[ObservedWorkspaceActivity] = queue.Queue(maxsize=2048)
        self._closed = threading.Event()
        self._thread = threading.Thread(
            target=self._read_loop,
            name="asg-workspace-watch",
            daemon=True,
        )
        self._thread.start()

    def poll(self) -> tuple[ObservedWorkspaceActivity, ...]:
        events: list[ObservedWorkspaceActivity] = []
        while len(events) < 256:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        return tuple(events)

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self._kernel32.CancelIoEx(self._handle, None)
        except (AttributeError, OSError):
            pass
        self._kernel32.CloseHandle(self._handle)
        self._thread.join(timeout=1)

    def _read_loop(self) -> None:
        buffer = self._ctypes.create_string_buffer(65536)
        returned = wintypes.DWORD()
        while not self._closed.is_set():
            ok = self._kernel32.ReadDirectoryChangesW(
                self._handle,
                buffer,
                len(buffer),
                True,
                self._FILTER,
                self._ctypes.byref(returned),
                None,
                None,
            )
            if not ok:
                break
            for activity in parse_windows_notify_buffer(buffer.raw[: returned.value]):
                try:
                    self._events.put_nowait(activity)
                except queue.Full:
                    break


class _WorkspaceSource(Protocol):
    def poll(self) -> Iterable[ObservedWorkspaceActivity]: ...
    def close(self) -> None: ...


class HostNativeObserver:
    """Product-owned thread that persists bounded host activity to the Ledger."""

    def __init__(
        self,
        database_path: Path,
        *,
        process_source: HostProcessSource | None = None,
        workspace_source_factory: Callable[[Path], _WorkspaceSource] | None = None,
        poll_interval: float = 0.2,
        windows: bool | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database_path = Path(database_path)
        self._process_source = process_source or _PsutilHostProcessSource()
        self._workspace_source_factory = (
            workspace_source_factory or _WindowsDirectoryChangesSource
        )
        self._poll_interval = max(float(poll_interval), 0.01)
        self._windows = os.name == "nt" if windows is None else bool(windows)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._engine = HostObservationEngine()
        self._plan = HostNativeObservationPlan()
        self._applied_plan: HostNativeObservationPlan | None = None
        self._plan_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._workspace_source: _WorkspaceSource | None = None
        self._reconcile: Callable[[], object] | None = None
        self._reconcile_basenames: frozenset[str] = frozenset()
        self._global_known: set[tuple[int, datetime]] | None = None
        self._reconciled_missing: set[str] = set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def update(self, plan: HostNativeObservationPlan) -> None:
        with self._plan_lock:
            self._plan = plan
            self._reconciled_missing.intersection_update(
                item.process_instance_id for item in plan.targets
            )

    def configure_reconciliation(
        self,
        callback: Callable[[], object],
        *,
        basenames: Iterable[str],
    ) -> None:
        """Re-run admission only after a bounded process lifecycle trigger."""

        with self._plan_lock:
            self._reconcile = callback
            self._reconcile_basenames = frozenset(
                item.casefold() for item in basenames if item
            )

    def start(self) -> None:
        if not self._windows or self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="asg-host-observer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None
        self._close_workspace_source()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._apply_plan()
            try:
                processes = tuple(self._process_source.snapshot())
                events = self._engine.observe_processes(
                    processes,
                    observed_at=self._clock(),
                )
                self._persist(events)
                self._maybe_reconcile(processes)
            except (OSError, RuntimeError, TypeError, ValueError):
                pass
            source = self._workspace_source
            if source is not None:
                try:
                    activities = tuple(source.poll())
                except (OSError, RuntimeError, TypeError, ValueError):
                    activities = ()
                workspace_events = tuple(
                    event
                    for activity in activities
                    if (
                        event := self._engine.observe_workspace_activity(
                            activity,
                            observed_at=self._clock(),
                        )
                    )
                    is not None
                )
                self._persist(workspace_events)
            self._stop.wait(self._poll_interval)

    def _maybe_reconcile(self, processes: tuple[ObservedProcess, ...]) -> None:
        current_keys = {item.key for item in processes}
        with self._plan_lock:
            callback = self._reconcile
            basenames = self._reconcile_basenames
            targets = self._plan.targets
            previous_keys = self._global_known
            self._global_known = current_keys
            newly_missing = {
                target.process_instance_id
                for target in targets
                if (target.pid, target.create_time) not in current_keys
                and target.process_instance_id not in self._reconciled_missing
            }
            self._reconciled_missing.update(newly_missing)
        if callback is None or previous_keys is None:
            return
        relevant_started = any(
            item.key not in previous_keys
            and item.executable_basename is not None
            and item.executable_basename.casefold() in basenames
            for item in processes
        )
        if not newly_missing and not relevant_started:
            return
        try:
            callback()
        except (OSError, RuntimeError, TypeError, ValueError, sqlite3.DatabaseError):
            return

    def _apply_plan(self) -> None:
        with self._plan_lock:
            plan = self._plan
        if plan == self._applied_plan:
            return
        self._close_workspace_source()
        workspace = plan.workspace
        self._engine.update(
            plan.targets,
            workspace=workspace.public_watch() if workspace is not None else None,
        )
        if workspace is not None:
            try:
                self._workspace_source = self._workspace_source_factory(
                    workspace.root_path
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                self._workspace_source = None
        self._applied_plan = plan
        self._persist(self._lifecycle_events(plan.targets))

    def _lifecycle_events(
        self,
        targets: tuple[HostAgentObservationTarget, ...],
    ) -> tuple[EvidenceEvent, ...]:
        observed_at = _utc(self._clock())
        return tuple(
            EvidenceEvent(
                schema_version=1,
                event_id=HostObservationEngine._event_id(
                    "lifecycle", target.agent_ref, target.process_instance_id
                ),
                recorded_at=observed_at,
                observed_at=observed_at,
                event_family=EventFamily.SUPERVISION,
                event_type=EventType.HOST_OBSERVER_LIFECYCLE,
                source="host-native-observer",
                result="AVAILABLE",
                execution_domain_id=target.execution_domain_id,
                supervision_session_id=None,
                transaction_id=None,
                checkpoint_id=None,
                subject_ref=target.agent_ref,
                evidence_refs=(target.agent_event_id,),
                payload_safe={
                    "activity_observability": "OBSERVABLE",
                    "agent_ref": target.agent_ref,
                    "reason_code": "HOST_NATIVE_OBSERVER_ACTIVE",
                },
            )
            for target in targets
        )

    def _persist(self, events: tuple[EvidenceEvent, ...]) -> None:
        if not events:
            return
        database = StateDB(self._database_path)
        try:
            database.connect()
            with database.transaction() as connection:
                if verify_ledger(connection):
                    return
                ledger = EvidenceLedger()
                for event in events:
                    exists = connection.execute(
                        "SELECT 1 FROM evidence_ledger_events WHERE event_id = ?",
                        (event.event_id,),
                    ).fetchone()
                    if exists is None:
                        ledger.append(connection, event)
                if verify_ledger(connection):
                    raise RuntimeError("HOST_OBSERVER_LEDGER_INVALID")
        except (OSError, RuntimeError, TypeError, ValueError, sqlite3.DatabaseError):
            return
        finally:
            database.close()

    def _close_workspace_source(self) -> None:
        source = self._workspace_source
        self._workspace_source = None
        if source is not None:
            try:
                source.close()
            except (OSError, RuntimeError):
                pass


__all__ = [
    "HostAgentObservationTarget",
    "HostNativeObservationPlan",
    "HostNativeObserver",
    "HostObservationEngine",
    "HostWorkspaceObservationTarget",
    "ObservedProcess",
    "ObservedWorkspaceActivity",
    "VerifiedWorkspaceWatch",
    "parse_windows_notify_buffer",
]
