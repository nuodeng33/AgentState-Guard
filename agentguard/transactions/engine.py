"""Transaction engine — plan, apply, verify, commit, rollback state machine."""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.hasher import hash_file
from ..core.snapshot import create_file_snapshot, serialize_snapshot, diff_snapshots
from ..core.whitelist import Whitelist
from ..core.runner import run_command
from ..storage.db import StateDB
from ..storage.snapshots import SnapshotStore
from ..storage.audit import append_event
from .coverage import compute_coverage, coverage_summary

VALID_TRANSITIONS = {
    "planned": ["preflight_failed", "ready", "applying", "cancelled"],
    "ready": ["applying", "cancelled"],
    "applying": ["verifying", "rollback_required"],
    "verifying": ["committed", "rollback_required"],
    "rollback_required": ["rolling_back", "rollback_failed"],
    "rolling_back": ["rolled_back", "rollback_failed"],
    "rolled_back": [],
    "rollback_failed": ["partially_recoverable"],
    "partially_recoverable": [],
    "committed": [],
    "preflight_failed": [],
    "cancelled": [],
}


class TransactionEngine:
    """Manages the transaction lifecycle."""

    def __init__(self, db: StateDB, snapshots: SnapshotStore, config: dict):
        self.db = db
        self.snapshots = snapshots
        self.config = config
        self.conn = db._conn

    def _transition(self, txn_id: int, new_status: str) -> Dict[str, Any]:
        """Attempt state transition. Returns result dict."""
        row = self.conn.execute(
            "SELECT id, status FROM transactions WHERE id = ?", (txn_id,)
        ).fetchone()
        if not row:
            return {"status": "error", "message": f"Transaction #{txn_id} not found"}

        current = row[1]
        allowed = VALID_TRANSITIONS.get(current, [])
        if new_status not in allowed:
            return {
                "status": "error",
                "message": f"Invalid transition: {current} → {new_status}",
                "current_status": current,
                "allowed": allowed,
            }

        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE transactions SET status = ?, completed_at = ? WHERE id = ?",
            (new_status, now, txn_id),
        )
        self.conn.commit()
        return {"status": "ok", "message": f"Transitioned to {new_status}", "txn_id": txn_id}

    def create_plan(
        self,
        label: str,
        files_to_modify: List[str],
        command_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new transaction plan with coverage computation.

        Steps:
        1. Pre-checkpoint current state
        2. Compute rollback coverage
        3. Create transaction record
        4. Log audit event
        """
        # Pre-checkpoint
        pre_cp_result = self._create_pre_checkpoint(label)
        if "error" in pre_cp_result:
            return pre_cp_result

        whitelist = Whitelist(self.config.get("security", {}).get("restore_whitelist", []))

        # Load checkpoint entries for coverage
        checkpoint_entries = {}
        if pre_cp_result.get("checkpoint_id"):
            cp_id = pre_cp_result["checkpoint_id"]
            snap_data = self._load_checkpoint_data(cp_id)
            if snap_data:
                checkpoint_entries = snap_data.get("files") or {}

        # Compute coverage
        coverage = compute_coverage(
            files_to_modify, whitelist, checkpoint_entries
        )

        # Create transaction
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            """INSERT INTO transactions
               (status, label, created_at, rollback_coverage, pre_checkpoint)
               VALUES (?, ?, ?, ?, ?)""",
            ("planned", label[:255], now, coverage.get("coverage_pct", 0),
             pre_cp_result.get("checkpoint_id")),
        )
        self.conn.commit()
        txn_id = cur.lastrowid

        # Audit event
        append_event(self.conn, "transaction.plan", "cli",
                     "success" if not coverage.get("not_recoverable") else "success_with_warnings",
                     {"txn_id": txn_id, "label": label, "coverage": coverage.get("coverage_label")})

        return {
            "transaction_id": txn_id,
            "label": label,
            "status": "planned",
            "coverage": coverage,
            "pre_checkpoint_id": pre_cp_result.get("checkpoint_id"),
        }

    def apply(self, txn_id: int) -> Dict[str, Any]:
        """Mark a planned transaction as applying (the CLI/GUI will execute steps)."""
        result = self._transition(txn_id, "applying")
        if result["status"] != "ok":
            return result

        append_event(self.conn, "transaction.apply", "cli", "success",
                     {"txn_id": txn_id})
        return {"status": "ok", "message": "Transaction set to applying", "txn_id": txn_id}

    def verify(self, txn_id: int) -> Dict[str, Any]:
        """Verify a transaction's file changes (hash comparison) and commit."""
        # Must be in applying state first
        txn = self.conn.execute(
            "SELECT id, status FROM transactions WHERE id = ?",
            (txn_id,),
        ).fetchone()
        if not txn:
            return {"status": "error", "message": f"Transaction #{txn_id} not found"}

        if txn[1] != "applying":
            allowed = VALID_TRANSITIONS.get(txn[1], [])
            return {
                "status": "error",
                "message": f"verify requires 'applying' state, current: {txn[1]}. Valid transitions: {allowed}",
            }

        # Check steps
        steps = self.conn.execute(
            "SELECT id, file_path, status FROM transaction_steps WHERE txn_id = ?",
            (txn_id,),
        ).fetchall()

        issues = []
        for step in steps:
            step_id, file_path, step_status = step
            if file_path and Path(file_path).is_file():
                current = hash_file(Path(file_path))
                if current:
                    self.conn.execute(
                        "UPDATE transaction_steps SET status = 'verified' WHERE id = ?",
                        (step_id,),
                    )
                else:
                    issues.append(f"Cannot verify {file_path}")

        if issues:
            self._transition(txn_id, "rollback_required")
            return {"status": "rollback_required", "issues": issues}

        self._transition(txn_id, "committed")
        append_event(self.conn, "transaction.commit", "cli", "success",
                     {"txn_id": txn_id})
        return {"status": "ok", "message": "Transaction committed", "txn_id": txn_id}

    def undo(self, txn_id: int, yes: bool = False) -> Dict[str, Any]:
        """Undo a committed transaction by restoring pre-checkpoint files.

        For now, undo = restore the pre-checkpoint if it exists.
        """
        row = self.conn.execute(
            "SELECT id, status, pre_checkpoint FROM transactions WHERE id = ?",
            (txn_id,),
        ).fetchone()
        if not row:
            return {"status": "error", "message": f"Transaction #{txn_id} not found"}

        if row[1] not in ("committed", "rolled_back"):
            return {"status": "error", "message": f"Cannot undo transaction in state: {row[1]}"}

        pre_cp_id = row[2]
        if not pre_cp_id:
            return {"status": "error", "message": "No pre-checkpoint available for undo"}

        self._transition(txn_id, "rolling_back")

        # Restore files from pre-checkpoint
        from ..commands.restore import cmd_restore
        whitelist_obj = Whitelist(self.config.get("security", {}).get("restore_whitelist", []))
        restorable_paths = self.config.get("security", {}).get("restore_whitelist", [])

        steps = self.conn.execute(
            "SELECT id, file_path FROM transaction_steps WHERE txn_id = ?",
            (txn_id,),
        ).fetchall()

        errors = []
        restored_count = 0
        for step in steps:
            step_id, file_path = step
            if file_path and Path(file_path).exists():
                result = cmd_restore(
                    target_path=file_path,
                    checkpoint_id=pre_cp_id,
                    db=self.db,
                    snapshots=self.snapshots,
                    whitelist=whitelist_obj,
                    restorable_paths=restorable_paths,
                    yes=True,
                )
                if result.get("status") in ("success", "hash_mismatch"):
                    restored_count += 1
                else:
                    errors.append(f"{file_path}: {result.get('message', 'unknown')}")

        if errors:
            self._transition(txn_id, "rollback_failed")
            append_event(self.conn, "transaction.undo", "cli", "failure",
                         {"txn_id": txn_id, "errors": errors})
            return {"status": "error", "errors": errors, "restored": restored_count}

        self._transition(txn_id, "rolled_back")
        append_event(self.conn, "transaction.undo", "cli", "success",
                     {"txn_id": txn_id})
        return {"status": "ok", "message": "Transaction undone", "txn_id": txn_id,
                "restored": restored_count}

    def list_transactions(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, status, label, created_at, completed_at, rollback_coverage, "
            "pre_checkpoint FROM transactions ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "status": r[1],
                "label": r[2],
                "created_at": r[3],
                "completed_at": r[4],
                "rollback_coverage": r[5],
                "pre_checkpoint": r[6],
            }
            for r in rows
        ]

    def get_transaction(self, txn_id: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT id, status, label, created_at, completed_at, "
            "rollback_coverage, rollback_reason, pre_checkpoint, post_checkpoint "
            "FROM transactions WHERE id = ?",
            (txn_id,),
        ).fetchone()
        if not row:
            return None
        steps = self.conn.execute(
            "SELECT id, step_order, action, file_path, status, message "
            "FROM transaction_steps WHERE txn_id = ? ORDER BY step_order",
            (txn_id,),
        ).fetchall()
        return {
            "id": row[0],
            "status": row[1],
            "label": row[2],
            "created_at": row[3],
            "completed_at": row[4],
            "rollback_coverage": row[5],
            "rollback_reason": row[6],
            "pre_checkpoint": row[7],
            "post_checkpoint": row[8],
            "steps": [
                {"id": s[0], "order": s[1], "action": s[2],
                 "file_path": s[3], "status": s[4], "message": s[5]}
                for s in steps
            ],
        }

    def _create_pre_checkpoint(self, label: str) -> Dict[str, Any]:
        """Create a pre-transaction checkpoint."""
        from ..commands.checkpoint import cmd_checkpoint
        result = cmd_checkpoint(f"pre-txn: {label[:200]}", self.config, self.db, self.snapshots)
        if "error" in result:
            return {"error": result["message"]}
        return {"checkpoint_id": result["checkpoint_id"]}

    def _load_checkpoint_data(self, cp_id: int) -> Optional[dict]:
        """Load checkpoint snapshot data."""
        cp = self.db.get_checkpoint(cp_id)
        if not cp:
            return None
        return self.snapshots.load(cp["snapshot_path"])
