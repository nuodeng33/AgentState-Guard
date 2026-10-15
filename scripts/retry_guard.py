"""Repository-local Retry Guard hook.

The guard blocks a third identical failed command only when the repository
HEAD, bounded working-tree evidence, and diagnosis evidence are unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPOSITORY_ROOT / ".claude"
STATE_FILE = STATE_DIR / "retry-guard-state.json"
MAX_FAILURES = 3
MAX_UNTRACKED_BYTES = 65536
IGNORED_PARTS = {
    ".git", ".pytest_cache", "__pycache__", "node_modules", "dist", "build",
    "coverage", ".venv", ".venv-validation",
}
SOURCE_OR_TEST_SUFFIXES = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json"}
SENSITIVE_NAME = re.compile(
    r"(?:^|[_./-])(credential|secret|token|password|private[_-]?key|api[_-]?key)(?:$|[_./-])",
    re.IGNORECASE,
)
ALWAYS_ALLOW_PREFIXES = [
    "git status", "git diff", "git log", "git rev-parse", "git show", "git branch",
    "git ls-files", "gh run", "gh pr view", "python3 -m pytest -k ",
    "python3 -m pytest tests/test_", "cat ", "ls ", "find ", "which ", "head ",
    "tail ", "echo ", "pwd", "date",
]


class StateCorruptionError(RuntimeError):
    pass


def _git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=REPOSITORY_ROOT, stderr=subprocess.DEVNULL)


def get_git_head() -> str:
    try:
        return _git("rev-parse", "HEAD").decode().strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _ignored(path: Path) -> bool:
    relative = path.relative_to(REPOSITORY_ROOT)
    if relative == Path(".claude/retry-guard-state.json"):
        return True
    return any(part in IGNORED_PARTS or part.startswith(".venv-") for part in relative.parts)


def _untracked_summary() -> bytes:
    try:
        names = _git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    except (OSError, subprocess.CalledProcessError):
        return b""
    digest = hashlib.sha256()
    for raw_name in sorted(name for name in names if name):
        relative = Path(os.fsdecode(raw_name))
        path = REPOSITORY_ROOT / relative
        if not path.is_file() or _ignored(path) or relative.suffix.lower() not in SOURCE_OR_TEST_SUFFIXES:
            continue
        digest.update(b"path\0")
        digest.update(raw_name)
        if SENSITIVE_NAME.search(relative.as_posix()):
            digest.update(b"sensitive-present\0")
            continue
        digest.update(b"content\0")
        with path.open("rb") as source:
            digest.update(source.read(MAX_UNTRACKED_BYTES))
    return digest.digest()


def get_working_tree_hash() -> str:
    """Return a bounded stable digest of staged, unstaged, and untracked evidence."""
    try:
        digest = hashlib.sha256()
        digest.update(b"unstaged\0")
        digest.update(_git("diff", "--no-color", "--binary"))
        digest.update(b"staged\0")
        digest.update(_git("diff", "--cached", "--no-color", "--binary"))
        digest.update(b"untracked\0")
        digest.update(_untracked_summary())
        return digest.hexdigest()[:16]
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def get_diagnosis_ledger_mtime() -> str:
    debug_dir = REPOSITORY_ROOT / "artifacts" / "debug"
    if not debug_dir.exists():
        return "none"
    files = list(debug_dir.rglob("diagnosis.md"))
    return str(max(file.stat().st_mtime for file in files)) if files else "none"


def fingerprint_command(command: str) -> str:
    return hashlib.sha256(command.strip().encode()).hexdigest()[:16]


def is_always_allowed(tool: str, command: str) -> bool:
    return tool == "Bash" and any(command.strip().startswith(prefix) for prefix in ALWAYS_ALLOW_PREFIXES)


def _validate_state(value: object) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("failures"), dict):
        raise StateCorruptionError("invalid state structure")
    current = value.get("_current")
    if current is not None and not isinstance(current, dict):
        raise StateCorruptionError("invalid current structure")
    if not all(isinstance(entry, dict) for entry in value["failures"].values()):
        raise StateCorruptionError("invalid failure entry")
    return value


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"failures": {}}
    try:
        return _validate_state(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, StateCorruptionError) as error:
        raise StateCorruptionError("retry guard state is invalid") from error


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")


def _clear_current_failure(failures: dict, fingerprint: str) -> None:
    failures.pop(fingerprint, None)


def pre_tool_use(tool: str, command: str) -> dict:
    if is_always_allowed(tool, command):
        return {"decision": "ALLOW", "reason": "Always-allowed command"}
    try:
        state = load_state()
    except StateCorruptionError:
        return {"decision": "BLOCK", "reason": "RETRY_GUARD_STATE_INVALID"}

    fingerprint = fingerprint_command(command)
    head, tree, ledger = get_git_head(), get_working_tree_hash(), get_diagnosis_ledger_mtime()
    failures = state["failures"]
    entry = failures.get(fingerprint, {})
    changed = (
        fingerprint in failures
        and (entry.get("head_hash") != head or entry.get("tree_hash") != tree or entry.get("ledger_mtime") != ledger)
    )
    if changed:
        _clear_current_failure(failures, fingerprint)
        entry = {}
    attempt = int(entry.get("attempts", 0)) + 1
    state["_current"] = {
        "fp": fingerprint, "head": head, "tree_hash": tree, "ledger_mtime": ledger,
    }
    save_state(state)
    if not changed and attempt >= MAX_FAILURES:
        return {"decision": "BLOCK", "reason": "REPEATED_FAILURE_WITHOUT_NEW_EVIDENCE"}
    return {"decision": "ALLOW", "reason": "New evidence" if changed else "Retry allowed"}


def post_tool_use(tool: str, command: str, exit_code: str, stderr_sample: str = "") -> None:
    del stderr_sample
    if is_always_allowed(tool, command):
        return
    try:
        state = load_state()
    except StateCorruptionError:
        return
    current = state.pop("_current", {})
    fingerprint = current.get("fp") or fingerprint_command(command)
    failures = state["failures"]
    exit_int = int(exit_code) if exit_code and exit_code != "None" else 0
    if exit_int not in (0, 130):
        entry = failures.get(fingerprint, {})
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["first_failure_ts"] = entry.get("first_failure_ts", time.time())
        entry["last_exit_code"] = exit_int
        entry["head_hash"] = current.get("head", get_git_head())
        entry["tree_hash"] = current.get("tree_hash", get_working_tree_hash())
        entry["ledger_mtime"] = current.get("ledger_mtime", get_diagnosis_ledger_mtime())
        entry["last_tool"] = tool
        failures[fingerprint] = entry
    else:
        _clear_current_failure(failures, fingerprint)
    save_state(state)


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: retry_guard.py <pre|post> <tool_name> [args...]", file=sys.stderr)
        raise SystemExit(1)
    mode, tool = sys.argv[1], sys.argv[2]
    if mode == "pre":
        result = pre_tool_use(tool, " ".join(sys.argv[3:]))
        print(json.dumps(result))
        if result["decision"] == "BLOCK":
            raise SystemExit(2)
    elif mode == "post":
        post_tool_use(tool, sys.argv[3] if len(sys.argv) > 3 else "", sys.argv[4] if len(sys.argv) > 4 else "0")
    else:
        print("Unknown mode", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
