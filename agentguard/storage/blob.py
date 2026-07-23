"""Content-addressed blob store with dedup and integrity verification."""

import gzip
import hashlib
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


class BlobStore:
    """Content-addressed blob storage with dedup.

    Blobs are stored at .agentguard/blobs/<sha256-prefix>/<sha256>
    and referenced by SQLite with ref counting.
    """

    def __init__(self, blob_dir: Path):
        self.blob_dir = blob_dir

    def _blob_path(self, sha256: str) -> Path:
        prefix = sha256[:4]
        return self.blob_dir / prefix / sha256

    def put(self, data: bytes, conn: sqlite3.Connection) -> str:
        """Store blob, return SHA-256. Deduplicates automatically."""
        sha256 = hashlib.sha256(data).hexdigest()
        compressed = gzip.compress(data)
        path = self._blob_path(sha256)

        # Check if already exists
        existing = conn.execute(
            "SELECT sha256 FROM blobs WHERE sha256 = ?", (sha256,)
        ).fetchone()
        if existing:
            conn.execute("UPDATE blobs SET ref_count = ref_count + 1 WHERE sha256 = ?",
                         (sha256,))
            return sha256

        # Atomic write
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent))
        try:
            os.write(fd, compressed)
            os.fsync(fd)
            os.close(fd)
            os.replace(tmp, path)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

        conn.execute(
            "INSERT OR IGNORE INTO blobs (sha256, size_bytes, compressed_size, ref_count, created_at) "
            "VALUES (?, ?, ?, 1, ?)",
            (sha256, len(data), len(compressed), time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())),
        )
        return sha256

    def get(self, sha256: str) -> Optional[bytes]:
        """Retrieve blob content by SHA-256."""
        path = self._blob_path(sha256)
        if not path.is_file():
            return None
        try:
            compressed = path.read_bytes()
            return gzip.decompress(compressed)
        except (gzip.BadGzipFile, OSError):
            return None

    def verify(self, sha256: str) -> bool:
        """Verify blob integrity: hash matches."""
        data = self.get(sha256)
        if data is None:
            return False
        return hashlib.sha256(data).hexdigest() == sha256

    def release(self, sha256: str, conn: sqlite3.Connection) -> bool:
        """Decrement ref count. If zero, mark for GC (don't delete now)."""
        row = conn.execute("SELECT ref_count FROM blobs WHERE sha256 = ?",
                           (sha256,)).fetchone()
        if not row:
            return False
        new_count = row[0] - 1
        if new_count <= 0:
            conn.execute("UPDATE blobs SET ref_count = 0 WHERE sha256 = ?", (sha256,))
            return True
        conn.execute("UPDATE blobs SET ref_count = ? WHERE sha256 = ?", (new_count, sha256))
        return False

    def find_orphans(self, conn: sqlite3.Connection) -> List[str]:
        """Find blobs with ref_count = 0 (orphans)."""
        rows = conn.execute("SELECT sha256 FROM blobs WHERE ref_count <= 0").fetchall()
        return [r[0] for r in rows]

    def find_missing(self, conn: sqlite3.Connection) -> List[str]:
        """Find blobs in DB but missing on disk."""
        rows = conn.execute("SELECT sha256 FROM blobs").fetchall()
        missing = []
        for (sha256,) in rows:
            if not self._blob_path(sha256).is_file():
                missing.append(sha256)
        return missing

    def remove_from_disk(self, sha256: str) -> bool:
        """Delete a blob file from disk. Does NOT update DB."""
        path = self._blob_path(sha256)
        if path.is_file():
            path.unlink()
            return True
        return False

    def total_size(self, conn: sqlite3.Connection) -> Dict[str, int]:
        """Return storage statistics."""
        total = conn.execute("SELECT COALESCE(SUM(size_bytes),0) FROM blobs").fetchone()[0]
        compressed = conn.execute(
            "SELECT COALESCE(SUM(compressed_size),0) FROM blobs"
        ).fetchone()[0]
        count = conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0]
        return {
            "total_bytes": total,
            "compressed_bytes": compressed,
            "blob_count": count,
        }
