"""Bounded host-native activity observation contracts."""

from __future__ import annotations

import sqlite3
import struct
import threading
import time
from datetime import UTC, datetime, timedelta

from agentguard.host_observer import (
    HostAgentObservationTarget,
    HostNativeObservationPlan,
    HostNativeObserver,
    HostObservationEngine,
    ObservedProcess,
    ObservedWorkspaceActivity,
    VerifiedWorkspaceWatch,
    parse_windows_notify_buffer,
)

NOW = datetime(2026, 8, 26, 2, 0, tzinfo=UTC)
ROOT_CREATED = NOW - timedelta(minutes=5)


def _target() -> HostAgentObservationTarget:
    return HostAgentObservationTarget(
        agent_ref="external-agent-codex",
        agent_event_id="discovery-codex",
        process_instance_id="process-codex",
        pid=101,
        create_time=ROOT_CREATED,
        execution_domain_id="windows-native",
    )


def _root() -> ObservedProcess:
    return ObservedProcess(
        pid=101,
        parent_pid=50,
        create_time=ROOT_CREATED,
        executable_basename="codex.exe",
    )


def test_process_existence_establishes_baseline_without_claiming_activity():
    engine = HostObservationEngine((_target(),))

    events = engine.observe_processes((_root(),), observed_at=NOW)

    assert events == ()


def test_admitted_agent_child_start_and_exit_emit_bounded_activity():
    engine = HostObservationEngine((_target(),))
    engine.observe_processes((_root(),), observed_at=NOW)
    child = ObservedProcess(
        pid=202,
        parent_pid=101,
        create_time=NOW + timedelta(seconds=1),
        executable_basename="git.exe",
    )

    started = engine.observe_processes(
        (_root(), child), observed_at=NOW + timedelta(seconds=1)
    )
    exited = engine.observe_processes(
        (_root(),), observed_at=NOW + timedelta(seconds=2)
    )

    assert [event.event_type.value for event in started] == ["PROCESS_STARTED"]
    assert [event.event_type.value for event in exited] == ["PROCESS_EXITED"]
    assert started[0].subject_ref == "external-agent-codex"
    assert started[0].evidence_refs == ("discovery-codex",)
    assert started[0].payload_safe == {
        "activity_kind": "CHILD_PROCESS",
        "agent_ref": "external-agent-codex",
        "executable_basename": "git.exe",
        "process_ref": started[0].payload_safe["process_ref"],
        "reason_code": "AGENT_CHILD_PROCESS_STARTED",
    }
    assert len(started[0].payload_safe["process_ref"]) == 64
    assert "argv" not in repr(started[0].payload_safe).casefold()
    assert "environment" not in repr(started[0].payload_safe).casefold()
    assert exited[0].payload_safe["reason_code"] == "AGENT_CHILD_PROCESS_EXITED"


def test_unrelated_process_activity_is_not_attributed_to_admitted_agent():
    engine = HostObservationEngine((_target(),))
    engine.observe_processes((_root(),), observed_at=NOW)
    unrelated = ObservedProcess(
        pid=303,
        parent_pid=50,
        create_time=NOW + timedelta(seconds=1),
        executable_basename="python.exe",
    )

    assert (
        engine.observe_processes(
            (_root(), unrelated), observed_at=NOW + timedelta(seconds=1)
        )
        == ()
    )


def test_verified_workspace_activity_is_metadata_only_and_not_agent_causality():
    watch = VerifiedWorkspaceWatch(
        workspace_id="workspace-123",
        execution_domain_id="windows-native",
        root_digest="sha256:" + "a" * 64,
        binding_event_id="workspace-scope-123",
        agent_refs=("external-agent-codex",),
    )
    engine = HostObservationEngine((_target(),), workspace=watch)

    event = engine.observe_workspace_activity(
        ObservedWorkspaceActivity(action="MODIFY", relative_path="src/secret.py"),
        observed_at=NOW,
    )

    assert event is not None
    assert event.event_type.value == "WORKSPACE_ACTIVITY_OBSERVED"
    assert event.subject_ref == "workspace-123"
    assert event.payload_safe == {
        "action": "MODIFY",
        "agent_refs": ["external-agent-codex"],
        "attribution": "WORKSPACE_SCOPE_ACTIVITY_NOT_AGENT_CAUSAL",
        "reason_code": "WORKSPACE_ACTIVITY_OBSERVED",
        "target_ref_digest": event.payload_safe["target_ref_digest"],
        "workspace_id": "workspace-123",
    }
    encoded = repr(event.payload_safe)
    assert "secret.py" not in encoded
    assert "src/" not in encoded


