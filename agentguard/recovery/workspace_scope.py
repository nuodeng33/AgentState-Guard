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
    root_path: Path = field(repr=False)
    root_digest: str = field(repr=False)
    agent_ids: tuple[str, ...] = ()
    process_instance_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    ledger_event_id: str = ""


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
        root_path = str(resolved.root_path) if bound else None
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
                           root_path, root_digest, agent_ids_json,
                           process_instance_ids_json, evidence_refs_json, ledger_event_id
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        connection = self._database._conn
        if connection is None:
            raise WorkspaceScopeError("WORKSPACE_SCOPE_DATABASE_CLOSED")
        if verify_ledger(connection):
            raise WorkspaceScopeError("WORKSPACE_SCOPE_LEDGER_INVALID")
        row = connection.execute(
            """SELECT observation_id, discovery_snapshot_id, observed_at, recorded_at,
                      status, workspace_id, execution_domain_id, root_path, root_digest,
                      agent_ids_json, process_instance_ids_json, evidence_refs_json,
                      ledger_event_id
               FROM workspace_scope_observations
               ORDER BY observation_sequence DESC LIMIT 1"""
        ).fetchone()
        if row is None or row[4] != "BOUND":
            return None
        try:
            agent_ids = _json_string_tuple(row[9])
            process_ids = _json_string_tuple(row[10])
            evidence_refs = _json_string_tuple(row[11])
            observed_at = _utc(datetime.fromisoformat(row[2]))
            recorded_at = _utc(datetime.fromisoformat(row[3]))
            root = validate_workspace_root_binding(
                Path(row[7]),
                execution_domain_id=row[6],
                expected_digest=row[8],
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if root is None:
            return None
        event = connection.execute(
            """SELECT event_type, result, execution_domain_id, subject_ref,
                      payload_safe_json
               FROM evidence_ledger_events WHERE event_id = ?""",
            (row[12],),
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
            agent_ids=agent_ids,
            process_instance_ids=process_ids,
            evidence_refs=evidence_refs,
            ledger_event_id=row[12],
        )

    def latest_result(self) -> WorkspaceScopeResult | None:
        """Return the latest durable status without treating stale BOUND as active."""

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
            resolved.root_path,
            resolved.workspace_id,
            resolved.root_digest,
            resolved.execution_domain_id,
        )
    ):
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
    "WorkspaceScopeError",
    "WorkspaceScopeResult",
    "WorkspaceScopeService",
]
