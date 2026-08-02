"""R4-P3A process collection tests using only fixed fake backends."""

import socket
import urllib.request
from datetime import UTC, datetime, timedelta

from agentguard.discovery import CapabilityStatus, DiscoveryErrorCode
from agentguard.discovery.agents import (
    ExecutableIdentityKind,
    ProcessCollector,
    ProcessState,
    ProcessWarningCode,
    build_process_relationships,
)

NOW = datetime(2026, 8, 2, 13, 0, tzinfo=UTC)


class FakeHandle:
    def __init__(
        self,
        *,
        pid=410,
        parent_pid=100,
        basename="tool",
        created=NOW - timedelta(minutes=5),
        identity="sha256:" + "a" * 64,
        cwd="/srv/project",
        fixed_facts=None,
        failures=None,
    ):
        self.pid = pid
        self._parent_pid = parent_pid
        self._basename = basename
        self._created = created
        self._identity = identity
        self._cwd = cwd
        self._fixed_facts = dict(fixed_facts or {})
        self._failures = dict(failures or {})

    def _value(self, name, value):
        failure = self._failures.get(name)
        if failure is not None:
            raise failure
        return value

    def parent_pid(self):
        return self._value("parent_pid", self._parent_pid)

    def executable_basename(self):
        return self._value("executable_basename", self._basename)

    def executable_identity_digest(self):
        raise AssertionError("backend executable identity must not be requested")

    def create_time(self):
        return self._value("create_time", self._created)

    def cwd(self):
        return self._value("cwd", self._cwd)

    def fixed_boolean_facts(self):
        return self._value("fixed_boolean_facts", self._fixed_facts)

    def cmdline(self):
        raise AssertionError("cmdline must never be called")

    def environ(self):
        raise AssertionError("environ must never be called")

    def net_connections(self):
        raise AssertionError("net_connections must never be called")

    def memory_maps(self):
        raise AssertionError("memory_maps must never be called")

    def open_files(self):
        raise AssertionError("open_files must never be called")


class FakeBackend:
    def __init__(self, handles=(), failure=None):
        self._handles = tuple(handles)
        self._failure = failure

    def iter_processes(self):
        if self._failure is not None:
            raise self._failure
        return self._handles


def _collector(
    handles=(),
    *,
    failure=None,
    domain="linux-host",
    allowed_fixed_fact_names=(),
):
    return ProcessCollector(
        backend=FakeBackend(handles, failure=failure),
        execution_domain_id=domain,
        collector="fake-processes",
        clock=lambda: NOW,
        home_path="/home/private-user",
        allowed_fixed_fact_names=allowed_fixed_fact_names,
    )


def test_empty_process_list_is_successful_empty_snapshot():
    result = _collector().collect()

    assert result.status is CapabilityStatus.AVAILABLE
    assert result.facts == ()
    assert result.errors == ()


def test_single_tool_process_collects_only_bounded_metadata():
    result = _collector([FakeHandle()]).collect()

    fact = result.facts[0]
    assert fact.pid == 410
    assert fact.parent_pid == 100
    assert fact.executable_basename == "tool"
    assert fact.executable_identity_kind is ExecutableIdentityKind.BASENAME_SHA256
    assert fact.executable_identity_verified is False
    assert fact.current_state is ProcessState.RUNNING
    assert fact.access_status is CapabilityStatus.AVAILABLE
    assert fact.sanitized is True
    assert len(fact.evidence_refs) == 1


def test_forbidden_psutil_like_methods_are_never_called():
    result = _collector([FakeHandle()]).collect()

    assert len(result.facts) == 1
    encoded = str(result.to_dict()).lower()
    for forbidden in (
        "command_line",
        "argv",
        "environment",
        "token",
        "secret",
        "api_key",
    ):
        assert forbidden not in encoded


def test_fixed_boolean_facts_require_explicit_whitelist():
    handle = FakeHandle(
        fixed_facts={
            "runtime_supported": True,
            "access_token_present": True,
        }
    )

    result = _collector(
        [handle],
        allowed_fixed_fact_names=("runtime_supported",),
    ).collect()

    assert result.facts[0].fixed_facts == {"runtime_supported": True}
    assert ProcessWarningCode.SENSITIVE_INPUT_REJECTED in result.facts[0].warnings
    assert "access_token" not in str(result.to_dict()).lower()


def test_one_process_permission_error_does_not_stop_other_facts():
    denied = FakeHandle(pid=411, failures={"create_time": PermissionError("denied")})
    visible = FakeHandle(pid=412)

    result = _collector([denied, visible]).collect()

    assert result.status is CapabilityStatus.DEGRADED
    assert len(result.facts) == 2
    denied_fact = next(item for item in result.facts if item.pid == 411)
    assert denied_fact.access_status is CapabilityStatus.PERMISSION_DENIED
    assert any(error.code is DiscoveryErrorCode.PERMISSION_DENIED for error in result.errors)
    assert any(item.pid == 412 and item.current_state is ProcessState.RUNNING for item in result.facts)


