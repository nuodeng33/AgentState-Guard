"""Docker CLI process discovery uses only bound, read-only fixed commands."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from agentguard.discovery.capabilities import CapabilityStatus
from agentguard.discovery.domains.docker_host import (
    DockerCommandResult,
    DockerHostProbe,
    DockerTarget,
)
from agentguard.discovery.probes.container_processes import (
    CONTAINER_PROCESS_SOURCE,
    ContainerProcessProtocolError,
    parse_container_processes,
    process_probe_source,
)

CONTAINER_ID = "a" * 64
OTHER_CONTAINER_ID = "b" * 64
BOOT_ID = "01234567-89ab-cdef-0123-456789abcdef"
OTHER_BOOT_ID = "fedcba98-7654-3210-fedc-ba9876543210"
LOCAL_ENDPOINT = "npipe:////./pipe/dockerDesktopLinuxEngine"


def _ok(value: str | bytes) -> DockerCommandResult:
    return DockerCommandResult(0, value.encode() if isinstance(value, str) else value)


def _ps(container_id: str = CONTAINER_ID, name: str = "agent-k3") -> DockerCommandResult:
    return _ok(json.dumps({
        "ID": container_id, "Names": name, "Status": "Up 2 hours", "State": "running",
    }))


def _processes(
    *, boot_id: str | None = BOOT_ID, partial_reason: str | None = None,
    pid: int = 515,
) -> DockerCommandResult:
    return _ok(json.dumps({
        "schema_version": 1,
        "boot_id": boot_id,
        "partial_reason": partial_reason,
        "processes": [{
            "pid": pid, "ppid": 1, "name": "kimi-code", "start_ticks": 123456,
            "executable_basename": "node", "cwd": "/workspace/project",
        }],
    }))


class FakeExecutor:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def run(self, argv, *, timeout, max_output_bytes, env):
        self.calls.append((tuple(argv), timeout, max_output_bytes, dict(env)))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _context(*responses):
    return FakeExecutor(
        _ok("desktop-linux\n"), _ok(json.dumps(LOCAL_ENDPOINT)), *responses,
    )


def test_local_context_lists_and_probes_only_bound_full_id():
    fake = _context(
        _ps(), _ok("desktop-linux\n"), _ok(json.dumps(LOCAL_ENDPOINT)),
        _ps(), _processes(),
    )
    probe = DockerHostProbe(executor=fake, environment={})

    listed = probe.list_targets()
    assert listed.status is CapabilityStatus.AVAILABLE
    assert listed.reason_code == "DOCKER_TARGETS_LISTED"
    target = listed.targets[0]
    assert target == DockerTarget(CONTAINER_ID, "agent-k3", "Up 2 hours")
    with pytest.raises(FrozenInstanceError):
        target.name = "other"

    observed = probe.probe_target(target)
    assert observed.status is CapabilityStatus.AVAILABLE
    assert observed.reason_code == "DOCKER_PROCESS_OBSERVED"
    assert observed.boot_id == BOOT_ID
    assert observed.processes == ({
        "pid": 515, "ppid": 1, "name": "kimi-code", "start_ticks": 123456,
        "executable_basename": "node", "cwd": "/workspace/project",
    },)
    assert "cwd" not in repr(observed)
    assert fake.calls[2][0] == (
        "docker", "ps", "--no-trunc", "--format", "{{json .}}",
    )
    assert fake.calls[-1][0] == (
        "docker", "exec", CONTAINER_ID, "python3", "-I", "-B", "-c",
        process_probe_source(),
    )
    assert fake.calls[-1][3]["DOCKER_HOST"] == LOCAL_ENDPOINT
    assert "DOCKER_CONTEXT" not in fake.calls[-1][3]
    assert not fake.responses


def test_forged_target_and_injected_ps_fields_never_reach_exec():
    fake = _context(_ps())
    probe = DockerHostProbe(executor=fake, environment={})
    target = probe.list_targets().targets[0]
    forged = DockerTarget(target.container_id, target.name, target.status)
    rejected = probe.probe_target(forged)
    assert rejected.status is CapabilityStatus.ERROR
    assert rejected.reason_code == "DOCKER_TARGET_NOT_BOUND"
    assert len(fake.calls) == 3

    for container_id, name in (("a" * 12, "agent-k3"), (CONTAINER_ID, "k3;touch /tmp/pwn")):
        invalid = _context(_ps(container_id, name))
        result = DockerHostProbe(executor=invalid, environment={}).list_targets()
        assert result.status is CapabilityStatus.ERROR
        assert result.reason_code == "DOCKER_TARGET_LIST_INVALID"
        assert len(invalid.calls) == 3


def test_remote_docker_host_and_context_fail_before_target_enumeration():
    remote_host = FakeExecutor()
    result = DockerHostProbe(
        executor=remote_host, environment={"DOCKER_HOST": "ssh://user@remote"},
    ).list_targets()
    assert result.status is CapabilityStatus.UNSUPPORTED
    assert result.reason_code == "DOCKER_REMOTE_ENDPOINT_REJECTED"
    assert remote_host.calls == []

    remote_context = FakeExecutor(
        _ok("remote\n"), _ok(json.dumps("tcp://192.0.2.10:2375")),
    )
    result = DockerHostProbe(executor=remote_context, environment={}).list_targets()
    assert result.status is CapabilityStatus.UNSUPPORTED
    assert result.reason_code == "DOCKER_REMOTE_ENDPOINT_REJECTED"
    assert len(remote_context.calls) == 2


def test_empty_unreachable_timeout_and_output_cap_are_explicit():
    empty = _context(_ok(b""))
    result = DockerHostProbe(executor=empty, environment={}).list_targets()
    assert result.status is CapabilityStatus.NOT_PRESENT
    assert result.reason_code == "DOCKER_TARGETS_EMPTY"
    assert not result.targets

    unavailable = FakeExecutor(FileNotFoundError())
    result = DockerHostProbe(executor=unavailable, environment={}).list_targets()
    assert result.status is CapabilityStatus.UNREACHABLE
    assert result.reason_code == "DOCKER_CLI_UNAVAILABLE"

    timed_out = _context(DockerCommandResult(-1, timed_out=True))
    result = DockerHostProbe(executor=timed_out, environment={}).list_targets()
    assert result.status is CapabilityStatus.UNREACHABLE
    assert result.reason_code == "DOCKER_CLI_TIMEOUT"

    excessive = _context(_ok(b"x" * (128 * 1024 + 1)))
    result = DockerHostProbe(executor=excessive, environment={}).list_targets()
    assert result.status is CapabilityStatus.ERROR
    assert result.reason_code == "DOCKER_OUTPUT_LIMIT_REACHED"


def test_single_process_denial_is_partial_and_boot_id_distinguishes_restart():
    fake = _context(
        _ps(),
        _ok("desktop-linux\n"), _ok(json.dumps(LOCAL_ENDPOINT)), _ps(),
        _processes(partial_reason="PROCESS_ACCESS_DENIED"),
        _ok("desktop-linux\n"), _ok(json.dumps(LOCAL_ENDPOINT)), _ps(),
        _processes(boot_id=OTHER_BOOT_ID),
    )
    probe = DockerHostProbe(executor=fake, environment={})
    target = probe.list_targets().targets[0]
    first = probe.probe_target(target)
    assert first.status is CapabilityStatus.DEGRADED
    assert first.reason_code == "DOCKER_PROCESS_ACCESS_DENIED"
    assert first.processes[0]["pid"] == 515
    second = probe.probe_target(target)
    assert second.status is CapabilityStatus.AVAILABLE
    assert second.boot_id == OTHER_BOOT_ID
    assert (target.container_id, first.boot_id, first.processes[0]["pid"]) != (
        target.container_id, second.boot_id, second.processes[0]["pid"]
    )


def test_recreated_container_cannot_reuse_stale_target():
    fake = _context(
        _ps(), _ok("desktop-linux\n"), _ok(json.dumps(LOCAL_ENDPOINT)),
        _ps(OTHER_CONTAINER_ID),
    )
    probe = DockerHostProbe(executor=fake, environment={})
    target = probe.list_targets().targets[0]
    observed = probe.probe_target(target)
    assert observed.status is CapabilityStatus.NOT_PRESENT
    assert observed.reason_code == "DOCKER_TARGET_CHANGED"
    assert len(fake.calls) == 6


def test_session_transport_rechecks_target_and_pins_local_endpoint(monkeypatch):
    fake = _context(
        _ps(), _ok("desktop-linux\n"), _ok(json.dumps(LOCAL_ENDPOINT)), _ps(),
    )
    probe = DockerHostProbe(executor=fake, environment={"DOCKER_CONTEXT": "desktop-linux"})
    target = probe.list_targets().targets[0]
    monkeypatch.setattr("agentguard.discovery.domains.docker_host.shutil.which", lambda name: "C:/docker.exe")
    assert probe.session_transport(DockerTarget(target.container_id, target.name, target.status)) is None
    executable, environment = probe.session_transport(target)
    assert executable == "C:/docker.exe"
    assert environment["DOCKER_HOST"] == LOCAL_ENDPOINT
    assert "DOCKER_CONTEXT" not in environment
    assert not any("exec" in call[0] for call in fake.calls)


def test_session_transport_rejects_changed_endpoint_before_listing():
    fake = _context(
        _ps(), _ok("desktop-linux\n"), _ok(json.dumps("npipe:////./pipe/otherEngine")),
    )
    probe = DockerHostProbe(executor=fake, environment={})
    target = probe.list_targets().targets[0]
    assert probe.session_transport(target) is None
    assert len(fake.calls) == 5


@pytest.mark.parametrize("mutation", [
    {"token": "private"},
    {"pid": True},
    {"cwd": "relative/path"},
    {"executable_basename": "/bin/node"},
])
def test_process_protocol_rejects_extra_or_unsafe_fields(mutation):
    payload = json.loads(_processes().stdout)
    payload["processes"][0].update(mutation)
    with pytest.raises(ContainerProcessProtocolError):
        parse_container_processes(json.dumps(payload).encode())


def test_process_protocol_rejects_missing_boot_identity_without_partial_reason():
    payload = json.loads(_processes(boot_id=None).stdout)
    with pytest.raises(ContainerProcessProtocolError):
        parse_container_processes(json.dumps(payload).encode())


def test_probe_source_excludes_command_line_environment_and_writes():
    assert '"/proc/sys/kernel/random/boot_id"' in CONTAINER_PROCESS_SOURCE
    assert 'root + "/stat"' in CONTAINER_PROCESS_SOURCE
    assert 'root + "/comm"' in CONTAINER_PROCESS_SOURCE
    assert 'root + "/" + link' in CONTAINER_PROCESS_SOURCE
    assert "command.read(8193)" in CONTAINER_PROCESS_SOURCE
    assert "item[\"cmdline\"]" not in CONTAINER_PROCESS_SOURCE
    assert "item[\"argv\"]" not in CONTAINER_PROCESS_SOURCE
    assert "environ" not in CONTAINER_PROCESS_SOURCE
    assert "write(" not in CONTAINER_PROCESS_SOURCE.replace("sys.stdout.write(", "")
