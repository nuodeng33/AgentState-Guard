"""Tests for audit log with hash chain."""

import sqlite3
import tempfile
from pathlib import Path

from agentguard.storage.audit import append_event, verify_chain, get_events
from agentguard.storage.migrations import MigrationEngine


def _setup():
    tmp = Path(tempfile.mktemp(suffix=".db"))
    conn = sqlite3.connect(str(tmp))
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT, checksum TEXT, duration_ms INTEGER)")
    conn.commit()
    engine = MigrationEngine(tmp)
    engine.migrate(conn)
    return conn, tmp


class TestAudit:
    def test_append_event(self):
        conn, path = _setup()
        try:
            result = append_event(conn, "checkpoint.create", "cli", "success")
            assert result["result"] == "success"
            assert result["curr_hash"] != result["prev_hash"]
        finally:
            conn.close()
            path.unlink(missing_ok=True)

    def test_chain_integrity(self):
        conn, path = _setup()
        try:
            append_event(conn, "event.a", "cli", "success")
            append_event(conn, "event.b", "gui", "success")
            append_event(conn, "event.c", "watcher", "failure")
            issues = verify_chain(conn)
            assert issues == []
        finally:
            conn.close()
            path.unlink(missing_ok=True)

    def test_tampering_detected(self):
        conn, path = _setup()
        try:
            append_event(conn, "event.1", "cli", "success")
            append_event(conn, "event.2", "cli", "success")
            # Tamper: modify the second event's result
            conn.execute("UPDATE audit_events SET result = 'failure' WHERE id = 2")
            conn.commit()
            issues = verify_chain(conn)
            assert len(issues) > 0
        finally:
            conn.close()
            path.unlink(missing_ok=True)

    def test_get_events(self):
        conn, path = _setup()
        try:
            append_event(conn, "e1", "cli", "success")
            append_event(conn, "e2", "gui", "failure")
            events = get_events(conn)
            assert len(events) == 2
        finally:
            conn.close()
            path.unlink(missing_ok=True)

    def test_get_events_filtered(self):
        conn, path = _setup()
        try:
            append_event(conn, "type.a", "cli", "success")
            append_event(conn, "type.b", "cli", "success")
            events = get_events(conn, event_type="type.a")
            assert len(events) == 1
        finally:
            conn.close()
            path.unlink(missing_ok=True)
