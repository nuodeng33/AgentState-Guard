"""Tests for blob store."""

import sqlite3
import tempfile
from pathlib import Path

from agentguard.storage.blob import BlobStore
from agentguard.storage.migrations import MigrationEngine


def _setup_db(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT, checksum TEXT, duration_ms INTEGER)")
    conn.commit()
    engine = MigrationEngine(db_path)
    engine.migrate(conn)
    return conn


class TestBlobStore:
    def setup_method(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="blob-test-"))
        self.db_path = self.tmp_dir / "test.db"
        self.conn = _setup_db(self.db_path)
        self.store = BlobStore(self.tmp_dir / "blobs")

    def teardown_method(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_put_and_get(self):
        sha = self.store.put(b"hello world", self.conn)
        assert len(sha) == 64
        data = self.store.get(sha)
        assert data == b"hello world"

    def test_dedup(self):
        sha1 = self.store.put(b"same data", self.conn)
        sha2 = self.store.put(b"same data", self.conn)
        assert sha1 == sha2
        row = self.conn.execute("SELECT ref_count FROM blobs WHERE sha256 = ?", (sha1,)).fetchone()
        assert row[0] == 2

    def test_verify(self):
        sha = self.store.put(b"verify me", self.conn)
        assert self.store.verify(sha) is True

    def test_verify_missing(self):
        assert self.store.verify("00" * 32) is False

    def test_release_and_orphan(self):
        sha = self.store.put(b"release test", self.conn)
        self.store.release(sha, self.conn)
        orphans = self.store.find_orphans(self.conn)
        assert sha in orphans

    def test_find_missing(self):
        # Insert a blob reference without actual file
        self.conn.execute(
            "INSERT INTO blobs (sha256, size_bytes, compressed_size, ref_count, created_at) VALUES (?, 0, 0, 1, '2026-01-01')",
            ("aa" * 32,),
        )
        self.conn.commit()
        missing = self.store.find_missing(self.conn)
        assert "aa" * 32 in missing

    def test_total_size(self):
        self.store.put(b"a", self.conn)
        self.store.put(b"bb", self.conn)
        stats = self.store.total_size(self.conn)
        assert stats["blob_count"] == 2
        assert stats["total_bytes"] >= 3

    def test_remove_from_disk(self):
        sha = self.store.put(b"remove me", self.conn)
        assert self.store.remove_from_disk(sha) is True
        assert self.store.get(sha) is None
