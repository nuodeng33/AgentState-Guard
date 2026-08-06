"""Server-computed P6 recovery coverage facts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from enum import Enum

from agentguard.evidence.ledger import verify_ledger
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore

from .manifest import validate_snapshot_v3


class RecoveryCoverageStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INSUFFICIENT = "INSUFFICIENT"
    MISSING = "MISSING"
    UNREACHABLE = "UNREACHABLE"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"


@dataclass(frozen=True)
class RecoveryCoverageFacts:
    """Safe, authoritative coverage dimensions; P6 never claims test restore."""

    status: RecoveryCoverageStatus
    checkpoint_id: str | None
    requested_targets: int
    authorized_snapshot_targets: int
    intact_manifest_blob_targets: int
    test_restore_verified_targets: int | None
    test_restore_status: str
    reason_code: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(sorted(set(self.evidence_refs))))
        if (
            self.requested_targets < 0
            or self.authorized_snapshot_targets < 0
            or self.intact_manifest_blob_targets < 0
            or self.authorized_snapshot_targets > self.requested_targets
            or self.intact_manifest_blob_targets > self.requested_targets
            or self.test_restore_verified_targets is not None
            or self.test_restore_status != "NOT_RUN_P6"
        ):
            raise ValueError("RECOVERY_COVERAGE_FACTS_INVALID")

    @property
    def authorized_snapshot_coverage(self) -> float | None:
        return self._ratio(self.authorized_snapshot_targets)

    @property
    def manifest_blob_coverage(self) -> float | None:
        return self._ratio(self.intact_manifest_blob_targets)

    def _ratio(self, covered: int) -> float | None:
        if self.requested_targets == 0:
            return None
        return round(covered / self.requested_targets, 6)

    def safe_summary(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "reason_code": self.reason_code,
            "requested_targets": self.requested_targets,
            "authorized_snapshot_targets": self.authorized_snapshot_targets,
            "intact_manifest_blob_targets": self.intact_manifest_blob_targets,
            "authorized_snapshot_coverage": self.authorized_snapshot_coverage,
            "manifest_blob_coverage": self.manifest_blob_coverage,
            "test_restore_verified_targets": None,
            "test_restore_status": "NOT_RUN_P6",
        }


class RecoveryCoverageService:
    """Derive coverage only from StateDB, Snapshot V3, and the verified ledger."""

    def __init__(self, database: StateDB, snapshots: SnapshotStore) -> None:
        self._database = database
        self._snapshots = snapshots

    def compute(
        self,
        *,
        checkpoint_id: str | None,
        target_refs: tuple[str, ...],
        execution_domain_id: str | None,
    ) -> RecoveryCoverageFacts:
        connection = self._database._conn
        if connection is None:
            return self._facts(
                RecoveryCoverageStatus.UNREACHABLE,
                checkpoint_id,
                target_refs,
                reason_code="RECOVERY_DATABASE_UNREACHABLE",
            )
        try:
            return self.compute_with_connection(
                connection,
                checkpoint_id=checkpoint_id,
                target_refs=target_refs,
                execution_domain_id=execution_domain_id,
            )
        except (OSError, sqlite3.DatabaseError, TypeError, ValueError):
            return self._facts(
                RecoveryCoverageStatus.EVIDENCE_INSUFFICIENT,
                checkpoint_id,
                tuple(sorted(set(target_refs))),
                reason_code="RECOVERY_FACTS_UNAVAILABLE",
            )

    def compute_with_connection(
        self,
        connection: sqlite3.Connection,
        *,
        checkpoint_id: str | None,
        target_refs: tuple[str, ...],
        execution_domain_id: str | None,
    ) -> RecoveryCoverageFacts:
        requested = tuple(sorted(set(target_refs)))
        if checkpoint_id is None or not checkpoint_id.isdecimal() or not execution_domain_id:
            return self._facts(
                RecoveryCoverageStatus.MISSING,
                checkpoint_id,
                requested,
                reason_code="RECOVERY_CHECKPOINT_MISSING",
            )
        checkpoint = self._database.get_checkpoint(int(checkpoint_id))
        if checkpoint is None:
            return self._facts(
                RecoveryCoverageStatus.MISSING,
                checkpoint_id,
                requested,
                reason_code="RECOVERY_CHECKPOINT_MISSING",
            )
        if verify_ledger(connection):
            return self._facts(
                RecoveryCoverageStatus.EVIDENCE_INSUFFICIENT,
                checkpoint_id,
                requested,
                reason_code="RECOVERY_LEDGER_INVALID",
            )

        evidence_refs, authorized_hashes = self._snapshot_evidence(
            connection,
            checkpoint_id,
            execution_domain_id,
            checkpoint["hash_sha256"],
        )
        if not evidence_refs:
            return self._facts(
                RecoveryCoverageStatus.EVIDENCE_INSUFFICIENT,
                checkpoint_id,
                requested,
                reason_code="RECOVERY_LEDGER_EVIDENCE_INSUFFICIENT",
            )
        requested_hashes = {self._target_digest(value) for value in requested}
        authorized_count = len(requested_hashes & authorized_hashes)

        artifact, load_reason = self._snapshots.load_recovery_v3_with_status(
            checkpoint["snapshot_path"]
        )
        if artifact is None:
            status = (
                RecoveryCoverageStatus.UNREACHABLE
                if load_reason
                in {"RECOVERY_ARTIFACT_NOT_FOUND", "RECOVERY_DOMAIN_UNREACHABLE"}
                else RecoveryCoverageStatus.EVIDENCE_INSUFFICIENT
            )
            return self._facts(
                status,
                checkpoint_id,
                requested,
                authorized=authorized_count,
                reason_code=load_reason,
                evidence_refs=evidence_refs,
            )
        valid, reason_code, digest = validate_snapshot_v3(
            artifact,
            expected_domain=execution_domain_id,
        )
        if not valid or digest != checkpoint["hash_sha256"]:
            return self._facts(
                RecoveryCoverageStatus.EVIDENCE_INSUFFICIENT,
                checkpoint_id,
                requested,
                authorized=authorized_count,
                reason_code=(
                    reason_code if not valid else "RECOVERY_MANIFEST_DIGEST_MISMATCH"
                ),
                evidence_refs=evidence_refs,
            )
        intact_hashes = {
            self._target_digest(entry["logical_path"])
            for entry in artifact["manifest"]
            if entry["classification"] == "restorable"
        }
        if intact_hashes != authorized_hashes:
            return self._facts(
                RecoveryCoverageStatus.EVIDENCE_INSUFFICIENT,
                checkpoint_id,
                requested,
                authorized=authorized_count,
                reason_code="RECOVERY_LEDGER_MANIFEST_MISMATCH",
                evidence_refs=evidence_refs,
            )
        intact_count = len(requested_hashes & intact_hashes)
        status = (
            RecoveryCoverageStatus.COMPLETE
            if requested
            and authorized_count == len(requested)
            and intact_count == len(requested)
            else RecoveryCoverageStatus.INSUFFICIENT
        )
        return self._facts(
            status,
            checkpoint_id,
            requested,
            authorized=authorized_count,
            intact=intact_count,
            reason_code=(
                "RECOVERY_COVERAGE_COMPLETE"
                if status is RecoveryCoverageStatus.COMPLETE
                else "RECOVERY_COVERAGE_INSUFFICIENT"
            ),
            evidence_refs=evidence_refs,
        )

    @staticmethod
    def _snapshot_evidence(
        connection: sqlite3.Connection,
        checkpoint_id: str,
        execution_domain_id: str,
        manifest_digest: str,
    ) -> tuple[tuple[str, ...], set[str]]:
        rows = connection.execute(
            """SELECT event_id, event_type, result, execution_domain_id,
                      subject_ref, payload_safe_json
               FROM evidence_ledger_events
               WHERE checkpoint_id = ? AND event_type IN ('CHECKPOINT_CREATED', 'MANIFEST_VERIFIED')
               ORDER BY sequence""",
            (checkpoint_id,),
        ).fetchall()
        matched_types: set[str] = set()
        refs: list[str] = []
        target_hashes: set[str] | None = None
        for event_id, event_type, result, domain, subject_ref, payload_json in rows:
            try:
                payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                continue
            hashes = payload.get("target_ref_digests")
            if (
                result != "AVAILABLE"
                or domain != execution_domain_id
                or subject_ref != f"manifest:{manifest_digest}"
                or payload.get("manifest_digest") != manifest_digest
                or not isinstance(hashes, list)
                or not all(
                    isinstance(value, str) and len(value) == 64 for value in hashes
                )
            ):
                continue
            current_hashes = set(hashes)
            if target_hashes is not None and target_hashes != current_hashes:
                return (), set()
            target_hashes = current_hashes
            matched_types.add(event_type)
            refs.append(event_id)
        if matched_types != {"CHECKPOINT_CREATED", "MANIFEST_VERIFIED"}:
            return (), set()
        return tuple(sorted(set(refs))), target_hashes or set()

    @staticmethod
    def _target_digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _facts(
        status: RecoveryCoverageStatus,
        checkpoint_id: str | None,
        target_refs: tuple[str, ...],
        *,
        authorized: int = 0,
        intact: int = 0,
        reason_code: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> RecoveryCoverageFacts:
        return RecoveryCoverageFacts(
            status=status,
            checkpoint_id=checkpoint_id,
            requested_targets=len(target_refs),
            authorized_snapshot_targets=authorized,
            intact_manifest_blob_targets=intact,
            test_restore_verified_targets=None,
            test_restore_status="NOT_RUN_P6",
            reason_code=reason_code,
            evidence_refs=evidence_refs,
        )
