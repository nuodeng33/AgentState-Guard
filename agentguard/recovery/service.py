"""P6 recovery orchestration across execution-domain adapters."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import uuid4

from agentguard.discovery.capabilities import CapabilityStatus
from agentguard.evidence.ledger import EvidenceLedger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore

from .contracts import RecoveryOperation, RecoveryOperationResult, RecoveryRequest
from .manifest import manifest_digest


class RecoveryService:
    """Route P6 recovery requests without invoking legacy restore behavior."""

    def __init__(
        self,
        *,
        database: StateDB,
        snapshots: SnapshotStore,
        adapters: Mapping[str, object],
        ledger: EvidenceLedger | None = None,
    ) -> None:
        self._database = database
        self._snapshots = snapshots
        self._adapters = dict(adapters)
        self._ledger = ledger or EvidenceLedger()

    def snapshot(self, request: RecoveryRequest) -> RecoveryOperationResult:
        adapter = self._adapters.get(request.execution_domain_id)
        if adapter is None:
            return self._result(request, CapabilityStatus.UNSUPPORTED, "RECOVERY_OPERATION_UNSUPPORTED")
        outcome = adapter.snapshot(request)
        if not outcome.ok or outcome.artifact is None:
            self._record_failure(outcome)
            return outcome
        artifact = outcome.artifact
        digest = outcome.manifest_digest or manifest_digest(artifact)
        file_count = len(artifact["manifest"])
        try:
            with self._database.transaction() as connection:
                checkpoint_id = self._database.insert_checkpoint(
                    "P6 recovery snapshot",
                    f"snapshots/snapshot-{self._next_checkpoint_id(connection):06d}.dat",
                    digest,
                    file_count,
                    {},
                    None,
                    None,
                )
                relative_path = self._snapshots.save_recovery_v3(checkpoint_id, artifact)
                if relative_path != f"snapshots/snapshot-{checkpoint_id:06d}.dat":
                    raise RuntimeError("RECOVERY_ARTIFACT_PATH_INVALID")
                checkpoint_ref = str(checkpoint_id)
                self._append_event(
                    connection,
                    EventType.CHECKPOINT_CREATED,
                    outcome,
                    checkpoint_ref,
                    digest,
                    file_count,
                )
                self._append_event(
                    connection,
                    EventType.MANIFEST_VERIFIED,
                    outcome,
                    checkpoint_ref,
                    digest,
                    file_count,
                )
        except OSError:
            return self._result(request, CapabilityStatus.UNREACHABLE, "RECOVERY_DOMAIN_UNREACHABLE")
        except (sqlite3.DatabaseError, RuntimeError, ValueError):
            return self._result(request, CapabilityStatus.ERROR, "RECOVERY_PERSISTENCE_FAILED")
        return RecoveryOperationResult(
            operation=request.operation,
            status=CapabilityStatus.AVAILABLE,
            reason_code="RECOVERY_SNAPSHOT_CREATED",
            execution_domain_id=request.execution_domain_id,
            checkpoint_id=checkpoint_ref,
            manifest_digest=digest,
        )

    def verify(self, request: RecoveryRequest) -> RecoveryOperationResult:
        checkpoint = self._checkpoint(request)
        if checkpoint is None:
            outcome = self._result(request, CapabilityStatus.NOT_PRESENT, "RECOVERY_CHECKPOINT_NOT_FOUND")
            self._record_failure(outcome)
            return outcome
        artifact = self._snapshots.load_recovery_v3(checkpoint["snapshot_path"])
        if artifact is None:
            outcome = self._result(request, CapabilityStatus.UNSUPPORTED, "LEGACY_SNAPSHOT_READ_ONLY")
            self._record_failure(outcome)
            return outcome
        adapter = self._adapters.get(request.execution_domain_id)
        if adapter is None:
            outcome = self._result(request, CapabilityStatus.UNSUPPORTED, "RECOVERY_OPERATION_UNSUPPORTED")
            self._record_failure(outcome)
            return outcome
        outcome = adapter.verify(
            RecoveryRequest(
                operation=RecoveryOperation.VERIFY,
                execution_domain_id=request.execution_domain_id,
                checkpoint_id=str(checkpoint["id"]),
                artifact=artifact,
            )
        )
        if outcome.ok and outcome.manifest_digest == checkpoint["hash_sha256"]:
            self._record_verified(outcome, len(artifact["manifest"]))
            return outcome
        failed = self._result(
            request,
            CapabilityStatus.ERROR,
            "RECOVERY_MANIFEST_INVALID",
            checkpoint_id=str(checkpoint["id"]),
        )
        self._record_failure(failed)
        return failed

    def restore(self, request: RecoveryRequest) -> RecoveryOperationResult:
        adapter = self._adapters.get(request.execution_domain_id)
        if adapter is None:
            outcome = self._result(request, CapabilityStatus.UNSUPPORTED, "RECOVERY_OPERATION_UNSUPPORTED")
        else:
            outcome = adapter.restore(request)
        self._record_failure(outcome)
        return outcome

    def _checkpoint(self, request: RecoveryRequest) -> dict | None:
        if request.checkpoint_id is None or not request.checkpoint_id.isdecimal():
            return None
        return self._database.get_checkpoint(int(request.checkpoint_id))

    def _record_verified(self, outcome: RecoveryOperationResult, file_count: int) -> None:
        with self._database.transaction() as connection:
            self._append_event(
                connection,
                EventType.MANIFEST_VERIFIED,
                outcome,
                outcome.checkpoint_id,
                outcome.manifest_digest,
                file_count,
            )

    def _record_failure(self, outcome: RecoveryOperationResult) -> None:
        with self._database.transaction() as connection:
            self._append_event(
                connection,
                EventType.RESTORE_FAILED,
                outcome,
                outcome.checkpoint_id,
                outcome.manifest_digest,
                0,
            )

    def _append_event(
        self,
        connection,
        event_type: EventType,
        outcome: RecoveryOperationResult,
        checkpoint_id: str | None,
        digest: str | None,
        file_count: int,
    ) -> None:
        self._ledger.append(
            connection,
            EvidenceEvent(
                schema_version=1,
                event_id=f"recovery-{uuid4()}",
                recorded_at=datetime.now(UTC),
                observed_at=None,
                event_family=EventFamily.RECOVERY,
                event_type=event_type,
                source="recovery-service",
                result=outcome.status.value,
                execution_domain_id=outcome.execution_domain_id,
                supervision_session_id=None,
                transaction_id=None,
                checkpoint_id=checkpoint_id,
                subject_ref=(f"manifest:{digest}" if digest else None),
                evidence_refs=(),
                payload_safe={
                    "adapter": type(self._adapters.get(outcome.execution_domain_id)).__name__,
                    "reason_code": outcome.reason_code,
                    "manifest_digest": digest,
                    "file_count": file_count,
                },
            ),
        )

    @staticmethod
    def _next_checkpoint_id(connection) -> int:
        row = connection.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM checkpoints").fetchone()
        return int(row[0])

    @staticmethod
    def _result(
        request: RecoveryRequest,
        status: CapabilityStatus,
        reason_code: str,
        *,
        checkpoint_id: str | None = None,
    ) -> RecoveryOperationResult:
        return RecoveryOperationResult(
            operation=request.operation,
            status=status,
            reason_code=reason_code,
            execution_domain_id=request.execution_domain_id,
            checkpoint_id=checkpoint_id or request.checkpoint_id,
        )
