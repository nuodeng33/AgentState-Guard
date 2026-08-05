"""Static contract for this project's Claude Code autonomy policy."""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = PROJECT_ROOT / ".claude" / "settings.local.json"


BROAD_DENIES = {
    "Bash(git push *)",
    "Bash(git fetch *)",
    "Bash(git remote *)",
    "Bash(git -c *)",
    "Bash(curl *)",
    "Bash(wget *)",
}


REQUIRED_DENIES = {
    "Bash(git push --force*)",
    "Bash(git push --force-with-lease*)",
    "Bash(git push -f *)",
    "Bash(git push --delete *)",
    "Bash(git reset *)",
    "Bash(git clean *)",
    "Bash(git rebase *)",
    "Bash(git merge *)",
    "Bash(git config --global *)",
    "Bash(sudo *)",
    "Bash(apt *)",
    "Bash(apt-get *)",
    "Read(./.env)",
    "Read(./**/*secret*)",
    "Read(./**/*token*)",
    "Read(./**/*credential*)",
    "Read(/var/run/docker.sock)",
    "Read(~/.ssh/**)",
    "Read(~/.aws/**)",
    "Read(~/.kube/**)",
}


def _settings() -> dict:
    return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))


def test_project_autonomy_uses_auto_without_bypass_permissions():
    settings = _settings()

    assert settings["permissions"]["defaultMode"] == "auto"
    assert settings["permissions"]["defaultMode"] != "bypassPermissions"


def test_project_autonomy_removes_broad_development_denies():
    settings = _settings()

    assert BROAD_DENIES.isdisjoint(settings["permissions"]["deny"])


def test_project_autonomy_retains_destructive_and_sensitive_denies():
    settings = _settings()

    assert REQUIRED_DENIES.issubset(settings["permissions"]["deny"])


def test_project_autonomy_retains_project_local_retry_guard_hooks():
    settings = _settings()
    hooks = settings["hooks"]

    pre_commands = [
        hook["command"]
        for entry in hooks["PreToolUse"]
        if entry["matcher"] == "Bash"
        for hook in entry["hooks"]
    ]
    post_commands = [
        hook["command"]
        for entry in hooks["PostToolUse"]
        if entry["matcher"] == "Bash"
        for hook in entry["hooks"]
    ]

    assert any("${CLAUDE_PROJECT_DIR}/scripts/retry_guard.py" in command for command in pre_commands)
    assert any("${CLAUDE_PROJECT_DIR}/scripts/retry_guard.py" in command for command in post_commands)


def test_project_autonomy_does_not_ignore_the_claude_directory():
    ignore_rules = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert ".claude/" not in ignore_rules
    assert ".claude/**" not in ignore_rules
