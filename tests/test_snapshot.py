"""Tests for snapshot module (v2)."""

from pathlib import Path

from agentguard.core.snapshot import (
    create_snapshot, create_file_snapshot, classify_file,
    serialize_snapshot, deserialize_snapshot, snapshot_is_valid,
    diff_snapshots, restore_file_content,
)


class TestSnapshot:
    def test_create_snapshot_v2_structure(self):
        snap = create_snapshot(
            label="test",
            file_snapshots={"/tmp/test.txt": {"mode": "audit_only", "sha256": "abc"}},
            config_snapshot={"key": "value"},
            versions={"python": "3.11"},
            container_state={"status": "running"},
            security_state={"privileged": False},
            git_info={"branch": "main", "commit": "abc"},
        )
        assert snap["format_version"] == 2
        assert snap["files"]["/tmp/test.txt"]["mode"] == "audit_only"
        assert snap["versions"]["python"] == "3.11"

    def test_serialize_deserialize_v2(self):
        snap = create_snapshot("v2", {}, {}, {}, {}, {}, {})
        raw = serialize_snapshot(snap)
        loaded = deserialize_snapshot(raw)
        assert loaded is not None
        assert loaded["format_version"] == 2

    def test_deserialize_corrupted(self):
        assert deserialize_snapshot(b"garbage") is None

    def test_snapshot_valid(self):
        f = Path("/tmp/test_snap.txt")
        f.write_text("content")
        entry = create_file_snapshot(f, "restorable")
        snap = create_snapshot("v", {str(f): entry}, {}, {}, {}, {}, {})
        raw = serialize_snapshot(snap)
        assert snapshot_is_valid(raw) is True

    def test_diff_no_changes(self):
        base = create_snapshot("base", {}, {}, {}, {}, {}, {})
        assert diff_snapshots(base, base) == []

    def test_diff_version_change(self):
        old = create_snapshot("old", {}, {}, {"py": "3.10"}, {}, {}, {})
        new = create_snapshot("new", {}, {}, {"py": "3.11"}, {}, {}, {})
        changes = diff_snapshots(new, old)
        assert any(c["type"] == "version_changed" for c in changes)

    def test_diff_hash_change_v2(self):
        old = create_snapshot("old", {"/f": {"mode": "audit_only", "sha256": "h1"}}, {}, {}, {}, {}, {})
        new = create_snapshot("new", {"/f": {"mode": "audit_only", "sha256": "h2"}}, {}, {}, {}, {}, {})
        changes = diff_snapshots(new, old)
        assert any(c["type"] == "hash_changed" for c in changes)

    def test_classify_audit(self, tmp_project: Path):
        f = tmp_project / "test.txt"
        f.write_text("hello")
        mode, _ = classify_file(f, [])
        assert mode == "audit_only"

    def test_classify_restorable(self, tmp_project: Path):
        f = tmp_project / "safe.txt"
        f.write_text("no secrets here")
        mode, _ = classify_file(f, [str(f)])
        assert mode == "restorable"

    def test_classify_sensitive_downgrade(self, tmp_project: Path):
        f = tmp_project / "creds.txt"
        f.write_text("API_KEY=sk-ant-test12345678")
        mode, _ = classify_file(f, [str(tmp_project)])
        assert mode == "audit_only"

    def test_file_snapshot_audit_hashes(self, tmp_project: Path):
        f = tmp_project / "a.txt"
        f.write_text("data")
        e = create_file_snapshot(f, "audit_only")
        assert e["sha256"] is not None
        assert "content_gz" not in e

    def test_file_snapshot_restorable_content(self, tmp_project: Path):
        f = tmp_project / "b.txt"
        data = "hello world"
        f.write_text(data)
        e = create_file_snapshot(f, "restorable")
        assert e["content_gz"] is not None
        restored = restore_file_content(e)
        assert restored == data.encode("utf-8")

    def test_restore_audit_returns_none(self, tmp_project: Path):
        f = tmp_project / "c.txt"
        f.write_text("data")
        e = create_file_snapshot(f, "audit_only")
        assert restore_file_content(e) is None

    def test_restorable_integrity_check(self, tmp_project: Path):
        f = tmp_project / "d.txt"
        f.write_text("integrity test")
        e = create_file_snapshot(f, "restorable")
        # Corrupt the content hash
        e["content_sha256"] = "badhash"
        assert restore_file_content(e) is None
