"""Thin product recovery composition over the canonical RecoveryService."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agentguard.discovery.domains import SelfRuntimeAdapter
from agentguard.recovery.contracts import RecoveryOperation, RecoveryRequest
from agentguard.recovery.policy import RestorePolicy
from agentguard.recovery.service import RecoveryService
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore

from .r4_controlled_change import ControlledChangeError, _production_snapshot

SCHEMA_VERSION = "product-recovery-action-1"


class ProductRecoveryError(RuntimeError):
    def __init__(self, reason_code: str, status_code: int = 409) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code


def recovery_failure(
    reason_code: str, checkpoint_id: str | None = None
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "UNCHANGED",
        "reason_code": reason_code,
        "checkpoint_id": checkpoint_id,
        "evidence_refs": [],
    }


def run_product_recovery(
    database: StateDB,
    snapshots: SnapshotStore,
    *,
    target: Path,
    operation: RecoveryOperation,
    checkpoint_id: str | None = None,
    confirmed: bool = False,
    discovery_service=None,
) -> dict[str, object]:
    try:
        _snapshot, domain_id = _production_snapshot(discovery_service)
    except ControlledChangeError as error:
        raise ProductRecoveryError("RECOVERY_DISCOVERY_UNAVAILABLE", 503) from error
    policy = RestorePolicy(
        approved_paths={domain_id: (target,)},
        validators={domain_id: "toml-parse"},
    )
    service = RecoveryService(
        database=database,
        snapshots=snapshots,
        adapters={domain_id: SelfRuntimeAdapter(recovery_policy=policy)},
    )
    request = RecoveryRequest(
        operation=operation,
        execution_domain_id=domain_id,
        target_path=target
        if operation in {RecoveryOperation.SNAPSHOT, RecoveryOperation.RESTORE}
        else None,
        checkpoint_id=checkpoint_id,
        user_approved=operation is RecoveryOperation.SNAPSHOT or confirmed,
    )
    try:
        if operation is RecoveryOperation.SNAPSHOT:
            result = service.snapshot(request)
        elif operation is RecoveryOperation.TEST_RESTORE:
            result = service.test_restore(request)
        elif operation is RecoveryOperation.RESTORE:
            if not confirmed:
                raise ProductRecoveryError("RECOVERY_CONFIRMATION_REQUIRED")
            result = service.restore(request)
        else:
            raise ProductRecoveryError("RECOVERY_OPERATION_UNSUPPORTED", 422)
    except (OSError, RuntimeError, sqlite3.DatabaseError, ValueError) as error:
        if isinstance(error, ProductRecoveryError):
            raise
        raise ProductRecoveryError("RECOVERY_OPERATION_FAILED", 503) from error
    if not result.ok:
        status_code = (
            404 if result.reason_code == "RECOVERY_CHECKPOINT_NOT_FOUND" else 409
        )
        raise ProductRecoveryError(result.reason_code, status_code)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "AVAILABLE",
        "reason_code": result.reason_code,
        "checkpoint_id": result.checkpoint_id,
        "manifest_digest": result.manifest_digest,
        "verified_targets": result.details.get("verified_targets"),
        "evidence_refs": _checkpoint_evidence(database, result.checkpoint_id),
    }


def _checkpoint_evidence(database: StateDB, checkpoint_id: str | None) -> list[str]:
    if database._conn is None or checkpoint_id is None:
        return []
    rows = database._conn.execute(
        """SELECT event_id FROM evidence_ledger_events
           WHERE checkpoint_id = ? ORDER BY sequence""",
        (checkpoint_id,),
    ).fetchall()
    return [str(row[0]) for row in rows]


__all__ = [
    "ProductRecoveryError",
    "recovery_failure",
    "run_product_recovery",
]
