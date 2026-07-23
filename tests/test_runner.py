"""Tests for runner module."""

import pytest
from pathlib import Path
from agentguard.core.runner import run_command, CommandResult, which


class TestRunner:
    def test_run_success(self):
        result = run_command(["echo", "hello"], timeout=5)
        assert result.success is True
        assert "hello" in result.stdout

    def test_run_with_stdout(self):
        result = run_command(["echo", "test-output"], timeout=5)
        assert result.returncode == 0
        assert result.stdout == "test-output"

    def test_run_with_exit_code(self):
        result = run_command(["bash", "-c", "exit 42"], timeout=5)
        assert result.returncode == 42
        assert result.success is False

    def test_run_command_not_found(self):
        with pytest.raises(FileNotFoundError):
            run_command(["nonexistent-command-xyz"], timeout=5)

    def test_run_timeout(self):
        result = run_command(["sleep", "10"], timeout=1)
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

    def test_which_found(self):
        path = which("echo")
        assert path is not None
        assert "/echo" in path or path.endswith("echo")

    def test_which_not_found(self):
        assert which("nonexistent-cmd-xyz789") is None

    def test_run_with_cwd(self, tmp_project: Path):
        result = run_command(["pwd"], timeout=5, cwd=tmp_project)
        assert result.success
        assert str(tmp_project.resolve()) in result.stdout

    def test_stderr_captured(self):
        result = run_command(["bash", "-c", "echo err-msg >&2"], timeout=5)
        assert "err-msg" in result.stderr

    def test_run_ls(self):
        result = run_command(["ls", "/"], timeout=5)
        assert result.success is True
        assert "etc" in result.stdout or "usr" in result.stdout