def test_workspace_activity_without_verified_binding_fails_closed():
    engine = HostObservationEngine((_target(),))

    assert (
        engine.observe_workspace_activity(
            ObservedWorkspaceActivity(action="CREATE", relative_path="new.txt"),
            observed_at=NOW,
        )
        is None
    )


class _ProcessSource:
    def __init__(self) -> None:
        self.processes = (_root(),)
        self.sampled = threading.Event()

    def snapshot(self):
        self.sampled.set()
        return self.processes


def _wait_for_event(database_path, event_type: str) -> tuple | None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with sqlite3.connect(database_path) as connection:
            row = connection.execute(
                "SELECT event_type, subject_ref, payload_safe_json "
                "FROM evidence_ledger_events WHERE event_type = ?",
                (event_type,),
            ).fetchone()
        if row is not None:
            return row
        time.sleep(0.01)
    return None


def test_product_owned_observer_lifecycle_persists_real_process_deltas(tmp_path):
    from agentguard.storage.db import StateDB

    database_path = tmp_path / "state.db"
    database = StateDB(database_path)
    database.connect()
    database.close()
    source = _ProcessSource()
    observer = HostNativeObserver(
        database_path,
        process_source=source,
        poll_interval=0.01,
        windows=True,
    )
    observer.update(HostNativeObservationPlan(targets=(_target(),)))

    observer.start()
    try:
        assert source.sampled.wait(2)
        source.processes = (
            _root(),
            ObservedProcess(
                pid=404,
                parent_pid=101,
                create_time=NOW,
                executable_basename="cargo.exe",
            ),
        )
        row = _wait_for_event(database_path, "PROCESS_STARTED")
    finally:
        observer.stop()

    assert row is not None
    assert row[0:2] == ("PROCESS_STARTED", "external-agent-codex")
    assert observer.running is False


def test_observer_reconciles_when_target_exits_or_known_launcher_appears(tmp_path):
    from agentguard.storage.db import StateDB

    database_path = tmp_path / "state.db"
    database = StateDB(database_path)
    database.connect()
    database.close()
    source = _ProcessSource()
    reconciled: list[str] = []
    observer = HostNativeObserver(
        database_path,
        process_source=source,
        poll_interval=0.01,
        windows=True,
    )
    observer.update(HostNativeObservationPlan(targets=(_target(),)))
    observer.configure_reconciliation(
        lambda: reconciled.append("refresh"),
        basenames={"codex.exe", "node.exe"},
    )

    observer.start()
    try:
        assert source.sampled.wait(2)
        assert reconciled == []
        source.processes = ()
        deadline = time.monotonic() + 2
        while len(reconciled) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert reconciled == ["refresh"]

        source.processes = (
            ObservedProcess(
                pid=505,
                parent_pid=50,
                create_time=NOW + timedelta(seconds=5),
                executable_basename="codex.exe",
            ),
        )
        deadline = time.monotonic() + 2
        while len(reconciled) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        observer.stop()

    assert reconciled == ["refresh", "refresh"]


def _notify_record(action: int, name: str, next_offset: int = 0) -> bytes:
    encoded = name.encode("utf-16-le")
    return struct.pack("<III", next_offset, action, len(encoded)) + encoded


def test_windows_notify_parser_pairs_rename_and_keeps_metadata_only():
    old = _notify_record(4, "old.py")
    padding = b"\x00" * ((4 - len(old) % 4) % 4)
    old = _notify_record(4, "old.py", len(old) + len(padding)) + padding
    raw = old + _notify_record(5, "new.py")

    activities = parse_windows_notify_buffer(raw)

    assert activities == (
        ObservedWorkspaceActivity(action="RENAME", relative_path="new.py"),
    )
