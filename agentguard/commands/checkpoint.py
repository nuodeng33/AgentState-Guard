"""checkpoint command — record environment state snapshot."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..core.hasher import hash_file
from ..core.snapshot import (
    create_snapshot, create_file_snapshot, classify_file, serialize_snapshot,
)
from ..core.docker import container_info
from ..core.versions import all_versions
from ..core.runner import run_command
from ..storage.db import StateDB
from ..storage.snapshots import SnapshotStore


def cmd_checkpoint(
    label: str,
    config: dict,
    db: StateDB,
    snapshots: SnapshotStore,
) -> Dict[str, object]:
    """Create a new environment checkpoint with full file snapshots."""
    git_branch, git_commit = _git_info()
    versions = all_versions()
    container_state = container_info(config.get("container_name", "agent-dev"))
    # No security facts are honestly derivable here; record none rather than
    # inventing one. Docker reachability is not evidence of privilege mode.
    security_state: Dict[str, bool] = {}

    # Build file snapshots
    restorable_paths = config.get("security", {}).get("restore_whitelist", [])
    file_snapshots: Dict[str, Dict[str, Any]] = {}
    tracked_paths = _get_tracked_paths(config)

    for path in tracked_paths:
        if not path or not path.is_file():
            continue
        mode, reason = classify_file(path, restorable_paths)
        entry = create_file_snapshot(path, mode)
        file_snapshots[str(path)] = entry

    snapshot_data = create_snapshot(
        label=label,
        file_snapshots=file_snapshots,
        config_snapshot=config,
        versions=versions,
        container_state=container_state,
        security_state=security_state,
        git_info={"branch": git_branch, "commit": git_commit},
    )

    # Serialize and compute snapshot hash
    raw = serialize_snapshot(snapshot_data)
    snap_hash = hashlib.sha256(raw).hexdigest()

    # Save to store (placeholder ID, will rename)
    snapshot_path_rel = snapshots.save(0, snapshot_data)

    cp_id = db.insert_checkpoint(
        label=label,
        snapshot_relative=snapshot_path_rel,
        hash_sha256=snap_hash,
        file_count=len(file_snapshots),
        versions=versions,
        git_branch=git_branch,
        git_commit=git_commit,
    )

    # Rename snapshot to use real ID
    old_path = snapshots.snapshot_dir / Path(snapshot_path_rel).name
    new_name = f"snapshot-{cp_id:06d}.dat"
    new_path = snapshots.snapshot_dir / new_name
    if old_path.exists():
        old_path.rename(new_path)
    if db._conn:
        db._conn.execute(
            "UPDATE checkpoints SET snapshot_path = ? WHERE id = ?",
            (str(new_path.relative_to(new_path.parent.parent)), cp_id),
        )
        db._conn.commit()

    restorable_count = sum(
        1 for e in file_snapshots.values() if e.get("mode") == "restorable"
    )
    audit_count = len(file_snapshots) - restorable_count

    return {
        "checkpoint_id": cp_id,
        "label": label,
        "created_at": snapshot_data["created_at_utc"],
        "total_files": len(file_snapshots),
        "restorable": restorable_count,
        "audit_only": audit_count,
        "git_branch": git_branch,
        "git_commit": git_commit,
        "sha256": snap_hash,
    }


def _git_info() -> Tuple[Optional[str], Optional[str]]:
    branch, commit = None, None
    for cmd, out_key in [(["git", "rev-parse", "--abbrev-ref", "HEAD"], "branch"),
                          (["git", "rev-parse", "HEAD"], "commit")]:
        try:
            r = run_command(cmd, timeout=5)
            if r.success and r.stdout:
                if out_key == "branch":
                    branch = r.stdout
                else:
                    commit = r.stdout[:12]
        except Exception:
            pass
    return branch, commit


def _get_tracked_paths(config: dict) -> list:
    """Return list of file paths to snapshot."""
    paths = []
    home = Path.home()
    config_dir = Path(config.get("base_dir", "/workspace"))

    # Always track key config files
    for p in [
        home / ".claude" / "settings.json",
        home / ".claude" / "settings.local.json",
    ]:
        if p:
            paths.append(p)

    # Config files from project
    for p in [
        config_dir / "config" / "agentguard.toml",
        config_dir / "config" / "agentguard.yaml",
    ]:
        if p and p.is_file():
            paths.append(p)

    return paths
