"""Durable, server-owned Host-native workspace protection scope."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentguard.discovery.workspace_authority import (
    ResolvedWorkspaceAuthority,
    storage_workspace_digest,
    validate_workspace_root_binding,
    workspace_root_digest,
)
from agentguard.evidence.canonical import canonical_json
from agentguard.evidence.ledger import EvidenceLedger, verify_ledger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.storage.db import StateDB


class WorkspaceScopeError(RuntimeError):
    """A durable scope could not be established without losing authority proof."""


@dataclass(frozen=True)
class WorkspaceScopeResult:
    observation_id: str
    status: str
    reason_code: str
    workspace_id: str | None = None
    root_digest: str | None = None
    ledger_event_id: str | None = None


@dataclass(frozen=True)
class DurableWorkspaceScope:
    observation_id: str
    discovery_snapshot_id: str
    observed_at: datetime
    recorded_at: datetime
    workspace_id: str
    execution_domain_id: str
    root_path: Path | None = field(repr=False)
    root_digest: str = field(repr=False)
    storage_kind: str = "HOST_PATH"
    storage_resource_identity: str | None = None
    storage_locator: str | None = field(default=None, repr=False)
    logical_root: str | None = None
    durability: str = "DURABLE"
    current_reachability: str = "AVAILABLE"
    protection_capability: str = "SUPPORTED"
    protection_reason_code: str = "STORAGE_BACKEND_SUPPORTED"
    agent_mutation_capability: str = "UNKNOWN"
    agent_ids: tuple[str, ...] = ()
    process_instance_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    ledger_event_id: str = ""


@dataclass(frozen=True)
class WorkspaceAuthorityResult:
    """Current fail-closed action authority for one durable workspace."""

    workspace_id: str | None
    authority_state: str
    authority_observation_ref: str | None
    execution_domain_id: str | None
    root_digest: str | None
    observed_at: datetime | None
    freshness: str
    reason_code: str
    evidence_refs: tuple[str, ...] = ()
    scope: DurableWorkspaceScope | None = field(default=None, repr=False)
    storage_kind: str | None = None
    storage_resource_identity: str | None = None
    logical_root: str | None = None
    durability: str | None = None
    current_reachability: str | None = None
    protection_capability: str | None = None
    protection_reason_code: str | None = None
    agent_mutation_capability: str | None = None

    def safe_summary(self) -> dict[str, object]:
        return {
            "status": self.authority_state,
            "authority_state": self.authority_state,
            "workspace_id": self.workspace_id,
            "authority_observation_ref": self.authority_observation_ref,
            "execution_domain_id": self.execution_domain_id,
            "root_digest": self.root_digest,
            "observed_at": (
                self.observed_at.isoformat() if self.observed_at is not None else None
            ),
            "freshness": self.freshness,
            "reason_code": self.reason_code,
            "evidence_refs": list(self.evidence_refs),
            "storage_kind": self.storage_kind,
            "storage_resource_identity": self.storage_resource_identity,
            "logical_root": self.logical_root,
            "durability": self.durability,
            "current_reachability": self.current_reachability,
            "protection_capability": self.protection_capability,
            "protection_reason_code": self.protection_reason_code,
            "agent_mutation_capability": self.agent_mutation_capability,
        }


class WorkspaceScopeService:
    def __init__(self, database: StateDB) -> None:
        self._database = database

    def bind(
        self,
        resolved: ResolvedWorkspaceAuthority,
        *,
        recorded_at: datetime,
        discovery_snapshot_id: str,
    ) -> WorkspaceScopeResult:
        recorded_at = _utc(recorded_at)
        _validate_resolved(resolved)
        observation_id = f"workspace-observation-{uuid4().hex[:24]}"
        event_id = f"workspace-scope-{hashlib.sha256(observation_id.encode()).hexdigest()[:24]}"
        bound = resolved.status == "BOUND"
        reason_code = (
            "WORKSPACE_PROTECTION_BOUND" if bound else resolved.reason_code
        )
        root_path = (
            str(resolved.root_path)
            if bound and resolved.root_path is not None
            else None
        )
        payload = {
            "agent_ids": list(resolved.agent_ids),
            "discovery_snapshot_id": discovery_snapshot_id,
            "fact_type": (
                "workspace.protection.binding"
                if bound
                else "workspace.protection.unavailable"
            ),
            "observation_id": observation_id,
            "process_instance_ids": list(resolved.process_instance_ids),
            "reason_code": reason_code,
            "root_digest": resolved.root_digest if bound else None,
            "storage_kind": resolved.storage_kind if bound else None,
            "storage_resource_identity": (
                resolved.storage_resource_identity if bound else None
            ),
            "logical_root": resolved.logical_root if bound else None,
            "durability": resolved.durability if bound else None,
            "current_reachability": resolved.current_reachability if bound else None,
            "protection_capability": resolved.protection_capability if bound else None,
            "protection_reason_code": resolved.protection_reason_code if bound else None,
            "agent_mutation_capability": (
                resolved.agent_mutation_capability if bound else None
            ),
            "status": resolved.status,
            "workspace_id": resolved.workspace_id if bound else None,
        }
        event = EvidenceEvent(
            schema_version=1,
            event_id=event_id,
            recorded_at=recorded_at,
            observed_at=recorded_at,
            event_family=EventFamily.DISCOVERY,
            event_type=(
                EventType.WORKSPACE_PROTECTION_BOUND
                if bound
                else EventType.PROBE_UNREACHABLE
            ),
            source="host-workspace-authority",
            result="available" if bound else "unavailable",
            execution_domain_id=(resolved.execution_domain_id if bound else None),
            supervision_session_id=None,
            transaction_id=None,
            checkpoint_id=None,
            subject_ref=resolved.workspace_id if bound else observation_id,
            evidence_refs=resolved.evidence_refs,
            payload_safe=payload,
        )

        try:
            with self._database.transaction() as connection:
                if verify_ledger(connection):
                    raise WorkspaceScopeError("WORKSPACE_SCOPE_LEDGER_INVALID")
                connection.execute(
                    """INSERT INTO workspace_scope_observations (
                           observation_id, discovery_snapshot_id, observed_at, recorded_at,
                           status, reason_code, workspace_id, execution_domain_id,
                           root_path, root_digest, storage_kind,
                           storage_resource_identity, storage_locator, logical_root,
                           durability, current_reachability, protection_capability,
                           protection_reason_code, agent_mutation_capability, agent_ids_json,
                           process_instance_ids_json, evidence_refs_json, ledger_event_id
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        observation_id,
                        discovery_snapshot_id,
                        recorded_at.isoformat(),
                        recorded_at.isoformat(),
                        resolved.status,
                        reason_code,
                        resolved.workspace_id if bound else None,
                        resolved.execution_domain_id if bound else None,
                        root_path,
                        resolved.root_digest if bound else None,
                        resolved.storage_kind if bound else None,
                        resolved.storage_resource_identity if bound else None,
                        resolved.storage_locator if bound else None,
                        resolved.logical_root if bound else None,
                        resolved.durability if bound else None,
                        resolved.current_reachability if bound else None,
                        resolved.protection_capability if bound else None,
                        resolved.protection_reason_code if bound else None,
                        resolved.agent_mutation_capability if bound else None,
                        canonical_json(list(resolved.agent_ids)),
                        canonical_json(list(resolved.process_instance_ids)),
                        canonical_json(list(resolved.evidence_refs)),
                        event_id,
                    ),
                )
                EvidenceLedger().append(connection, event)
                if verify_ledger(connection):
                    raise WorkspaceScopeError("WORKSPACE_SCOPE_LEDGER_INVALID")
        except WorkspaceScopeError:
            raise
        except Exception as exc:
            raise WorkspaceScopeError("WORKSPACE_SCOPE_PERSIST_FAILED") from exc
        return WorkspaceScopeResult(
            observation_id=observation_id,
            status=resolved.status,
            reason_code=reason_code,
            workspace_id=resolved.workspace_id if bound else None,
            root_digest=resolved.root_digest if bound else None,
            ledger_event_id=event_id,
        )

    def active_scope(self) -> DurableWorkspaceScope | None:
        """Compatibility view of the database-last observation.

        Action consumers must use ``resolve_authority`` instead: the database-last
        observation is not a system-wide active workspace authority.
        """

        connection = self._database._conn
        if connection is None:
            raise WorkspaceScopeError("WORKSPACE_SCOPE_DATABASE_CLOSED")
        if verify_ledger(connection):
            raise WorkspaceScopeError("WORKSPACE_SCOPE_LEDGER_INVALID")
        row = connection.execute(
            """SELECT observation_id, discovery_snapshot_id, observed_at, recorded_at,
                      status, workspace_id, execution_domain_id, root_path, root_digest,
                      storage_kind, storage_resource_identity, storage_locator, logical_root,
                      durability, current_reachability, protection_capability,
                      protection_reason_code, agent_mutation_capability,
                      agent_ids_json, process_instance_ids_json, evidence_refs_json,
                      ledger_event_id
               FROM workspace_scope_observations
               ORDER BY observation_sequence DESC LIMIT 1"""
        ).fetchone()
        if row is None or row[4] != "BOUND":
            return None
        return _scope_from_row(connection, row)

    def resolve_authority(self, workspace_id: str | None) -> WorkspaceAuthorityResult:
        """Resolve the latest valid durable authority for one workspace."""

        connection = self._database._conn
        if connection is None:
            raise WorkspaceScopeError("WORKSPACE_SCOPE_DATABASE_CLOSED")
        if verify_ledger(connection):
            raise WorkspaceScopeError("WORKSPACE_SCOPE_LEDGER_INVALID")
        if not isinstance(workspace_id, str) or not workspace_id:
            return _unknown_authority(
                workspace_id,
                "WORKSPACE_PROTECTION_AUTHORITY_MISSING",
            )
        rows = connection.execute(
            """SELECT observation_id, discovery_snapshot_id, observed_at, recorded_at,
                      status, workspace_id, execution_domain_id, root_path, root_digest,
                      storage_kind, storage_resource_identity, storage_locator, logical_root,
                      durability, current_reachability, protection_capability,
                      protection_reason_code, agent_mutation_capability,
                      agent_ids_json, process_instance_ids_json, evidence_refs_json,
                      ledger_event_id
               FROM workspace_scope_observations
               WHERE status = 'BOUND' AND workspace_id = ?
               ORDER BY observation_sequence DESC""",
            (workspace_id,),
        ).fetchall()
        for row in rows:
            scope = _scope_from_row(connection, row)
            if scope is not None:
                refs = tuple(
                    dict.fromkeys((*scope.evidence_refs, scope.ledger_event_id))
                )
                return WorkspaceAuthorityResult(
                    workspace_id=scope.workspace_id,
                    authority_state="BOUND",
                    authority_observation_ref=scope.observation_id,
                    execution_domain_id=scope.execution_domain_id,
                    root_digest=scope.root_digest,
                    observed_at=scope.observed_at,
                    freshness="CURRENT",
                    reason_code="WORKSPACE_PROTECTION_BOUND",
                    evidence_refs=refs,
                    scope=scope,
                    storage_kind=scope.storage_kind,
                    storage_resource_identity=scope.storage_resource_identity,
                    logical_root=scope.logical_root,
                    durability=scope.durability,
                    current_reachability=scope.current_reachability,
                    protection_capability=scope.protection_capability,
                    protection_reason_code=scope.protection_reason_code,
                    agent_mutation_capability=scope.agent_mutation_capability,
                )
        return _unknown_authority(
            workspace_id,
            (
                "WORKSPACE_SCOPE_BINDING_INVALID"
                if rows
                else "WORKSPACE_PROTECTION_AUTHORITY_MISSING"
            ),
        )

    def authority_results(self) -> tuple[WorkspaceAuthorityResult, ...]:
        """Return independent current results for every durable workspace history."""

        connection = self._database._conn
        if connection is None:
            raise WorkspaceScopeError("WORKSPACE_SCOPE_DATABASE_CLOSED")
        if verify_ledger(connection):
            raise WorkspaceScopeError("WORKSPACE_SCOPE_LEDGER_INVALID")
        rows = connection.execute(
            """SELECT workspace_id, MAX(observation_sequence)
               FROM workspace_scope_observations
               WHERE status = 'BOUND' AND workspace_id IS NOT NULL
               GROUP BY workspace_id ORDER BY workspace_id"""
        ).fetchall()
        return tuple(self.resolve_authority(row[0]) for row in rows)

    def latest_result(self) -> WorkspaceScopeResult | None:
        """Return the latest observation for diagnostics, never action authority."""

        connection = self._database._conn
        if connection is None:
            raise WorkspaceScopeError("WORKSPACE_SCOPE_DATABASE_CLOSED")
        if verify_ledger(connection):
            raise WorkspaceScopeError("WORKSPACE_SCOPE_LEDGER_INVALID")
        row = connection.execute(
            """SELECT observation_id, status, reason_code, workspace_id,
                      root_digest, ledger_event_id
               FROM workspace_scope_observations
               ORDER BY observation_sequence DESC LIMIT 1"""
        ).fetchone()
        if row is None:
            return None
        return WorkspaceScopeResult(
            observation_id=row[0],
            status=row[1],
            reason_code=row[2],
            workspace_id=row[3],
            root_digest=row[4],
            ledger_event_id=row[5],
        )


