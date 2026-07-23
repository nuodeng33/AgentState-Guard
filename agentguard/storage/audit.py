"""Audit log with hash chain for tampering detection."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..core.sanitizer import sanitize_text


def _compute_hash(prev_hash: str, event_data: str) -> str:
    """Compute the hash chain entry."""
    h = hashlib.sha256(f"{prev_hash}|{event_data}".encode())
    return h.hexdigest()


def append_event(
    conn: sqlite3.Connection,
    event_type: str,
    source: str,
    result: str,
    details: Optional[Dict[str, object]] = None,
    txn_id: Optional[int] = None,
    checkpoint_id: Optional[int] = None,
    norm_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Append an event to the audit log. Returns the event record.

    Args:
        conn: SQLite connection.
        event_type: Type of event (e.g. 'checkpoint.create', 'restore.apply').
        source: Origin ('cli', 'gui', 'host_import', 'watcher').
        result: 'success', 'failure', or 'cancelled'.
        details: Optional dict (will be sanitized).
        txn_id: Optional transaction ID.
        checkpoint_id: Optional checkpoint ID.
        norm_path: Optional normalized path.
    """
    # Get previous hash
    prev = conn.execute(
        "SELECT curr_hash FROM audit_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prev_hash = prev[0] if prev else "0" * 64

    timestamp = datetime.now(timezone.utc).isoformat()
    details_safe = sanitize_text(json.dumps(details or {}, default=str))

    event_data = f"{timestamp}|{event_type}|{source}|{result}"
    curr_hash = _compute_hash(prev_hash, event_data)

    conn.execute(
        """INSERT INTO audit_events
           (timestamp, event_type, source, txn_id, checkpoint_id,
            norm_path, result, prev_hash, curr_hash, details_safe)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (timestamp, event_type, source, txn_id, checkpoint_id,
         norm_path, result, prev_hash, curr_hash, details_safe),
    )

    return {
        "timestamp": timestamp,
        "event_type": event_type,
        "source": source,
        "result": result,
        "prev_hash": prev_hash[:16],
        "curr_hash": curr_hash[:16],
    }


def verify_chain(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Verify the integrity of the audit hash chain.

    Returns list of inconsistencies found (empty = chain intact).
    """
    rows = conn.execute(
        "SELECT id, timestamp, event_type, source, result, prev_hash, curr_hash "
        "FROM audit_events ORDER BY id ASC"
    ).fetchall()

    issues = []
    expected_prev = "0" * 64

    for row in rows:
        event_id, ts, etype, source, result, prev_hash, curr_hash = row

        if prev_hash != expected_prev:
            issues.append({
                "id": event_id,
                "type": "hash_break",
                "message": f"Event #{event_id}: prev_hash mismatch",
                "expected_prev": expected_prev[:16],
                "actual_prev": prev_hash[:16],
            })

        # Recompute
        event_data = f"{ts}|{etype}|{source}|{result}"
        computed = _compute_hash(expected_prev, event_data)

        if computed != curr_hash:
            issues.append({
                "id": event_id,
                "type": "hash_mismatch",
                "message": f"Event #{event_id}: curr_hash does not match computed",
                "expected": computed[:16],
                "actual": curr_hash[:16],
            })

        expected_prev = curr_hash

    return issues


def get_events(
    conn: sqlite3.Connection,
    limit: int = 100,
    offset: int = 0,
    event_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Query audit events with optional type filter."""
    if event_type:
        rows = conn.execute(
            "SELECT id, timestamp, event_type, source, result, prev_hash, curr_hash "
            "FROM audit_events WHERE event_type = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (event_type, limit, offset),
        )
    else:
        rows = conn.execute(
            "SELECT id, timestamp, event_type, source, result, prev_hash, curr_hash "
            "FROM audit_events ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )

    return [
        {
            "id": r[0],
            "timestamp": r[1],
            "event_type": r[2],
            "source": r[3],
            "result": r[4],
            "prev_hash": r[5][:16],
            "curr_hash": r[6][:16],
        }
        for r in rows
    ]
