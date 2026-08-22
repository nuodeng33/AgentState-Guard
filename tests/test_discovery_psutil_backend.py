"""Contract and static-safety tests for the bounded psutil backend."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentguard.discovery import CapabilityStatus, DiscoveryErrorCode
from agentguard.discovery.agents import (
    ProcessCollector,
    ProcessState,
    PsutilProcessBackend,
)

CREATED = datetime(2026, 8, 2, 12, 0, tzinfo=UTC).timestamp()
NOW = datetime(2026, 8, 2, 13, 0, tzinfo=UTC)
HOME = r"C:\Users\private-person"


class FakeNoSuchProcess(Exception):
    pass


class FakeZombieProcess(Exception):
    pass


class FakeAccessDenied(Exception):
    pass


class FakeProcess:
    def __init__(
        self,
        pid: int,
        *,
        ppid: int = 1,
        name: str = "python.exe",
        create_times: tuple[object, ...] = (CREATED,),
        status: object = "running",
        cwd: object = HOME + r"\work\repo",
        running: object = True,
        failures: dict[str, object] | None = None,
    ) -> None:
        self.pid = pid
        self.info = {
            "pid": pid,
            "ppid": ppid,
            "name": name,
            "create_time": create_times[0] if create_times else None,
            "status": status,
        }
        self._ppid = ppid
        self._name = name
        self._create_times = list(create_times)
        self._last_create_time = create_times[-1] if create_times else CREATED
        self._status = status
        self._cwd = cwd
        self._running = running
        self._failures = dict(failures or {})
        self.oneshot_calls = 0

    def _value(self, field: str, value: object) -> object:
        failure = self._failures.get(field)
        if isinstance(failure, BaseException):
            raise failure
        return value

    def ppid(self) -> int:
        return int(self._value("ppid", self._ppid))

    def name(self) -> str:
        return str(self._value("name", self._name))

    def create_time(self) -> float:
        value = (
            self._create_times.pop(0)
            if self._create_times
            else self._last_create_time
        )
        if isinstance(value, BaseException):
            raise value
        self._last_create_time = value
        return float(value)

    def status(self) -> str:
        value = self._value("status", self._status)
        if isinstance(value, BaseException):
            raise value
        return str(value)

    def cwd(self) -> str | None:
        value = self._value("cwd", self._cwd)
        if isinstance(value, BaseException):
            raise value
        return None if value is None else str(value)

    def is_running(self) -> bool:
        value = self._value("is_running", self._running)
        if isinstance(value, BaseException):
            raise value
        return bool(value)

    def oneshot(self):
        self.oneshot_calls += 1
        return nullcontext()

    def cmdline(self):
        raise AssertionError("cmdline must never be called")

    def environ(self):
        raise AssertionError("environ must never be called")

    def open_files(self):
        raise AssertionError("open_files must never be called")

    def net_connections(self):
        raise AssertionError("net_connections must never be called")

    def memory_maps(self):
        raise AssertionError("memory_maps must never be called")

    def memory_full_info(self):
        raise AssertionError("memory_full_info must never be called")


class FakePsutil:
    NoSuchProcess = FakeNoSuchProcess
    ZombieProcess = FakeZombieProcess
    AccessDenied = FakeAccessDenied
    STATUS_ZOMBIE = "zombie"

    def __init__(
        self,
        processes: tuple[FakeProcess, ...],
        *,
        authoritative_processes: tuple[FakeProcess, ...] | None = None,
    ) -> None:
        self._processes = processes
        authoritative = (
            processes if authoritative_processes is None else authoritative_processes
        )
        self._by_pid = {item.pid: item for item in authoritative}
        self.process_iter_calls: list[tuple[str, ...] | None] = []

    def process_iter(self, attrs=None, ad_value=None):
        del ad_value
        self.process_iter_calls.append(tuple(attrs) if attrs is not None else None)
        return iter(self._processes)

    def Process(self, pid: int):
        process = self._by_pid.get(pid)
        if process is None:
            raise FakeNoSuchProcess(pid)
        return process


def _collect(
    module: FakePsutil,
    *,
    domain: str = "windows-native",
):
    return ProcessCollector(
        backend=PsutilProcessBackend(psutil_module=module),
        execution_domain_id=domain,
        collector="psutil-processes",
        clock=lambda: NOW,
        home_path=HOME,
    ).collect()


def _reason_codes(result) -> set[str]:
    return {str(error.details.get("reason_code")) for error in result.errors}


def test_empty_process_iter_returns_available_empty_result():
    module = FakePsutil(())

    result = _collect(module)

    assert result.status is CapabilityStatus.AVAILABLE
    assert result.facts == ()
    assert module.process_iter_calls == [
        ("pid", "ppid", "name", "create_time", "status")
    ]


def test_process_that_vanishes_during_iteration_does_not_fail_scan():
    class VanishingPsutil(FakePsutil):
        def process_iter(self, attrs=None, ad_value=None):
            del ad_value
            self.process_iter_calls.append(tuple(attrs) if attrs is not None else None)

            def items():
                raise FakeNoSuchProcess(410)
                yield

            return items()

    result = _collect(VanishingPsutil(()))

    assert result.status is CapabilityStatus.AVAILABLE
    assert result.facts == ()


def test_process_iter_access_denied_uses_psutil_specific_reason():
    class DeniedPsutil(FakePsutil):
        def process_iter(self, attrs=None, ad_value=None):
            del attrs, ad_value
            raise FakeAccessDenied()

    result = _collect(DeniedPsutil(()))

    assert result.status is CapabilityStatus.PERMISSION_DENIED
    assert _reason_codes(result) == {"PROCESS_ACCESS_DENIED"}


def test_process_iter_unknown_failure_uses_stable_reason_without_message():
    class FailingPsutil(FakePsutil):
        def process_iter(self, attrs=None, ad_value=None):
            del attrs, ad_value
            raise RuntimeError("secret enumeration diagnostic")

    result = _collect(FailingPsutil(()))

    assert result.status is CapabilityStatus.ERROR
    assert _reason_codes(result) == {"PROCESS_COLLECTOR_FAILURE"}
    assert "secret enumeration diagnostic" not in str(result.to_dict())


def test_single_process_maps_bounded_sanitized_fact_and_cwd():
    module = FakePsutil(
        (
            FakeProcess(
                410,
                name=r"C:\private\python.exe",
                cwd=HOME + r"\work\repo",
            ),
        )
    )

    result = _collect(module)

    fact = result.facts[0]
    assert fact.pid == 410
    assert fact.parent_pid == 1
    assert fact.executable_basename == "python.exe"
    assert fact.executable_identity_kind.value == "BASENAME_SHA256"
    assert fact.executable_identity_verified is False
    assert fact.create_time == datetime.fromtimestamp(CREATED, tz=UTC)
    assert fact.current_state is ProcessState.RUNNING
    assert fact.access_status is CapabilityStatus.AVAILABLE
    assert result.workspace_candidates[0].path_hint == r"~\work\repo"
    encoded = str(result.to_dict()).casefold()
    assert "private-person" not in encoded
    assert r"c:\private".casefold() not in encoded


def test_multiple_processes_have_stable_pid_order():
    module = FakePsutil((FakeProcess(900), FakeProcess(20), FakeProcess(410)))

    result = _collect(module)

    assert [item.pid for item in result.facts] == [20, 410, 900]


def test_process_order_is_identical_for_reversed_candidate_iteration():
    forward = _collect(
        FakePsutil((FakeProcess(20), FakeProcess(410), FakeProcess(900)))
    )
    reverse = _collect(
        FakePsutil((FakeProcess(900), FakeProcess(410), FakeProcess(20)))
    )

    assert forward.to_dict() == reverse.to_dict()


def test_duplicate_pid_candidate_produces_one_success_fact():
    process = FakeProcess(410)

    result = _collect(FakePsutil((process, process)))

    assert len(result.facts) == 1
    assert result.facts[0].pid == 410


def test_process_iter_object_is_only_a_candidate_not_identity_authority():
    candidate = FakeProcess(
        410,
        ppid=999,
        name="stale.exe",
        cwd=HOME + r"\stale",
    )
    authoritative = FakeProcess(
        410,
        ppid=1,
        name="python.exe",
        cwd=HOME + r"\current",
    )

    result = _collect(
        FakePsutil(
            (candidate,),
            authoritative_processes=(authoritative,),
        )
    )

    fact = result.facts[0]
    assert fact.parent_pid == 1
    assert fact.executable_basename == "python.exe"
    assert result.workspace_candidates[0].path_hint == r"~\current"


def test_access_denied_field_retains_process_as_degraded():
    denied = FakeProcess(
        410,
        failures={"ppid": FakeAccessDenied(410)},
    )

    result = _collect(FakePsutil((denied,)))

    fact = result.facts[0]
    assert fact.current_state is ProcessState.RUNNING
    assert fact.parent_pid is None
    assert fact.access_status is CapabilityStatus.DEGRADED
    assert "PROCESS_ACCESS_DENIED" in _reason_codes(result)


def test_no_such_process_maps_to_exited_not_present():
    process = FakeProcess(410, create_times=(FakeNoSuchProcess(410),))

    result = _collect(FakePsutil((process,)))

    assert result.facts[0].current_state is ProcessState.EXITED
    assert result.facts[0].access_status is CapabilityStatus.NOT_PRESENT
    assert _reason_codes(result) == {"PROCESS_EXITED"}


def test_zombie_process_maps_to_exited_degraded():
    process = FakeProcess(410, create_times=(FakeZombieProcess(410),))

    result = _collect(FakePsutil((process,)))

    assert result.facts[0].current_state is ProcessState.EXITED
    assert result.facts[0].access_status is CapabilityStatus.DEGRADED
    assert _reason_codes(result) == {"PROCESS_ZOMBIE"}


def test_cwd_access_denied_is_structured_and_does_not_drop_fact():
    process = FakeProcess(
        410,
        failures={"cwd": FakeAccessDenied(410)},
    )

    result = _collect(FakePsutil((process,)))

    assert result.facts[0].current_state is ProcessState.RUNNING
    assert result.facts[0].access_status is CapabilityStatus.DEGRADED
    assert result.workspace_candidates[0].path_hint is None
    assert (
        result.workspace_candidates[0].access_status
        is CapabilityStatus.PERMISSION_DENIED
    )
    assert "PROCESS_ACCESS_DENIED" in _reason_codes(result)


def test_process_exit_after_initial_create_time_is_not_left_running():
    process = FakeProcess(
        410,
        create_times=(CREATED, FakeNoSuchProcess(410)),
    )

    result = _collect(FakePsutil((process,)))

    assert result.facts[0].current_state is ProcessState.EXITED
    assert result.facts[0].access_status is CapabilityStatus.NOT_PRESENT
    assert "PROCESS_EXITED" in _reason_codes(result)


def test_pid_reuse_during_collection_rejects_old_running_identity():
    process = FakeProcess(410, create_times=(CREATED, CREATED + 10))

    result = _collect(FakePsutil((process,)))

    assert result.facts[0].current_state is ProcessState.EXITED
    assert result.facts[0].access_status is CapabilityStatus.NOT_PRESENT
    assert result.facts[0].parent_pid is None
    assert result.facts[0].executable_basename is None
    assert result.facts[0].executable_identity_digest is None
    assert result.facts[0].fixed_facts == {}
    assert result.workspace_candidates == ()
    assert "PROCESS_INSTANCE_REPLACED" in _reason_codes(result)


def test_missing_psutil_keeps_discovery_importable_and_is_not_empty_success():
    script = r'''
import builtins
import json
from datetime import UTC, datetime

real_import = builtins.__import__

def blocked_import(name, *args, **kwargs):
    if name == "psutil":
        error = ModuleNotFoundError("blocked dependency diagnostic")
        error.name = "psutil"
        raise error
    return real_import(name, *args, **kwargs)

builtins.__import__ = blocked_import
import agentguard.discovery.models
import agentguard.discovery.domains.self_runtime
from agentguard.discovery import ProcessCollector, PsutilProcessBackend

result = ProcessCollector(
    backend=PsutilProcessBackend(),
    execution_domain_id="missing-dependency-test",
    collector="psutil-processes",
    clock=lambda: datetime(2026, 8, 2, 13, 0, tzinfo=UTC),
).collect()
print(json.dumps({
    "status": result.status.value,
    "facts": len(result.facts),
    "reason_codes": [error.details.get("reason_code") for error in result.errors],
    "serialized": result.to_dict(),
}, sort_keys=True))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["status"] == "UNSUPPORTED"
    assert payload["facts"] == 0
    assert payload["reason_codes"] == ["PROCESS_BACKEND_UNAVAILABLE"]
    assert "blocked dependency diagnostic" not in str(payload["serialized"])


def test_same_pid_and_create_time_are_distinct_across_domains():
    first = _collect(FakePsutil((FakeProcess(410),)), domain="windows-native")
    second = _collect(FakePsutil((FakeProcess(410),)), domain="wsl-runtime")

    assert first.facts[0].process_instance_id != second.facts[0].process_instance_id


def test_not_implemented_field_is_structured_unsupported():
    process = FakeProcess(410, failures={"ppid": NotImplementedError()})

    result = _collect(FakePsutil((process,)))

    assert result.facts[0].current_state is ProcessState.RUNNING
    assert result.facts[0].access_status is CapabilityStatus.DEGRADED
    assert any(error.code is DiscoveryErrorCode.UNSUPPORTED for error in result.errors)
    assert "PROCESS_FIELD_UNSUPPORTED" in _reason_codes(result)


def test_unknown_exception_is_stable_error_without_original_message():
    process = FakeProcess(
        410,
        failures={"name": RuntimeError("secret diagnostic payload")},
    )

    result = _collect(FakePsutil((process,)))

    assert result.facts[0].access_status is CapabilityStatus.DEGRADED
    assert "PROCESS_COLLECTOR_FAILURE" in _reason_codes(result)
    assert "secret diagnostic payload" not in str(result.to_dict())


def test_one_failed_process_does_not_discard_other_process():
    exited = FakeProcess(410, create_times=(FakeNoSuchProcess(410),))
    visible = FakeProcess(411)

    result = _collect(FakePsutil((exited, visible)))

    assert len(result.facts) == 2
    assert any(item.pid == 411 and item.current_state is ProcessState.RUNNING for item in result.facts)


def test_backend_never_calls_forbidden_process_apis():
    result = _collect(FakePsutil((FakeProcess(410),)))

    assert result.facts[0].current_state is ProcessState.RUNNING


def test_production_backend_ast_contains_no_forbidden_calls():
    backend_path = (
        Path(__file__).parents[1]
        / "agentguard"
        / "discovery"
        / "agents"
        / "psutil_backend.py"
    )
    tree = ast.parse(backend_path.read_text(encoding="utf-8"))
    forbidden = {
        "environ",
        "open_files",
        "net_connections",
        "memory_maps",
        "memory_full_info",
        "kill",
        "terminate",
        "suspend",
        "resume",
        "send_signal",
        "nice",
        "ionice",
        "rlimit",
    }
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert called.isdisjoint(forbidden)


def test_production_backend_cmdline_read_is_confined_to_bounded_anchor_method():
    """The sanctioned bounded-launcher exception never leaks raw argv.

    Process command lines may be read only inside
    ``bounded_launcher_anchors``, which reduces them to on-disk script
    anchors; every other method must stay free of command-line reads.
    """
    backend_path = (
        Path(__file__).parents[1]
        / "agentguard"
        / "discovery"
        / "agents"
        / "psutil_backend.py"
    )
    tree = ast.parse(backend_path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr in {"cmdline", "environ"}
                and node.name != "bounded_launcher_anchors"
            ):
                offenders.append(node.name)

    assert offenders == []


@pytest.mark.parametrize(
    "forbidden_import",
    [
        "socket",
        "urllib",
        "requests",
        "httpx",
        "subprocess",
        "docker",
    ],
)
def test_production_backend_has_no_offline_boundary_imports(forbidden_import):
    backend_path = (
        Path(__file__).parents[1]
        / "agentguard"
        / "discovery"
        / "agents"
        / "psutil_backend.py"
    )
    tree = ast.parse(backend_path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])

    assert forbidden_import not in imported
