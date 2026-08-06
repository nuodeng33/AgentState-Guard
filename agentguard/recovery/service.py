"""P6 recovery orchestration across execution-domain adapters."""

from __future__ import annotations

import hashlib
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
from .manifest import validate_snapshot_v3


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
            outcome = self._result(
                request,
                CapabilityStatus.UNSUPPORTED,
                "RECOVERY_OPERATION_UNSUPPORTED",
            )
            self._record_failure(outcome)
            return outcome
        try:
            outcome = adapter.snapshot(request)
        except PermissionError:
            outcome = self._result(request, CapabilityStatus.PERMISSION_DENIED, "RECOVERY_PERMISSION_DENIED")
        except OSError:
            outcome = self._result(request, CapabilityStatus.UNREACHABLE, "RECOVERY_DOMAIN_UNREACHABLE")
        except Exception:  # noqa: BLE001 - adapter boundary fails closed.
            outcome = self._result(request, CapabilityStatus.ERROR, "RECOVERY_ADAPTER_FAILED")
        if not self._outcome_matches(request, outcome):
            failed = self._result(request, CapabilityStatus.ERROR, "RECOVERY_ADAPTER_RESULT_INVALID")
            self._record_failure(failed)
            return failed
        if not outcome.ok or outcome.artifact is None:
            self._record_failure(outcome)
            return outcome
        artifact = outcome.artifact
        valid, _reason_code, digest = validate_snapshot_v3(
            artifact,
            expected_domain=request.execution_domain_id,
        )
        if not valid or digest is None or outcome.manifest_digest != digest:
            failed = self._result(request, CapabilityStatus.ERROR, "RECOVERY_ADAPTER_RESULT_INVALID")
            self._record_failure(failed)
            return failed
        file_count = len(artifact["manifest"])
        target_ref_digests = self._target_ref_digests(artifact)
        relative_path: str | None = None
        try:
            with self._database.transaction() as connection:
                checkpoint_id = self._database.insert_checkpoint(
                    "P6 recovery snapshot",
                    "snapshots/PENDING",
                    digest,
                    file_count,
                    {},
                    None,
                    None,
                )
                relative_path = self._snapshots.save_recovery_v3(checkpoint_id, artifact)
                expected_path = f"snapshots/snapshot-{checkpoint_id:06d}.dat"
                if relative_path != expected_path:
                    raise RuntimeError("RECOVERY_ARTIFACT_PATH_INVALID")
                connection.execute(
                    "UPDATE checkpoints SET snapshot_path = ? WHERE id = ?",
                    (relative_path, checkpoint_id),
                )
                checkpoint_ref = str(checkpoint_id)
                self._append_event(
                    connection,
                    EventType.CHECKPOINT_CREATED,
                    outcome,
                    checkpoint_ref,
                    digest,
                    file_count,
                    target_ref_digests,
                )
                self._append_event(
                    connection,
                    EventType.MANIFEST_VERIFIED,
                    outcome,
                    checkpoint_ref,
                    digest,
                    file_count,
                    target_ref_digests,
                )
        except OSError:
            self._remove_artifact(relative_path)
            return self._result(request, CapabilityStatus.UNREACHABLE, "RECOVERY_DOMAIN_UNREACHABLE")
        except (sqlite3.DatabaseError, RuntimeError, ValueError):
            self._remove_artifact(relative_path)
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
        artifact, load_reason = self._snapshots.load_recovery_v3_with_status(
            checkpoint["snapshot_path"]
        )
        if artifact is None:
            status = {
                "LEGACY_SNAPSHOT_READ_ONLY": CapabilityStatus.UNSUPPORTED,
                "RECOVERY_ARTIFACT_NOT_FOUND": CapabilityStatus.NOT_PRESENT,
                "RECOVERY_DOMAIN_UNREACHABLE": CapabilityStatus.UNREACHABLE,
            }.get(load_reason, CapabilityStatus.ERROR)
            outcome = self._result(request, status, load_reason)
            self._record_failure(outcome)
            return outcome
        valid, reason_code, digest = validate_snapshot_v3(
            artifact,
            expected_domain=request.execution_domain_id,
        )
        if not valid or digest != checkpoint["hash_sha256"]:
            outcome = self._result(request, CapabilityStatus.ERROR, reason_code)
            self._record_failure(outcome)
            return outcome
        adapter = self._adapters.get(request.execution_domain_id)
        if adapter is None:
            outcome = self._result(request, CapabilityStatus.UNSUPPORTED, "RECOVERY_OPERATION_UNSUPPORTED")
            self._record_failure(outcome)
            return outcome
        try:
            outcome = adapter.verify(
                RecoveryRequest(
                    operation=RecoveryOperation.VERIFY,
                    execution_domain_id=request.execution_domain_id,
                    checkpoint_id=str(checkpoint["id"]),
                    artifact=artifact,
                )
            )
        except PermissionError:
            outcome = self._result(request, CapabilityStatus.PERMISSION_DENIED, "RECOVERY_PERMISSION_DENIED")
        except OSError:
            outcome = self._result(request, CapabilityStatus.UNREACHABLE, "RECOVERY_DOMAIN_UNREACHABLE")
        except Exception:  # noqa: BLE001 - adapter boundary fails closed.
            outcome = self._result(request, CapabilityStatus.ERROR, "RECOVERY_ADAPTER_FAILED")
        if (
            self._outcome_matches(request, outcome)
            and outcome.ok
            and outcome.manifest_digest == checkpoint["hash_sha256"]
        ):
            if not self._record_verified(
                outcome,
                len(artifact["manifest"]),
                self._target_ref_digests(artifact),
            ):
                return self._result(
                    request,
                    CapabilityStatus.ERROR,
                    "RECOVERY_PERSISTENCE_FAILED",
                    checkpoint_id=str(checkpoint["id"]),
                )
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
            outcome = self._result(
                request,
                CapabilityStatus.UNSUPPORTED,
                "REAL_RESTORE_OUT_OF_SCOPE_P6",
            )
        self._record_failure(outcome)
        return outcome

    def _checkpoint(self, request: RecoveryRequest) -> dict | None:
        if request.checkpoint_id is None or not request.checkpoint_id.isdecimal():
            return None
        return self._database.get_checkpoint(int(request.checkpoint_id))

    def _record_verified(
        self,
        outcome: RecoveryOperationResult,
        file_count: int,
        target_ref_digests: tuple[str, ...],
    ) -> bool:
        try:
            with self._database.transaction() as connection:
                self._append_event(
                    connection,
                    EventType.MANIFEST_VERIFIED,
                    outcome,
                    outcome.checkpoint_id,
                    outcome.manifest_digest,
                    file_count,
                    target_ref_digests,
                )
        except (OSError, sqlite3.DatabaseError, RuntimeError, ValueError):
            return False
        return True

    def _record_failure(self, outcome: RecoveryOperationResult) -> None:
        try:
            with self._database.transaction() as connection:
                self._append_event(
                    connection,
                    EventType.RESTORE_FAILED,
                    outcome,
                    outcome.checkpoint_id,
                    outcome.manifest_digest,
                    0,
                    (),
                )
        except (OSError, sqlite3.DatabaseError, RuntimeError, ValueError):
            return

    def _append_event(
        self,
        connection,
        event_type: EventType,
        outcome: RecoveryOperationResult,
        checkpoint_id: str | None,
        digest: str | None,
        file_count: int,
        target_ref_digests: tuple[str, ...],
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
                    "target_ref_digests": list(target_ref_digests),
                },
            ),
        )

    @staticmethod
    def _target_ref_digests(artifact: dict) -> tuple[str, ...]:
        return tuple(
            sorted(
                hashlib.sha256(entry["logical_path"].encode("utf-8")).hexdigest()
                for entry in artifact["manifest"]
                if entry["classification"] == "restorable"
            )
        )

    @staticmethod
    def _outcome_matches(
        request: RecoveryRequest,
        outcome: object,
    ) -> bool:
        return (
            isinstance(outcome, RecoveryOperationResult)
            and outcome.operation is request.operation
            and outcome.execution_domain_id == request.execution_domain_id
        )

    def _remove_artifact(self, relative_path: str | None) -> None:
        if relative_path is None:
            return
        try:
            self._snapshots.delete(relative_path)
        except OSError:
            return

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
