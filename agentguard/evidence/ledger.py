"""SQLite persistence and verification for the R4 append-only Evidence Ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
import threading
from collections import OrderedDict
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .canonical import canonical_json
from .models import EventFamily, EventType, EvidenceEvent

_GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class LedgerReceipt:
    sequence: int
    event_id: str
    prev_hash: str
    curr_hash: str


def _hash_authority(authority: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(authority).encode("utf-8")).hexdigest()


class EvidenceLedger:
    """Append validated events without owning transaction commit or rollback."""

    def append(self, connection: sqlite3.Connection, event: EvidenceEvent) -> LedgerReceipt:
        row = connection.execute(
            "SELECT sequence, curr_hash FROM evidence_ledger_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = 1 if row is None else int(row[0]) + 1
        prev_hash = _GENESIS_HASH if row is None else str(row[1])
        authority = event.authority_dict(sequence=sequence, prev_hash=prev_hash)
        curr_hash = _hash_authority(authority)
        connection.execute(
            """INSERT INTO evidence_ledger_events (
                   sequence, event_id, schema_version, recorded_at, observed_at,
                   event_family, event_type, source, result, execution_domain_id,
                   supervision_session_id, transaction_id, checkpoint_id, subject_ref,
                   evidence_refs_json, payload_safe_json, payload_digest, prev_hash, curr_hash
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sequence,
                event.event_id,
                event.schema_version,
                authority["recorded_at"],
                authority["observed_at"],
                event.event_family.value,
                event.event_type.value,
                event.source,
                event.result,
                event.execution_domain_id,
                event.supervision_session_id,
                event.transaction_id,
                event.checkpoint_id,
                event.subject_ref,
                canonical_json(authority["evidence_refs"]),
                canonical_json(event.payload_safe),
                event.payload_digest,
                prev_hash,
                curr_hash,
            ),
        )
        return LedgerReceipt(sequence, event.event_id, prev_hash, curr_hash)


def verify_ledger(connection: sqlite3.Connection) -> list[str]:
    """Return stable verification codes for every chain or serialization defect."""
    return _verify_ledger_rows(_ledger_rows(connection))


def _ledger_rows(connection: sqlite3.Connection):
    return connection.execute(
        """SELECT sequence, event_id, schema_version, recorded_at, observed_at,
                  event_family, event_type, source, result, execution_domain_id,
                  supervision_session_id, transaction_id, checkpoint_id, subject_ref,
                  evidence_refs_json, payload_safe_json, payload_digest, prev_hash, curr_hash
           FROM evidence_ledger_events ORDER BY sequence ASC"""
    ).fetchall()


_READ_VALIDATED: OrderedDict[bytes, None] = OrderedDict()
_READ_VALIDATION_LOCK = threading.Lock()
_READ_VALIDATION_LIMIT = 64
_READ_AUTHORITY_VALIDATED: OrderedDict[bytes, None] = OrderedDict()
_READ_AUTHORITY_LIMIT = 65536


def _ledger_fingerprint(rows, *, cancelled=None) -> bytes | None:
    """Bind every raw authority value, SQLite type, column and row boundary.

    This digest is a process-local validation key, never a stored ledger hash.
    Unsupported/custom row values bypass caching instead of granting trust.
    """
    digest = hashlib.sha256(b"ASG:ledger-validation:v1:19-columns")
    for row in rows:
        _check_read_cancelled(cancelled)
        if len(row) != 19:
            return None
        digest.update(b"R")
        for value in row:
            if value is None:
                tag, data = b"N", b""
            elif type(value) is int:
                tag, data = b"I", str(value).encode("ascii")
            elif type(value) is float:
                tag, data = b"F", struct.pack(">d", value)
            elif type(value) is str:
                tag, data = b"S", value.encode("utf-8")
            elif type(value) is bytes:
                tag, data = b"B", value
            else:
                return None
            digest.update(tag)
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
        digest.update(b"E")
    return digest.digest()


def _cacheable_utc_time(value: object) -> bool:
    """Require a parsed UTC offset; a UTC-looking suffix can still be naive."""
    if not isinstance(value, str) or not value.endswith("+00:00"):
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() == timedelta(0)
    except ValueError:
        return False


class LedgerReadCancelled(RuntimeError):
    """An explicitly cancelled display read never produces a valid receipt."""


def _check_read_cancelled(cancelled) -> None:
    if cancelled is not None and cancelled():
        raise LedgerReadCancelled("LEDGER_READ_CANCELLED")


@contextmanager
def _read_validation_lock(cancelled):
    if cancelled is None:
        with _READ_VALIDATION_LOCK:
            yield
        return
    while True:
        _check_read_cancelled(cancelled)
        if _READ_VALIDATION_LOCK.acquire(timeout=0.05):
            break
    try:
        _check_read_cancelled(cancelled)
        yield
    finally:
        _READ_VALIDATION_LOCK.release()


