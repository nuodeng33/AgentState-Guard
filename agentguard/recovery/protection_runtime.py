"""Durable background worker for protected workspace operations."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path


class ProtectionRuntime:
    """One bounded worker per Core; durable operations outlive any HTTP request."""
    def __init__(self, db_path, state_dir, domains):
        from .protection import now

        self.db_path, self.state_dir, self.domains = Path(db_path), Path(state_dir), domains
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.last_error = None
        self.discovery_refresh = None
        self.initialize_sources = None
        self.on_initialized = None
        self.initialization_lock = threading.Lock()
        self.initial_discovery_complete = threading.Event()
        self.background_discovery = False
        self.discovery_thread = None
        self.started_at = now()
        self.poll_state = {}

    @contextmanager
    def service(self):
        from .protection import ProtectionService

        service = ProtectionService(self.db_path, self.state_dir, self.domains)
        try:
            yield service
        finally:
            service.close()

    def start(self):
        from .protection import now

        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.initial_discovery_complete.clear()
        self.started_at = now()
        self.thread = threading.Thread(target=self._run, name="asg-protection", daemon=True)
        self.thread.start()

    def notify(self):
        self.wake.set()

    def close(self):
        with self.initialization_lock:
            self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=10)
        # A blocked adapter is interrupted; its durable receipts reconcile next start.
        self.domains.close()
        if self.discovery_thread:
            self.discovery_thread.join(timeout=2)

    def _run(self):
        # The OS releases this lease on process death; an old live Core cannot
        # be mistaken for a crashed worker and compensated by a second process.
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.state_dir / "protection-worker.lock", "a+b") as lease:
            self._run_leased(lease)

    def _run_leased(self, lease):
        acquired = False
        try:
            lease.seek(0, 2)
            if lease.tell() == 0:
                lease.write(b"\0")
                lease.flush()
            lease.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                self.last_error = "PROTECTION_WORKER_ALREADY_RUNNING"
                return
            self._run_owned()
        finally:
            if acquired and os.name == "nt":
                import msvcrt

                lease.seek(0)
                msvcrt.locking(lease.fileno(), msvcrt.LK_UNLCK, 1)

    def _run_owned(self):
        first = True
        while not self.stop_event.is_set():
            try:
                with self.service() as service:
                    if first:
                        if self.initialize_sources:
                            self.initialize_sources()
                        if self.stop_event.is_set():
                            break
                        service.restore_disconnections()
                        with service.db.transaction() as conn:
                            # Never replay a WSL startup command after a crash.
                            conn.execute("UPDATE protection_operations SET state='FAILED',reason_code='PROTECTION_SOURCE_INTERRUPTED' WHERE kind='SOURCE' AND state IN ('RUNNING','QUEUED') AND created_at<?", (self.started_at,))
                            conn.execute("UPDATE protection_previews SET status='EXPIRED' WHERE status='READY' AND created_at<?", (self.started_at,))
                        for op in service._rows("SELECT id FROM protection_operations WHERE kind='RESTORE' AND state IN ('RUNNING','COMPENSATING','NEEDS_ATTENTION')"):
                            service.reconcile(op["id"])
                        first = False
                        with self.initialization_lock:
                            if not self.stop_event.is_set():
                                if self.on_initialized:
                                    self.on_initialized()
                                if self.background_discovery and self.discovery_refresh:
                                    self.discovery_thread = threading.Thread(target=self._observe, name="asg-discovery", daemon=True)
                                    self.discovery_thread.start()
                    # A journal/storage failure can leave an interrupted operation
                    # without killing the worker. Reconcile it before new work.
                    for op in service._rows("SELECT id FROM protection_operations WHERE kind='RESTORE' AND state IN ('RUNNING','COMPENSATING')"):
                        service.reconcile(op["id"])
                    for op in service._rows("SELECT id FROM protection_operations WHERE kind='SOURCE' AND state='QUEUED' ORDER BY created_at"):
                        service.execute_source(op["id"])
                        if self.discovery_refresh:
                            self.discovery_refresh()
                    for op in service._rows("SELECT id FROM protection_operations WHERE kind='RESTORE' AND state='QUEUED' ORDER BY created_at"):
                        if self.stop_event.is_set():
                            break
                        service.execute(op["id"])
                    for scope in service._rows("SELECT id FROM protection_scopes WHERE enabled=1 ORDER BY created_at"):
                        if self.stop_event.is_set():
                            break
                        self._poll_scope(service, scope["id"])
                self.last_error = None
            except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError) as exc:
                self.last_error = getattr(exc, "reason_code", "PROTECTION_WORKER_UNAVAILABLE")
            self.wake.wait(2)
            self.wake.clear()

    def _poll_scope(self, service, scope_id):
        service.maybe_collect(scope_id)
        scope = service.scope(scope_id)
        cache = self.poll_state.get(scope_id, {})
        timestamp = time.monotonic()
        fingerprint = None
        try:
            fingerprint = self.domains.fingerprint(json.loads(scope["binding_json"]))
        except (OSError, RuntimeError, ValueError, AttributeError):
            # A full bounded scan records a precise offline/coverage failure.
            pass
        if (fingerprint is not None and cache.get("fingerprint") == fingerprint
                and timestamp - cache.get("full_at", 0) < 30
                and not scope["pending_digest"] and scope["status"] == "PROTECTED"):
            return
        result = service.capture(scope_id)
        self.poll_state[scope_id] = {"fingerprint": fingerprint,
            "full_at": timestamp if result["status"] in {"RECORDED", "UNCHANGED"} else cache.get("full_at", 0)}

    def _observe(self):
        deadline = time.monotonic()
        while not self.stop_event.wait(max(0, deadline - time.monotonic())):
            started = time.monotonic()
            try:
                result = self.discovery_refresh()
                if (not self.stop_event.is_set() and isinstance(result, dict)
                        and result.get("reason_code") == "DISCOVERY_REFRESHED"
                        and result.get("status") in {"AVAILABLE", "DEGRADED"}
                        and isinstance(result.get("snapshot_id"), str) and result["snapshot_id"]):
                    self.initial_discovery_complete.set()
            except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError):
                # Discovery failure does not stop independent file recording.
                pass
            # Fixed start cadence, no accumulated catch-up queue or extra five
            # seconds after a slow scan. A minimum pause bounds back-to-back work.
            deadline = max(started + 2, time.monotonic() + 0.25)
