"""Checkpoint-bound, unattributed Host-native workspace change observation."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from agentguard.discovery.workspace_authority import validate_workspace_root_binding
from agentguard.evidence.canonical import canonical_json
from agentguard.evidence.ledger import EvidenceLedger, verify_ledger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore

from .manifest import validate_snapshot_v3
from .workspace_permissions import PermissionBackend, PermissionCapabilityError
from .workspace_policy import WorkspaceScanLimits, scan_workspace
from .workspace_scope import DurableWorkspaceScope


@dataclass(frozen=True)
class WorkspaceChangeObservation:
    status: str
    reason_code: str
    change_count: int = 0
    checkpoint_id: str | None = None
    evidence_refs: tuple[str, ...] = ()


class WorkspaceChangeObserver:
    """Compare one active server-owned scope with its newest valid checkpoint."""

    def __init__(
        self,
        *,
        database: StateDB,
        snapshots: SnapshotStore,
        permission_backend: PermissionBackend,
        scan_limits: WorkspaceScanLimits | None = None,
        ledger: EvidenceLedger | None = None,
    ) -> None:
        self._database = database
        self._snapshots = snapshots
        self._permission_backend = permission_backend
        self._scan_limits = scan_limits
        self._ledger = ledger or EvidenceLedger()

    def observe(
        self,
        scope: DurableWorkspaceScope,
        *,
        observed_at: datetime,
    ) -> WorkspaceChangeObservation:
        observed_at = _utc(observed_at)
        root = validate_workspace_root_binding(
            scope.root_path,
            execution_domain_id=scope.execution_domain_id,
            expected_digest=scope.root_digest,
        )
        if root is None:
            return WorkspaceChangeObservation(
                "UNREACHABLE", "WORKSPACE_SCOPE_BINDING_INVALID"
            )
        checkpoint = self._latest_workspace_checkpoint(scope)
        if checkpoint is None:
            return WorkspaceChangeObservation(
                "NOT_RUN", "WORKSPACE_CHECKPOINT_NOT_FOUND"
            )
        checkpoint_id, artifact = checkpoint
        try:
            current = scan_workspace(
                root,
                permission_backend=self._permission_backend,
                limits=self._scan_limits,
            )
        except PermissionCapabilityError as exc:
            return WorkspaceChangeObservation("UNREACHABLE", exc.reason_code)
        except PermissionError:
            return WorkspaceChangeObservation(
                "UNREACHABLE", "WORKSPACE_SCAN_PERMISSION_DENIED"
            )
        except OSError:
            return WorkspaceChangeObservation(
                "UNREACHABLE", "WORKSPACE_SCAN_UNREACHABLE"
            )
        if not current.complete:
            return WorkspaceChangeObservation("DEGRADED", current.reason_code)
        if any(entry.category == "unreachable" for entry in current.entries):
            return WorkspaceChangeObservation(
                "DEGRADED", "WORKSPACE_CHANGE_OBSERVATION_UNREACHABLE"
            )

        workspace = artifact["workspace"]
        baseline = {
            entry["relative_path"]: entry for entry in workspace["coverage"]
        }
        after = {
            entry.relative_path: entry.to_extension_dict()
            for entry in current.entries
        }
        changes = _workspace_diff(
            workspace_id=scope.workspace_id,
            checkpoint_id=checkpoint_id,
            baseline=baseline,
            current=after,
        )
        refs: list[str] = []
        try:
            with self._database.transaction() as connection:
                if verify_ledger(connection):
                    raise RuntimeError("WORKSPACE_CHANGE_LEDGER_INVALID")
                for change in changes:
                    existing = connection.execute(
                        """SELECT event_id FROM evidence_ledger_events
                           WHERE transaction_id = ? AND event_type = 'OBSERVED_CHANGE'""",
                        (change["change_id"],),
                    ).fetchone()
                    if existing is not None:
                        refs.append(existing[0])
                        continue
                    event_id = f"workspace-event-{change['change_id'].rsplit('-', 1)[-1]}"
                    self._ledger.append(
                        connection,
                        EvidenceEvent(
                            schema_version=1,
                            event_id=event_id,
                            recorded_at=datetime.now(UTC),
                            observed_at=observed_at,
                            event_family=EventFamily.CHANGE,
                            event_type=EventType.OBSERVED_CHANGE,
                            source="host-workspace-observer",
                            result=change["change_kind"],
                            execution_domain_id=scope.execution_domain_id,
                            supervision_session_id=None,
                            transaction_id=change["change_id"],
                            checkpoint_id=checkpoint_id,
                            subject_ref=scope.workspace_id,
                            evidence_refs=(scope.ledger_event_id,),
                            payload_safe={
                                "attribution": "UNATTRIBUTED",
                                "change_kind": change["change_kind"],
                                "coverage_after": change["coverage_after"],
                                "coverage_before": change["coverage_before"],
                                "reason_code": "WORKSPACE_CHANGE_OBSERVED",
                                "recovery_disposition": change[
                                    "recovery_disposition"
                                ],
                                "target_ref_digest": change["target_ref_digest"],
                                "workspace_id": scope.workspace_id,
                            },
                        ),
                    )
                    refs.append(event_id)
                if verify_ledger(connection):
                    raise RuntimeError("WORKSPACE_CHANGE_LEDGER_INVALID")
        except (sqlite3.DatabaseError, RuntimeError, ValueError):
            return WorkspaceChangeObservation(
                "ERROR", "WORKSPACE_CHANGE_PERSIST_FAILED"
            )
        return WorkspaceChangeObservation(
            "AVAILABLE",
            "WORKSPACE_CHANGES_OBSERVED" if changes else "WORKSPACE_UNCHANGED",
            change_count=len(changes),
            checkpoint_id=checkpoint_id,
            evidence_refs=tuple(refs),
        )

    def _latest_workspace_checkpoint(
        self, scope: DurableWorkspaceScope
    ) -> tuple[str, dict] | None:
        connection = self._database._conn
        if connection is None:
            return None
        rows = connection.execute(
            """SELECT id, snapshot_path, hash_sha256 FROM checkpoints
               ORDER BY id DESC"""
        ).fetchall()
        for checkpoint_id, snapshot_path, expected_digest in rows:
            artifact, _reason = self._snapshots.load_recovery_v3_with_status(
                snapshot_path
            )
            if artifact is None:
                continue
            valid, _reason, digest = validate_snapshot_v3(
                artifact,
                expected_domain=scope.execution_domain_id,
            )
            workspace = artifact.get("workspace")
            if (
                valid
                and digest == expected_digest
                and isinstance(workspace, dict)
                and workspace.get("workspace_id") == scope.workspace_id
                and workspace.get("root_digest") == scope.root_digest
                and workspace.get("execution_domain_id")
                == scope.execution_domain_id
            ):
                return str(checkpoint_id), artifact
        return None


def _workspace_diff(
    *,
    workspace_id: str,
    checkpoint_id: str,
    baseline: dict[str, dict],
    current: dict[str, dict],
) -> tuple[dict[str, str | None], ...]:
    changes: list[dict[str, str | None]] = []
    for relative in sorted(set(baseline) | set(current), key=lambda value: (value.casefold(), value)):
        before = baseline.get(relative)
        after = current.get(relative)
        if before is None:
            kind = "CREATED"
        elif after is None:
            kind = "DELETED"
        elif all(
            before.get(field) == after.get(field)
            for field in (
                "object_kind",
                "category",
                "reason_code",
                "size",
                "observation_digest",
                "permission_proof",
            )
        ):
            continue
        else:
            kind = "MODIFIED"
        before_category = before.get("category") if before is not None else None
        after_category = after.get("category") if after is not None else None
        disposition = _recovery_disposition(
            kind,
            before_category=before_category,
            after_category=after_category,
        )
        target_ref = hashlib.sha256(
            canonical_json(
                {"relative_path": relative, "workspace_id": workspace_id}
            ).encode()
        ).hexdigest()
        change_material = {
            "after": after.get("observation_digest") if after is not None else None,
            "before": before.get("observation_digest") if before is not None else None,
            "checkpoint_id": checkpoint_id,
            "kind": kind,
            "relative_path": relative,
            "workspace_id": workspace_id,
        }
        change_id = "workspace-change-" + hashlib.sha256(
            canonical_json(change_material).encode()
        ).hexdigest()[:32]
        changes.append(
            {
                "change_id": change_id,
                "change_kind": kind,
                "coverage_after": after_category,
                "coverage_before": before_category,
                "recovery_disposition": disposition,
                "target_ref_digest": target_ref,
            }
        )
    return tuple(changes)


def _recovery_disposition(
    change_kind: str,
    *,
    before_category: object,
    after_category: object,
) -> str:
    if change_kind == "CREATED":
        if after_category == "restorable":
            return "NOT_IN_CHECKPOINT"
        return str(after_category).upper()
    if before_category == "restorable":
        return "RECOVERABLE"
    return str(before_category).upper()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("WORKSPACE_CHANGE_TIME_INVALID")
    return value.astimezone(UTC)


__all__ = ["WorkspaceChangeObservation", "WorkspaceChangeObserver"]
