"""Windows-to-WSL orchestration tests; all operating-system access is mocked."""

from __future__ import annotations

import json
import socket
import subprocess
import urllib.request
from datetime import UTC, datetime

import pytest

from agentguard.discovery import (
    CapabilityStatus,
    DiscoveryError,
    DiscoveryErrorCode,
)
from agentguard.discovery.command_runner import (
    CommandExecution,
    CommandId,
    WslCommandRunner,
)
from agentguard.discovery.domains.windows import WindowsAdapter

OBSERVED_AT = "2026-08-02T10:30:00+00:00"
NOW = datetime(2026, 8, 2, 10, 30, tzinfo=UTC)


class FakeRunner:
    def __init__(self, executions):
        self.executions = list(executions)
        self.calls = []
        self.workspace_flags = []

    def run(self, command_id, *, distro=None, include_workspace=False):
        self.calls.append((command_id, distro))
        self.workspace_flags.append(include_workspace)
        return self.executions.pop(0)


def _execution(command_id, *, stdout="", status=CapabilityStatus.AVAILABLE, returncode=0, error=None):
    return CommandExecution(
        command_id=command_id,
        status=status,
        stdout=stdout,
        returncode=returncode,
        error=error,
    )


def _failure(command_id, status, code, *, returncode=None):
    return _execution(
        command_id,
        status=status,
        returncode=returncode,
        error=DiscoveryError(
            code=code,
            message="Structured WSL failure",
            collector="test",
            source=command_id.value,
        ),
    )


def _probe_payload(*, container=False):
    execution_domain = {"kind": "WSL", "generation": "WSL2"}
    evidence = [
        {
            "evidence_id": "probe-wsl",
            "collector": "wsl_probe",
            "source": "fixed_probe",
            "observed_at": OBSERVED_AT,
            "fact_type": "wsl.kernel",
            "value": {"present": True},
            "reliability": "HIGH",
            "confidence": 0.9,
            "status": "AVAILABLE",
            "sanitized": True,
        }
    ]
    if container:
        execution_domain["container"] = {
            "detected": True,
            "runtime": "DOCKER",
            "evidence_ids": ["probe-container"],
        }
        evidence.append(
            {
                **evidence[0],
                "evidence_id": "probe-container",
                "fact_type": "container.current",
            }
        )
    return {
        "schema_version": "1.0",
        "probe_version": "1.0",
        "distro": "Ubuntu",
        "observed_at": OBSERVED_AT,
        "execution_domain": execution_domain,
        "kernel": {"system": "Linux", "release": "6.8-wsl2", "version": "test"},
        "identity": {"uid": 1000, "gid": 1000},
        "capabilities": {
            "DISCOVERY_LITE": "AVAILABLE",
            "DISCOVERY_FULL": "AVAILABLE",
            "RECOVERY_CAPABLE": "UNKNOWN",
        },
        "evidence": evidence,
        "errors": [],
        "sanitized": True,
        "warnings": [],
    }


def _adapter(runner, *, system="Windows", os_name="nt"):
    return WindowsAdapter(
        runner=runner,
        platform_system=lambda: system,
        platform_release=lambda: "11",
        platform_version=lambda: "test-build",
        os_name=os_name,
        clock=lambda: NOW,
    )


def test_non_windows_adapter_is_unsupported_without_invoking_wsl():
    runner = FakeRunner([])

    snapshot = _adapter(runner, system="Linux", os_name="posix").discover()

    assert snapshot.status is CapabilityStatus.UNSUPPORTED
    assert snapshot.errors[0].code is DiscoveryErrorCode.UNSUPPORTED
    assert runner.calls == []


def test_missing_wsl_cli_is_not_present():
    runner = FakeRunner(
        [_failure(CommandId.WSL_LIST_VERBOSE, CapabilityStatus.NOT_PRESENT, DiscoveryErrorCode.NOT_PRESENT)]
    )

    snapshot = _adapter(runner).discover()

    assert snapshot.status is CapabilityStatus.NOT_PRESENT
    assert snapshot.errors[0].code is DiscoveryErrorCode.NOT_PRESENT
    assert any(item.fact_type == "wsl.command_result" for item in snapshot.evidence)


def test_empty_wsl_list_is_available_and_contains_only_windows_root():
    runner = FakeRunner([_execution(CommandId.WSL_LIST_VERBOSE, stdout="  NAME  STATE  VERSION\n")])

    result = _adapter(runner).list_distros()
    snapshot = _adapter(FakeRunner([_execution(CommandId.WSL_LIST_VERBOSE, stdout="")])).discover()

    assert result.status is CapabilityStatus.AVAILABLE
    assert result.distros == ()
    assert snapshot.status is CapabilityStatus.AVAILABLE
    assert snapshot.domains[0].children == ()


