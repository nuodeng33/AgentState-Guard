"""Display-only last-verified R4 snapshots; never an authorization source."""
from __future__ import annotations

import copy
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from agentguard.api.r4_projection import R4ReadProjectionService
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore


class VerifiedAgentReads:
    """An owned worker fully verifies history inside one read-only DB snapshot."""

    def __init__(self, db_path: Path, snapshots_dir: Path | None = None, *, interval: float = 5.0,
                 clock: Callable[[], datetime] | None = None) -> None:
        self._db_path = db_path
        self._snapshots_dir = snapshots_dir or db_path.parent / "snapshots"
        self._interval = interval
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._connection: sqlite3.Connection | None = None
        self._projection: dict | None = None
        self._states: dict[str, str] = {}
        self._verified_at: dict[str, datetime] = {}
        self._checked_at: dict[str, datetime] = {}
        self._refreshing = False

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or self._stop.is_set():
                return
            self._thread = threading.Thread(
                target=self._run, name="asg-verified-agent-reads", daemon=True)
            self._thread.start()

    def request_refresh(self) -> None:
        self.start()
        self._wake.set()

    def close(self) -> None:
        with self._lock:
            self._stop.set()
            self._wake.set()
            if self._connection is not None:
                self._connection.interrupt()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            self._refresh()
            self._wake.wait(self._interval)

    def _refresh(self) -> None:
        with self._lock:
            if self._stop.is_set():
                return
            self._refreshing = True
        database = StateDB(self._db_path)
        try:
            # Bootstrap/migrations remain owned by Core, never by this reader.
            connection = sqlite3.connect(self._db_path.resolve().as_uri() + "?mode=ro", uri=True)
            database._conn = connection
            connection.set_progress_handler(lambda: int(self._stop.is_set()), 1000)
            with self._lock:
                self._connection = connection
            with database.transaction(immediate=False):
                results = R4ReadProjectionService(
                    database, SnapshotStore(self._snapshots_dir), clock=self._clock,
                    cancelled=self._stop.is_set
                ).verified_views(on_verified=self._publish)
        except (OSError, sqlite3.DatabaseError, RuntimeError, TypeError, ValueError):
            results = {view: R4ReadProjectionService.unavailable(view)
                       for view in ("agents", "runtime", "supervision", "changes", "recovery")}
        finally:
            with self._lock:
                self._connection = None
            database.close()
        with self._lock:
            already_published = set(self._checked_at)
        self._publish({view: result for view, result in results.items()
                       if view == "recovery" or view not in already_published
                       or result["reason_code"] in {"R4_LEDGER_INVALID", "R4_DATABASE_UNREACHABLE"}})
        with self._lock:
            self._refreshing = False

    def _publish(self, results: dict) -> None:
        completed = self._clock()
        with self._lock:
            if self._stop.is_set():
                return
            if self._projection is None:
                self._projection = {}
            for view, result in results.items():
                state = ("INVALID" if result["reason_code"] == "R4_LEDGER_INVALID"
                         else "ERROR" if result["reason_code"] == "R4_DATABASE_UNREACHABLE"
                         else "VERIFIED")
                self._states[view] = state
                self._checked_at[view] = completed
                self._projection[view] = result if state == "VERIFIED" else None
                if state == "VERIFIED":
                    self._verified_at[view] = completed

    def read(self, view: str = "agents") -> dict:
        self.start()
        now = self._clock()
        with self._lock:
            state = self._states.get(view, "PENDING")
            result = copy.deepcopy((self._projection or {}).get(view)) if state == "VERIFIED" else None
            verified_at, checked_at, refreshing = (
                self._verified_at.get(view), self._checked_at.get(view), self._refreshing)
        if (state == "VERIFIED" and verified_at is not None
                and (now - verified_at).total_seconds() > self._interval):
            state = "STALE"
        reason = {
            "PENDING": "R4_VERIFICATION_PENDING",
            "VERIFIED": "R4_SNAPSHOT_VERIFIED",
            "STALE": "R4_VERIFICATION_STALE",
            "INVALID": "R4_LEDGER_INVALID",
            "ERROR": "R4_VERIFICATION_UNAVAILABLE",
        }[state]
        if result is None:
            result = R4ReadProjectionService.unavailable(view)
            if state != "ERROR":
                result["reason_code"] = reason
        freshness_service = R4ReadProjectionService(None, None, clock=lambda: now)
        observations = (result.get("observed_agents", []) if view == "supervision"
                        else result["items"] if view in {"agents", "runtime"} else [])
        for item in observations:
            if "freshness" not in item:
                continue
            freshness = freshness_service._freshness(item.get("observed_at"))
            if freshness != "FRESH":
                item.update(freshness=freshness, uncertainty=True,
                            reason_code=f"{'RUNTIME' if view == 'runtime' else 'AGENT'}_OBSERVATION_{freshness}")
                if view != "runtime":
                    item.update(presence=freshness, lifecycle="UNKNOWN")
                    item["management_capabilities"] = {
                        **item.get("management_capabilities", {}), "observe": False}
                else:
                    item["availability"] = "UNKNOWN"
                if view != "supervision":
                    result["status"] = "DEGRADED"
        result["verification"] = {
            "state": state,
            "verified_at": verified_at.isoformat() if verified_at else None,
            "checked_at": checked_at.isoformat() if checked_at else None,
            "refreshing": refreshing,
            "reason_code": reason,
        }
        return result
