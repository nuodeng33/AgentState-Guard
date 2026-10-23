"""Tests for runner module."""

import os
import sys
from pathlib import Path

import pytest

from agentguard.core.runner import CommandResult, run_command, which


class TestRunner:
    def test_run_success(self):
        result = run_command([sys.executable, "-c", "print('hello')"], timeout=5)
        assert result.success is True
        assert "hello" in result.stdout

    def test_run_with_stdout(self):
        result = run_command([sys.executable, "-c", "print('test-output')"], timeout=5)
        assert result.returncode == 0
        assert result.stdout == "test-output"

    def test_run_with_exit_code(self):
        result = run_command(
            [sys.executable, "-c", "import sys; sys.exit(42)"], timeout=5
        )
        assert result.returncode == 42
        assert result.success is False

    def test_run_command_not_found(self):
        with pytest.raises(FileNotFoundError):
            run_command(["nonexistent-command-xyz"], timeout=5)

    def test_run_timeout(self):
        result = run_command(
            [sys.executable, "-c", "import time; time.sleep(10)"], timeout=1
        )
        assert result.timed_out is True
        assert result.returncode == -1

    def test_command_result_properties(self):
        r = CommandResult(0, "out", "")
        assert r.success is True
        assert r.output == "out"

    def test_command_result_failure(self):
        r = CommandResult(1, "", "error msg")
        assert r.success is False
        assert "error msg" in r.output

    def test_which_found(self, monkeypatch):
        executable = Path(sys.executable)
        monkeypatch.setenv(
            "PATH", str(executable.parent) + os.pathsep + os.environ.get("PATH", "")
        )
        path = which(executable.name)
        assert path is not None
        assert Path(path).resolve() == executable.resolve()

    def test_which_not_found(self):
        assert which("nonexistent-cmd-xyz789") is None

    def test_run_with_cwd(self, tmp_project: Path):
        result = run_command(
            [sys.executable, "-c", "import os; print(os.getcwd())"],
            timeout=5,
            cwd=tmp_project,
        )
        assert result.success
        assert Path(result.stdout).resolve() == tmp_project.resolve()

    def test_stderr_captured(self):
        result = run_command(
            [sys.executable, "-c", "import sys; print('err-msg', file=sys.stderr)"],
            timeout=5,
        )
        assert "err-msg" in result.stderr

    def test_run_directory_listing(self, tmp_project: Path):
        (tmp_project / "entry.txt").write_text("content")
        script = "from pathlib import Path; print(*(p.name for p in Path('.').iterdir()))"
        result = run_command(
            [sys.executable, "-c", script],
            timeout=5,
            cwd=tmp_project,
        )
        assert result.success is True
        assert "entry.txt" in result.stdout.splitlines()


@pytest.mark.parametrize("platform,expected", [("win32", 0x08000000), ("linux", 0)])
def test_background_probes_never_create_windows_console(monkeypatch, platform, expected):
    import subprocess

    from agentguard.core import runner
    calls = []
    monkeypatch.setattr(runner.sys, "platform", platform)
    monkeypatch.setattr(runner.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    def execute(cmd, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
    monkeypatch.setattr(runner.subprocess, "run", execute)
    assert runner.run_command(["probe"]).success
    assert calls[0]["creationflags"] == expected
