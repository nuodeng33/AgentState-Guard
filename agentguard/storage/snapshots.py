"""Snapshot file management on disk."""

from pathlib import Path
from typing import Any

from ..core.snapshot import deserialize_snapshot, serialize_snapshot, snapshot_is_valid


class SnapshotStore:
    """Manages snapshot files on disk."""

    def __init__(self, snapshot_dir: Path):
        self.snapshot_dir = snapshot_dir
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def save(self, snapshot_id: int, data: dict[str, Any]) -> str:
        """Save a snapshot, return its relative path."""
        filename = f"snapshot-{snapshot_id:06d}.dat"
        path = self.snapshot_dir / filename
        compressed = serialize_snapshot(data)
        path.write_bytes(compressed)
        return str(path.relative_to(self.snapshot_dir.parent))

    def load(self, relative_path: str) -> dict[str, Any] | None:
        """Load a snapshot by its relative path."""
        path = (self.snapshot_dir / Path(relative_path).name).resolve()
        if not path.is_file():
            return None
        try:
            raw = path.read_bytes()
            return deserialize_snapshot(raw)
        except (OSError, ValueError):
            return None

    def load_by_id(self, snapshot_id: int) -> dict[str, Any] | None:
        """Load a snapshot by numeric ID."""
        path = self.snapshot_dir / f"snapshot-{snapshot_id:06d}.dat"
        if not path.is_file():
            return None
        return self.load(path.name)

    def save_recovery_v3(self, snapshot_id: int, artifact: dict[str, Any]) -> str:
        """Persist a P6 artifact without changing the legacy snapshot contract."""
        blobs = artifact.get("blobs")
        if artifact.get("format_version") != 3 or not isinstance(blobs, dict):
            raise ValueError("RECOVERY_ARTIFACT_INVALID")
        stored = dict(artifact)
        stored["blobs"] = {
            digest: content.hex()
            for digest, content in blobs.items()
            if isinstance(digest, str) and isinstance(content, bytes)
        }
        if len(stored["blobs"]) != len(blobs):
            raise ValueError("RECOVERY_ARTIFACT_INVALID")
        return self.save(snapshot_id, stored)

    def load_recovery_v3(self, relative_path: str) -> dict[str, Any] | None:
        """Load only a P6 artifact, decoding content-addressed blobs to bytes."""
        artifact = self.load(relative_path)
        if not isinstance(artifact, dict) or artifact.get("format_version") != 3:
            return None
        blobs = artifact.get("blobs")
        if not isinstance(blobs, dict):
            return None
        decoded: dict[str, bytes] = {}
        try:
            for digest, content in blobs.items():
                if not isinstance(digest, str) or not isinstance(content, str):
                    return None
                decoded[digest] = bytes.fromhex(content)
        except ValueError:
            return None
        artifact["blobs"] = decoded
        return artifact

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

    def list_files(self) -> list[Path]:
        """Return all snapshot file paths sorted by ID."""
        files = sorted(
            self.snapshot_dir.glob("snapshot-*.dat"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return files