def test_running_wsl2_is_listed_as_child_without_full_probe():
    output = "  NAME       STATE      VERSION\n* Ubuntu    Running    2\n"
    runner = FakeRunner([_execution(CommandId.WSL_LIST_VERBOSE, stdout=output)])

    snapshot = _adapter(runner).discover()

    wsl = snapshot.domains[0].children[0]
    assert snapshot.status is CapabilityStatus.AVAILABLE
    assert "WSL2" in wsl.label
    assert wsl.capabilities.get("discovery_lite").status is CapabilityStatus.AVAILABLE
    assert wsl.capabilities.get("discovery_full").status is CapabilityStatus.UNKNOWN
    assert runner.calls == [(CommandId.WSL_LIST_VERBOSE, None)]


def test_mixed_distros_are_listed_but_none_are_deep_scanned():
    output = (
        "  NAME       STATE      VERSION\n"
        "  Ubuntu    Running    2\n"
        "  Legacy    Stopped    1\n"
    )
    runner = FakeRunner([_execution(CommandId.WSL_LIST_VERBOSE, stdout=output)])

    snapshot = _adapter(runner).discover()

    assert len(snapshot.domains[0].children) == 2
    assert runner.calls == [(CommandId.WSL_LIST_VERBOSE, None)]


def test_permission_timeout_and_nonzero_list_failures_remain_distinct():
    cases = (
        (CapabilityStatus.PERMISSION_DENIED, DiscoveryErrorCode.PERMISSION_DENIED, CapabilityStatus.PERMISSION_DENIED),
        (CapabilityStatus.ERROR, DiscoveryErrorCode.TIMEOUT, CapabilityStatus.ERROR),
        (CapabilityStatus.ERROR, DiscoveryErrorCode.COLLECTOR_FAILURE, CapabilityStatus.UNREACHABLE),
    )
    for runner_status, code, expected in cases:
        runner = FakeRunner([_failure(CommandId.WSL_LIST_VERBOSE, runner_status, code, returncode=1)])
        snapshot = _adapter(runner).discover()
        assert snapshot.status is expected


def test_real_runner_access_denial_result_remains_permission_denied_in_adapter():
    completed = subprocess.CompletedProcess(
        ["wsl.exe"],
        0xFFFFFFFF,
        stdout="Wsl/EnumerateDistros/Service/E_ACCESSDENIED".encode("utf-16le"),
        stderr=b"",
    )
    execution = WslCommandRunner(executor=lambda *_args, **_kwargs: completed).run(
        CommandId.WSL_LIST_VERBOSE
    )

    snapshot = _adapter(FakeRunner([execution])).discover()

    assert snapshot.status is CapabilityStatus.PERMISSION_DENIED
    assert snapshot.errors[0].code is DiscoveryErrorCode.PERMISSION_DENIED
    assert snapshot.errors[0].details["reason_code"] == "WSL_E_ACCESSDENIED"


def test_stopped_distro_is_not_started_without_explicit_authorization():
    output = "  NAME       STATE      VERSION\n  Ubuntu    Stopped    2\n"
    runner = FakeRunner([_execution(CommandId.WSL_LIST_VERBOSE, stdout=output)])

    result = _adapter(runner).probe_distro("Ubuntu")

    assert result.snapshot.status is CapabilityStatus.DEGRADED
    assert "STOPPED_NOT_PROBED" in result.warnings
    assert result.snapshot.domains[0].children[0].capabilities.get("discovery_full").status is CapabilityStatus.UNKNOWN
    assert runner.calls == [(CommandId.WSL_LIST_VERBOSE, None)]


def test_unknown_localized_state_is_not_probed_or_started_by_default():
    output = "  名称       状态       版本\n  Ubuntu    正在运行    2\n"
    runner = FakeRunner([_execution(CommandId.WSL_LIST_VERBOSE, stdout=output)])

    result = _adapter(runner).probe_distro("Ubuntu")

    assert result.snapshot.status is CapabilityStatus.DEGRADED
    assert "UNKNOWN_STATE_NOT_PROBED" in result.warnings
    assert runner.calls == [(CommandId.WSL_LIST_VERBOSE, None)]


