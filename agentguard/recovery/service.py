"""P6 recovery orchestration across execution-domain adapters."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentguard.discovery.capabilities import CapabilityStatus
from agentguard.evidence.canonical import canonical_json
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
        self._baseline_candidates: dict[str, tuple[dict[str, str], str]] = {}

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

    def test_restore(self, request: RecoveryRequest) -> RecoveryOperationResult:
        checkpoint = self._checkpoint(request)
        if checkpoint is None:
            outcome = self._result(request, CapabilityStatus.NOT_PRESENT, "RECOVERY_CHECKPOINT_NOT_FOUND")
            self._record_failure(outcome)
            return outcome
        artifact, load_reason = self._snapshots.load_recovery_v3_with_status(
            checkpoint["snapshot_path"]
        )
        if artifact is None:
            status = CapabilityStatus.UNSUPPORTED if load_reason == "LEGACY_SNAPSHOT_READ_ONLY" else CapabilityStatus.ERROR
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
        staging_root = Path(tempfile.mkdtemp(prefix="agentguard-test-restore-root-"))
        try:
            started = self._result(
                request,
                CapabilityStatus.AVAILABLE,
                "TEST_RESTORE_STARTED",
                checkpoint_id=str(checkpoint["id"]),
            )
            outcome = adapter.test_restore(
                RecoveryRequest(
                    operation=RecoveryOperation.TEST_RESTORE,
                    execution_domain_id=request.execution_domain_id,
                    checkpoint_id=str(checkpoint["id"]),
                    artifact=artifact,
                    sandbox_path=staging_root,
                )
            )
            if not self._outcome_matches(request, outcome):
                outcome = self._result(request, CapabilityStatus.ERROR, "RECOVERY_ADAPTER_RESULT_INVALID")
            with self._database.transaction() as connection:
                self._append_event(connection, EventType.TEST_RESTORE_STARTED, started, str(checkpoint["id"]), digest, 0, ())
                if outcome.ok:
                    self._append_event(connection, EventType.FILE_RESTORED, outcome, str(checkpoint["id"]), digest, outcome.details.get("verified_targets", 0), ())
                    self._append_event(connection, EventType.VALIDATOR_PASSED, outcome, str(checkpoint["id"]), digest, outcome.details.get("verified_targets", 0), ())
                else:
                    self._append_event(connection, EventType.RESTORE_FAILED, outcome, str(checkpoint["id"]), digest, 0, ())
            return outcome
        except (OSError, sqlite3.DatabaseError, RuntimeError, ValueError):
            failure = self._result(request, CapabilityStatus.ERROR, "RECOVERY_PERSISTENCE_FAILED")
            self._record_failure(failure)
            return failure
        finally:
            if "outcome" not in locals() or not outcome.ok:
                shutil.rmtree(staging_root, ignore_errors=True)

    def prepare_drill(
        self,
        *,
        checkpoint_id: str | None,
        execution_domain_id: str,
    ) -> dict[str, object]:
        context = self._drill_context(checkpoint_id, execution_domain_id)
        if context is None:
            return {"status": "FAILED", "reason_code": "DRILL_R2_EVIDENCE_REQUIRED"}
        drill_id = f"drill-{uuid4()}"
        binding = self._binding_digest(drill_id, context)
        now = datetime.now(UTC).isoformat()
        try:
            with self._database.transaction() as connection:
                connection.execute(
                    """INSERT INTO recovery_drills (
                           drill_id, checkpoint_id, execution_domain_id, manifest_digest,
                           target_refs_digest, binding_digest, status, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, 'AWAITING_APPROVAL', ?)""",
                    (drill_id, checkpoint_id, execution_domain_id, context["manifest_digest"],
                     context["target_refs_digest"], binding, now),
                )
                self._append_drill_event(
                    connection, EventType.RECOVERY_DRILL_PREPARED, drill_id,
                    "AWAITING_APPROVAL", context, binding,
                )
        except (sqlite3.DatabaseError, RuntimeError, ValueError):
            return {"status": "FAILED", "reason_code": "RECOVERY_PERSISTENCE_FAILED"}
        return {"drill_id": drill_id, "status": "AWAITING_APPROVAL", "binding_digest": binding}

    def approve_drill(self, drill_id: str) -> dict[str, object]:
        try:
            with self._database.transaction() as connection:
                row = connection.execute(
                    """SELECT checkpoint_id, execution_domain_id, manifest_digest,
                              target_refs_digest, binding_digest, status
                       FROM recovery_drills WHERE drill_id = ?""",
                    (drill_id,),
                ).fetchone()
                if row is None or row[5] != "AWAITING_APPROVAL":
                    return {"status": "FAILED", "reason_code": "DRILL_APPROVAL_INVALID"}
                approval_id = f"drill-approval-{uuid4()}"
                connection.execute(
                    """INSERT INTO recovery_drill_approvals
                           (approval_id, drill_id, binding_digest, approved_at)
                       VALUES (?, ?, ?, ?)""",
                    (approval_id, drill_id, row[4], datetime.now(UTC).isoformat()),
                )
                connection.execute(
                    "UPDATE recovery_drills SET status = 'APPROVED' WHERE drill_id = ?",
                    (drill_id,),
                )
                context = {
                    "checkpoint_id": row[0], "execution_domain_id": row[1],
                    "manifest_digest": row[2], "target_refs_digest": row[3],
                }
                self._append_drill_event(
                    connection, EventType.RECOVERY_DRILL_APPROVED, drill_id,
                    "APPROVED", context, row[4],
                )
        except (sqlite3.DatabaseError, RuntimeError, ValueError):
            return {"status": "FAILED", "reason_code": "RECOVERY_PERSISTENCE_FAILED"}
        return {"drill_id": drill_id, "status": "APPROVED", "binding_digest": row[4]}

    def run_drill(self, drill_id: str) -> dict[str, object]:
        try:
            with self._database.transaction() as connection:
                row = connection.execute(
                    """SELECT checkpoint_id, execution_domain_id, manifest_digest,
                              target_refs_digest, binding_digest, status
                       FROM recovery_drills WHERE drill_id = ?""",
                    (drill_id,),
                ).fetchone()
                if row is None:
                    return {"status": "FAILED", "reason_code": "DRILL_NOT_FOUND"}
                context = {
                    "checkpoint_id": row[0], "execution_domain_id": row[1],
                    "manifest_digest": row[2], "target_refs_digest": row[3],
                }
                if row[5] != "APPROVED" or self._drill_context(row[0], row[1]) != context:
                    return {"status": "FAILED", "reason_code": "DRILL_APPROVAL_MISSING_OR_CONSUMED"}
                run_id = f"drill-run-{uuid4()}"
                consumed = connection.execute(
                    """UPDATE recovery_drill_approvals
                       SET consumed_at = ?, consumed_by_run_id = ?
                       WHERE drill_id = ? AND binding_digest = ? AND consumed_at IS NULL""",
                    (datetime.now(UTC).isoformat(), run_id, drill_id, row[4]),
                ).rowcount
                if consumed != 1:
                    return {"status": "FAILED", "reason_code": "DRILL_APPROVAL_MISSING_OR_CONSUMED"}
                connection.execute(
                    "UPDATE recovery_drills SET status = 'RUNNING' WHERE drill_id = ?",
                    (drill_id,),
                )
                self._append_drill_event(
                    connection, EventType.RECOVERY_DRILL_STARTED, drill_id,
                    "RUNNING", context, row[4], {"run_id": run_id},
                )
        except (sqlite3.DatabaseError, RuntimeError, ValueError):
            return {"status": "FAILED", "reason_code": "RECOVERY_PERSISTENCE_FAILED"}

        result = self._run_managed_drill(context)
        cleaned = self._cleanup_drill_root(result.details.get("managed_target_root"))
        if result.ok and not cleaned:
            result = self._result(
                RecoveryRequest(
                    operation=RecoveryOperation.DRILL_RESTORE,
                    execution_domain_id=context["execution_domain_id"],
                    checkpoint_id=context["checkpoint_id"],
                ),
                CapabilityStatus.ERROR,
                "DRILL_CLEANUP_FAILED",
            )
        try:
            with self._database.transaction() as connection:
                final_status = "VERIFIED_R3" if result.ok else "FAILED"
                connection.execute(
                    """UPDATE recovery_drills SET status = ?, completed_at = ?
                       WHERE drill_id = ? AND status = 'RUNNING'""",
                    (final_status, datetime.now(UTC).isoformat(), drill_id),
                )
                details = {
                    "verified_targets": result.details.get("verified_targets", 0),
                    "drift_established": result.details.get("drift_established", False),
                    "managed_target_cleaned": cleaned,
                    "reason_code": result.reason_code,
                }
                if result.details.get("drift_established"):
                    self._append_drill_event(
                        connection, EventType.DRIFT_ESTABLISHED, drill_id,
                        "AVAILABLE", context, row[4], details,
                    )
                if result.ok:
                    self._append_drill_event(
                        connection, EventType.FILE_RESTORED, drill_id,
                        "AVAILABLE", context, row[4], details,
                    )
                    self._append_drill_event(
                        connection, EventType.VALIDATOR_PASSED, drill_id,
                        "AVAILABLE", context, row[4], details,
                    )
                    self._append_drill_event(
                        connection, EventType.RECOVERY_DRILL_VERIFIED, drill_id,
                        "VERIFIED_R3", context, row[4], details,
                    )
                self._append_drill_event(
                    connection,
                    EventType.RECOVERY_DRILL_COMPLETED if result.ok else EventType.RESTORE_FAILED,
                    drill_id, final_status, context, row[4], details,
                )
        except (sqlite3.DatabaseError, RuntimeError, ValueError):
            return {"status": "FAILED", "reason_code": "RECOVERY_PERSISTENCE_FAILED"}
        if not result.ok:
            return {"status": "FAILED", "reason_code": result.reason_code}
        return {
            "drill_id": drill_id,
            "status": "VERIFIED_R3",
            "reason_code": "DRILL_VERIFIED_R3",
            "verified_targets": result.details["verified_targets"],
            "drift_established": True,
            "managed_target_cleaned": cleaned,
        }

    def _run_managed_drill(self, context: dict[str, str]) -> RecoveryOperationResult:
        checkpoint = self._database.get_checkpoint(int(context["checkpoint_id"]))
        if checkpoint is None:
            return self._result(
                RecoveryRequest(RecoveryOperation.DRILL_RESTORE, context["execution_domain_id"]),
                CapabilityStatus.NOT_PRESENT,
                "RECOVERY_CHECKPOINT_NOT_FOUND",
            )
        artifact, reason_code = self._snapshots.load_recovery_v3_with_status(checkpoint["snapshot_path"])
        valid, validation_reason, digest = validate_snapshot_v3(
            artifact, expected_domain=context["execution_domain_id"]
        )
        if artifact is None or not valid or digest != context["manifest_digest"]:
            return self._result(
                RecoveryRequest(RecoveryOperation.DRILL_RESTORE, context["execution_domain_id"], checkpoint_id=context["checkpoint_id"]),
                CapabilityStatus.ERROR,
                validation_reason if artifact is not None else reason_code,
            )
        adapter = self._adapters.get(context["execution_domain_id"])
        if adapter is None or context["execution_domain_id"] != "self-runtime":
            return self._result(
                RecoveryRequest(RecoveryOperation.DRILL_RESTORE, context["execution_domain_id"], checkpoint_id=context["checkpoint_id"]),
                CapabilityStatus.UNSUPPORTED,
                "RECOVERY_OPERATION_UNSUPPORTED",
            )
        managed_base = self._database.db_path.parent / ".agentguard-r3-drills"
        try:
            managed_base.mkdir(mode=0o700, exist_ok=True)
            base = managed_base.resolve(strict=True)
            source_paths = [Path(entry["logical_path"]).resolve() for entry in artifact["manifest"]]
            if any(path == base or base in path.parents for path in source_paths):
                raise ValueError("DRILL_TARGET_OVERLAPS_SOURCE")
            drill_root = Path(tempfile.mkdtemp(prefix="drill-", dir=base))
            if drill_root.resolve(strict=True).parent != base or drill_root.is_symlink():
                raise ValueError("DRILL_TARGET_UNSAFE")
            request = RecoveryRequest(
                operation=RecoveryOperation.DRILL_RESTORE,
                execution_domain_id=context["execution_domain_id"],
                checkpoint_id=context["checkpoint_id"],
                artifact=artifact,
                drill_root=drill_root,
            )
            outcome = adapter.drill_restore(request)
            if not self._outcome_matches(request, outcome) or outcome.manifest_digest != digest:
                return self._result(request, CapabilityStatus.ERROR, "RECOVERY_ADAPTER_RESULT_INVALID")
            return replace(
                outcome,
                details={**outcome.details, "managed_target_root": str(drill_root.resolve())},
            )
        except (OSError, ValueError):
            return self._result(
                RecoveryRequest(RecoveryOperation.DRILL_RESTORE, context["execution_domain_id"], checkpoint_id=context["checkpoint_id"]),
                CapabilityStatus.ERROR,
                "DRILL_TARGET_UNSAFE",
            )

    @staticmethod
    def _cleanup_drill_root(root: object) -> bool:
        if not isinstance(root, str):
            return False
        try:
            path = Path(root)
            if not path.is_dir() or path.is_symlink() or path.name == ".agentguard-r3-drills":
                return False
            shutil.rmtree(path)
            return not path.exists()
        except OSError:
            return False

    def _drill_context(
        self,
        checkpoint_id: str | None,
        execution_domain_id: str,
    ) -> dict[str, str] | None:
        if execution_domain_id != "self-runtime" or checkpoint_id is None or not checkpoint_id.isdecimal():
            return None
        checkpoint = self._database.get_checkpoint(int(checkpoint_id))
        if checkpoint is None:
            return None
        artifact, _reason = self._snapshots.load_recovery_v3_with_status(checkpoint["snapshot_path"])
        if artifact is None:
            return None
        valid, _reason, digest = validate_snapshot_v3(artifact, expected_domain=execution_domain_id)
        if not valid or digest != checkpoint["hash_sha256"]:
            return None
        conn = self._database._conn
        if conn is None or not self._has_r2_evidence(conn, checkpoint_id, execution_domain_id, digest):
            return None
        targets = self._target_ref_digests(artifact)
        if not targets:
            return None
        return {
            "checkpoint_id": checkpoint_id,
            "execution_domain_id": execution_domain_id,
            "manifest_digest": digest,
            "target_refs_digest": hashlib.sha256(canonical_json(list(targets)).encode()).hexdigest(),
        }

    @staticmethod
    def _has_r2_evidence(connection, checkpoint_id: str, domain: str, digest: str) -> bool:
        rows = connection.execute(
            """SELECT event_type, result, execution_domain_id, subject_ref
               FROM evidence_ledger_events
               WHERE checkpoint_id = ? AND event_type IN ('TEST_RESTORE_STARTED', 'FILE_RESTORED', 'VALIDATOR_PASSED')""",
            (checkpoint_id,),
        ).fetchall()
        return {
            event_type for event_type, result, current_domain, subject_ref in rows
            if result == "AVAILABLE" and current_domain == domain and subject_ref == f"manifest:{digest}"
        } == {"TEST_RESTORE_STARTED", "FILE_RESTORED", "VALIDATOR_PASSED"}

    @staticmethod
    def _binding_digest(drill_id: str, context: dict[str, str]) -> str:
        payload = {"operation": "SELF_RUNTIME_R3_DRILL", "drill_id": drill_id, **context}
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()

    def _append_drill_event(
        self, connection, event_type: EventType, drill_id: str, result: str,
        context: dict[str, str], binding_digest: str, details: dict[str, object] | None = None,
    ) -> None:
        self._ledger.append(
            connection,
            EvidenceEvent(
                schema_version=1, event_id=f"recovery-drill-{uuid4()}",
                recorded_at=datetime.now(UTC), observed_at=None,
                event_family=EventFamily.RECOVERY, event_type=event_type,
                source="recovery-drill-service", result=result,
                execution_domain_id=context["execution_domain_id"], supervision_session_id=None,
                transaction_id=None, checkpoint_id=context["checkpoint_id"],
                subject_ref=f"manifest:{context['manifest_digest']}", evidence_refs=(),
                payload_safe={
                    "drill_id": drill_id, "binding_digest": binding_digest,
                    "manifest_digest": context["manifest_digest"],
                    "target_refs_digest": context["target_refs_digest"],
                    **(details or {}),
                },
            ),
        )

    def create_trusted_baseline(
        self,
        *,
        checkpoint_id: str | None,
        execution_domain_id: str,
    ) -> dict[str, object]:
        context = self._drill_context(checkpoint_id, execution_domain_id)
        if context is None:
            return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_R3_REQUIRED"}
        connection = self._database._conn
        if connection is None or not self._has_r3_evidence(connection, context):
            return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_R3_REQUIRED"}
        candidate_id = f"baseline-candidate-{uuid4()}"
        confirmation = hashlib.sha256(
            canonical_json({"operation": "TRUSTED_BASELINE_CONFIRM", "candidate_id": candidate_id, **context}).encode()
        ).hexdigest()
        self._baseline_candidates[candidate_id] = (context, confirmation)
        return {"candidate_id": candidate_id, "status": "AWAITING_CONFIRMATION", "confirmation_digest": confirmation, "reason_code": "TRUSTED_BASELINE_CONFIRMATION_REQUIRED"}

    def confirm_trusted_baseline(self, candidate_id: str) -> dict[str, object]:
        candidate = self._baseline_candidates.pop(candidate_id, None)
        if candidate is None:
            return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_CONFIRMATION_INVALID"}
        context, confirmation = candidate
        connection = self._database._conn
        if connection is None or not self._has_r3_evidence(connection, context):
            return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_R3_REQUIRED"}
        baseline_id = f"baseline-{uuid4()}"
        evidence_digest = hashlib.sha256(canonical_json({"confirmation": confirmation, **context}).encode()).hexdigest()
        try:
            with self._database.transaction() as transaction:
                transaction.execute(
                    """INSERT INTO trusted_baselines (
                           baseline_id, checkpoint_id, execution_domain_id, manifest_digest,
                           target_refs_digest, recovery_evidence_digest, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (baseline_id, context["checkpoint_id"], context["execution_domain_id"],
                     context["manifest_digest"], context["target_refs_digest"], evidence_digest,
                     datetime.now(UTC).isoformat()),
                )
                self._append_baseline_event(
                    transaction, EventType.TRUSTED_BASELINE_CREATED, baseline_id,
                    "TRUSTED", context, evidence_digest,
                )
        except (sqlite3.DatabaseError, RuntimeError, ValueError):
            return {"status": "FAILED", "reason_code": "RECOVERY_PERSISTENCE_FAILED"}
        return {"baseline_id": baseline_id, "status": "TRUSTED", "recovery_evidence_digest": evidence_digest}

    def retire_trusted_baseline(self, baseline_id: str, reason_code: str) -> dict[str, object]:
        if not reason_code or len(reason_code) > 128:
            return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_RETIREMENT_INVALID"}
        try:
            with self._database.transaction() as connection:
                row = connection.execute(
                    """SELECT checkpoint_id, execution_domain_id, manifest_digest, target_refs_digest
                       FROM trusted_baselines WHERE baseline_id = ?""",
                    (baseline_id,),
                ).fetchone()
                if row is None:
                    return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_NOT_FOUND"}
                retired = connection.execute(
                    "SELECT 1 FROM trusted_baseline_retirements WHERE baseline_id = ?",
                    (baseline_id,),
                ).fetchone()
                if retired is not None:
                    return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_ALREADY_RETIRED"}
                connection.execute(
                    """INSERT INTO trusted_baseline_retirements
                           (retirement_id, baseline_id, retired_at, reason_code)
                       VALUES (?, ?, ?, ?)""",
                    (f"baseline-retirement-{uuid4()}", baseline_id, datetime.now(UTC).isoformat(), reason_code),
                )
                context = {"checkpoint_id": row[0], "execution_domain_id": row[1], "manifest_digest": row[2], "target_refs_digest": row[3]}
                self._append_baseline_event(connection, EventType.TRUSTED_BASELINE_RETIRED, baseline_id, "RETIRED", context, reason_code)
        except (sqlite3.DatabaseError, RuntimeError, ValueError):
            return {"status": "FAILED", "reason_code": "RECOVERY_PERSISTENCE_FAILED"}
        return {"baseline_id": baseline_id, "status": "RETIRED"}

    @staticmethod
    def _has_r3_evidence(connection, context: dict[str, str]) -> bool:
        row = connection.execute(
            """SELECT 1 FROM recovery_drills WHERE checkpoint_id = ?
               AND execution_domain_id = ? AND manifest_digest = ?
               AND target_refs_digest = ? AND status = 'VERIFIED_R3'""",
            (context["checkpoint_id"], context["execution_domain_id"], context["manifest_digest"], context["target_refs_digest"]),
        ).fetchone()
        return row is not None

    def _append_baseline_event(
        self, connection, event_type: EventType, baseline_id: str, result: str,
        context: dict[str, str], evidence_digest: str,
    ) -> None:
        self._ledger.append(
            connection,
            EvidenceEvent(
                schema_version=1, event_id=f"trusted-baseline-{uuid4()}",
                recorded_at=datetime.now(UTC), observed_at=None,
                event_family=EventFamily.RECOVERY, event_type=event_type,
                source="trusted-baseline-service", result=result,
                execution_domain_id=context["execution_domain_id"], supervision_session_id=None,
                transaction_id=None, checkpoint_id=context["checkpoint_id"],
                subject_ref=f"manifest:{context['manifest_digest']}", evidence_refs=(),
                payload_safe={"baseline_id": baseline_id, "manifest_digest": context["manifest_digest"], "target_refs_digest": context["target_refs_digest"], "recovery_evidence_digest": evidence_digest},
            ),
        )

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
