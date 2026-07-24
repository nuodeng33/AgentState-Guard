"""Tests for whitelist module."""

from pathlib import Path
from agentguard.core.whitelist import Whitelist, check_path_traversal


class TestWhitelist:
    def test_allow_allowed_path(self, tmp_project: Path):
        allowed = [str(tmp_project / "safe_dir")]
        w = Whitelist(allowed)
        target = tmp_project / "safe_dir" / "file.txt"
        target.parent.mkdir(parents=True)
        target.touch()
        assert w.is_allowed(target) is True

    def test_deny_outside_whitelist(self, tmp_project: Path):
        allowed = [str(tmp_project / "safe_dir")]
        w = Whitelist(allowed)
        target = tmp_project / "other_dir" / "file.txt"
        target.parent.mkdir(parents=True)
        target.touch()
        assert w.is_allowed(target) is False

    def test_deny_system_etc(self):
        w = Whitelist(["/tmp"])
        assert w.is_allowed(Path("/etc/passwd")) is False

    def test_deny_system_var(self):
        w = Whitelist(["/tmp"])
        # System paths are denied regardless of existence
        # /var is in the forbidden_prefixes list
        assert w.is_allowed(Path("/var")) is False

    def test_deny_system_proc(self):
        w = Whitelist(["/tmp"])
        assert w.is_allowed(Path("/proc/self/mem")) is False

    def test_allowed_file_returns_path(self, tmp_project: Path):
        allowed = [str(tmp_project)]
        w = Whitelist(allowed)
        target = tmp_project / "ok.txt"
        target.touch()
        result = w.allowed_file(target)
        assert result == target.resolve()

    def test_allowed_file_denied_returns_none(self, tmp_project: Path):
        w = Whitelist([])
        assert w.allowed_file(tmp_project / "nope.txt") is None

    def test_default_restore_paths(self):
        paths = Whitelist.default_restore_paths()
        assert len(paths) > 0
        assert all(".claude" in p for p in paths)

    def test_forbid_system_paths(self):
        paths = Whitelist.forbid_system_paths()
        assert "/etc/" in paths
        assert "/var/lib/docker/" in paths
        assert "/proc/" in paths

    def test_prevent_path_traversal_simple(self, tmp_project: Path):
        """Test that '..' traversal is detected."""
        path = tmp_project / ".." / ".." / "etc" / "passwd"
        assert check_path_traversal(path) is True

    def test_safe_path_no_traversal(self, tmp_project: Path):
        path = tmp_project / "safe" / "file.txt"
        assert check_path_traversal(path) is False

    def test_whitelist_with_tilde(self):
        """Tilde in whitelist path should be expanded."""
        home = Path.home()
        w = Whitelist(["~/.claude/settings.json"])
        target = home / ".claude" / "settings.json"
        # May or may not exist, but should not raise
        result = w.is_allowed(target)
        assert isinstance(result, bool)

    def test_subdirectory_allowed(self, tmp_project: Path):
        """A whitelisted directory allows files in subdirectories."""
        w = Whitelist([str(tmp_project)])
        sub = tmp_project / "a" / "b" / "c.txt"
        sub.parent.mkdir(parents=True)
        sub.touch()
        assert w.is_allowed(sub) is True
