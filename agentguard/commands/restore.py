"""restore command — restore a single whitelisted file from checkpoint content."""

import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from ..core.hasher import hash_file
from ..core.snapshot import restore_file_content, classify_file, create_file_snapshot
from ..core.whitelist import Whitelist
from ..core.sanitizer import sanitize_text
from ..storage.db import StateDB
from ..storage.snapshots import SnapshotStore


def cmd_restore(
    target_path: str,
    checkpoint_id: int,
    db: StateDB,
    snapshots: SnapshotStore,
    whitelist: Whitelist,
    restorable_paths: list,
    yes: bool = False,
) -> Dict[str, object]:
    """Restore a single whitelisted file from checkpoint content.

    Only works with restorable snapshots (audit_only refuses).
    """
    # 1. Get checkpoint
    cp = db.get_checkpoint(checkpoint_id)
    if cp is None:
        return {"status": "error", "message": f"Checkpoint #{checkpoint_id} not found"}

    snap_data = snapshots.load(cp["snapshot_path"])
    if snap_data is None:
        return {"status": "error", "message": "Snapshot data corrupt, refusing restore"}

    target = Path(target_path).resolve()

    # 2. Whitelist check
    if not whitelist.is_allowed(target):
        return {
            "status": "error",
            "message": f"Path not in restore whitelist: {target_path}",
        }

    # 3. Symlink + traversal check
    if target.is_symlink():
        return {"status": "error", "message": "Cannot restore symlinks"}
    if _check_path_traversal(target):
        return {"status": "error", "message": "Path traversal detected"}

    # 4. Classify the file
    mode, reason = classify_file(target, restorable_paths)

    # 5. Look up file in snapshot
    files = snap_data.get("files") or {}
    file_hashes = snap_data.get("file_hashes") or {}
    file_entry = None

    # Try exact path match in v2 files
    str_target = str(target)
    if str_target in files:
        file_entry = files[str_target]
    else:
        # Fallback: search by hash key ending
        for k, v in files.items():
            if str_target.endswith(k) or k.endswith(target.name):
                file_entry = v
                break
        if not file_entry:
            # v1 fallback: hash-only check
            for k, v in file_hashes.items():
                if str_target.endswith(k) or k.endswith(target.name):
                    return {
                        "status": "error",
                        "message": f"File '{target_path}' was saved as audit_only (hash-only) in checkpoint #{checkpoint_id}. "
                                   f"Original content was not preserved. Cannot restore.",
                    }
                    break

    if not file_entry:
        return {
            "status": "error",
            "message": f"No record for {target_path} in checkpoint #{checkpoint_id}",
        }

    # 6. Check mode
    entry_mode = file_entry.get("mode", "audit_only")
    if entry_mode != "restorable":
        return {
            "status": "error",
            "message": f"File '{target_path}' was saved as {entry_mode}. "
                       f"Original content was not preserved. Cannot restore.\n"
                       f"Reason: Add file to security.restore_whitelist in config/agentguard.yaml "
                       f"and ensure it contains no sensitive data.",
        }

    # 7. Extract content from snapshot
    content = restore_file_content(file_entry)
    if content is None:
        return {
            "status": "error",
            "message": f"Snapshot content for '{target_path}' is corrupt or integrity check failed. Cannot restore.",
        }

    # 8. Show diff (dry-run or before confirm)
    current_hash = hash_file(target) if target.exists() else None
    expected_hash = file_entry.get("sha256")

    if not yes:
        size_info = f" ({len(content)} bytes)"
        print(f"File: {target}")
        print(f"Size: {size_info}")
        if current_hash:
            print(f"Current SHA256: {current_hash[:16]}...")
        print(f"Checkpoint SHA256: {expected_hash[:16] if expected_hash else 'N/A'}...")
        print(f"Mode: restore from checkpoint #{checkpoint_id}")
        confirm = input("Restore this file? [y/N] ").strip().lower()
        if confirm != "y":
            return {"status": "cancelled", "message": "Restore cancelled by user"}

    # 9. Atomic restore
    result = _atomic_restore(target, content, file_entry)

    # 10. Log
    new_hash = hash_file(target) if target.exists() else None
    db.log_restore(
        checkpoint_id=checkpoint_id,
        file_path=str_target,
        hash_before=current_hash,
        hash_after=new_hash,
        status=result["status"],
    )

    return result


def _atomic_restore(target: Path, content: bytes, file_entry: dict) -> Dict[str, object]:
    """Atomically restore file content with permission preservation."""
    if not target.parent.exists():
        return {"status": "error", "message": f"Parent directory does not exist: {target.parent}"}

    if target.is_dir():
        return {"status": "error", "message": "Target is a directory, not a file"}

    # Parse original mode
    mode_str = file_entry.get("mode_oct", "0o644")
    try:
        file_mode = int(mode_str, 8) if mode_str.startswith("0") else int(mode_str)
    except (ValueError, TypeError):
        file_mode = 0o644

    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".agentguard-restore-{target.name}-",
    )
    try:
        # Write content
        os.write(fd, content)
        os.fsync(fd)
        # Set permissions before rename
        os.fchmod(fd, stat.S_IMODE(file_mode))
        os.close(fd)

        # Atomic rename
        os.replace(tmp_path, target)

        # Sync parent directory
        try:
            parent_fd = os.open(str(target.parent), os.O_RDONLY)
            os.fsync(parent_fd)
            os.close(parent_fd)
        except OSError:
            pass

    except Exception as e:
        # Cleanup
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return {"status": "error", "message": f"Restore failed: {e}"}

    # Verify hash
    expected = file_entry.get("sha256")
    actual = hash_file(target)
    match = actual == expected if actual and expected else None

    if match is False:
        return {
            "status": "hash_mismatch",
            "message": f"File restored but hash mismatch: expected {expected[:16] if expected else 'N/A'}..., got {actual[:16] if actual else 'N/A'}...",
            "file": str(target),
            "expected_sha256": expected,
            "actual_sha256": actual,
        }

    return {
        "status": "success",
        "message": f"Restored {target} from checkpoint",
        "file": str(target),
        "sha256": actual,
        "match": True,
    }


def _check_path_traversal(path: Path) -> bool:
    """Return True if path contains traversal."""
    raw = str(path)
    if "/../" in raw or raw.startswith("../"):
        return True
    return False
