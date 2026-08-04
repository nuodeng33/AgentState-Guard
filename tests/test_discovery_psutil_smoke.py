"""Real local psutil smoke tests with no product-specific assumptions."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import psutil

from agentguard.discovery import CapabilityStatus
from agentguard.discovery.agents import (
    ProcessCollector,
    ProcessState,
    PsutilProcessBackend,
    WorkspacePathKind,
)


def _collect_current_processes():
    return ProcessCollector(
        backend=PsutilProcessBackend(),
        execution_domain_id="windows-local-smoke",
        collector="psutil-processes",
        clock=lambda: datetime.now(UTC),
        home_path=str(Path.home()),
    ).collect()


def test_real_psutil_finds_current_python_with_stable_private_fact():
    first = _collect_current_processes()
    second = _collect_current_processes()

    current = next(item for item in first.facts if item.pid == os.getpid())
    repeated = next(item for item in second.facts if item.pid == os.getpid())
    assert current.current_state is ProcessState.RUNNING
    assert current.access_status in {
        CapabilityStatus.AVAILABLE,
        CapabilityStatus.DEGRADED,
    }
    assert current.create_time is not None
    assert current.create_time.tzinfo is UTC
    assert current.executable_basename is not None
    assert "/" not in current.executable_basename
    assert "\\" not in current.executable_basename
    assert current.process_instance_id == repeated.process_instance_id
    workspace = next(
        item
        for item in first.workspace_candidates
        if current.evidence_refs[0] in item.evidence_refs
    )
    assert workspace.sanitized is True
    if workspace.path_hint is None:
        assert workspace.path_kind is WorkspacePathKind.UNKNOWN
    else:
        cwd = Path.cwd()
        try:
            cwd.relative_to(Path.home())
        except ValueError:
            assert workspace.path_hint == os.path.normpath(os.getcwd())
            assert workspace.path_kind is WorkspacePathKind.NATIVE
        else:
            assert workspace.path_hint == "~" or workspace.path_hint.startswith(
                ("~/", "~\\")
            )
            assert workspace.path_kind is WorkspacePathKind.REDACTED
    encoded = str(first.to_dict()).casefold()
    for forbidden in (
        "command_line",
        "cmdline",
        "environment",
        "api_key",
        "access_token",
        "remote_url",
        "open_files",
        "net_connections",
    ):
        assert forbidden not in encoded


def test_short_lived_python_is_discovered_then_not_left_running():
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        during = _collect_current_processes()
        child_fact = next(item for item in during.facts if item.pid == child.pid)
        assert child_fact.current_state is ProcessState.RUNNING
        assert child.stdin is not None
        child.stdin.write(b"x")
        child.stdin.close()
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.wait(timeout=5)

    after = _collect_current_processes()
    assert not any(
        item.pid == child.pid and item.current_state is ProcessState.RUNNING
        for item in after.facts
    )


def test_installed_psutil_version_satisfies_runtime_floor():
    version = tuple(int(part) for part in psutil.__version__.split(".")[:3])

    assert (7, 2, 2) <= version < (8, 0, 0)
