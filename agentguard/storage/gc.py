"""Garbage collection for blob store and checkpoints."""

import sqlite3
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .blob import BlobStore


class GCPlan:
    """A plan for garbage collection."""

    def __init__(self):
        self.checkpoints_to_delete: List[Dict[str, Any]] = []
        self.blobs_to_remove: List[str] = []
        self.protected_checkpoints: List[int] = []
        self.estimated_savings: int = 0
        self.notes: List[str] = []
        self.retention: Dict[str, Any] = {}


def plan_gc(
    conn: sqlite3.Connection,
    blob_store: BlobStore,
    keep_last_n: int = 10,
    keep_daily: int = 7,
    keep_weekly: int = 4,
    keep_monthly: int = 3,
    protected_ids: Optional[List[int]] = None,
    last_known_good_id: Optional[int] = None,
) -> GCPlan:
    """Create a GC plan without deleting anything.

    Retention rules:
    - Keep last N checkpoints
    - Keep at least one per day for D days
    - Keep at least one per week for W weeks
    - Keep at least one per month for M months
    - Protected IDs are never deleted
    - Last Known Good is never deleted
    - Checkpoints referenced by pending transactions are never deleted
    """
    plan = GCPlan()
    plan.retention = {
        "keep_last_n": keep_last_n,
        "keep_daily": keep_daily,
        "keep_weekly": keep_weekly,
        "keep_monthly": keep_monthly,
        "protected_ids": list(protected_ids or []),
        "last_known_good_id": last_known_good_id,
    }
    protected = set(protected_ids or [])
    if last_known_good_id:
        protected.add(last_known_good_id)

    # Check pending transactions
    pending_txns = conn.execute(
        "SELECT id, pre_checkpoint, post_checkpoint FROM transactions "
        "WHERE status NOT IN ('committed','rolled_back','cancelled')"
    ).fetchall()
    for txn in pending_txns:
        if txn[1]:
            protected.add(txn[1])
        if txn[2]:
            protected.add(txn[2])

    rows = conn.execute(
        "SELECT id, label, created_at FROM checkpoints ORDER BY created_at DESC"
    ).fetchall()

    # Apply retention rules
    keep = set(protected)
    now = datetime.now(timezone.utc)

    for i, row in enumerate(rows):
        cp_id = row[0]
        if i < keep_last_n:
            keep.add(cp_id)
            continue
        if cp_id in protected:
            keep.add(cp_id)
            continue
        created = _parse_ts(row[2])
        if created is None:
            keep.add(cp_id)
            continue
        # Daily
        if (now - created).days < keep_daily:
            keep.add(cp_id)
            continue
        # Weekly
        if (now - created).days < keep_weekly * 7:
            keep.add(cp_id)
            continue
        # Monthly
        if (now - created).days < keep_monthly * 30:
            keep.add(cp_id)
            continue

    # Collect candidates for deletion
    to_delete_snapshots = []
    for row in rows:
        cp_id = row[0]
        if cp_id not in keep:
            to_delete_snapshots.append({
                "id": cp_id,
                "label": row[1],
                "created_at": row[2],
            })

    # Find orphan blobs
    orphans = blob_store.find_orphans(conn)
    orphan_info = []
    for sha256 in orphans:
        row = conn.execute(
            """SELECT size_bytes FROM blobs b WHERE b.sha256 = ?
               AND b.ref_count <= 0
               AND NOT EXISTS (SELECT 1 FROM protection_blob_refs p WHERE p.sha256 = b.sha256)
               AND NOT EXISTS (SELECT 1 FROM snapshot_files s WHERE s.blob_sha256 = b.sha256)""",
            (sha256,),
        ).fetchone()
        if row:
            orphan_info.append(sha256)
            plan.estimated_savings += row[0]

    plan.checkpoints_to_delete = to_delete_snapshots
    plan.blobs_to_remove = orphan_info
    plan.protected_checkpoints = list(keep)

    if not to_delete_snapshots and not orphan_info:
        plan.notes.append("Nothing to clean up")

    plan.notes.append(f"Keeping {len(keep)}/{len(rows)} checkpoints")
    plan.notes.append(f"Found {len(orphan_info)} orphan blobs (~{plan.estimated_savings // 1024}KB)")

    return plan