def test_explicit_authorization_probes_only_selected_stopped_distro():
    output = (
        "  NAME        STATE      VERSION\n"
        "  Ubuntu     Stopped    2\n"
        "  Debian     Stopped    2\n"
    )
    runner = FakeRunner(
        [
            _execution(CommandId.WSL_LIST_VERBOSE, stdout=output),
            _execution(CommandId.WSL_PROBE_DISTRO, stdout=json.dumps(_probe_payload(container=True))),
        ]
    )

    result = _adapter(runner).probe_distro("Ubuntu", allow_start=True)

    windows = result.snapshot.domains[0]
    wsl = windows.children[0]
    assert result.snapshot.status is CapabilityStatus.AVAILABLE
    assert wsl.children[0].kind.value == "CONTAINER"
    assert "PROBE_MAY_START_DISTRO" in result.warnings
    assert windows.capabilities.get("host_docker_daemon").status is CapabilityStatus.UNKNOWN
    assert windows.capabilities.get("security_posture").status is CapabilityStatus.UNKNOWN
    assert runner.calls == [
        (CommandId.WSL_LIST_VERBOSE, None),
        (CommandId.WSL_PROBE_DISTRO, "Ubuntu"),
    ]


@pytest.mark.parametrize("returncode", [127, 42])
def test_python_or_probe_module_missing_keeps_lite_available_and_full_not_present(returncode):
    output = "  NAME       STATE      VERSION\n  Ubuntu    Running    2\n"
    runner = FakeRunner(
        [
            _execution(CommandId.WSL_LIST_VERBOSE, stdout=output),
            _failure(
                CommandId.WSL_PROBE_DISTRO,
                CapabilityStatus.ERROR,
                DiscoveryErrorCode.COLLECTOR_FAILURE,
                returncode=returncode,
            ),
        ]
    )

    result = _adapter(runner).probe_distro("Ubuntu")

    wsl = result.snapshot.domains[0].children[0]
    assert result.snapshot.status is CapabilityStatus.DEGRADED
    assert wsl.capabilities.get("discovery_lite").status is CapabilityStatus.AVAILABLE
    assert wsl.capabilities.get("discovery_full").status is CapabilityStatus.NOT_PRESENT
    assert wsl.capabilities.get("discovery_full").error.code is DiscoveryErrorCode.NOT_PRESENT
    assert any(item.fact_type == "wsl.probe_command_result" for item in result.snapshot.evidence)


def test_missing_selected_distro_is_not_present_and_never_probed():
    runner = FakeRunner([_execution(CommandId.WSL_LIST_VERBOSE, stdout="")])

    result = _adapter(runner).probe_distro("Ubuntu")

    assert result.snapshot.status is CapabilityStatus.NOT_PRESENT
    assert result.snapshot.errors[0].code is DiscoveryErrorCode.NOT_PRESENT
    assert runner.calls == [(CommandId.WSL_LIST_VERBOSE, None)]


def test_corrupt_and_incompatible_probe_documents_map_to_distinct_failures():
    output = "  NAME       STATE      VERSION\n  Ubuntu    Running    2\n"
    corrupt_runner = FakeRunner(
        [
            _execution(CommandId.WSL_LIST_VERBOSE, stdout=output),
            _execution(CommandId.WSL_PROBE_DISTRO, stdout="{bad-json"),
        ]
    )
    incompatible = _probe_payload()
    incompatible["schema_version"] = "9.0"
    schema_runner = FakeRunner(
        [
            _execution(CommandId.WSL_LIST_VERBOSE, stdout=output),
            _execution(CommandId.WSL_PROBE_DISTRO, stdout=json.dumps(incompatible)),
        ]
    )

    corrupt = _adapter(corrupt_runner).probe_distro("Ubuntu")
    schema = _adapter(schema_runner).probe_distro("Ubuntu")

    assert corrupt.snapshot.status is CapabilityStatus.ERROR
    assert corrupt.snapshot.errors[-1].code is DiscoveryErrorCode.INVALID_DATA
    assert schema.snapshot.status is CapabilityStatus.UNSUPPORTED
    assert schema.snapshot.errors[-1].code is DiscoveryErrorCode.UNSUPPORTED


def test_discovery_never_calls_network_apis(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    runner = FakeRunner([_execution(CommandId.WSL_LIST_VERBOSE, stdout="")])

    snapshot = _adapter(runner).discover()

    assert snapshot.status is CapabilityStatus.AVAILABLE


def test_explicit_workspace_request_is_forwarded_to_only_the_selected_probe():
    output = "  NAME       STATE      VERSION\n  Ubuntu    Running    2\n"
    payload = _probe_payload()
    payload["cwd"] = "/workspace/project"
    runner = FakeRunner(
        [
            _execution(CommandId.WSL_LIST_VERBOSE, stdout=output),
            _execution(CommandId.WSL_PROBE_DISTRO, stdout=json.dumps(payload)),
        ]
    )

    result = _adapter(runner).probe_distro("Ubuntu", allow_workspace=True)

    assert runner.workspace_flags == [False, True]
    assert result.workspace is not None
    assert result.workspace.native_path == "/workspace/project"
    assert result.workspace.windows_display_only is True
