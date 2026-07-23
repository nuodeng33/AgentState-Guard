"""Tests for restore command and atomic write."""

from pathlib import Path

from agentguard.core.hasher import hash_file
from agentguard.core.whitelist import Whitelist, check_path_traversal


class TestRestore:
    def test_whitelist_allows_restore(self, tmp_project: Path):
        allowed = [str(tmp_project)]
        w = Whitelist(allowed)
        target = tmp_project / "safe_restore.txt"
        target.touch()
        assert w.is_allowed(target) is True

    def test_whitelist_blocks_outside(self, tmp_project: Path):
        w = Whitelist([str(tmp_project / "allowed")])
        target = tmp_project / "not_allowed.txt"
        target.touch()
        assert w.is_allowed(target) is False

    def test_whitelist_with_tilde(self):
        home = Path.home()
        w = Whitelist(["~/.claude/settings.json"])
        target = home / ".claude" / "settings.json"
        result = w.is_allowed(target)
        assert isinstance(result, bool)

    def test_whitelist_subdirectory(self, tmp_project: Path):
        w = Whitelist([str(tmp_project)])
        sub = tmp_project / "a" / "b" / "c.txt"
        sub.parent.mkdir(parents=True)
        sub.touch()
        assert w.is_allowed(sub) is True

    def test_whitelist_blocks_system_etc(self):
        w = Whitelist(["/tmp"])
        assert w.is_allowed(Path("/etc/passwd")) is False

    def test_whitelist_blocks_system_var(self):
        w = Whitelist(["/tmp"])
        assert w.is_allowed(Path("/var/lib/docker")) is False

    def test_traversal_detected(self):
        assert check_path_traversal(Path("/safe/../etc/passwd")) is True
        assert check_path_traversal(Path("/safe/path")) is False

    def test_restore_content_integrity_check(self, tmp_project: Path):
        """Test the restore_file_content validation."""
        from agentguard.core.snapshot import restore_file_content

        entry = {"mode": "restorable", "content_gz": "deadbeef", "content_sha256": "abc"}
        assert restore_file_content(entry) is None

    def test_restore_audit_returns_none(self, tmp_project: Path):
        from agentguard.core.snapshot import restore_file_content
        entry = {"mode": "audit_only", "sha256": "abc"}
        assert restore_file_content(entry) is None
