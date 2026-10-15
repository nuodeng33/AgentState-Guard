"""Verified binding for the single server-owned controlled-change target."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime

from agentguard.evidence.canonical import canonical_json
from agentguard.storage.db import StateDB

from .ledger import EvidenceLedger, LedgerReceipt, verify_ledger
from .models import EventFamily, EventType, EvidenceEvent

_SAFE_ATOM = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


def target_refs_digest(target_refs: tuple[str, ...]) -> str:
    return hashlib.sha256(
        canonical_json(sorted(set(target_refs))).encode("utf-8")
    ).hexdigest()


def record_product_target_binding(
    database: StateDB,
    *,
    snapshot_id: str,
    execution_domain_id: str,
    target_refs: tuple[str, ...],
    recorded_at: datetime,
) -> LedgerReceipt:
    """Bind a fixed server target to one verified current runtime observation."""
    digest = target_refs_digest(target_refs)
    subject_id = f"product-config-{digest[:24]}"
    event_id = f"product-target-{hashlib.sha256((snapshot_id + digest).encode()).hexdigest()[:24]}"
    with database.transaction() as connection:
        if verify_ledger(connection):
            raise ValueError("PRODUCT_TARGET_LEDGER_INVALID")
        runtime = _runtime_event(
            connection,
            snapshot_id=snapshot_id,
            execution_domain_id=execution_domain_id,
        )
        if runtime is None:
            raise ValueError("PRODUCT_TARGET_RUNTIME_MISSING")
        event = EvidenceEvent(
            schema_version=1,
            event_id=event_id,
            recorded_at=recorded_at,
            observed_at=None,
            event_family=EventFamily.DISCOVERY,
            event_type=EventType.CONTROLLED_TARGET_BOUND,
            source="product-controlled-target",
            result="available",
            execution_domain_id=execution_domain_id,
            supervision_session_id=None,
            transaction_id=None,
            checkpoint_id=None,
            subject_ref=subject_id,
            evidence_refs=(runtime[0],),
            payload_safe={
                "binding_id": event_id,
                "fact_type": "product.target.binding",
                "runtime_event_id": runtime[0],
                "snapshot_id": snapshot_id,
                "subject_id": subject_id,
                "target_refs_digest": digest,
            },
        )
        receipt = EvidenceLedger().append(connection, event)
        if verify_ledger(connection):
            raise ValueError("PRODUCT_TARGET_LEDGER_INVALID")
    return receipt


def resolve_verified_product_target_binding(
    connection: sqlite3.Connection,
    *,
    binding_ref: str,
    execution_domain_id: str,
    expected_target_refs_digest: str,
) -> dict[str, str | None]:
    def unknown(reason_code: str) -> dict[str, str | None]:
        return {
            "status": "UNKNOWN",
            "subject_id": None,
            "binding_ref": None,
            "reason_code": reason_code,
        }

    if verify_ledger(connection):
        return unknown("PRODUCT_TARGET_LEDGER_INVALID")
    if not all(
        isinstance(value, str) and _SAFE_ATOM.fullmatch(value)
        for value in (binding_ref, execution_domain_id)
    ) or not re.fullmatch(r"[0-9a-f]{64}", expected_target_refs_digest):
        return unknown("PRODUCT_TARGET_BINDING_INVALID")
    row = connection.execute(
        """SELECT event_id, result, execution_domain_id, subject_ref,
                  evidence_refs_json, payload_safe_json
           FROM evidence_ledger_events
           WHERE event_id = ? AND event_type = 'CONTROLLED_TARGET_BOUND'""",
        (binding_ref,),
    ).fetchone()
    if row is None:
        return unknown("PRODUCT_TARGET_BINDING_MISSING")
    try:
        evidence_refs = json.loads(row[4])
        payload = json.loads(row[5])
    except (json.JSONDecodeError, TypeError):
        return unknown("PRODUCT_TARGET_BINDING_INVALID")
    if (
        row[1].casefold() != "available"
        or row[2] != execution_domain_id
        or not isinstance(row[3], str)
        or not _SAFE_ATOM.fullmatch(row[3])
        or not isinstance(evidence_refs, list)
        or len(evidence_refs) != 1
        or not isinstance(payload, dict)
        or payload.get("binding_id") != binding_ref
        or payload.get("fact_type") != "product.target.binding"
        or payload.get("subject_id") != row[3]
        or payload.get("target_refs_digest") != expected_target_refs_digest
        or payload.get("runtime_event_id") != evidence_refs[0]
    ):
        return unknown("PRODUCT_TARGET_BINDING_INVALID")
    runtime = _runtime_event(
        connection,
        snapshot_id=payload.get("snapshot_id"),
        execution_domain_id=execution_domain_id,
        event_id=evidence_refs[0],
    )
    if runtime is None:
        return unknown("PRODUCT_TARGET_BINDING_STALE")
    return {
        "status": "BOUND",
        "subject_id": row[3],
        "binding_ref": binding_ref,
        "reason_code": "PRODUCT_TARGET_BINDING_VERIFIED",
    }


def _runtime_event(
    connection: sqlite3.Connection,
    *,
    snapshot_id: object,
    execution_domain_id: str,
    event_id: str | None = None,
) -> tuple[str] | None:
    if not isinstance(snapshot_id, str) or not _SAFE_ATOM.fullmatch(snapshot_id):
        return None
    parameters: tuple[object, ...]
    event_filter = ""
    if event_id is None:
        parameters = (execution_domain_id,)
    else:
        event_filter = " AND event_id = ?"
        parameters = (execution_domain_id, event_id)
    rows = connection.execute(
        """SELECT event_id, result, payload_safe_json
           FROM evidence_ledger_events
           WHERE event_type = 'RUNTIME_DETECTED'
             AND execution_domain_id = ?"""
        + event_filter
        + " ORDER BY sequence DESC",
        parameters,
    ).fetchall()
    matches = []
    for runtime_event_id, result, payload_json in rows:
        try:
            payload = json.loads(payload_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if (
            result.casefold() == "available"
            and isinstance(payload, dict)
            and payload.get("fact_type") == "runtime.metadata"
            and payload.get("snapshot_id") == snapshot_id
        ):
            matches.append((runtime_event_id,))
    return matches[0] if len(matches) == 1 else None


__all__ = [
    "record_product_target_binding",
    "resolve_verified_product_target_binding",
    "target_refs_digest",
]