def test_process_exit_is_not_reported_as_collector_error():
    exited = FakeHandle(pid=411, failures={"create_time": ProcessLookupError("gone")})

    result = _collector([exited]).collect()

    fact = result.facts[0]
    assert fact.current_state is ProcessState.EXITED
    assert fact.access_status is CapabilityStatus.NOT_PRESENT
    assert ProcessWarningCode.PROCESS_EXITED in fact.warnings
    assert result.errors[0].code is DiscoveryErrorCode.NOT_PRESENT
    assert result.errors[0].details["reason_code"] == "PROCESS_EXITED"


def test_partially_visible_process_is_degraded_but_retained():
    partial = FakeHandle(failures={"parent_pid": PermissionError("denied")})

    result = _collector([partial]).collect()

    fact = result.facts[0]
    assert fact.current_state is ProcessState.RUNNING
    assert fact.access_status is CapabilityStatus.DEGRADED
    assert fact.parent_pid is None
    assert ProcessWarningCode.PARTIAL_VISIBILITY in fact.warnings


def test_process_exit_during_optional_read_does_not_leave_running_assertion():
    raced = FakeHandle(
        failures={"executable_basename": ProcessLookupError("gone")}
    )

    result = _collector([raced]).collect()

    fact = result.facts[0]
    assert fact.current_state is ProcessState.EXITED
    assert fact.access_status is CapabilityStatus.NOT_PRESENT
    assert ProcessWarningCode.PROCESS_EXITED in fact.warnings
    assert any(
        error.code is DiscoveryErrorCode.NOT_PRESENT
        and error.details["reason_code"] == "PROCESS_EXITED"
        for error in result.errors
    )


def test_one_exited_process_does_not_discard_other_process_fact():
    exited = FakeHandle(
        pid=411,
        failures={"executable_basename": ProcessLookupError("gone")},
    )
    visible = FakeHandle(pid=412)

    result = _collector([exited, visible]).collect()

    assert any(
        item.pid == 411
        and item.current_state is ProcessState.EXITED
        and item.access_status is CapabilityStatus.NOT_PRESENT
        for item in result.facts
    )
    assert any(
        item.pid == 412 and item.current_state is ProcessState.RUNNING
        for item in result.facts
    )


def test_backend_unsupported_maps_to_structured_status():
    result = _collector(failure=NotImplementedError("platform")).collect()

    assert result.status is CapabilityStatus.UNSUPPORTED
    assert result.errors[0].code is DiscoveryErrorCode.UNSUPPORTED


def test_backend_exception_maps_to_error_without_free_text_details():
    result = _collector(failure=RuntimeError("secret diagnostic value")).collect()

    assert result.status is CapabilityStatus.ERROR
    assert result.errors[0].code is DiscoveryErrorCode.COLLECTOR_FAILURE
    assert result.errors[0].details == {"reason_code": "PROCESS_ENUMERATION_FAILED"}
    assert "secret diagnostic value" not in str(result.to_dict())


def test_pid_reuse_produces_distinct_facts_and_relationship_instances():
    old = FakeHandle(pid=410, created=NOW - timedelta(hours=1), parent_pid=None)
    reused = FakeHandle(pid=410, created=NOW - timedelta(minutes=1), parent_pid=None)
    result = _collector([old, reused]).collect()

    assert len({item.process_instance_id for item in result.facts}) == 2
    assert len(result.relationships) == 2


def test_parent_that_has_exited_is_marked_unavailable_not_invented():
    child = FakeHandle(pid=420, parent_pid=99)
    result = _collector([child]).collect()

    relationship = result.relationships[0]
    assert relationship.parent_instance_id is None
    assert relationship.orphaned is True
    assert relationship.parent_unavailable is True
    assert ProcessWarningCode.PARENT_UNAVAILABLE in relationship.warnings


def test_corrupt_parent_cycle_is_not_materialized():
    first = FakeHandle(pid=1, parent_pid=2, created=NOW - timedelta(minutes=2))
    second = FakeHandle(pid=2, parent_pid=1, created=NOW - timedelta(minutes=2))
    result = _collector([first, second]).collect()

    assert all(item.parent_instance_id is None for item in result.relationships)
    assert all(ProcessWarningCode.PARENT_CYCLE in item.warnings for item in result.relationships)


def test_same_pid_in_another_domain_is_not_linked_as_parent():
    parent = _collector(
        [FakeHandle(pid=100, parent_pid=None)],
        domain="windows-host",
    ).collect().facts[0]
    child = _collector(
        [FakeHandle(pid=200, parent_pid=100)],
        domain="wsl-runtime",
    ).collect().facts[0]

    relationships = build_process_relationships((parent, child))
    child_relation = next(
        item for item in relationships if item.process_instance_id == child.process_instance_id
    )
    assert child_relation.parent_instance_id is None
    assert child_relation.parent_unavailable is True


def test_collection_is_offline_and_does_not_touch_provider_or_database(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("forbidden external integration was called")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)

    from agentguard.ai.provider import AIProvider
    from agentguard.storage.db import StateDB

    monkeypatch.setattr(AIProvider, "analyze", forbidden)
    monkeypatch.setattr(StateDB, "connect", forbidden)

    result = _collector([FakeHandle()]).collect()

    assert result.status is CapabilityStatus.AVAILABLE