def _scope_from_row(
    connection,
    row,
) -> DurableWorkspaceScope | None:
    if row is None or row[4] != "BOUND":
        return None
    try:
        agent_ids = _json_string_tuple(row[18])
        process_ids = _json_string_tuple(row[19])
        evidence_refs = _json_string_tuple(row[20])
        observed_at = _utc(datetime.fromisoformat(row[2]))
        recorded_at = _utc(datetime.fromisoformat(row[3]))
        storage_kind = row[9]
        if storage_kind in {"HOST_PATH", "DOCKER_BIND"}:
            root = validate_workspace_root_binding(
                Path(row[7]),
                execution_domain_id=row[6],
                expected_digest=row[8],
            )
            if root is None:
                return None
        else:
            root = None
            if (
                not isinstance(row[10], str)
                or not isinstance(row[12], str)
                or storage_workspace_digest(row[10], row[12], row[6]) != row[8]
            ):
                return None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    event = connection.execute(
        """SELECT event_type, result, execution_domain_id, subject_ref,
                  payload_safe_json
           FROM evidence_ledger_events WHERE event_id = ?""",
        (row[21],),
    ).fetchone()
    if event is None:
        return None
    try:
        payload = json.loads(event[4])
    except (TypeError, json.JSONDecodeError):
        return None
    if (
        event[0] != EventType.WORKSPACE_PROTECTION_BOUND.value
        or event[1] != "available"
        or event[2] != row[6]
        or event[3] != row[5]
        or not isinstance(payload, dict)
        or payload.get("observation_id") != row[0]
        or payload.get("workspace_id") != row[5]
        or payload.get("root_digest") != row[8]
        or payload.get("storage_kind") != row[9]
        or payload.get("storage_resource_identity") != row[10]
        or payload.get("logical_root") != row[12]
        or payload.get("durability") != row[13]
        or payload.get("current_reachability") != row[14]
        or payload.get("protection_capability") != row[15]
        or payload.get("protection_reason_code") != row[16]
        or payload.get("agent_mutation_capability") != row[17]
        or payload.get("agent_ids") != list(agent_ids)
        or payload.get("process_instance_ids") != list(process_ids)
    ):
        return None
    return DurableWorkspaceScope(
        observation_id=row[0],
        discovery_snapshot_id=row[1],
        observed_at=observed_at,
        recorded_at=recorded_at,
        workspace_id=row[5],
        execution_domain_id=row[6],
        root_path=root,
        root_digest=row[8],
        storage_kind=row[9],
        storage_resource_identity=row[10],
        storage_locator=row[11],
        logical_root=row[12],
        durability=row[13],
        current_reachability=row[14],
        protection_capability=row[15],
        protection_reason_code=row[16],
        agent_mutation_capability=row[17],
        agent_ids=agent_ids,
        process_instance_ids=process_ids,
        evidence_refs=evidence_refs,
        ledger_event_id=row[21],
    )


