"""Security contract tests for the only P2B subprocess boundary."""

from __future__ import annotations

import subprocess

import pytest

from agentguard.discovery import CapabilityStatus, DiscoveryErrorCode
from agentguard.discovery.command_runner import (
    PROBE_MODULE_MISSING_EXIT,
    CommandId,
    WslCommandRunner,
    validate_distro_name,
)


class RecordingExecutor:
    def __init__(self, result=None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.error is not None:
            raise self.error
        return self.result or subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")


def test_allowlisted_list_command_uses_fixed_argv_without_shell_or_inherited_environment():
    executor = RecordingExecutor()
    runner = WslCommandRunner(executor=executor)

    result = runner.run(CommandId.WSL_LIST_VERBOSE)

    argv, kwargs = executor.calls[0]
    assert argv[0].lower().endswith("wsl.exe")
    assert argv[1:] == ["--list", "--verbose"]
    assert kwargs == {
        "capture_output": True,
        "check": False,
        "env": {},
        "shell": False,
        "text": False,
        "timeout": 5.0,
    }
    assert result.command_id is CommandId.WSL_LIST_VERBOSE
    assert result.status is CapabilityStatus.AVAILABLE


@pytest.mark.parametrize("command_id", ["docker", "curl", "arbitrary-command"])
def test_non_allowlisted_command_id_is_rejected_before_subprocess(command_id):
    executor = RecordingExecutor()
    runner = WslCommandRunner(executor=executor)

    with pytest.raises(ValueError, match="not allowlisted"):
        runner.run(command_id)

    assert executor.calls == []


def test_distro_with_spaces_is_validated_and_passed_as_separate_argv():
    executor = RecordingExecutor()
    runner = WslCommandRunner(executor=executor)

    runner.run(CommandId.WSL_PROBE_DISTRO, distro="Ubuntu Dev")

    argv, _kwargs = executor.calls[0]
    assert argv[0].lower().endswith("wsl.exe")
    assert argv[1:4] == ["--distribution", "Ubuntu Dev", "--exec"]
    assert argv[-1] == "Ubuntu Dev"
    assert "Ubuntu Dev" in argv


def test_explicit_wsl_executable_must_be_an_absolute_wsl_exe_path():
    executor = RecordingExecutor()
    runner = WslCommandRunner(
        executor=executor,
        wsl_executable=r"C:\Windows\System32\wsl.exe",
    )

    runner.run(CommandId.WSL_LIST_VERBOSE)

    argv, kwargs = executor.calls[0]
    assert argv[0] == r"C:\Windows\System32\wsl.exe"
    assert kwargs["env"] == {}

    with pytest.raises(ValueError, match="absolute"):
        WslCommandRunner(executor=executor, wsl_executable="wsl.exe")


def test_default_wsl_path_uses_only_system_root_and_is_not_serialized(monkeypatch):
    monkeypatch.setenv("SystemRoot", r"D:\MockWindows")
    executor = RecordingExecutor()
    runner = WslCommandRunner(executor=executor)

    result = runner.run(CommandId.WSL_LIST_VERBOSE)

    argv, kwargs = executor.calls[0]
    assert argv[0] == r"D:\MockWindows\System32\wsl.exe"
    assert kwargs["env"] == {}
    encoded = str(result.to_dict())
    assert "MockWindows" not in encoded
    assert "SystemRoot" not in encoded


def test_workspace_probe_uses_one_fixed_flag_after_the_validated_distro():
    executor = RecordingExecutor()
    runner = WslCommandRunner(executor=executor)

    runner.run(
        CommandId.WSL_PROBE_DISTRO,
        distro="Ubuntu Dev",
        include_workspace=True,
    )

    argv, _kwargs = executor.calls[0]
    assert argv[4:7] == ["python3", "-c", argv[6]]
    assert argv[-2:] == ["Ubuntu Dev", "--workspace"]


def test_workspace_flag_is_rejected_for_non_probe_commands():
    executor = RecordingExecutor()
    runner = WslCommandRunner(executor=executor)

    with pytest.raises(ValueError, match="workspace"):
        runner.run(CommandId.WSL_LIST_VERBOSE, include_workspace=True)

    assert executor.calls == []


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "Ubuntu\n--exec",
        "Ubuntu\x00evil",
        "Ubuntu;curl example.invalid",
        "../Ubuntu",
        "a" * 129,
    ],
)
def test_malicious_or_ambiguous_distro_names_are_rejected(name):
    with pytest.raises(ValueError, match="distribution name"):
        validate_distro_name(name)


