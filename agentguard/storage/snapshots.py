"""Snapshot file management on disk."""

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.hasher import hash_file
from ..core.snapshot import serialize_snapshot, deserialize_snapshot, snapshot_is_valid


class SnapshotStore:
    """Manages snapshot files on disk."""

    def __init__(self, snapshot_dir: Path):
        self.snapshot_dir = snapshot_dir
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def save(self, snapshot_id: int, data: Dict[str, Any]) -> str:
        """Save a snapshot, return its relative path."""
        filename = f"snapshot-{snapshot_id:06d}.dat"
        path = self.snapshot_dir / filename
        compressed = serialize_snapshot(data)
        path.write_bytes(compressed)
        return str(path.relative_to(self.snapshot_dir.parent))

    def load(self, relative_path: str) -> Optional[Dict[str, Any]]:
        """Load a snapshot by its relative path."""
        path = (self.snapshot_dir / Path(relative_path).name).resolve()
        if not path.is_file():
            return None
        try:
            raw = path.read_bytes()
            return deserialize_snapshot(raw)
        except (OSError, ValueError):
            return None

    def load_by_id(self, snapshot_id: int) -> Optional[Dict[str, Any]]:
        """Load a snapshot by numeric ID."""
        path = self.snapshot_dir / f"snapshot-{snapshot_id:06d}.dat"
        if not path.is_file():
            return None
        return self.load(path.name)

    def validate(self, path: Path) -> bool:
        """Verify a snapshot file is valid."""
        if not path.is_file():
            return False
        try:
            raw = path.read_bytes()
            return snapshot_is_valid(raw)
        except (OSError, ValueError):
            return False

    def delete(self, relative_path: str) -> bool:
        """Delete a snapshot file."""
        path = (self.snapshot_dir / Path(relative_path).name).resolve()
        if path.is_file():
            path.unlink()
            return True
        return False

    def list_files(self) -> List[Path]:
        """Return all snapshot file paths sorted by ID."""
        files = sorted(
            self.snapshot_dir.glob("snapshot-*.dat"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return files
