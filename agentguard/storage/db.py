"""SQLite database for checkpoint metadata."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class StateDB:
    """SQLite-backed checkpoint metadata store."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        """Open or create the SQLite database."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _init_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                label       TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                snapshot_path TEXT NOT NULL,
                hash_sha256 TEXT NOT NULL,
                file_count  INTEGER DEFAULT 0,
                version_info TEXT,
                git_branch  TEXT,
                git_commit  TEXT,
                notes       TEXT
            );
            CREATE TABLE IF NOT EXISTS restore_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                checkpoint_id INTEGER,
                file_path   TEXT NOT NULL,
                restored_at TEXT NOT NULL,
                hash_before TEXT,
                hash_after  TEXT,
                status      TEXT NOT NULL,
                FOREIGN KEY (checkpoint_id) REFERENCES checkpoints(id)
            );
            CREATE INDEX IF NOT EXISTS idx_checkpoints_created
                ON checkpoints(created_at DESC);
        """)
        self._conn.commit()

    def insert_checkpoint(
        self,
        label: str,
        snapshot_relative: str,
        hash_sha256: str,
        file_count: int,
        versions: Dict[str, Optional[str]],
        git_branch: Optional[str],
        git_commit: Optional[str],
    ) -> int:
        """Insert a checkpoint record, return its ID."""
        assert self._conn is not None
        import json
        cur = self._conn.execute(
            """INSERT INTO checkpoints
               (label, created_at, snapshot_path, hash_sha256, file_count,
                version_info, git_branch, git_commit)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                label,
                datetime.now(timezone.utc).isoformat(),
                snapshot_relative,
                hash_sha256,
                file_count,
                json.dumps(versions, default=str),
                git_branch,
                git_commit,
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def list_checkpoints(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent checkpoint records."""
        assert self._conn is not None
        rows = self._conn.execute(
            """SELECT id, label, created_at, snapshot_path, hash_sha256,
                      file_count, git_branch, git_commit
               FROM checkpoints
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "label": r[1],
                "created_at": r[2],
                "snapshot_path": r[3],
                "hash_sha256": r[4],
                "file_count": r[5],
                "git_branch": r[6],
                "git_commit": r[7],
            }
            for r in rows
        ]

    def get_checkpoint(self, checkpoint_id: int) -> Optional[Dict[str, Any]]:
        """Return a single checkpoint by ID."""
        assert self._conn is not None
        r = self._conn.execute(
            "SELECT * FROM checkpoints WHERE id = ?", (checkpoint_id,)
        ).fetchone()
        if r is None:
            return None
        return {
            "id": r[0],
            "label": r[1],
            "created_at": r[2],
            "snapshot_path": r[3],
            "hash_sha256": r[4],
            "file_count": r[5],
            "version_info": r[6],
            "git_branch": r[7],
            "git_commit": r[8],
            "notes": r[9],
        }

    def log_restore(
        self,
        checkpoint_id: int,
        file_path: str,
        hash_before: Optional[str],
        hash_after: Optional[str],
        status: str,
    ) -> None:
        """Record a restore operation."""
        assert self._conn is not None
        self._conn.execute(
            """INSERT INTO restore_log
               (checkpoint_id, file_path, restored_at, hash_before, hash_after, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                checkpoint_id,
                file_path,
                datetime.now(timezone.utc).isoformat(),
                hash_before,
                hash_after,
                status,
            ),
        )
        self._conn.commit()