def test_missing_wsl_executable_maps_to_not_present_without_raw_command():
    runner = WslCommandRunner(executor=RecordingExecutor(error=FileNotFoundError("private path")))

    result = runner.run(CommandId.WSL_LIST_VERBOSE)

    assert result.status is CapabilityStatus.NOT_PRESENT
    assert result.error is not None
    assert result.error.code is DiscoveryErrorCode.NOT_PRESENT
    assert "private path" not in str(result.to_dict())
    assert "wsl.exe --list" not in str(result.to_dict())


def test_permission_error_maps_to_structured_permission_denied():
    runner = WslCommandRunner(executor=RecordingExecutor(error=PermissionError("private detail")))

    result = runner.run(CommandId.WSL_LIST_VERBOSE)

    assert result.status is CapabilityStatus.PERMISSION_DENIED
    assert result.error is not None
    assert result.error.code is DiscoveryErrorCode.PERMISSION_DENIED
    assert "private detail" not in str(result.to_dict())


def test_timeout_maps_to_error_with_timeout_code():
    timeout = subprocess.TimeoutExpired(["wsl.exe"], 5, output=b"partial secret")
    runner = WslCommandRunner(executor=RecordingExecutor(error=timeout))

    result = runner.run(CommandId.WSL_STATUS)

    assert result.status is CapabilityStatus.ERROR
    assert result.error is not None
    assert result.error.code is DiscoveryErrorCode.TIMEOUT
    assert result.stdout == ""
    assert "partial secret" not in str(result.to_dict())


def test_nonzero_exit_is_structured_without_stderr_contents():
    completed = subprocess.CompletedProcess(
        ["wsl.exe"],
        1,
        stdout=b"ignored",
        stderr=b"token=do-not-record",
    )
    runner = WslCommandRunner(executor=RecordingExecutor(result=completed))

    result = runner.run(CommandId.WSL_LIST_VERBOSE)

    assert result.status is CapabilityStatus.ERROR
    assert result.returncode == 1
    assert result.error is not None
    assert result.error.code is DiscoveryErrorCode.COLLECTOR_FAILURE
    assert "do-not-record" not in str(result.to_dict())


def test_utf16le_bom_output_is_decoded_for_windows_wsl_compatibility():
    output = "  NAME      STATE      VERSION\r\n* Ubuntu    Running    2\r\n".encode("utf-16")
    completed = subprocess.CompletedProcess(["wsl.exe"], 0, stdout=output, stderr=b"")
    runner = WslCommandRunner(executor=RecordingExecutor(result=completed))

    result = runner.run(CommandId.WSL_LIST_VERBOSE)

    assert result.status is CapabilityStatus.AVAILABLE
    assert "Ubuntu" in result.stdout
    assert "\x00" not in result.stdout


def test_non_utf8_output_maps_to_invalid_data():
    completed = subprocess.CompletedProcess(["wsl.exe"], 0, stdout=b"\x80\x81\x82", stderr=b"")
    runner = WslCommandRunner(executor=RecordingExecutor(result=completed))

    result = runner.run(CommandId.WSL_LIST_VERBOSE)

    assert result.status is CapabilityStatus.ERROR
    assert result.error is not None
    assert result.error.code is DiscoveryErrorCode.INVALID_DATA
    assert result.stdout == ""


def test_oversized_output_is_truncated_and_rejected():
    valid_json_prefix = b'{"schema_version":"1.0"}'
    completed = subprocess.CompletedProcess(
        ["wsl.exe"],
        0,
        stdout=valid_json_prefix + b" " * 20,
        stderr=b"",
    )
    runner = WslCommandRunner(
        executor=RecordingExecutor(result=completed),
        max_output_bytes=len(valid_json_prefix),
    )

    result = runner.run(CommandId.WSL_LIST_VERBOSE)

    assert result.status is CapabilityStatus.ERROR
    assert result.truncated is True
    assert result.error is not None
    assert result.error.code is DiscoveryErrorCode.INVALID_DATA
    assert result.error.details["reason_code"] == "OUTPUT_TOO_LARGE"
    assert "OUTPUT_TOO_LARGE" in result.warnings
    assert result.stdout == ""


def test_probe_module_missing_exit_code_is_stable_and_not_a_shell_status():
    assert PROBE_MODULE_MISSING_EXIT == 42