def _unknown_authority(
    workspace_id: str | None,
    reason_code: str,
) -> WorkspaceAuthorityResult:
    return WorkspaceAuthorityResult(
        workspace_id=workspace_id,
        authority_state="UNKNOWN",
        authority_observation_ref=None,
        execution_domain_id=None,
        root_digest=None,
        observed_at=None,
        freshness="UNKNOWN",
        reason_code=reason_code,
    )


def _validate_resolved(resolved: ResolvedWorkspaceAuthority) -> None:
    if resolved.status not in {"BOUND", "NOT_OBSERVED", "UNAVAILABLE"}:
        raise WorkspaceScopeError("WORKSPACE_SCOPE_STATUS_INVALID")
    if resolved.status != "BOUND":
        if any(
            value is not None
            for value in (
                resolved.root_path,
                resolved.workspace_id,
                resolved.root_digest,
                resolved.execution_domain_id,
            )
        ):
            raise WorkspaceScopeError("WORKSPACE_SCOPE_UNAVAILABLE_HAS_AUTHORITY")
        return
    if any(
        value is None
        for value in (
            resolved.workspace_id,
            resolved.root_digest,
            resolved.execution_domain_id,
        )
    ):
        raise WorkspaceScopeError("WORKSPACE_SCOPE_BINDING_INCOMPLETE")
    if resolved.storage_kind in {"HOST_PATH", "DOCKER_BIND"}:
        if resolved.root_path is None:
            raise WorkspaceScopeError("WORKSPACE_SCOPE_BINDING_INCOMPLETE")
        root = validate_workspace_root_binding(
            resolved.root_path,
            execution_domain_id=resolved.execution_domain_id,
            expected_digest=resolved.root_digest,
        )
        if root is None or workspace_root_digest(
            root, resolved.execution_domain_id
        ) != resolved.root_digest:
            raise WorkspaceScopeError("WORKSPACE_SCOPE_BINDING_INVALID")
    else:
        if (
            resolved.root_path is not None
            or resolved.storage_resource_identity is None
            or resolved.logical_root is None
            or storage_workspace_digest(
                resolved.storage_resource_identity,
                resolved.logical_root,
                resolved.execution_domain_id,
            )
            != resolved.root_digest
        ):
            raise WorkspaceScopeError("WORKSPACE_SCOPE_BINDING_INVALID")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise WorkspaceScopeError("WORKSPACE_SCOPE_TIME_INVALID")
    return value.astimezone(UTC)


def _json_string_tuple(value: object) -> tuple[str, ...]:
    parsed = json.loads(value) if isinstance(value, str) else None
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise ValueError("WORKSPACE_SCOPE_JSON_INVALID")
    return tuple(parsed)


__all__ = [
    "DurableWorkspaceScope",
    "WorkspaceAuthorityResult",
    "WorkspaceScopeError",
    "WorkspaceScopeResult",
    "WorkspaceScopeService",
]