def execute_gc(conn: sqlite3.Connection, blob_store: BlobStore, plan: GCPlan,
               dry_run: bool = True) -> Dict[str, Any]:
    """Execute (or dry-run) a GC plan."""
    result = {
        "dry_run": dry_run,
        "checkpoints_deleted": 0,
        "blobs_removed": 0,
        "space_reclaimed_bytes": 0,
        "errors": [],
    }

    if dry_run:
        result["checkpoints_to_delete"] = len(plan.checkpoints_to_delete)
        result["blobs_to_remove"] = len(plan.blobs_to_remove)
        result["estimated_savings"] = plan.estimated_savings
        return result

    if conn.in_transaction:
        raise RuntimeError("GC_REQUIRES_NO_ACTIVE_TRANSACTION")

    staged: list[tuple[Path, Path]] = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        # A plan is advisory: retention and references may have changed since creation.
        current = plan_gc(conn, blob_store, **plan.retention)
        current_checkpoints = {cp["id"] for cp in current.checkpoints_to_delete}
        current_blobs = set(current.blobs_to_remove)
        for cp in plan.checkpoints_to_delete:
            cp_id = cp["id"]
            if cp_id not in current_checkpoints:
                continue
            row = conn.execute(
                "SELECT snapshot_path FROM checkpoints WHERE id = ?", (cp_id,)
            ).fetchone()
            if row is None:
                continue
            path = _snapshot_path(blob_store, row[0])
            if path.is_file():
                _stage_file(path, staged)
            conn.execute("DELETE FROM snapshot_files WHERE checkpoint_id = ?", (cp_id,))
            conn.execute("DELETE FROM checkpoints WHERE id = ?", (cp_id,))
            result["checkpoints_deleted"] += 1

        for sha256 in plan.blobs_to_remove:
            if sha256 not in current_blobs:
                continue
            # Recheck after checkpoint changes, while the write lock still excludes new refs.
            row = conn.execute(
                """SELECT size_bytes FROM blobs b WHERE b.sha256 = ? AND b.ref_count <= 0
                   AND NOT EXISTS (SELECT 1 FROM protection_blob_refs p WHERE p.sha256 = b.sha256)
                   AND NOT EXISTS (SELECT 1 FROM snapshot_files s WHERE s.blob_sha256 = b.sha256)""",
                (sha256,),
            ).fetchone()
            if row is None:
                continue
            path = blob_store._blob_path(sha256)
            if path.is_file():
                _stage_file(path, staged)
            conn.execute("DELETE FROM blobs WHERE sha256 = ?", (sha256,))
            result["blobs_removed"] += 1
            result["space_reclaimed_bytes"] += row[0]
        conn.commit()
    except Exception:
        conn.rollback()
        for original, staged_path in reversed(staged):
            os.replace(staged_path, original)
        raise
    for _, staged_path in staged:
        staged_path.unlink()

    return result


def _snapshot_path(blob_store: BlobStore, relative: str) -> Path:
    if not isinstance(relative, str):
        raise ValueError("GC_SNAPSHOT_PATH_INVALID")
    path = Path(relative)
    if path.is_absolute() or not path.parts or path.parts[0] != "snapshots" or ".." in path.parts:
        raise ValueError("GC_SNAPSHOT_PATH_INVALID")
    root = blob_store.blob_dir.parent.resolve()
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root / "snapshots"):
        raise ValueError("GC_SNAPSHOT_PATH_INVALID")
    return candidate


def _stage_file(path: Path, staged: list[tuple[Path, Path]]) -> None:
    staged_path = path.with_name(f"{path.name}.gc-{uuid.uuid4().hex}")
    os.replace(path, staged_path)
    staged.append((path, staged_path))


def _parse_ts(ts: str) -> Optional[datetime]:
    """Parse ISO timestamp string."""
    try:
        # Handle 'Z' suffix
        ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
