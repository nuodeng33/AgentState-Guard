"""Thin product recovery composition over the canonical RecoveryService."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from agentguard.discovery.domains import SelfRuntimeAdapter
from agentguard.recovery.contracts import RecoveryOperation, RecoveryRequest
from agentguard.recovery.policy import RestorePolicy
from agentguard.recovery.service import RecoveryService
from agentguard.recovery.workspace_adapter import HostWorkspaceRecoveryAdapter
from agentguard.recovery.workspace_permissions import (
    PermissionCapabilityError,
    current_user_permission_backend,
)
from agentguard.recovery.workspace_scope import (
    DurableWorkspaceScope,
    WorkspaceScopeError,
    WorkspaceScopeService,
)
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore

from .r4_controlled_change import ControlledChangeError, _production_snapshot

SCHEMA_VERSION = "product-recovery-action-1"


class ProductRecoveryError(RuntimeError):
    def __init__(self, reason_code: str, status_code: int = 409) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code


@dataclass(frozen=True)
class _RecoveryContext:
    scope_kind: str
    domain_id: str
    target: Path | None
    adapter: object
    workspace: DurableWorkspaceScope | None = None


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
    context = _recovery_context(
        database,
        snapshots,
        product_target=target,
        operation=operation,
        checkpoint_id=checkpoint_id,
        discovery_service=discovery_service,
    )
    service = RecoveryService(
        database=database,
        snapshots=snapshots,
        adapters={context.domain_id: context.adapter},
    )
    request = RecoveryRequest(
        operation=operation,
        execution_domain_id=context.domain_id,
        target_path=context.target
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
        "scope_kind": result.details.get("scope_kind", context.scope_kind),
        "workspace_id": result.details.get(
            "workspace_id",
            context.workspace.workspace_id if context.workspace is not None else None,
        ),
        "coverage": result.details.get("coverage"),
        "coverage_reason_counts": result.details.get("coverage_reason_counts"),
        "scan_complete": result.details.get("scan_complete"),
        "scan_reason_code": result.details.get("scan_reason_code"),
        "post_restore_status": result.details.get("post_restore_status"),
        "quarantined_targets": result.details.get("quarantined_targets"),
        "residue_targets": result.details.get("residue_targets"),
        "evidence_refs": _checkpoint_evidence(database, result.checkpoint_id),
    }


def _recovery_context(
    database: StateDB,
    snapshots: SnapshotStore,
    *,
    product_target: Path,
    operation: RecoveryOperation,
    checkpoint_id: str | None,
    discovery_service,
) -> _RecoveryContext:
    scope_service = WorkspaceScopeService(database)
    try:
        active = scope_service.active_scope()
        latest = scope_service.latest_result()
    except WorkspaceScopeError as exc:
        raise ProductRecoveryError("WORKSPACE_SCOPE_BINDING_INVALID") from exc

    workspace_artifact = None
    if operation is not RecoveryOperation.SNAPSHOT and checkpoint_id is not None:
        checkpoint = (
            database.get_checkpoint(int(checkpoint_id))
            if checkpoint_id.isdecimal()
            else None
        )
        if checkpoint is not None:
            workspace_artifact = snapshots.load_recovery_v3(
                checkpoint["snapshot_path"]
            )
    artifact_workspace = (
        workspace_artifact.get("workspace")
        if isinstance(workspace_artifact, dict)
        else None
    )
    wants_workspace = operation is RecoveryOperation.SNAPSHOT and latest is not None
    wants_workspace = wants_workspace or isinstance(artifact_workspace, dict)

    if wants_workspace:
        if operation is RecoveryOperation.SNAPSHOT and latest is not None:
            if latest.status == "UNAVAILABLE":
                raise ProductRecoveryError(latest.reason_code)
            if latest.status == "NOT_OBSERVED":
                return _product_config_context(product_target, discovery_service)
        if active is None:
            raise ProductRecoveryError("WORKSPACE_SCOPE_BINDING_INVALID")
        if isinstance(artifact_workspace, dict) and (
            artifact_workspace.get("workspace_id") != active.workspace_id
            or artifact_workspace.get("execution_domain_id")
            != active.execution_domain_id
            or artifact_workspace.get("root_digest") != active.root_digest
        ):
            raise ProductRecoveryError("WORKSPACE_SCOPE_BINDING_INVALID")
        try:
            backend = current_user_permission_backend()
        except PermissionCapabilityError as exc:
            raise ProductRecoveryError(exc.reason_code, 503) from exc
        return _RecoveryContext(
            scope_kind="HOST_WORKSPACE",
            domain_id=active.execution_domain_id,
            target=None,
            adapter=HostWorkspaceRecoveryAdapter(
                scope=active,
                permission_backend=backend,
                quarantine_root=snapshots.snapshot_dir.parent / "workspace-quarantine",
            ),
            workspace=active,
        )
    return _product_config_context(product_target, discovery_service)


def _product_config_context(product_target: Path, discovery_service) -> _RecoveryContext:
    try:
        _snapshot, domain_id = _production_snapshot(discovery_service)
    except ControlledChangeError as error:
        raise ProductRecoveryError("RECOVERY_DISCOVERY_UNAVAILABLE", 503) from error
    policy = RestorePolicy(
        approved_paths={domain_id: (product_target,)},
        validators={domain_id: "toml-parse"},
    )
    return _RecoveryContext(
        scope_kind="PRODUCT_CONFIG",
        domain_id=domain_id,
        target=product_target,
        adapter=SelfRuntimeAdapter(recovery_policy=policy),
    )


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
