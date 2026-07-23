"""diff command — compare current state against a checkpoint."""

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.hasher import hash_file
from ..core.snapshot import diff_snapshots, create_file_snapshot, classify_file
from ..core.sanitizer import sanitize_diff
from ..core.versions import all_versions
from ..storage.db import StateDB
from ..storage.snapshots import SnapshotStore


def cmd_diff(
    db: StateDB,
    snapshots: SnapshotStore,
    checkpoint_id: Optional[int] = None,
    config: Optional[dict] = None,
) -> Dict[str, object]:
    """Compare current state against a checkpoint."""
    target_id = checkpoint_id
    if target_id is None:
        cps = db.list_checkpoints(limit=1)
        if not cps:
            return {"error": "No checkpoints found. Run 'agentguard checkpoint' first."}
        target_id = cps[0]["id"]

    cp = db.get_checkpoint(target_id)
    if cp is None:
        return {"error": f"Checkpoint #{target_id} not found"}

    snap_data = snapshots.load(cp["snapshot_path"])
    if snap_data is None:
        return {"error": f"Snapshot #{target_id} is corrupt"}

    # Build current file snapshot for comparison
    restorable_paths = (config or {}).get("security", {}).get("restore_whitelist", [])
    current_files: Dict[str, Dict[str, Any]] = {}

    # Get tracked paths from snapshot reference
    ref_files = snap_data.get("files") or {}
    ref_hashes = snap_data.get("file_hashes") or {}

    all_paths = set(list(ref_files.keys()) + list(ref_hashes.keys()))
    for fpath_str in all_paths:
        p = Path(fpath_str)
        if p.is_file():
            mode, _ = classify_file(p, restorable_paths)
            current_files[fpath_str] = create_file_snapshot(p, mode)

    # Build current snapshot-like dict
    current_snap = {
        "files": current_files,
        "file_hashes": {},
        "versions": all_versions(),
    }

    # Use shared diff engine
    changes = diff_snapshots(current_snap, snap_data)

    # Count by type
    file_changes = [c for c in changes if c["type"] in ("hash_changed", "file_missing")]
    version_changes = [c for c in changes if c["type"] == "version_changed"]
    config_changes = [c for c in changes if c["type"] == "config_changed"]

    result: Dict[str, object] = {
        "checkpoint": {
            "id": cp["id"],
            "label": cp["label"],
            "created_at": cp["created_at"],
        },
        "file_changes": [],
        "version_changes": version_changes,
        "config_changes": config_changes,
        "changed_count": len(file_changes),
        "version_changed_count": len(version_changes),
        "config_changed_count": len(config_changes),
    }

    # Format file changes with sanitized paths
    for c in file_changes:
        entry = dict(c)
        if "key" in entry:
            entry["file"] = _sanitize_path(str(entry.pop("key")))
        result["file_changes"].append(entry)  # type: ignore

    return result


def _sanitize_path(path: str) -> str:
    home = str(Path.home())
    if path.startswith(home):
        return "~" + path[len(home):]
    return path