def verify_ledger_snapshot(
    connection: sqlite3.Connection, *, cancelled: Callable[[], bool] | None = None,
) -> list[str]:
    """Read all raw rows; reuse only successful identical historical validation.

    The optional read-only cancellation hook covers lock waits and Python loops.
    Cancellation is an exception, never a success or a partial validation. Writers
    retain verify_ledger's unconditional validator with no cancellation behavior.
    """
    with _read_validation_lock(cancelled):
        rows = _ledger_rows(connection)
        cacheable_times = True
        for row in rows:
            _check_read_cancelled(cancelled)
            if not (_cacheable_utc_time(row[3])
                    and (row[4] is None or _cacheable_utc_time(row[4]))):
                cacheable_times = False
                break
        key = ((_ledger_fingerprint(rows, cancelled=cancelled) if cancelled is not None
                else _ledger_fingerprint(rows)) if cacheable_times else None)
        if key is None:
            return _verify_ledger_rows(rows, cancelled=cancelled)
        _check_read_cancelled(cancelled)
        if key in _READ_VALIDATED:
            _READ_VALIDATED.move_to_end(key)
            return []
        problems = _verify_ledger_rows(rows, _read_cache=True, cancelled=cancelled)
        _check_read_cancelled(cancelled)
        if not problems:
            _READ_VALIDATED[key] = None
            if len(_READ_VALIDATED) > _READ_VALIDATION_LIMIT:
                _READ_VALIDATED.popitem(last=False)
        return problems


def _verify_ledger_rows(rows, *, _read_cache=False, cancelled=None) -> list[str]:
    problems: list[str] = []
    expected_sequence = 1
    expected_prev_hash = _GENESIS_HASH
    event_ids: set[str] = set()
    # Admit only the newest bounded tail. Older cache misses must not evict
    # retained rows before this ascending pass reaches and reuses them.
    cache_start = max(0, len(rows) - _READ_AUTHORITY_LIMIT) if _read_cache else 0
    for row_index, row in enumerate(rows):
        _check_read_cancelled(cancelled)
        sequence = int(row[0])
        if sequence != expected_sequence:
            problems.append("LEDGER_SEQUENCE_INVALID")
        if row[1] in event_ids:
            problems.append("LEDGER_EVENT_ID_DUPLICATE")
        event_ids.add(row[1])
        if row[17] != expected_prev_hash:
            problems.append("LEDGER_PREV_HASH_INVALID")
        # Sequence, duplicate IDs and predecessor links are checked on EVERY
        # pass. Only intrinsic authority of an identical complete raw row can
        # reuse success; no prefix/head/count or stored chain hash grants trust.
        problems.extend(
            _verify_authority_cached(row, sequence, admit=row_index >= cache_start)
            if _read_cache else _verify_authority_row(row, sequence)
        )
        expected_sequence += 1
        expected_prev_hash = row[18]
    return problems


def _verify_authority_row(row, sequence):
    problems = []
    try:
        recorded_at = datetime.fromisoformat(row[3]).astimezone(UTC)
        observed_at = datetime.fromisoformat(row[4]).astimezone(UTC) if row[4] else None
        evidence_refs = json.loads(row[14])
        payload_safe = json.loads(row[15])
        event = EvidenceEvent(
            schema_version=int(row[2]),
            event_id=row[1],
            recorded_at=recorded_at,
            observed_at=observed_at,
            event_family=EventFamily(row[5]),
            event_type=EventType(row[6]),
            source=row[7],
            result=row[8],
            execution_domain_id=row[9],
            supervision_session_id=row[10],
            transaction_id=row[11],
            checkpoint_id=row[12],
            subject_ref=row[13],
            evidence_refs=tuple(evidence_refs),
            payload_safe=payload_safe,
        )
        # EvidenceEvent already validates the freshly decoded payload. Its
        # authority dictionary computes the same payload digest once; reuse
        # it instead of validating/serializing the same object repeatedly.
        authority = event.authority_dict(sequence=sequence, prev_hash=row[17])
        if authority["payload_digest"] != row[16]:
            problems.append("LEDGER_PAYLOAD_DIGEST_INVALID")
        payload_json = json.dumps(payload_safe, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False)
        if canonical_json(evidence_refs) != row[14] or payload_json != row[15]:
            problems.append("LEDGER_JSON_NONCANONICAL")
        # Full authority/privacy validation (including nested depth) remains.
        expected_hash = _hash_authority(authority)
        if expected_hash != row[18]:
            problems.append("LEDGER_CURR_HASH_INVALID")
    except (ValueError, TypeError, json.JSONDecodeError):
        problems.append("LEDGER_AUTHORITY_INVALID")
    return problems


def _verify_authority_cached(row, sequence, *, admit=True):
    """Called only under the read-validation lock and a pinned read snapshot."""
    key = _ledger_fingerprint([row])
    if key is None:
        return _verify_authority_row(row, sequence)
    if key in _READ_AUTHORITY_VALIDATED:
        _READ_AUTHORITY_VALIDATED.move_to_end(key)
        return []
    problems = _verify_authority_row(row, sequence)
    if not problems and admit:
        _READ_AUTHORITY_VALIDATED[key] = None
        if len(_READ_AUTHORITY_VALIDATED) > _READ_AUTHORITY_LIMIT:
            _READ_AUTHORITY_VALIDATED.popitem(last=False)
    return problems
