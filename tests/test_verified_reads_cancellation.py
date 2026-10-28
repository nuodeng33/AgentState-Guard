"""Read cancellation never publishes partial validation or changes writers."""
import threading
import time

import pytest

from agentguard.api.verified_agents import VerifiedAgentReads
from agentguard.evidence import ledger
from agentguard.storage.db import StateDB
from tests.test_evidence_ledger import _event


def _database(root):
    database = StateDB(root / "state.db")
    database.connect()
    with database.transaction():
        for index in range(3):
            ledger.EvidenceLedger().append(database._conn, _event(event_id=f"cancel-{index}"))
    return database


def test_shutdown_cancels_read_validation_lock_wait_without_publication(tmp_path):
    database = _database(tmp_path)
    database.close()
    service = VerifiedAgentReads(tmp_path / "state.db")
    ledger._READ_VALIDATION_LOCK.acquire()
    try:
        service.start()
        deadline = time.monotonic() + 2
        while not service.read()["verification"]["refreshing"]:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        started = time.monotonic()
        service.close()
        assert time.monotonic() - started < 1
        assert not service._thread.is_alive()
        result = service.read()
        assert result["verification"]["state"] == "PENDING"
        assert result["verification"]["verified_at"] is None
        assert result["verification"]["checked_at"] is None
    finally:
        ledger._READ_VALIDATION_LOCK.release()
        service.close()


def test_shutdown_cancels_python_fingerprint_loop_without_late_publication(tmp_path, monkeypatch):
    database = _database(tmp_path)
    database.close()
    entered = threading.Event()
    original = ledger._ledger_fingerprint

    def fingerprint_until_cancelled(rows, **kwargs):
        entered.set()
        while True:
            original(rows, **kwargs)

    monkeypatch.setattr(ledger, "_ledger_fingerprint", fingerprint_until_cancelled)
    service = VerifiedAgentReads(tmp_path / "state.db")
    service.start()
    try:
        assert entered.wait(2)
        started = time.monotonic()
        service.close()
        assert time.monotonic() - started < 1
        assert not service._thread.is_alive()
        assert service.read()["verification"]["checked_at"] is None
    finally:
        service.close()


def test_cancelled_authority_pass_is_not_cached_and_writer_remains_unconditional(tmp_path, monkeypatch):
    database = _database(tmp_path)
    stop = threading.Event()
    original = ledger._verify_authority_row
    ledger._READ_VALIDATED.clear()
    ledger._READ_AUTHORITY_VALIDATED.clear()

    def cancel_after_first_row(row, sequence):
        result = original(row, sequence)
        stop.set()
        return result

    monkeypatch.setattr(ledger, "_verify_authority_row", cancel_after_first_row)
    try:
        with pytest.raises(ledger.LedgerReadCancelled, match="LEDGER_READ_CANCELLED"):
            ledger.verify_ledger_snapshot(database._conn, cancelled=stop.is_set)
        assert not ledger._READ_VALIDATED
        # Writer has no cancellation parameter or context inherited from a reader.
        assert ledger.verify_ledger(database._conn) == []
        stop.clear()
        monkeypatch.setattr(ledger, "_verify_authority_row", original)
        assert ledger.verify_ledger_snapshot(database._conn) == []
        with pytest.raises(ledger.LedgerReadCancelled):
            ledger.verify_ledger_snapshot(database._conn, cancelled=lambda: True)
    finally:
        database.close()
