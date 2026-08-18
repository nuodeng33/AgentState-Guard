"""Windows-native host facts regression tests (physical dogfood findings).

A native Windows host must never run Linux/container-only probes. Probes that
cannot be completed reliably report UNKNOWN / UNREACHABLE, never a false
ABSENT / FAIL. External Python detection follows real Windows semantics
(python.exe / py launcher) and is clearly distinguished from the bundled
sidecar runtime. Existing Linux/container behavior must not regress.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import agentguard.commands.doctor as doctor_module
import agentguard.commands.status as status_module
from agentguard.commands.doctor import doctor
from agentguard.commands.status import status
from agentguard.core.versions import all_versions

_WIN_ONLY_TOKENS = ("/proc/net/tcp", "ss", "bash", "/home/agent", "/workspace")


def _windows(*args, **kwargs):
    """Simulate a bare physical Windows host where no probe command exists."""
    raise FileNotFoundError("Command not found: Windows host simulation")


def _by_check(results):
    return {item["check"]: item for item in results}


class TestWindowsDoctor:
    def test_no_linux_probe_commands_spawned(self):
        with (
            patch.object(doctor_module.os, "name", "nt"),
            patch.object(doctor_module, "run_command") as run_mock,
            patch.object(doctor_module, "which", return_value=None),
        ):
            run_mock.side_effect = _windows
            results = doctor({"port": 3001})

        assert results, "doctor must still report on a bare Windows host"
        spawned = [call.args[0][0] for call in run_mock.call_args_list]
        for binary in spawned:
            assert binary not in ("ss", "bash", "docker")
        for item in results:
            for token in _WIN_ONLY_TOKENS:
                assert token not in str(item["message"])

    def test_tool_presence_unknown_or_absent_never_spurious_fail(self):
        with (
            patch.object(doctor_module.os, "name", "nt"),
            patch.object(doctor_module, "run_command") as run_mock,
            patch.object(doctor_module, "which", return_value=None),
        ):
            run_mock.side_effect = _windows
            results = doctor({})

        safe = {"PASS", "SKIP", "UNREACHABLE", "INFO", "WARN"}
        for check in ("python", "node", "git"):
            item = _by_check(results)[check]
            assert item["status"] in safe, item
        checks_run = {item["check"] for item in results}
        assert "python" not in checks_run or "python3" not in str(
            _by_check(results).get("python", {})
        )
        assert "config-files" not in checks_run
        assert "filesystem" not in checks_run
        assert "port" not in checks_run
        assert not any(
            str(item["message"]).endswith("not found")
            and item["status"] == "FAIL"
            for item in results
        )


class TestWindowsStatus:
    def test_no_linux_probe_commands_spawned(self):
        with (
            patch.object(status_module.os, "name", "nt"),
            patch.object(status_module, "run_command") as run_mock,
            patch.object(status_module, "docker_available", return_value=False),
            patch("agentguard.core.versions.run_command", side_effect=_windows),
        ):
            run_mock.side_effect = _windows
            status({})

        spawned = [call.args[0][0] for call in run_mock.call_args_list]
        for binary in spawned:
            assert binary not in ("ss", "bash")

    def test_port_check_key_matches_configured_port(self):
        calls = []

        def fake_run(cmd, timeout=15, **kwargs):
            calls.append(cmd[0])
            raise FileNotFoundError("missing")

        with (
            patch.object(status_module.os, "name", "posix"),
            patch.object(status_module, "run_command", side_effect=fake_run),
            patch.object(status_module, "docker_available", return_value=False),
            patch("agentguard.core.versions.run_command", side_effect=fake_run),
        ):
            result = status({"port": 8080})

        assert "port_8080" in result["checks"]
        assert "port_3001" not in result["checks"]


class TestWindowsExternalPython:
    def test_py_launcher_version_accepted_on_windows(self):
        with patch("agentguard.core.versions.os") as core_os:
            core_os.name = "nt"
            core_os.sep = "\\"
            core_os.pathsep = ";"
            core_os.environ = {}
            core_os.fspath = __import__("os").fspath
            with (
                patch.object(core_os.path, "isfile", lambda *_a: False),
                patch("agentguard.core.versions.which", return_value="C:\\Windows\\py.exe"),
                patch("agentguard.core.versions.run_command") as run_mock,
            ):
                run_mock.return_value = type(
                    "R", (), {"success": True, "stdout": "Python 3.12.4"}
                )()
                versions = all_versions()

        assert versions["python"] == "Python 3.12.4"
        commands = [call.args[0][0] for call in run_mock.call_args_list]
        assert "python3" not in commands
        assert "py" in commands

    def test_windows_bundled_sidecar_is_not_external_python(self, tmp_path):
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        sidecar = bundle / ("agentguard-sidecar" + (".exe" if sys.platform == "win32" else ""))
        sidecar.write_bytes(b"MZ")
        fake_sys = type(
            "FakeSys",
            (),
            {
                "_MEIPASS": str(bundle),
                "executable": str(sidecar),
                "frozen": True,
            },
        )()
        with (
            patch("agentguard.core.versions.sys", fake_sys),
            patch("agentguard.core.versions.which", return_value=None),
        ):
            versions = all_versions()
        assert "python" not in versions
        assert versions["python_bundled"] == "bundled"


class TestPosixPythonFallback:
    def test_posix_python3_missing_falls_back_to_python(self):
        def fake_which(binary):
            return "/usr/bin/python" if binary == "python" else None

        def fake_run(cmd, timeout=15, **kwargs):
            if cmd[0] == "python":
                return type("R", (), {"success": True, "stdout": "Python 3.11.2"})()
            raise FileNotFoundError(cmd[0])

        with (
            patch("agentguard.core.versions.which", side_effect=fake_which),
            patch("agentguard.core.versions.run_command", side_effect=fake_run),
        ):
            versions = all_versions()

        assert versions["python"] == "Python 3.11.2"
