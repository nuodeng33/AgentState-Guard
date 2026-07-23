"""Command execution with timeout and error handling."""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple


class CommandResult:
    """Result of a command execution."""

    def __init__(self, returncode: int, stdout: str, stderr: str, timed_out: bool = False):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def output(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()


def run_command(
    cmd: List[str],
    timeout: int = 15,
    cwd: Optional[Path] = None,
    check: bool = False,
    env: Optional[dict] = None,
) -> CommandResult:
    """Execute a command with timeout.

    Args:
        cmd: Command and arguments as list.
        timeout: Seconds before killing the process.
        cwd: Working directory.
        check: If True, raises on non-zero exit.
        env: Environment variables override.

    Returns:
        CommandResult with stdout, stderr, returncode.

    Raises:
        FileNotFoundError: If the command binary doesn't exist.
        PermissionError: If the command isn't executable.
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
        result = CommandResult(proc.returncode, proc.stdout.strip(), proc.stderr.strip())
    except subprocess.TimeoutExpired as e:
        partial_out = e.stdout.decode("utf-8", errors="replace") if e.stdout else ""
        partial_err = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        result = CommandResult(-1, partial_out.strip(), partial_err.strip(), timed_out=True)
    except FileNotFoundError:
        raise FileNotFoundError(f"Command not found: {cmd[0]}")
    except OSError as e:
        raise PermissionError(f"Cannot execute {cmd[0]}: {e}")

    if check and not result.success:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")

    return result


def which(binary: str) -> Optional[str]:
    """Return path to binary or None if not found."""
    return shutil.which(binary)
