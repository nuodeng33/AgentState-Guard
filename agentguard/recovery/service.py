"""P6 recovery orchestration across execution-domain adapters."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
        now = datetime.now(UTC)
        try:
            with self._database.transaction() as connection:
                from agentguard.supervision.service import SupervisionService

                supervision = SupervisionService(self._database)
                session = supervision.create_recovery_approval_session(
                    connection,
                    operation_kind="SELF_RUNTIME_R3_DRILL",
                )
                session_identity = connection.execute(
                    """SELECT declared_intent_digest FROM supervision_sessions
                       WHERE supervision_session_id = ?""",
                    (session.supervision_session_id,),
                ).fetchone()[0]
                connection.execute(
                    """INSERT INTO recovery_drills (
                           drill_id, checkpoint_id, execution_domain_id, manifest_digest,
                           target_refs_digest, binding_digest, status, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, 'AWAITING_APPROVAL', ?)""",
                    (drill_id, checkpoint_id, execution_domain_id, context["manifest_digest"],
                     context["target_refs_digest"], binding, now.isoformat()),
                )
                connection.execute(
                    """INSERT INTO recovery_drill_bindings
                       (drill_id, supervision_session_id, session_identity_digest,
                        policy_version, operation_kind, binding_digest, created_at)
                       VALUES (?, ?, ?, ?, 'SELF_RUNTIME_R3_DRILL', ?, ?)""",
                    (
                        drill_id, session.supervision_session_id, session_identity,
                        "P4-LOCAL-1", binding, now.isoformat(),
                    ),
                )
                self._append_drill_event(
                    connection, EventType.RECOVERY_DRILL_PREPARED, drill_id,
                    "AWAITING_APPROVAL", context, binding,
                    supervision_session_id=session.supervision_session_id,
                )
        except (sqlite3.DatabaseError, RuntimeError, ValueError, IndexError):
            return {"status": "FAILED", "reason_code": "RECOVERY_PERSISTENCE_FAILED"}
        return {
            "drill_id": drill_id,
            "supervision_session_id": session.supervision_session_id,
            "status": "AWAITING_APPROVAL",
            "binding_digest": binding,
        }

    def approve_drill(self, drill_id: str) -> dict[str, object]:
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=15)
        authorization_id = f"recovery-authorization-{uuid4()}"
        nonce = uuid4().hex
        try:
            with self._database.transaction() as connection:
                row = connection.execute(
                    """SELECT d.checkpoint_id, d.execution_domain_id, d.manifest_digest,
                              d.target_refs_digest, d.binding_digest, d.status,
                              b.supervision_session_id, b.session_identity_digest,
                              b.policy_version
                       FROM recovery_drills d
                       JOIN recovery_drill_bindings b USING (drill_id)
                       WHERE d.drill_id = ?""",
                    (drill_id,),
                ).fetchone()
                if row is None or row[5] != "AWAITING_APPROVAL":
                    return {"status": "FAILED", "reason_code": "DRILL_APPROVAL_INVALID"}
                context = {
                    "checkpoint_id": row[0], "execution_domain_id": row[1],
                    "manifest_digest": row[2], "target_refs_digest": row[3],
                }
                fingerprint = self._drill_fingerprint(drill_id, row[4])
                binding = self._authorization_binding_digest(
                    authorization_id, drill_id, row[7], context,
                    "SELF_RUNTIME_R3_DRILL", fingerprint, row[8], nonce,
                )
                authorization = {
                    "authorization_id": authorization_id, "subject_id": drill_id,
                    "session_identity_digest": row[7], "checkpoint_id": row[0],
                    "execution_domain_id": row[1], "manifest_digest": row[2],
                    "operation_kind": "SELF_RUNTIME_R3_DRILL",
                    "target_refs_digest": row[3], "drill_fingerprint": fingerprint,
                    "policy_version": row[8], "binding_digest": binding,
                    "issued_at": now.isoformat(), "expires_at": expires_at.isoformat(),
                    "nonce": nonce,
                }
                from agentguard.supervision.service import SupervisionService

                approved = SupervisionService(self._database)._approve_recovery_authorization_in_transaction(
                    connection, row[6], authorization
                )
                if approved.status != "APPROVED":
                    return {"status": "FAILED", "reason_code": "DRILL_APPROVAL_INVALID"}
                if connection.execute(
                    """UPDATE recovery_drills SET status = 'APPROVED'
                       WHERE drill_id = ? AND status = 'AWAITING_APPROVAL'""",
                    (drill_id,),
                ).rowcount != 1:
                    raise RuntimeError("DRILL_APPROVAL_STATE_INVALID")
                self._append_drill_event(
                    connection, EventType.RECOVERY_DRILL_APPROVED, drill_id,
                    "APPROVED", context, row[4],
                    {"authorization_binding_digest": binding}, row[6],
                )
        except (sqlite3.DatabaseError, RuntimeError, ValueError, KeyError, IndexError):
            return {"status": "FAILED", "reason_code": "RECOVERY_PERSISTENCE_FAILED"}
        return {"drill_id": drill_id, "status": "APPROVED", "binding_digest": row[4]}


    def run_drill(self, drill_id: str) -> dict[str, object]:
        try:
            with self._database.transaction() as connection:
                row = connection.execute(
                    """SELECT d.checkpoint_id, d.execution_domain_id, d.manifest_digest,
                              d.target_refs_digest, d.binding_digest, d.status,
                              b.supervision_session_id, b.session_identity_digest,
                              b.policy_version
                       FROM recovery_drills d
                       JOIN recovery_drill_bindings b USING (drill_id)
                       WHERE d.drill_id = ?""",
                    (drill_id,),
                ).fetchone()
                if row is None:
                    return {"status": "FAILED", "reason_code": "DRILL_NOT_FOUND"}
                context = {
                    "checkpoint_id": row[0], "execution_domain_id": row[1],
                    "manifest_digest": row[2], "target_refs_digest": row[3],
                }
                fingerprint = self._drill_fingerprint(drill_id, row[4])
                authorization = connection.execute(
                    """SELECT session_identity_digest, checkpoint_id, execution_domain_id,
                              manifest_digest, operation_kind, target_refs_digest,
                              drill_fingerprint, policy_version, expires_at, nonce, consumed_at
                       FROM recovery_authorizations WHERE subject_id = ?""",
                    (drill_id,),
                ).fetchone()
                if row[5] != "APPROVED" or authorization is None or authorization[10] is not None:
                    return {"status": "FAILED", "reason_code": "DRILL_APPROVAL_MISSING_OR_CONSUMED"}
                try:
                    expired = datetime.now(UTC) >= datetime.fromisoformat(authorization[8])
                except ValueError:
                    return {"status": "FAILED", "reason_code": "DRILL_APPROVAL_MISSING_OR_CONSUMED"}
                if expired or authorization[:8] != (
                    row[7], row[0], row[1], row[2], "SELF_RUNTIME_R3_DRILL",
                    row[3], fingerprint, row[8],
                ):
                    return {"status": "FAILED", "reason_code": "DRILL_APPROVAL_MISSING_OR_CONSUMED"}
                run_id = f"drill-run-{uuid4()}"
                consumed = connection.execute(
                    """UPDATE recovery_authorizations
                       SET consumed_at = ?, consumed_by_ref = ?
                       WHERE subject_id = ? AND nonce = ? AND consumed_at IS NULL
                         AND expires_at > ?""",
                    (
                        datetime.now(UTC).isoformat(), run_id, drill_id,
                        authorization[9], datetime.now(UTC).isoformat(),
                    ),
                ).rowcount
                if consumed != 1:
                    return {"status": "FAILED", "reason_code": "DRILL_APPROVAL_MISSING_OR_CONSUMED"}
                connection.execute(
                    "UPDATE recovery_drills SET status = 'RUNNING' WHERE drill_id = ? AND status = 'APPROVED'",
                    (drill_id,),
                )
                self._append_drill_event(
                    connection, EventType.RECOVERY_DRILL_STARTED, drill_id,
                    "RUNNING", context, row[4], {"run_id": run_id}, row[6],
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
                        "AVAILABLE", context, row[4], details, row[6],
                    )
                if result.ok:
                    self._append_drill_event(
                        connection, EventType.FILE_RESTORED, drill_id,
                        "AVAILABLE", context, row[4], details, row[6],
                    )
                    self._append_drill_event(
                        connection, EventType.VALIDATOR_PASSED, drill_id,
                        "AVAILABLE", context, row[4], details, row[6],
                    )
                    self._append_drill_event(
                        connection, EventType.RECOVERY_DRILL_VERIFIED, drill_id,
                        "VERIFIED_R3", context, row[4], details, row[6],
                    )
                self._append_drill_event(
                    connection,
                    EventType.RECOVERY_DRILL_COMPLETED if result.ok else EventType.RESTORE_FAILED,
                    drill_id, final_status, context, row[4], details, row[6],
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

    @staticmethod
    def _drill_fingerprint(drill_id: str, binding_digest: str) -> str:
        return hashlib.sha256(canonical_json({
            "drill_id": drill_id,
            "binding_digest": binding_digest,
        }).encode()).hexdigest()

    def _append_drill_event(
        self, connection, event_type: EventType, drill_id: str, result: str,
        context: dict[str, str], binding_digest: str, details: dict[str, object] | None = None,
        supervision_session_id: str | None = None,
    ) -> None:
        self._ledger.append(
            connection,
            EvidenceEvent(
                schema_version=1, event_id=f"recovery-drill-{uuid4()}",
                recorded_at=datetime.now(UTC), observed_at=None,
                event_family=EventFamily.RECOVERY, event_type=event_type,
                source="recovery-drill-service", result=result,
                execution_domain_id=context["execution_domain_id"], supervision_session_id=supervision_session_id,
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
        evidence = (
            self._validated_r3_evidence(connection, context)
            if connection is not None
            else None
        )
        if evidence is None:
            return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_R3_REQUIRED"}
        candidate_id = f"baseline-candidate-{uuid4()}"
        binding = self._baseline_binding_digest(candidate_id, context, evidence)
        now = datetime.now(UTC)
        try:
            with self._database.transaction() as transaction:
                from agentguard.supervision.service import SupervisionService

                supervision = SupervisionService(self._database)
                session = supervision.create_recovery_approval_session(
                    transaction,
                    operation_kind="TRUSTED_BASELINE_CONFIRM",
                )
                session_identity = transaction.execute(
                    """SELECT declared_intent_digest FROM supervision_sessions
                       WHERE supervision_session_id = ?""",
                    (session.supervision_session_id,),
                ).fetchone()[0]
                transaction.execute(
                    """INSERT INTO trusted_baseline_candidates
                       (candidate_id, checkpoint_id, execution_domain_id, manifest_digest,
                        target_refs_digest, recovery_evidence_digest, binding_digest, status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'CANDIDATE', ?)""",
                    (
                        candidate_id,
                        context["checkpoint_id"],
                        context["execution_domain_id"],
                        context["manifest_digest"],
                        context["target_refs_digest"],
                        evidence["recovery_evidence_digest"],
                        binding,
                        now.isoformat(),
                    ),
                )
                transaction.execute(
                    """INSERT INTO trusted_baseline_candidate_bindings
                       (candidate_id, supervision_session_id, session_identity_digest,
                        policy_version, drill_fingerprint, binding_digest, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        candidate_id,
                        session.supervision_session_id,
                        session_identity,
                        evidence["policy_version"],
                        evidence["drill_fingerprint"],
                        binding,
                        now.isoformat(),
                    ),
                )
        except (sqlite3.DatabaseError, RuntimeError, ValueError, IndexError):
            return {"status": "FAILED", "reason_code": "RECOVERY_PERSISTENCE_FAILED"}
        return {
            "candidate_id": candidate_id,
            "supervision_session_id": session.supervision_session_id,
            "status": "CANDIDATE",
            "binding_digest": binding,
            "reason_code": "TRUSTED_BASELINE_CONFIRMATION_REQUIRED",
        }

    def show_trusted_baseline(self, baseline_id: str) -> dict[str, object]:
        connection = self._database._conn
        if connection is None:
            return {"status": "FAILED", "reason_code": "RECOVERY_DATABASE_UNREACHABLE"}
        candidate = connection.execute(
            "SELECT candidate_id, status, binding_digest, checkpoint_id, execution_domain_id, manifest_digest FROM trusted_baseline_candidates WHERE candidate_id = ?",
            (baseline_id,),
        ).fetchone()
        if candidate is not None:
            return {"candidate_id": candidate[0], "status": candidate[1], "binding_digest": candidate[2], "checkpoint_id": candidate[3], "execution_domain_id": candidate[4], "manifest_digest": candidate[5]}
        baseline = connection.execute(
            """SELECT baseline_id, checkpoint_id, execution_domain_id, manifest_digest
               FROM trusted_baselines WHERE baseline_id = ?""",
            (baseline_id,),
        ).fetchone()
        if baseline is None:
            return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_NOT_FOUND"}
        retired = connection.execute(
            "SELECT 1 FROM trusted_baseline_retirements WHERE baseline_id = ?", (baseline_id,)
        ).fetchone()
        if retired is not None:
            return {"baseline_id": baseline[0], "status": "RETIRED", "checkpoint_id": baseline[1], "execution_domain_id": baseline[2], "manifest_digest": baseline[3]}
        try:
            checkpoint = self._database.get_checkpoint(int(baseline[1]))
            if checkpoint is None:
                raise ValueError("checkpoint unavailable")
            artifact, _reason = self._snapshots.load_recovery_v3_with_status(
                checkpoint["snapshot_path"]
            )
            if artifact is None:
                raise ValueError("artifact unavailable")
            target_refs = tuple(
                entry["logical_path"]
                for entry in artifact["manifest"]
                if entry["classification"] == "restorable"
            )
            from .coverage import RecoveryCoverageService

            facts = RecoveryCoverageService(self._database, self._snapshots).compute(
                checkpoint_id=baseline[1],
                target_refs=target_refs,
                execution_domain_id=baseline[2],
            )
        except (OSError, TypeError, ValueError, KeyError, sqlite3.DatabaseError):
            facts = None
        if facts is None or facts.trusted_baseline_status != "TRUSTED" or facts.trusted_baseline_id != baseline_id:
            return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_AUTHORITY_INVALID"}
        return {"baseline_id": baseline[0], "status": "TRUSTED", "checkpoint_id": baseline[1], "execution_domain_id": baseline[2], "manifest_digest": baseline[3]}

    def show_drill(self, drill_id: str) -> dict[str, object]:
        connection = self._database._conn
        if connection is None:
            return {"status": "FAILED", "reason_code": "RECOVERY_DATABASE_UNREACHABLE"}
        row = connection.execute(
            """SELECT drill_id, checkpoint_id, execution_domain_id, manifest_digest,
                      target_refs_digest, status
               FROM recovery_drills WHERE drill_id = ?""",
            (drill_id,),
        ).fetchone()
        if row is None:
            return {"status": "FAILED", "reason_code": "DRILL_NOT_FOUND"}
        if row[5] == "VERIFIED_R3":
            context = self._drill_context(row[1], row[2])
            if (
                context is None
                or context["manifest_digest"] != row[3]
                or context["target_refs_digest"] != row[4]
                or self._validated_r3_evidence(connection, context, drill_id=row[0]) is None
            ):
                return {"status": "FAILED", "reason_code": "RECOVERY_DRILL_AUTHORITY_INVALID"}
        return {
            "drill_id": row[0],
            "checkpoint_id": row[1],
            "execution_domain_id": row[2],
            "manifest_digest": row[3],
            "status": row[5],
        }

    def approve_trusted_baseline(self, candidate_id: str) -> dict[str, object]:
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=15)
        authorization_id = f"recovery-authorization-{uuid4()}"
        nonce = uuid4().hex
        try:
            with self._database.transaction() as connection:
                row = connection.execute(
                    """SELECT c.checkpoint_id, c.execution_domain_id, c.manifest_digest,
                              c.target_refs_digest, c.recovery_evidence_digest, c.status,
                              b.supervision_session_id, b.session_identity_digest,
                              b.policy_version, b.drill_fingerprint
                       FROM trusted_baseline_candidates c
                       JOIN trusted_baseline_candidate_bindings b USING (candidate_id)
                       WHERE c.candidate_id = ?""",
                    (candidate_id,),
                ).fetchone()
                if row is None or row[5] != "CANDIDATE":
                    return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_CONFIRMATION_INVALID"}
                context = self._context_from_row(row)
                evidence = self._validated_r3_evidence(connection, context)
                if (
                    evidence is None
                    or evidence["recovery_evidence_digest"] != row[4]
                    or evidence["drill_fingerprint"] != row[9]
                    or evidence["policy_version"] != row[8]
                ):
                    return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_R3_REQUIRED"}
                binding = self._authorization_binding_digest(
                    authorization_id, candidate_id, row[7], context,
                    "TRUSTED_BASELINE_CONFIRM", row[9], row[8], nonce,
                )
                authorization = {
                    "authorization_id": authorization_id, "subject_id": candidate_id,
                    "session_identity_digest": row[7], "checkpoint_id": row[0],
                    "execution_domain_id": row[1], "manifest_digest": row[2],
                    "operation_kind": "TRUSTED_BASELINE_CONFIRM",
                    "target_refs_digest": row[3], "drill_fingerprint": row[9],
                    "policy_version": row[8], "binding_digest": binding,
                    "issued_at": now.isoformat(), "expires_at": expires_at.isoformat(),
                    "nonce": nonce,
                }
                from agentguard.supervision.service import SupervisionService

                approved = SupervisionService(self._database)._approve_recovery_authorization_in_transaction(
                    connection, row[6], authorization
                )
                if approved.status != "APPROVED":
                    return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_CONFIRMATION_INVALID"}
        except (sqlite3.DatabaseError, RuntimeError, ValueError, KeyError, IndexError):
            return {"status": "FAILED", "reason_code": "RECOVERY_PERSISTENCE_FAILED"}
        return {"authorization_id": authorization_id, "nonce": nonce, "status": "APPROVED"}

    def confirm_trusted_baseline(
        self,
        candidate_id: str,
        authorization_id: str | None = None,
        nonce: str | None = None,
    ) -> dict[str, object]:
        if not authorization_id or not nonce:
            return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_CONFIRMATION_INVALID"}
        now = datetime.now(UTC)
        baseline_id = f"baseline-{uuid4()}"
        try:
            with self._database.transaction() as transaction:
                row = transaction.execute(
                    """SELECT c.checkpoint_id, c.execution_domain_id, c.manifest_digest,
                              c.target_refs_digest, c.recovery_evidence_digest, c.status,
                              b.supervision_session_id, b.session_identity_digest,
                              b.policy_version, b.drill_fingerprint
                       FROM trusted_baseline_candidates c
                       JOIN trusted_baseline_candidate_bindings b USING (candidate_id)
                       WHERE c.candidate_id = ?""",
                    (candidate_id,),
                ).fetchone()
                if row is None or row[5] != "CANDIDATE":
                    return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_CONFIRMATION_INVALID"}
                context = self._context_from_row(row)
                evidence = self._validated_r3_evidence(transaction, context)
                if (
                    evidence is None
                    or evidence["recovery_evidence_digest"] != row[4]
                    or evidence["drill_fingerprint"] != row[9]
                    or evidence["policy_version"] != row[8]
                ):
                    return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_R3_REQUIRED"}
                authorization = transaction.execute(
                    """SELECT subject_id, supervision_session_id, session_identity_digest,
                              checkpoint_id, execution_domain_id, manifest_digest,
                              operation_kind, target_refs_digest, drill_fingerprint,
                              policy_version, binding_digest, expires_at, nonce, consumed_at
                       FROM recovery_authorizations WHERE authorization_id = ?""",
                    (authorization_id,),
                ).fetchone()
                if authorization is None or authorization[13] is not None:
                    return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_CONFIRMATION_INVALID"}
                try:
                    expired = now >= datetime.fromisoformat(authorization[11])
                except ValueError:
                    return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_CONFIRMATION_INVALID"}
                if expired:
                    return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_CONFIRMATION_EXPIRED"}
                expected = self._authorization_binding_digest(
                    authorization_id, candidate_id, row[7], context,
                    "TRUSTED_BASELINE_CONFIRM", row[9], row[8], nonce,
                )
                if authorization[:11] != (
                    candidate_id, row[6], row[7], row[0], row[1], row[2],
                    "TRUSTED_BASELINE_CONFIRM", row[3], row[9], row[8], expected,
                ) or authorization[12] != nonce:
                    return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_CONFIRMATION_INVALID"}
                session = transaction.execute(
                    """SELECT status, decision, declared_intent_digest FROM supervision_sessions
                       WHERE supervision_session_id = ?""",
                    (row[6],),
                ).fetchone()
                if session != ("APPROVED", "REVIEW", row[7]):
                    return {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_CONFIRMATION_INVALID"}
                transaction.execute(
                    """INSERT INTO trusted_baselines
                       (baseline_id, checkpoint_id, execution_domain_id, manifest_digest,
                        target_refs_digest, recovery_evidence_digest, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (baseline_id, row[0], row[1], row[2], row[3], row[4], now.isoformat()),
                )
                if transaction.execute(
                    """UPDATE trusted_baseline_candidates SET status = 'CONSUMED'
                       WHERE candidate_id = ? AND status = 'CANDIDATE'""",
                    (candidate_id,),
                ).rowcount != 1:
                    raise RuntimeError("TRUSTED_BASELINE_CONSUME_FAILED")
                if transaction.execute(
                    """UPDATE recovery_authorizations
                       SET consumed_at = ?, consumed_by_ref = ?
                       WHERE authorization_id = ? AND nonce = ? AND consumed_at IS NULL
                         AND expires_at > ?""",
                    (now.isoformat(), baseline_id, authorization_id, nonce, now.isoformat()),
                ).rowcount != 1:
                    raise RuntimeError("RECOVERY_AUTHORIZATION_CONSUME_FAILED")
                self._append_baseline_event(
                    transaction, EventType.TRUSTED_BASELINE_CREATED, baseline_id,
                    "TRUSTED", context, row[4], row[6], row[8], row[9],
                    now.isoformat(), candidate_id, authorization_id,
                )
        except (sqlite3.DatabaseError, RuntimeError, ValueError):
            return {"status": "FAILED", "reason_code": "RECOVERY_PERSISTENCE_FAILED"}
        return {"baseline_id": baseline_id, "status": "TRUSTED", "recovery_evidence_digest": row[4]}

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
                self._append_baseline_event(
                    connection, EventType.TRUSTED_BASELINE_RETIRED, baseline_id, "RETIRED",
                    context, reason_code, "retired", "P4-LOCAL-1", "retired",
                )
        except (sqlite3.DatabaseError, RuntimeError, ValueError):
            return {"status": "FAILED", "reason_code": "RECOVERY_PERSISTENCE_FAILED"}
        return {"baseline_id": baseline_id, "status": "RETIRED"}

    @staticmethod
    def _context_from_row(row: tuple[object, ...]) -> dict[str, str]:
        return {
            "checkpoint_id": str(row[0]),
            "execution_domain_id": str(row[1]),
            "manifest_digest": str(row[2]),
            "target_refs_digest": str(row[3]),
        }

    @staticmethod
    def _baseline_binding_digest(
        candidate_id: str,
        context: dict[str, str],
        evidence: dict[str, str],
    ) -> str:
        return hashlib.sha256(canonical_json({
            "operation": "TRUSTED_BASELINE_CONFIRM",
            "candidate_id": candidate_id,
            **context,
            "recovery_evidence_digest": evidence["recovery_evidence_digest"],
            "drill_fingerprint": evidence["drill_fingerprint"],
            "policy_version": evidence["policy_version"],
        }).encode()).hexdigest()

    @staticmethod
    def _authorization_binding_digest(
        authorization_id: str,
        subject_id: str,
        session_identity_digest: str,
        context: dict[str, str],
        operation_kind: str,
        drill_fingerprint: str,
        policy_version: str,
        nonce: str,
    ) -> str:
        return hashlib.sha256(canonical_json({
            "authorization_id": authorization_id,
            "subject_id": subject_id,
            "session_identity_digest": session_identity_digest,
            **context,
            "operation_kind": operation_kind,
            "drill_fingerprint": drill_fingerprint,
            "policy_version": policy_version,
            "nonce": nonce,
        }).encode()).hexdigest()

    def _validated_r3_evidence(
        self,
        connection,
        context: dict[str, str],
        *,
        drill_id: str | None = None,
    ) -> dict[str, str] | None:
        from agentguard.evidence.ledger import verify_ledger

        if verify_ledger(connection):
            return None
        drills = connection.execute(
            """SELECT drill_id, binding_digest FROM recovery_drills
               WHERE checkpoint_id = ? AND execution_domain_id = ?
                 AND manifest_digest = ? AND target_refs_digest = ?
                 AND (? IS NULL OR drill_id = ?)
                 AND status = 'VERIFIED_R3'
               ORDER BY created_at, drill_id""",
            (
                context["checkpoint_id"], context["execution_domain_id"],
                context["manifest_digest"], context["target_refs_digest"],
                drill_id, drill_id,
            ),
        ).fetchall()
        if not drills:
            return None
        drill_id, binding_digest = drills[0]
        expected_results = {
            "RECOVERY_DRILL_PREPARED": "AWAITING_APPROVAL",
            "RECOVERY_DRILL_APPROVED": "APPROVED",
            "RECOVERY_DRILL_STARTED": "RUNNING",
            "DRIFT_ESTABLISHED": "AVAILABLE",
            "FILE_RESTORED": "AVAILABLE",
            "VALIDATOR_PASSED": "AVAILABLE",
            "RECOVERY_DRILL_VERIFIED": "VERIFIED_R3",
            "RECOVERY_DRILL_COMPLETED": "VERIFIED_R3",
        }
        binding = connection.execute(
            """SELECT b.supervision_session_id, b.session_identity_digest, b.policy_version,
                      b.binding_digest, s.status, s.decision, s.declared_intent_digest
               FROM recovery_drill_bindings b
               JOIN supervision_sessions s USING (supervision_session_id)
               WHERE b.drill_id = ?""",
            (drill_id,),
        ).fetchone()
        if (
            binding is None
            or binding[3] != binding_digest
            or binding[4] != "APPROVED"
            or binding[5] != "REVIEW"
            or binding[1] != binding[6]
            or binding[2] != "P4-LOCAL-1"
        ):
            return None
        adverse = connection.execute(
            """SELECT 1 FROM evidence_ledger_events
               WHERE checkpoint_id = ? AND execution_domain_id = ?
                 AND event_type IN ('SCOPE_DRIFT', 'EXTERNAL_EFFECT_UNKNOWN', 'RESTORE_FAILED')
               LIMIT 1""",
            (context["checkpoint_id"], context["execution_domain_id"]),
        ).fetchone()
        if adverse is not None:
            return None
        events = connection.execute(
            """SELECT event_id, event_type, result, supervision_session_id, payload_safe_json
               FROM evidence_ledger_events WHERE checkpoint_id = ?
                 AND execution_domain_id = ? AND subject_ref = ?
               ORDER BY sequence""",
            (
                context["checkpoint_id"], context["execution_domain_id"],
                f"manifest:{context['manifest_digest']}",
            ),
        ).fetchall()
        required = set(expected_results)
        seen: set[str] = set()
        refs: list[str] = []
        for event_id, event_type, result, supervision_session_id, payload_json in events:
            try:
                payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                return None
            if payload.get("drill_id") != drill_id:
                continue
            if (
                payload.get("binding_digest") != binding_digest
                or payload.get("manifest_digest") != context["manifest_digest"]
                or payload.get("target_refs_digest") != context["target_refs_digest"]
            ):
                return None
            if event_type in required and supervision_session_id != binding[0]:
                return None
            if event_type in {"RESTORE_FAILED", "SCOPE_DRIFT", "EXTERNAL_EFFECT_UNKNOWN"}:
                return None
            if event_type in required:
                if event_type in seen or result != expected_results[event_type]:
                    return None
                seen.add(event_type)
                refs.append(event_id)
        if seen != required:
            return None
        return {
            "recovery_evidence_digest": hashlib.sha256(
                canonical_json(sorted(refs)).encode()
            ).hexdigest(),
            "drill_fingerprint": hashlib.sha256(canonical_json({
                "drill_id": drill_id, "binding_digest": binding_digest,
            }).encode()).hexdigest(),
            "policy_version": "P4-LOCAL-1",
        }

    def _has_r3_evidence(self, connection, context: dict[str, str]) -> bool:
        return self._validated_r3_evidence(connection, context) is not None

    def _append_baseline_event(
        self, connection, event_type: EventType, baseline_id: str, result: str,
        context: dict[str, str], evidence_digest: str, supervision_session_id: str,
        policy_version: str, drill_fingerprint: str, created_at: str | None = None,
        candidate_id: str | None = None, authorization_id: str | None = None,
    ) -> None:
        self._ledger.append(
            connection,
            EvidenceEvent(
                schema_version=1, event_id=f"trusted-baseline-{uuid4()}",
                recorded_at=datetime.now(UTC), observed_at=None,
                event_family=EventFamily.RECOVERY, event_type=event_type,
                source="trusted-baseline-service", result=result,
                execution_domain_id=context["execution_domain_id"],
                supervision_session_id=supervision_session_id,
                transaction_id=None, checkpoint_id=context["checkpoint_id"],
                subject_ref=f"baseline:{baseline_id}", evidence_refs=(),
                payload_safe={
                    "baseline_id": baseline_id,
                    "candidate_id": candidate_id,
                    "authorization_id": authorization_id,
                    "manifest_digest": context["manifest_digest"],
                    "target_refs_digest": context["target_refs_digest"],
                    "recovery_evidence_digest": evidence_digest,
                    "baseline_binding_digest": hashlib.sha256(canonical_json({
                        "baseline_id": baseline_id,
                        "checkpoint_id": context["checkpoint_id"],
                        "execution_domain_id": context["execution_domain_id"],
                        "manifest_digest": context["manifest_digest"],
                        "target_refs_digest": context["target_refs_digest"],
                        "recovery_evidence_digest": evidence_digest,
                        "created_at": created_at,
                    }).encode()).hexdigest() if created_at is not None else None,
                    "created_at": created_at,
                    "policy_version": policy_version,
                    "drill_fingerprint": drill_fingerprint,
                },
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
