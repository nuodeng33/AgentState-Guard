"""SQLite persistence and verification for the R4 append-only Evidence Ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
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


def _ledger_fingerprint(rows) -> bytes | None:
    """Bind every raw authority value, SQLite type, column and row boundary.

    This digest is a process-local validation key, never a stored ledger hash.
    Unsupported/custom row values bypass caching instead of granting trust.
    """
    digest = hashlib.sha256(b"ASG:ledger-validation:v1:19-columns")
    for row in rows:
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


def verify_ledger_snapshot(connection: sqlite3.Connection) -> list[str]:
    """Read all raw rows every time; share only successful identical validation.

    Callers keep their projection in the same SQLite read snapshot. There is no
    TTL, trusted prefix/head/count or projection cache. The lock coalesces full
    validation of identical concurrent reads; invalid results are never cached.
    Writers continue to use verify_ledger's unconditional complete validator.
    """
    # Serialize raw scans too: concurrent sqlite fetchall calls repeatedly
    # contend for the GIL. Each caller still reads/fingerprints its own snapshot.
    with _READ_VALIDATION_LOCK:
        rows = _ledger_rows(connection)
        # Event writers emit UTC-aware ISO times. The legacy validator also accepts
        # some naive times via local-zone conversion; those are not a pure function
        # of raw bytes and must keep unconditional validation, never cache reuse.
        canonical_times = all(
            isinstance(row[3], str) and row[3].endswith("+00:00")
            and (row[4] is None or isinstance(row[4], str) and row[4].endswith("+00:00"))
            for row in rows
        )
        key = _ledger_fingerprint(rows) if canonical_times else None
        if key is None:
            return _verify_ledger_rows(rows)
        if key in _READ_VALIDATED:
            _READ_VALIDATED.move_to_end(key)
            return []
        problems = _verify_ledger_rows(rows)
        if not problems:
            _READ_VALIDATED[key] = None
            if len(_READ_VALIDATED) > _READ_VALIDATION_LIMIT:
                _READ_VALIDATED.popitem(last=False)
        return problems


def _verify_ledger_rows(rows) -> list[str]:
    problems: list[str] = []
    expected_sequence = 1
    expected_prev_hash = _GENESIS_HASH
    event_ids: set[str] = set()
    for row in rows:
        sequence = int(row[0])
        if sequence != expected_sequence:
            problems.append("LEDGER_SEQUENCE_INVALID")
        if row[1] in event_ids:
            problems.append("LEDGER_EVENT_ID_DUPLICATE")
        event_ids.add(row[1])
        if row[17] != expected_prev_hash:
            problems.append("LEDGER_PREV_HASH_INVALID")
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
            if event.payload_digest != row[16]:
                problems.append("LEDGER_PAYLOAD_DIGEST_INVALID")
            if canonical_json(evidence_refs) != row[14] or canonical_json(payload_safe) != row[15]:
                problems.append("LEDGER_JSON_NONCANONICAL")
            expected_hash = _hash_authority(
                event.authority_dict(sequence=sequence, prev_hash=row[17])
            )
            if expected_hash != row[18]:
                problems.append("LEDGER_CURR_HASH_INVALID")
        except (ValueError, TypeError, json.JSONDecodeError):
            problems.append("LEDGER_AUTHORITY_INVALID")
        expected_sequence += 1
        expected_prev_hash = row[18]
    return problems
