#!/usr/bin/env python3
"""Retry Guard — PreToolUse hook for Claude Code v2.1.

Tracks failed command invocations and blocks the third identical failure
when no new evidence has been gathered.

State file: .claude/retry-guard-state.json

Usage (PreToolUse):
    python3 scripts/retry-guard.py pre <tool_name> <command_text>

Usage (PostToolUse):
    python3 scripts/retry-guard.py post <tool_name> <command_text> <exit_code> <stderr_sample>

Default blocking rule:
  - 3rd identical failure (same command fingerprint, same exit code)
  - Git HEAD unchanged
  - Working tree hash unchanged
  - diagnosis ledger mtime unchanged

Output:
  - "ALLOW" with optional warning
  - "BLOCK" with REPEATED_FAILURE_WITHOUT_NEW_EVIDENCE message
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


STATE_DIR = Path(__file__).resolve().parent.parent / ".claude"
STATE_FILE = STATE_DIR / "retry-guard-state.json"
MAX_FAILURES = 3

# Commands never blocked
ALWAYS_ALLOW_PREFIXES = [
    "git status",
    "git diff",
    "git log",
    "git rev-parse",
    "git show",
    "git branch",
    "git ls-files",
    "gh run",
    "gh pr view",
    "python3 -m pytest -k ",
    "python3 -m pytest tests/test_",
    "cat ",
    "ls ",
    "find ",
    "which ",
    "head ",
    "tail ",
    "echo ",
    "pwd",
    "date",
]

# Commands that RESET failure count (success)
RESET_ON_SUCCESS = True


def get_git_head() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, cwd=STATE_DIR.parent
            )
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def get_working_tree_hash() -> str:
    try:
        result = subprocess.check_output(
            ["git", "diff", "--no-color", "--no-stat"],
            stderr=subprocess.DEVNULL,
            cwd=STATE_DIR.parent,
        )
        return hashlib.sha256(result).hexdigest()[:16]
    except Exception:
        return "unknown"


def get_diagnosis_ledger_mtime() -> str:
    debug_dir = STATE_DIR.parent / "artifacts" / "debug"
    if not debug_dir.exists():
        return "none"
    diagnosis_files = list(debug_dir.rglob("diagnosis.md"))
    if not diagnosis_files:
        return "none"
    latest = max(f.stat().st_mtime for f in diagnosis_files)
    return str(latest)


def fingerprint_command(command: str) -> str:
    """Create a fingerprint for a command, ignoring variable arguments."""
    # Remove common variable parts
    cleaned = command.strip()
    # Hash the cleaned command
    return hashlib.sha256(cleaned.encode()).hexdigest()[:16]


def is_always_allowed(tool: str, command: str) -> bool:
    if tool not in ("Bash",):
        return False
    cmd_trimmed = command.strip()
    for prefix in ALWAYS_ALLOW_PREFIXES:
        if cmd_trimmed.startswith(prefix):
            return True
    return False


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {"failures": {}}
    return {"failures": {}}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def redact_secrets(text: str) -> str:
    """Redact potential secrets from command text."""
    import re

    # Redact token patterns
    patterns = [
        (r"--token[= ]\S+", "--token=<REDACTED>"),
        (r"--password[= ]\S+", "--password=<REDACTED>"),
        (r"--key[= ]\S+", "--key=<REDACTED>"),
        (r"Authorization: Bearer \S+", "Authorization: Bearer <REDACTED>"),
        (r"ghp_[A-Za-z0-9]{36}", "ghp_<REDACTED>"),
        (r"gho_[A-Za-z0-9]{36}", "gho_<REDACTED>"),
        (r"xox[baprs]-[A-Za-z0-9-]+", "xox<REDACTED>"),
        (r"sk-[A-Za-z0-9]{20,}", "sk-<REDACTED>"),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


def pre_tool_use(tool: str, command: str) -> dict:
    if is_always_allowed(tool, command):
        return {"decision": "ALLOW", "reason": "Always-allowed command"}

    fp = fingerprint_command(command)
    head = get_git_head()
    tree_hash = get_working_tree_hash()
    ledger_mtime = get_diagnosis_ledger_mtime()

    state = load_state()
    failures = state.get("failures", {})

    entry = failures.get(fp, {})
    attempt = entry.get("attempts", 0) + 1
    first_ts = entry.get("first_failure_ts", time.time())
    last_head = entry.get("head_hash", head)
    last_tree = entry.get("tree_hash", tree_hash)
    last_ledger = entry.get("ledger_mtime", ledger_mtime)
    last_exit = entry.get("last_exit_code")

    # Store attempt info for post-use
    state["_current"] = {
        "fp": fp,
        "attempt": attempt,
        "head": head,
        "tree_hash": tree_hash,
        "ledger_mtime": ledger_mtime,
        "command": redact_secrets(command),
    }
    save_state(state)

    # Check block condition
    head_unchanged = head == last_head
    tree_unchanged = tree_hash == last_tree
    ledger_unchanged = ledger_mtime == last_ledger

    # If HEAD or working tree changed, reset
    if not head_unchanged or not tree_unchanged:
        if fp in failures:
            del failures[fp]
            state["failures"] = failures
            save_state(state)
        return {"decision": "ALLOW", "reason": "Code changed since last failure"}

    # If ledger was updated, reset
    if ledger_mtime != last_ledger and last_ledger != "none":
        if fp in failures:
            del failures[fp]
            state["failures"] = failures
            save_state(state)
        return {"decision": "ALLOW", "reason": "New diagnosis ledger entry added"}

    if attempt >= MAX_FAILURES:
        return {
            "decision": "BLOCK",
            "reason": "REPEATED_FAILURE_WITHOUT_NEW_EVIDENCE",
            "detail": (
                f"Command '{redact_secrets(command)}' has failed {attempt} times "
                f"with the same fingerprint. Git HEAD and working tree unchanged. "
                f"No new diagnosis ledger entry found. "
                f"Invoke /failure-investigator before retrying."
            ),
        }

    return {"decision": "ALLOW", "warn": f"Failure attempt {attempt}/{MAX_FAILURES}"}


def post_tool_use(tool: str, command: str, exit_code: str, stderr_sample: str = ""):
    if is_always_allowed(tool, command):
        return

    state = load_state()
    current = state.pop("_current", {})

    fp = current.get("fp") or fingerprint_command(command)
    exit_int = int(exit_code) if exit_code and exit_code != "None" else 0

    failures = state.get("failures", {})

    if exit_int != 0 and exit_int != 130:  # Non-zero exit and not SIGINT
        entry = failures.get(fp, {})
        entry["attempts"] = entry.get("attempts", 0) + 1
        entry["first_failure_ts"] = entry.get("first_failure_ts", time.time())
        entry["last_exit_code"] = exit_int
        entry["head_hash"] = current.get("head", get_git_head())
        entry["tree_hash"] = current.get("tree_hash", get_working_tree_hash())
        entry["ledger_mtime"] = current.get("ledger_mtime", get_diagnosis_ledger_mtime())
        entry["last_command"] = redact_secrets(command[:200])
        entry["last_tool"] = tool
        failures[fp] = entry
    else:
        # Success — reset failure count
        if fp in failures:
            del failures[fp]

    state["failures"] = failures
    save_state(state)


def main():
    if len(sys.argv) < 3:
        print("Usage: retry-guard.py <pre|post> <tool_name> [args...]", file=sys.stderr)
        sys.exit(1)

    mode = sys.argv[1]
    tool = sys.argv[2]

    if mode == "pre":
        command = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else ""
        result = pre_tool_use(tool, command)
        print(json.dumps(result))
        if result.get("decision") == "BLOCK":
            sys.exit(2)

    elif mode == "post":
        command = sys.argv[3] if len(sys.argv) > 3 else ""
        exit_code = sys.argv[4] if len(sys.argv) > 4 else "0"
        stderr_sample = sys.argv[5] if len(sys.argv) > 5 else ""
        post_tool_use(tool, command, exit_code, stderr_sample)

    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
