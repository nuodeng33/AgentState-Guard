"""Contract tests for repository-local Retry Guard evidence handling."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "retry_guard.py"
SETTINGS = Path(__file__).parents[1] / ".claude" / "settings.local.json"
COMPAT_SCRIPT = Path(__file__).parents[1] / "scripts" / "retry-guard.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("retry_guard_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Retry Guard Test"], check=True)
    (tmp_path / "tracked.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True)
    (tmp_path / ".claude").mkdir()
    return tmp_path


def _guard_for_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    guard = _load_guard()
    monkeypatch.setattr(guard, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(guard, "STATE_DIR", tmp_path / ".claude")
    monkeypatch.setattr(guard, "STATE_FILE", tmp_path / ".claude" / "retry-guard-state.json")
    return guard


def _record_failure(guard, command: str, exit_code: str = "1") -> str:
    assert guard.pre_tool_use("Bash", command)["decision"] == "ALLOW"
    guard.post_tool_use("Bash", command, exit_code)
    return guard.fingerprint_command(command)


def test_working_tree_summary_is_stable_and_tracks_untracked_source_lifecycle(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    guard = _guard_for_repo(repo, monkeypatch)

    baseline = guard.get_working_tree_hash()
    assert baseline == guard.get_working_tree_hash()

    source = repo / "feature.py"
    source.write_text("value = 1\n", encoding="utf-8")
    added = guard.get_working_tree_hash()
    assert added != baseline

    source.write_text("value = 2\n", encoding="utf-8")
    changed = guard.get_working_tree_hash()
    assert changed != added

    source.unlink()
    assert guard.get_working_tree_hash() == baseline


def test_working_tree_summary_includes_staged_and_unstaged_tracked_changes(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    guard = _guard_for_repo(repo, monkeypatch)
    baseline = guard.get_working_tree_hash()

    tracked = repo / "tracked.py"
    tracked.write_text("value = 2\n", encoding="utf-8")
    assert guard.get_working_tree_hash() != baseline

    subprocess.run(["git", "-C", str(repo), "add", "tracked.py"], check=True)
    staged = guard.get_working_tree_hash()
    assert staged != baseline

    tracked.write_text("value = 3\n", encoding="utf-8")
    assert guard.get_working_tree_hash() != staged


def test_only_untracked_source_and_test_files_contribute_to_summary(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    guard = _guard_for_repo(repo, monkeypatch)
    baseline = guard.get_working_tree_hash()

    (repo / "notes.txt").write_text("not executable evidence\n", encoding="utf-8")
    assert guard.get_working_tree_hash() == baseline

    (repo / "web").mkdir()
    (repo / "web" / "widget.ts").write_text("export const value = 1;\n", encoding="utf-8")
    assert guard.get_working_tree_hash() != baseline


def test_generated_assets_and_guard_state_do_not_change_summary(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    guard = _guard_for_repo(repo, monkeypatch)
    baseline = guard.get_working_tree_hash()

    generated = repo / ".venv-validation" / "lib"
    generated.mkdir(parents=True)
    (generated / "generated.py").write_text("value = 1\n", encoding="utf-8")
    guard.STATE_FILE.write_text('{"failures": {}}', encoding="utf-8")

    assert guard.get_working_tree_hash() == baseline


def test_sensitive_untracked_file_does_not_store_or_read_its_body(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    guard = _guard_for_repo(repo, monkeypatch)
    sensitive = repo / "provider_token.txt"
    secret = "sensitive-fixture-value-never-persisted"
    sensitive.write_text(secret, encoding="utf-8")

    observed = []
    original_read_bytes = Path.read_bytes

    def recording_read_bytes(path: Path):
        if path == sensitive:
            observed.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", recording_read_bytes)
    summary = guard.get_working_tree_hash()
    _record_failure(guard, "run protected operation")

    assert summary
    assert observed == []
    assert secret not in guard.STATE_FILE.read_text(encoding="utf-8")


def test_new_evidence_clears_only_the_current_fingerprint(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    guard = _guard_for_repo(repo, monkeypatch)
    current = _record_failure(guard, "run current operation")
    other = _record_failure(guard, "run other operation")

    (repo / "new_test.py").write_text("assert True\n", encoding="utf-8")
    result = guard.pre_tool_use("Bash", "run current operation")
    state = guard.load_state()

    assert result["decision"] == "ALLOW"
    assert current not in state["failures"]
    assert other in state["failures"]


def test_third_identical_failure_blocks_and_new_evidence_allows_again(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    guard = _guard_for_repo(repo, monkeypatch)
    command = "run repeat operation"

    _record_failure(guard, command)
    _record_failure(guard, command)
    assert guard.pre_tool_use("Bash", command)["decision"] == "BLOCK"

    (repo / "diagnostic.py").write_text("diagnostic = True\n", encoding="utf-8")
    assert guard.pre_tool_use("Bash", command)["decision"] == "ALLOW"


def test_success_clears_only_current_fingerprint(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    guard = _guard_for_repo(repo, monkeypatch)
    current = _record_failure(guard, "run current operation")
    other = _record_failure(guard, "run other operation")

    assert guard.pre_tool_use("Bash", "run current operation")["decision"] == "ALLOW"
    guard.post_tool_use("Bash", "run current operation", "0")
    failures = guard.load_state()["failures"]

    assert current not in failures
    assert other in failures


def test_hook_uses_project_root_and_compatibility_entry_delegates():
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for group in ("PreToolUse", "PostToolUse")
        for item in settings["hooks"][group]
        if item["matcher"] == "Bash"
        for hook in item["hooks"]
    ]

    assert all("${CLAUDE_PROJECT_DIR}/scripts/retry_guard.py" in command for command in commands)
    assert "retry_guard import main" in COMPAT_SCRIPT.read_text(encoding="utf-8")
