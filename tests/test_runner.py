"""Tests for runner module."""

from pathlib import Path

import pytest

from agentguard.core.runner import CommandResult, run_command, which


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


class TestNoConsoleWindowsInvocation:
    """Windows packaged-GUI contract: bounded probes must never flash consoles.

    The central runner passes CREATE_NO_WINDOW on Windows so child docker/node/
    git/python/agent-CLI probes launched by the GUI sidecar open no visible
    console window. POSIX passes the neutral 0 flag with behavior unchanged.
    """

    def test_run_command_always_passes_creationflags(self, monkeypatch):
        import subprocess

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = run_command(["docker", "--version"], timeout=5)
        assert result.success is True
        assert result.stdout == "ok"
        assert "creationflags" in captured, "runner must set console creation flags"

    def test_windows_constant_wires_create_no_window(self):
        import importlib
        import subprocess

        # Simulate the Windows-only constant, verify the runner picks it up,
        # then restore the module exactly as found (suite-order safety).
        original = getattr(subprocess, "CREATE_NO_WINDOW", None)
        subprocess.CREATE_NO_WINDOW = 0x08000000
        import agentguard.core.runner as runner_mod

        reloaded = importlib.reload(runner_mod)
        try:
            assert reloaded._NO_WINDOW_FLAGS == 0x08000000

            seen: dict = {}
            real_run = subprocess.run

            def fake_run(cmd, **kwargs):
                seen.update(kwargs)
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            subprocess.run = fake_run
            try:
                reloaded.run_command(["node", "--version"], timeout=5)
            finally:
                subprocess.run = real_run
            assert seen["creationflags"] == 0x08000000
        finally:
            if original is None:
                del subprocess.CREATE_NO_WINDOW
            else:
                subprocess.CREATE_NO_WINDOW = original
            importlib.reload(runner_mod)

    def test_posix_flag_is_neutral_zero(self):
        import sys

        import agentguard.core.runner as runner_mod

        if sys.platform.startswith("win"):
            pytest.skip("POSIX-neutral check only applies off Windows")
        assert runner_mod._NO_WINDOW_FLAGS == 0

    def test_real_invocation_still_captures_output_and_code(self):
        # Behavior preservation through the central flag change on the
        # platform running the suite: stdout/stderr/returncode contract.
        ok = run_command(["bash", "-c", "echo out-line; echo err-line >&2; exit 0"], timeout=5)
        assert ok.returncode == 0
        assert ok.stdout == "out-line"
        assert ok.stderr == "err-line"

        bad = run_command(["bash", "-c", "echo boom >&2; exit 7"], timeout=5)
        assert bad.returncode == 7
        assert bad.stderr == "boom"
