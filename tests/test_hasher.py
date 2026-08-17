"""Tests for hasher module."""

from pathlib import Path

from agentguard.core.hasher import check_known_hash, hash_bytes, hash_file, hash_text


class TestHasher:
    def test_hash_file_exists(self, test_file: Path):
        h = hash_file(test_file)
        assert h is not None
        assert len(h) == 64  # SHA-256 hex

    def test_hash_file_not_found(self):
        h = hash_file(Path("/nonexistent/file.txt"))
        assert h is None

    def test_hash_file_directory(self, tmp_project: Path):
        h = hash_file(tmp_project)
        assert h is None

    def test_hash_file_permission_denied(self, tmp_path: Path, monkeypatch):
        restricted = tmp_path / "restricted.txt"
        restricted.write_text("content")

        def deny_open(_path, *_args, **_kwargs):
            raise PermissionError("synthetic permission denial")

        monkeypatch.setattr(Path, "open", deny_open)
        assert hash_file(restricted) is None

    def test_hash_bytes_consistency(self):
        data = b"test data"
        assert hash_bytes(data) == hash_bytes(data)

    def test_hash_bytes_different(self):
        assert hash_bytes(b"data1") != hash_bytes(b"data2")

    def test_hash_text(self):
        assert hash_text("hello") == hash_text("hello")
        assert hash_text("hello") != hash_text("world")

    def test_check_known_hash_match(self, test_file: Path):
        h = hash_file(test_file)
        assert h is not None
        assert check_known_hash(test_file, h) is True

    def test_check_known_hash_mismatch(self, test_file: Path):
        assert check_known_hash(test_file, "0" * 64) is False

    def test_check_known_hash_missing(self):
        assert check_known_hash(Path("/nonexistent"), "0" * 64) is False

    def test_hash_different_algorithms(self, test_file: Path):
        sha256 = hash_file(test_file, "sha256")
        sha1 = hash_file(test_file, "sha1")
        assert sha256 is not None and sha1 is not None
        assert sha256 != sha1

    def test_hash_identical_content(self, tmp_project: Path):
        a = tmp_project / "a.txt"
        b = tmp_project / "b.txt"
        a.write_text("same content")
        b.write_text("same content")
        assert hash_file(a) == hash_file(b)
