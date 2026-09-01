"""Read-only, fail-closed R4 API projections from durable server state."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

from agentguard.evidence.canonical import canonical_json, flatten_bounded_digest_tree
from agentguard.evidence.discovery_adapter import (
    resolve_verified_workspace_correlation,
)
from agentguard.evidence.ledger import verify_ledger
from agentguard.policy.models import POLICY_VERSION
from agentguard.recovery.coverage import RecoveryCoverageService
from agentguard.recovery.manifest import validate_snapshot_v3
from agentguard.recovery.workspace_scope import (
    WorkspaceScopeError,
    WorkspaceScopeService,
)
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore
from agentguard.supervision.service import SupervisionService

_SAFE_ATOM = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_SAFE_EVENT_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_SAFE_LABEL = re.compile(r"[A-Za-z0-9 _.:+-]{1,64}")
_INSTANCE_LABEL = re.compile(r"[A-Z][A-Z0-9_]{1,15} [0-9a-f]{6}")


def _instance_label(value: object) -> str | None:
    return value if isinstance(value, str) and _INSTANCE_LABEL.fullmatch(value) else None
_ACTIVITY_TYPES = frozenset(
    {
        "USER_APPROVED",
        "USER_REJECTED",
        "SESSION_ACTIVATED",
        "SESSION_COMPLETED",
        "SESSION_FAILED",
        "OBSERVED_CHANGE",
        "PROCESS_STARTED",
        "PROCESS_EXITED",
        "SANDBOX_PROCESS_STARTED",
        "SANDBOX_PROCESS_EXITED",
        "SANDBOX_NETWORK_OBSERVED",
        "WORKSPACE_ACTIVITY_OBSERVED",
        "SCOPE_DRIFT",
        "EXTERNAL_EFFECT_UNKNOWN",
        "CHECKPOINT_CREATED",
        "MANIFEST_VERIFIED",
        "TEST_RESTORE_STARTED",
        "FILE_QUARANTINED",
        "FILE_RESTORED",
        "VALIDATOR_PASSED",
        "RESTORE_FAILED",
        "RECOVERY_DRILL_COMPLETED",
        "RECOVERY_DRILL_VERIFIED",
        "RECOVERY_DRILL_PREPARED",
        "RECOVERY_DRILL_APPROVED",
        "RECOVERY_DRILL_STARTED",
        "DRIFT_ESTABLISHED",
        "TRUSTED_BASELINE_CREATED",
        "TRUSTED_BASELINE_RETIRED",
    }
)
_PROCESS_ACTIVITY_TYPES = frozenset(
    {
        "PROCESS_STARTED",
        "PROCESS_EXITED",
        "SANDBOX_PROCESS_STARTED",
        "SANDBOX_PROCESS_EXITED",
        "SANDBOX_NETWORK_OBSERVED",
    }
)
_RECOVERY_FIELDS = {
    "status",
    "reason_code",
    "requested_targets",
    "authorized_snapshot_targets",
    "intact_manifest_blob_targets",
    "authorized_snapshot_coverage",
    "manifest_blob_coverage",
    "test_restore_verified_targets",
    "test_restore_status",
    "recovery_level",
    "r1_verified",
    "r2_verified",
    "r3_verified",
    "trusted_baseline_status",
    "trusted_baseline_id",
}

_ACTIVATION_CONTEXT_FIELDS = {
    "execution_domain_id",
    "workspace_id",
    "workspace_binding_ref",
    "approved_scope_digest",
    "target_refs_digest",
    "product_sha",
}


def _atom(value: object) -> str | None:
    return value if isinstance(value, str) and _SAFE_ATOM.fullmatch(value) else None


def _label(value: object) -> str | None:
    return value if isinstance(value, str) and _SAFE_LABEL.fullmatch(value) else None


def _json_object(value: object) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value) if isinstance(value, str) else None
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _base(
    view: str, status: str, reason_code: str, items: list[dict[str, Any]]
) -> dict[str, Any]:
    refs = sorted({ref for item in items for ref in item.get("evidence_refs", [])})
    projection = {
        "schema_version": "r4-p8-1",
        "view": view,
        "status": status,
        "reason_code": reason_code,
        "evidence_refs": refs,
        "items": items,
    }
    observed = sorted(
        {value for item in items if isinstance((value := item.get("observed_at")), str)}
    )
    if observed:
        projection["observed_at"] = observed[-1]
    return projection


def _safe_result(value: object) -> str | None:
    return _atom(value) if value is not None else None


def _activity_detail(payload: dict[str, Any]) -> dict[str, Any]:
    affected: list[str] = []
    single = payload.get("target_ref_digest")
    if isinstance(single, str) and re.fullmatch(r"[0-9a-f]{64}", single):
        affected.append(single)
    multiple = payload.get("target_ref_digests")
    flattened = flatten_bounded_digest_tree(multiple)
    if flattened is not None:
        affected.extend(flattened)
    verification = payload.get("verification")
    verification_result = (
        _safe_result(verification.get("result"))
        if isinstance(verification, dict)
        else None
    )
    return {
        "affected_objects": sorted(set(affected)),
        "verification": verification_result,
        "attribution": _atom(payload.get("attribution")),
        "change_kind": _atom(payload.get("change_kind")) or _atom(payload.get("action")),
        "coverage_after": _atom(payload.get("coverage_after")),
        "coverage_before": _atom(payload.get("coverage_before")),
        "recovery_disposition": _atom(payload.get("recovery_disposition")),
        "workspace_id": _atom(payload.get("workspace_id")),
    }


def _activity_category(event_type: str) -> str:
    if event_type in _PROCESS_ACTIVITY_TYPES:
        return "PROCESS_ACTIVITY"
    if event_type == "WORKSPACE_ACTIVITY_OBSERVED":
        return "WORKSPACE_ACTIVITY"
    if event_type in {"OBSERVED_CHANGE", "SCOPE_DRIFT", "EXTERNAL_EFFECT_UNKNOWN"}:
        return "CHANGE"
    if event_type == "CHECKPOINT_CREATED":
        return "CHECKPOINT"
    if event_type in {"USER_APPROVED", "USER_REJECTED"}:
        return "APPROVAL"
    if event_type in {"SESSION_ACTIVATED", "SESSION_COMPLETED", "SESSION_FAILED"}:
        return "SUPERVISION"
    if event_type in {"MANIFEST_VERIFIED", "VALIDATOR_PASSED", "RECOVERY_DRILL_VERIFIED"}:
        return "VERIFICATION"
    if event_type.startswith("RECOVERY_") or event_type in {
        "TEST_RESTORE_STARTED",
        "FILE_QUARANTINED",
        "FILE_RESTORED",
        "RESTORE_FAILED",
        "DRIFT_ESTABLISHED",
    }:
        return "RECOVERY"
    return "ACTIVITY"


def _verified_activity_page(
    connection: sqlite3.Connection,
    *,
    session_id: str | None = None,
    limit: int = 50,
    before_sequence: int | None = None,
    include_process_activity: bool = True,
    workspace_id: str | None = None,
    checkpoint_id: str | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    event_types = (
        _ACTIVITY_TYPES
        if include_process_activity
        else _ACTIVITY_TYPES - _PROCESS_ACTIVITY_TYPES
    )
    where = "event_type IN ({})".format(",".join("?" for _item in event_types))
    parameters: list[object] = sorted(event_types)
    if session_id is not None:
        where += " AND supervision_session_id = ?"
        parameters.append(session_id)
    if before_sequence is not None:
        where += " AND sequence < ?"
        parameters.append(before_sequence)
    if workspace_id is not None:
        where += " AND json_extract(payload_safe_json, '$.workspace_id') = ?"
        parameters.append(workspace_id)
    if checkpoint_id is not None:
        where += " AND checkpoint_id = ?"
        parameters.append(checkpoint_id)
    bounded_limit = max(1, min(int(limit), 100))
    parameters.append(bounded_limit + 1)
    rows = connection.execute(
        f"""SELECT sequence, event_id, recorded_at, observed_at, event_family, event_type,
                   source, result, supervision_session_id, transaction_id,
                   checkpoint_id, subject_ref, evidence_refs_json, payload_safe_json,
                   execution_domain_id
            FROM evidence_ledger_events
            WHERE {where}
            ORDER BY sequence DESC LIMIT ?""",
        tuple(parameters),
    ).fetchall()
    has_more = len(rows) > bounded_limit
    rows = rows[:bounded_limit]
    activities: list[dict[str, Any]] = []
    for row in rows:
        try:
            evidence_refs = json.loads(row[12])
            payload = json.loads(row[13])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(evidence_refs, list) or not isinstance(payload, dict):
            continue
        detail = _activity_detail(payload)
        reason_code = _atom(payload.get("reason_code"))
        activities.append(
            {
                "sequence": row[0],
                "event_id": row[1],
                "timestamp": row[3] or row[2],
                "observed_at": row[3],
                "recorded_at": row[2],
                "event_family": row[4],
                "category": _activity_category(row[5]),
                "source": row[6],
                # Keep the historical actor field additive-compatible. New
                # product presentation separates source from causal
                # attribution through actor_attribution.
                "actor": row[6],
                "actor_attribution": detail["attribution"] or "UNKNOWN",
                "subject": row[11]
                if _SAFE_EVENT_ID.fullmatch(row[11] or "")
                else None,
                "type": row[5],
                "result": row[7],
                "affected_objects": detail["affected_objects"],
                "checkpoint_id": row[10],
                "supervision_session_id": row[8],
                "change_id": row[9],
                "policy_summary": (
                    row[7] if row[5] == "POLICY_EVALUATED" else None
                ),
                "approval_summary": (
                    row[7] if row[5] in {"USER_APPROVED", "USER_REJECTED"} else None
                ),
                "verification_summary": detail["verification"],
                "reason_code": reason_code or row[5],
                "execution_domain_id": row[14],
                "attribution": detail["attribution"],
                "change_kind": detail["change_kind"],
                "coverage_after": detail["coverage_after"],
                "coverage_before": detail["coverage_before"],
                "recovery_disposition": detail["recovery_disposition"],
                "workspace_id": detail["workspace_id"],
                "evidence_refs": sorted(
                    {
                        row[1],
                        *(
                            item
                            for item in evidence_refs
                            if isinstance(item, str) and item
                        ),
                    }
                ),
            }
        )
    next_cursor = rows[-1][0] if has_more and rows else None
    return activities, next_cursor


def _verified_activities(
    connection: sqlite3.Connection,
    *,
    session_id: str | None = None,
    limit: int = 50,
    before_sequence: int | None = None,
    include_process_activity: bool = True,
    workspace_id: str | None = None,
    checkpoint_id: str | None = None,
) -> list[dict[str, Any]]:
    return _verified_activity_page(
        connection,
        session_id=session_id,
        limit=limit,
        before_sequence=before_sequence,
        include_process_activity=include_process_activity,
        workspace_id=workspace_id,
        checkpoint_id=checkpoint_id,
    )[0]


def _agent_activity_summary(
    connection: sqlite3.Connection,
    *,
    agent_ref: str,
    workspace_id: str | None,
) -> dict[str, Any]:
    candidates = _verified_activities(connection, limit=200)
    activities = [
        item
        for item in candidates
        if item.get("subject") == agent_ref
        or (
            workspace_id is not None
            and item.get("workspace_id") == workspace_id
            and item.get("attribution") == "WORKSPACE_SCOPE_ACTIVITY_NOT_AGENT_CAUSAL"
        )
    ][:20]
    lifecycle = connection.execute(
        """SELECT event_id, source, result, payload_safe_json
           FROM evidence_ledger_events
           WHERE event_type = 'HOST_OBSERVER_LIFECYCLE' AND subject_ref = ?
           ORDER BY sequence DESC LIMIT 1""",
        (agent_ref,),
    ).fetchone()
    observer_active = False
    lifecycle_ref = None
    if lifecycle is not None:
        payload = _json_object(lifecycle[3])
        observer_active = (
            lifecycle[1] == "host-native-observer"
            and lifecycle[2] == "AVAILABLE"
            and payload is not None
            and payload.get("agent_ref") == agent_ref
            and payload.get("activity_observability") == "OBSERVABLE"
        )
        lifecycle_ref = lifecycle[0] if observer_active else None
    observable = observer_active or bool(activities)
    return {
        "activity_observability": "OBSERVABLE" if observable else "UNKNOWN",
        "latest_activity": activities[0] if activities else None,
        "recent_activity_count": len(activities),
        "recent_verified_activities": activities,
        "activity_reason_code": (
            "HOST_ACTIVITY_OBSERVED"
            if activities
            else "HOST_NATIVE_OBSERVER_ACTIVE"
            if observer_active
            else "ACTIVITY_OBSERVABILITY_NOT_ESTABLISHED"
        ),
        "activity_evidence_refs": [lifecycle_ref] if lifecycle_ref else [],
    }


def _latest_discovery_snapshot_id(connection: sqlite3.Connection) -> str | None:
    rows = connection.execute(
        """SELECT payload_safe_json FROM evidence_ledger_events
           WHERE event_type IN (
               'RUNTIME_DETECTED', 'AGENT_DETECTED',
               'WORKSPACE_LINKED', 'PROBE_UNREACHABLE'
           ) ORDER BY sequence DESC"""
    ).fetchall()
    for (payload_json,) in rows:
        payload = _json_object(payload_json)
        snapshot_id = _atom(payload.get("snapshot_id")) if payload else None
        if snapshot_id is not None:
            return snapshot_id
    return None


def _verified_policy_agent_binding(
    database: StateDB,
    connection: sqlite3.Connection,
    *,
    policy_events: list[tuple[Any, ...]],
    declared_intent_digest: str,
    decision: str,
    requires_checkpoint: bool,
    requires_manual_approval: bool,
) -> tuple[str, tuple[str, ...]] | None:
    """Resolve one session to one Agent only through its signed workspace binding."""
    if len(policy_events) != 1:
        return None
    event_id, result, source, event_domain, payload_json = policy_events[0]
    payload = _json_object(payload_json)
    authority = payload.get("policy_authority") if payload else None
    policy_digest = payload.get("policy_binding_digest") if payload else None
    if (
        source != "supervision-service"
        or result != decision
        or payload is None
        or payload.get("status") != decision
        or not isinstance(authority, dict)
        or not isinstance(policy_digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", policy_digest)
        or hashlib.sha256(canonical_json(authority).encode("utf-8")).hexdigest()
        != policy_digest
        or authority.get("declared_intent_digest") != declared_intent_digest
        or authority.get("decision") != decision
        or authority.get("requires_checkpoint") is not requires_checkpoint
        or authority.get("requires_manual_approval") is not requires_manual_approval
        or authority.get("policy_version") != POLICY_VERSION
    ):
        return None
    context = authority.get("activation_context")
    if not isinstance(context, dict) or set(context) != _ACTIVATION_CONTEXT_FIELDS:
        return None
    domain = _atom(context.get("execution_domain_id"))
    workspace_id = _atom(context.get("workspace_id"))
    binding_ref = _atom(context.get("workspace_binding_ref"))
    if (
        domain is None
        or workspace_id is None
        or binding_ref is None
        or event_domain != domain
        or not re.fullmatch(r"[0-9a-f]{64}", str(context.get("approved_scope_digest")))
        or not re.fullmatch(r"[0-9a-f]{64}", str(context.get("target_refs_digest")))
        or not re.fullmatch(r"[0-9a-f]{40}", str(context.get("product_sha")))
    ):
        return None
    binding_row = connection.execute(
        """SELECT event_id, execution_domain_id, payload_safe_json
           FROM evidence_ledger_events
           WHERE event_id = ? AND event_type = 'WORKSPACE_LINKED'""",
        (binding_ref,),
    ).fetchone()
    if binding_row is None or binding_row[1] != domain:
        return None
    binding_payload = _json_object(binding_row[2])
    agent_event_id = (
        _atom(binding_payload.get("agent_event_id")) if binding_payload else None
    )
    if agent_event_id is None:
        return None
    verified = resolve_verified_workspace_correlation(
        connection,
        agent_event_id=agent_event_id,
    )
    if (
        verified["status"] != "LINKED"
        or verified["binding_ref"] != binding_ref
        or verified["workspace_id"] != workspace_id
    ):
        return None
    try:
        authority = WorkspaceScopeService(database).resolve_authority(workspace_id)
    except WorkspaceScopeError:
        return None
    if authority.authority_state != "BOUND":
        return None
    agent_domain = connection.execute(
        """SELECT execution_domain_id FROM evidence_ledger_events
           WHERE event_id = ? AND event_type = 'AGENT_DETECTED'""",
        (agent_event_id,),
    ).fetchone()
    if agent_domain is None or agent_domain[0] != domain:
        return None
    return agent_event_id, (event_id, binding_ref)


def _verified_agent_ref(
    connection: sqlite3.Connection,
    item: dict[str, Any],
) -> tuple[str, str] | None:
    evidence_refs = item.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        return None
    safe_refs = [ref for ref in evidence_refs if _SAFE_EVENT_ID.fullmatch(ref or "")]
    if not safe_refs:
        return None
    placeholders = ",".join("?" for _ref in safe_refs)
    rows = connection.execute(
        f"""SELECT event_id, execution_domain_id, subject_ref, payload_safe_json
            FROM evidence_ledger_events
            WHERE event_type = 'AGENT_DETECTED' AND event_id IN ({placeholders})""",
        tuple(safe_refs),
    ).fetchall()
    if len(rows) != 1:
        return None
    event_id, domain, subject_ref, payload_json = rows[0]
    payload = _json_object(payload_json)
    agent_ref = _atom(payload.get("agent_id")) if payload else None
    if (
        payload is None
        or payload.get("fact_type") != "agent.metadata"
        or agent_ref is None
        or subject_ref != agent_ref
        or domain != item.get("execution_domain_id")
    ):
        return None
    return agent_ref, event_id


def _workspace_authority_projection(
    database: StateDB,
    correlation: dict[str, Any],
) -> dict[str, Any]:
    workspace_id = _atom(correlation.get("workspace_id"))
    if correlation.get("status") != "LINKED" or workspace_id is None:
        return {
            "status": "UNKNOWN",
            "authority_state": "UNKNOWN",
            "workspace_id": workspace_id,
            "authority_observation_ref": None,
            "execution_domain_id": None,
            "root_digest": None,
            "observed_at": None,
            "freshness": "UNKNOWN",
            "reason_code": "WORKSPACE_CORRELATION_MISSING",
            "evidence_refs": [],
        }
    try:
        return WorkspaceScopeService(database).resolve_authority(
            workspace_id
        ).safe_summary()
    except WorkspaceScopeError:
        return {
            "status": "UNKNOWN",
            "authority_state": "UNKNOWN",
            "workspace_id": workspace_id,
            "authority_observation_ref": None,
            "execution_domain_id": None,
            "root_digest": None,
            "observed_at": None,
            "freshness": "UNKNOWN",
            "reason_code": "WORKSPACE_SCOPE_BINDING_INVALID",
            "evidence_refs": [],
        }


def _agent_supervision_summaries(
    connection: sqlite3.Connection,
    agent_items: list[dict[str, Any]],
    session_items: list[dict[str, Any]],
    session_agent_events: dict[str, tuple[str, tuple[str, ...]]],
    protection_by_workspace: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    protection_by_workspace = protection_by_workspace or {}
    sessions_by_agent_event: dict[
        str, list[tuple[dict[str, Any], tuple[str, ...]]]
    ] = {}
    for session in session_items:
        binding = session_agent_events.get(session["supervision_session_id"])
        if binding is None:
            continue
        agent_event_id, binding_refs = binding
        sessions_by_agent_event.setdefault(agent_event_id, []).append(
            (session, binding_refs)
        )

    summaries: list[dict[str, Any]] = []
    for item in agent_items:
        identity = _verified_agent_ref(connection, item)
        agent_ref = identity[0] if identity is not None else None
        agent_event_id = identity[1] if identity is not None else None
        linked = sessions_by_agent_event.get(agent_event_id or "", [])
        linked.sort(
            key=lambda value: (
                value[0].get("created_at") or "",
                value[0]["supervision_session_id"],
            )
        )
        selected, binding_refs = linked[-1] if linked else (None, ())
        workspace = item.get("workspace")
        workspace_correlation = item.get("workspace_correlation")
        workspace_status = (
            workspace.get("status") if isinstance(workspace, dict) else None
        )
        workspace_reason = (
            workspace.get("reason_code") if isinstance(workspace, dict) else None
        )
        workspace_id = (
            _atom(workspace.get("workspace_id"))
            if isinstance(workspace, dict)
            else None
        )
        protection = protection_by_workspace.get(workspace_id or "")
        if protection is None:
            protection = {
                "storage_kind": (
                    _atom(workspace.get("storage_kind"))
                    if isinstance(workspace, dict)
                    else None
                ),
                "protection_state": (
                    "BOUND_NO_CHECKPOINT" if workspace_status == "BOUND" else "UNKNOWN"
                ),
                "verification_state": "NOT_VERIFIED",
                "latest_checkpoint": None,
                "latest_verified_change": None,
                "recovery_disposition": "NOT_CHECKPOINTED",
            }
        if identity is None:
            supervision_status = "UNKNOWN"
            reason_code = "AGENT_INSTANCE_IDENTITY_INVALID"
        elif selected is not None:
            supervision_status = "SUPERVISED"
            reason_code = "AGENT_SUPERVISION_SESSION_VERIFIED"
        elif workspace_status == "BOUND":
            supervision_status = "WORKSPACE_BOUND"
            reason_code = "AGENT_WORKSPACE_BOUND_NO_SUPERVISION_SESSION"
        elif workspace_reason in {
            "WORKSPACE_CORRELATION_MISSING",
            "WORKSPACE_PROTECTION_AUTHORITY_MISSING",
        }:
            supervision_status = "OBSERVED_ONLY"
            reason_code = "AGENT_OBSERVED_ONLY"
        else:
            supervision_status = "UNKNOWN"
            reason_code = "AGENT_WORKSPACE_AUTHORITY_UNKNOWN"
        refs = set(item.get("evidence_refs", []))
        if selected is not None:
            refs.update(selected.get("evidence_refs", []))
            refs.update(binding_refs)
        summaries.append(
            {
                "agent_ref": agent_ref,
                "detected_identity": item.get("detected_identity"),
                "instance_label": item.get("instance_label"),
                "role": item.get("role"),
                "lifecycle": item.get("lifecycle"),
                "confidence": item.get("confidence"),
                "execution_domain_id": item.get("execution_domain_id"),
                "workspace": workspace,
                "workspace_correlation": workspace_correlation,
                "supervision_status": supervision_status,
                "supervision_session_id": (
                    selected["supervision_session_id"] if selected else None
                ),
                "policy_decision": selected.get("policy_decision")
                if selected
                else None,
                "pending_approval": selected.get("pending_approval")
                if selected
                else None,
                "latest_checkpoint": selected.get("latest_checkpoint")
                if selected
                else protection.get("latest_checkpoint"),
                "latest_verified_change": protection.get("latest_verified_change"),
                "storage_kind": protection.get("storage_kind"),
                "protection_state": protection.get("protection_state", "UNKNOWN"),
                "verification_state": protection.get(
                    "verification_state", "NOT_VERIFIED"
                ),
                "recovery_disposition": protection.get(
                    "recovery_disposition", "UNKNOWN"
                ),
                "latest_verified_activity": (
                    selected.get("latest_verified_activity")
                    if selected
                    else item.get("latest_activity")
                ),
                "activity_observability": item.get("activity_observability"),
                "latest_activity": item.get("latest_activity"),
                "recent_activity_count": item.get("recent_activity_count", 0),
                "recent_verified_activities": item.get(
                    "recent_verified_activities", []
                ),
                "activity_reason_code": item.get("activity_reason_code"),
                "reason_code": reason_code,
                "uncertainty": supervision_status == "UNKNOWN",
                "observed_at": item.get("observed_at"),
                "evidence_refs": sorted(ref for ref in refs if isinstance(ref, str)),
            }
        )
    return summaries


def _recovery_item_protection_fields(
    item: dict[str, Any],
    *,
    latest_verified_change: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if item.get("actual_restore_status") == "VERIFIED":
        protection_state = "RECOVERY_VERIFIED"
        verification_state = "RECOVERY_VERIFIED"
    elif item.get("r2_verified") is True:
        protection_state = "RECOVERABLE"
        verification_state = "TEST_RESTORE_VERIFIED"
    elif item.get("authorized_snapshot_targets", 0) > 0:
        protection_state = "RECOVERABLE"
        verification_state = (
            "MANIFEST_VERIFIED"
            if item.get("manifest_integrity") == "VERIFIED"
            else "NOT_VERIFIED"
        )
    elif latest_verified_change is not None:
        protection_state = "CHANGE_PROVEN"
        verification_state = "CHANGE_PROVEN"
    else:
        protection_state = "CHECKPOINTED"
        verification_state = (
            "MANIFEST_VERIFIED"
            if item.get("manifest_integrity") == "VERIFIED"
            else "NOT_VERIFIED"
        )
    return {
        "protection_state": protection_state,
        "verification_state": verification_state,
        "latest_verified_change": latest_verified_change,
        "recovery_disposition": (
            latest_verified_change.get("recovery_disposition")
            if latest_verified_change is not None
            else "RESTORABLE"
            if item.get("authorized_snapshot_targets", 0) > 0
            else "AUDIT_ONLY"
        ),
    }


def _current_workspace_agent_map(
    connection: sqlite3.Connection,
) -> dict[str, str]:
    latest_snapshot_id = _latest_discovery_snapshot_id(connection)
    if latest_snapshot_id is None:
        return {}
    rows = connection.execute(
        """SELECT event_id, subject_ref, payload_safe_json
           FROM evidence_ledger_events
           WHERE event_type = 'AGENT_DETECTED'
           ORDER BY sequence"""
    ).fetchall()
    result: dict[str, str] = {}
    conflicts: set[str] = set()
    for event_id, subject_ref, payload_json in rows:
        payload = _json_object(payload_json)
        agent_ref = _atom(subject_ref)
        if (
            payload is None
            or payload.get("snapshot_id") != latest_snapshot_id
            or agent_ref is None
        ):
            continue
        correlation = resolve_verified_workspace_correlation(
            connection,
            agent_event_id=event_id,
        )
        workspace_id = _atom(correlation.get("workspace_id"))
        if correlation.get("status") != "BOUND" or workspace_id is None:
            continue
        if workspace_id in result and result[workspace_id] != agent_ref:
            conflicts.add(workspace_id)
        result[workspace_id] = agent_ref
    for workspace_id in conflicts:
        result.pop(workspace_id, None)
    return result


class R4ReadProjectionService:
    """Project only verified ledger and durable service facts into safe DTOs."""

    def __init__(self, database: StateDB, snapshots: SnapshotStore) -> None:
        self._database = database
        self._snapshots = snapshots

    @staticmethod
    def unavailable(view: str) -> dict[str, Any]:
        projection = _base(view, "DEGRADED", "R4_DATABASE_UNREACHABLE", [])
        return _with_empty_recovery(projection) if view == "recovery" else projection

    def runtime(self) -> dict[str, Any]:
        connection = self._verified_connection("runtime")
        if isinstance(connection, dict):
            return connection
        rows = connection.execute(
            """SELECT event_id, event_type, result, observed_at,
                      execution_domain_id, payload_safe_json
               FROM evidence_ledger_events
               WHERE event_type IN ('RUNTIME_DETECTED', 'PROBE_UNREACHABLE')
               ORDER BY sequence"""
        ).fetchall()
        latest_snapshot_id = _latest_discovery_snapshot_id(connection)
        items: list[dict[str, Any]] = []
        for (
            event_id,
            event_type,
            result,
            observed_at,
            event_domain,
            payload_json,
        ) in rows:
            payload = _json_object(payload_json)
            if latest_snapshot_id is not None and (
                payload is None or payload.get("snapshot_id") != latest_snapshot_id
            ):
                continue
            value = payload.get("value") if payload else None
            if not isinstance(value, dict):
                continue
            domain = _atom(event_domain) or _atom(value.get("execution_domain_id"))
            available = result.casefold() == "available"
            if (
                event_type == "RUNTIME_DETECTED"
                and payload.get("fact_type") == "runtime.metadata"
            ):
                runtime_type = _atom(value.get("runtime_kind"))
                if runtime_type is None:
                    continue
                capabilities = value.get("capabilities", [])
                safe_capabilities = (
                    sorted({item for item in capabilities if _atom(item)})
                    if isinstance(capabilities, list)
                    else []
                )
                items.append(
                    {
                        "runtime_type": runtime_type,
                        "domain_label": _label(payload.get("domain_label")),
                        "execution_domain_id": domain,
                        "availability": "AVAILABLE" if available else "UNKNOWN",
                        "capabilities": safe_capabilities,
                        "reason_code": "RUNTIME_DETECTED"
                        if available
                        else "RUNTIME_STATE_UNCERTAIN",
                        "uncertainty": not available or domain is None,
                        "observed_at": observed_at,
                        "evidence_refs": [event_id],
                    }
                )
            elif (
                event_type == "PROBE_UNREACHABLE"
                and payload.get("fact_type") == "probe.unreachable"
                and value.get("scope", "runtime") == "runtime"
            ):
                items.append(
                    {
                        "runtime_type": None,
                        "execution_domain_id": domain,
                        "availability": "UNREACHABLE",
                        "capabilities": [],
                        "reason_code": "DISCOVERY_PROBE_UNREACHABLE",
                        "uncertainty": True,
                        "observed_at": observed_at,
                        "evidence_refs": [event_id],
                    }
                )
        items.sort(
            key=lambda item: (
                item["execution_domain_id"] or "",
                item["runtime_type"] or "",
                item["evidence_refs"][0],
            )
        )
        degraded = any(item["availability"] != "AVAILABLE" for item in items)
        return _base(
            "runtime",
            "DEGRADED" if degraded else "AVAILABLE" if items else "EMPTY",
            "R4_RUNTIME_DEGRADED"
            if degraded
            else "R4_RUNTIME_AVAILABLE"
            if items
            else "R4_STATE_EMPTY",
            items,
        )

    def agents(self) -> dict[str, Any]:
        connection = self._verified_connection("agents")
        if isinstance(connection, dict):
            return connection
        rows = connection.execute(
            """SELECT sequence, event_id, result, observed_at,
                      execution_domain_id, subject_ref, payload_safe_json
               FROM evidence_ledger_events
               WHERE event_type IN ('AGENT_DETECTED', 'PROBE_UNREACHABLE')
               ORDER BY sequence"""
        ).fetchall()
        latest_snapshot_id = _latest_discovery_snapshot_id(connection)
        latest_rows: dict[str, tuple[Any, ...]] = {}
        failure_refs: list[str] = []
        degradation_scopes: list[dict[str, str | None]] = []
        for row in rows:
            payload = _json_object(row[6])
            if latest_snapshot_id is not None and (
                payload is None or payload.get("snapshot_id") != latest_snapshot_id
            ):
                continue
            value = payload.get("value") if payload else None
            if (
                payload
                and payload.get("fact_type") == "probe.unreachable"
                and isinstance(value, dict)
                and value.get("scope") == "agents"
            ):
                failure_refs.append(row[1])
                scope = {
                    "execution_domain_id": _atom(value.get("execution_domain_id")),
                    "label": _label(value.get("domain_label")),
                    "reason_code": _atom(value.get("reason_code"))
                    or "AGENT_DISCOVERY_UNREACHABLE",
                }
                if scope not in degradation_scopes:
                    degradation_scopes.append(scope)
                continue
            agent_id = _atom(payload.get("agent_id")) if payload else None
            latest_rows[agent_id or row[1]] = row
        items: list[dict[str, Any]] = []
        for (
            _sequence,
            event_id,
            result,
            observed_at,
            event_domain,
            subject_ref,
            payload_json,
        ) in sorted(latest_rows.values(), key=lambda row: row[0]):
            payload = _json_object(payload_json)
            value = payload.get("value") if payload else None
            if (
                not isinstance(value, dict)
                or payload.get("fact_type") != "agent.metadata"
            ):
                continue
            identity = _atom(payload.get("agent_type")) or _atom(
                value.get("agent_kind")
            )
            if identity is None:
                continue
            confidence = payload.get("confidence")
            if (
                not isinstance(confidence, (int, float))
                or isinstance(confidence, bool)
                or not 0 <= confidence <= 1
            ):
                confidence = 0.0
            available = result.casefold() == "available"
            lifecycle = (
                _atom(payload.get("lifecycle"))
                or _atom(value.get("lifecycle"))
                or ("DETECTED" if available else "UNKNOWN")
            )
            if lifecycle not in {
                "DETECTED",
                "RUNNING",
                "OBSERVED",
                "INTEGRATED",
                "ENFORCED",
                "UNKNOWN",
            }:
                lifecycle = "UNKNOWN"
            domain = _atom(event_domain)
            if domain is None and payload.get("agent_id") is None:
                domain = _atom(value.get("execution_domain_id"))
            workspace_correlation = resolve_verified_workspace_correlation(
                connection,
                agent_event_id=event_id,
            )
            workspace = _workspace_authority_projection(
                self._database,
                workspace_correlation,
            )
            instance_label = _instance_label(value.get("instance_label"))
            agent_ref = _atom(payload.get("agent_id"))
            activity = (
                _agent_activity_summary(
                    connection,
                    agent_ref=agent_ref,
                    workspace_id=_atom(workspace_correlation.get("workspace_id")),
                )
                if agent_ref is not None
                else {
                    "activity_observability": "UNKNOWN",
                    "latest_activity": None,
                    "recent_activity_count": 0,
                    "recent_verified_activities": [],
                    "activity_reason_code": "ACTIVITY_OBSERVABILITY_NOT_ESTABLISHED",
                    "activity_evidence_refs": [],
                }
            )
            items.append(
                {
                    "detected_identity": instance_label or identity,
                    "instance_label": instance_label,
                    "role": _atom(value.get("role")) or "detected",
                    "lifecycle": lifecycle if available else "UNKNOWN",
                    "confidence": confidence,
                    "execution_domain_id": domain,
                    "workspace": workspace,
                    "workspace_correlation": workspace_correlation,
                    "reason_code": "AGENT_DETECTED"
                    if available
                    else "AGENT_STATE_UNCERTAIN",
                    "uncertainty": not available
                    or domain is None
                    or workspace["authority_state"] != "BOUND",
                    "observed_at": observed_at,
                    "activity_observability": activity["activity_observability"],
                    "latest_activity": activity["latest_activity"],
                    "recent_activity_count": activity["recent_activity_count"],
                    "recent_verified_activities": activity[
                        "recent_verified_activities"
                    ],
                    "activity_reason_code": activity["activity_reason_code"],
                    "evidence_refs": [
                        event_id,
                        *(
                            [workspace_correlation["binding_ref"]]
                            if workspace_correlation["binding_ref"] is not None
                            else []
                        ),
                        *workspace["evidence_refs"],
                        *activity["activity_evidence_refs"],
                    ],
                }
            )
        items.sort(
            key=lambda item: (
                item["execution_domain_id"] or "",
                item["detected_identity"],
                item["evidence_refs"][0],
            )
        )
        complete = (
            bool(items)
            and not failure_refs
            and all(not item["uncertainty"] for item in items)
        )
        projection = _base(
            "agents",
            "AVAILABLE"
            if complete
            else "DEGRADED"
            if items or failure_refs
            else "EMPTY",
            "R4_AGENTS_AVAILABLE"
            if complete
            else "R4_AGENT_DISCOVERY_PARTIAL"
            if items and failure_refs
            else "R4_AGENT_DISCOVERY_UNREACHABLE"
            if failure_refs
            else "R4_WORKSPACE_BINDING_INCOMPLETE"
            if items
            else "R4_STATE_EMPTY",
            items,
        )
        projection["identified_count"] = len(items)
        projection["degradation_scopes"] = sorted(
            degradation_scopes,
            key=lambda item: (
                item["execution_domain_id"] or "",
                item["reason_code"] or "",
            ),
        )
        if failure_refs:
            projection["evidence_refs"] = sorted(
                {*projection["evidence_refs"], *failure_refs}
            )
        return projection

    def supervision(self) -> dict[str, Any]:
        connection = self._verified_connection("supervision")
        if isinstance(connection, dict):
            return connection
        sessions = connection.execute(
            """SELECT supervision_session_id, status, decision,
                      requires_checkpoint, requires_manual_approval,
                      created_at, updated_at, declared_intent_digest
               FROM supervision_sessions ORDER BY created_at, supervision_session_id"""
        ).fetchall()
        items: list[dict[str, Any]] = []
        session_agent_events: dict[str, tuple[str, tuple[str, ...]]] = {}
        supervision = SupervisionService(self._database)
        for (
            session_id,
            status,
            decision,
            requires_checkpoint,
            requires_manual,
            created_at,
            updated_at,
            declared_intent_digest,
        ) in sessions:
            events = connection.execute(
                """SELECT event_id, event_type, result, source, execution_domain_id, payload_safe_json
                   FROM evidence_ledger_events
                   WHERE supervision_session_id = ? ORDER BY sequence""",
                (session_id,),
            ).fetchall()
            recovery_facts = None
            ai_assessment = None
            refs: list[str] = []
            policy_events: list[tuple[Any, ...]] = []
            for (
                event_id,
                event_type,
                result,
                source,
                event_domain,
                payload_json,
            ) in events:
                refs.append(event_id)
                if event_type == "POLICY_EVALUATED":
                    policy_events.append(
                        (event_id, result, source, event_domain, payload_json)
                    )
                payload = _json_object(payload_json)
                if payload is None:
                    continue
                if event_type == "POLICY_EVALUATED" and isinstance(
                    payload.get("recovery_facts"), dict
                ):
                    recovery_facts = {
                        key: payload["recovery_facts"].get(key)
                        for key in sorted(_RECOVERY_FIELDS)
                    }
                if event_type == "AI_ASSESSED":
                    ai_assessment = {
                        "decision": _atom(payload.get("decision")) or _atom(result),
                        "severity": _atom(payload.get("severity")),
                    }
            activities = _verified_activities(
                connection,
                session_id=session_id,
                limit=20,
            )
            confirmed = next(
                (
                    item
                    for item in activities
                    if item["type"]
                    in {
                        "SESSION_COMPLETED",
                        "SESSION_FAILED",
                        "USER_APPROVED",
                        "USER_REJECTED",
                    }
                ),
                None,
            )
            latest_checkpoint = next(
                (
                    {
                        "checkpoint_id": item["checkpoint_id"],
                        "reason_code": item["reason_code"],
                        "evidence_refs": item["evidence_refs"],
                    }
                    for item in activities
                    if item["checkpoint_id"] is not None
                ),
                None,
            )
            failed_reason = next(
                (
                    item["reason_code"]
                    for item in activities
                    if item["type"] in {"SESSION_FAILED", "RESTORE_FAILED"}
                ),
                None,
            )
            verified_agent = _verified_policy_agent_binding(
                self._database,
                connection,
                policy_events=policy_events,
                declared_intent_digest=declared_intent_digest,
                decision=decision,
                requires_checkpoint=bool(requires_checkpoint),
                requires_manual_approval=bool(requires_manual),
            )
            if verified_agent is not None:
                session_agent_events[session_id] = verified_agent
            items.append(
                {
                    "supervision_session_id": session_id,
                    "status": status,
                    "observable_status": status,
                    "policy_decision": decision,
                    "manual_approval": status == "APPROVED",
                    "requires_manual_approval": bool(requires_manual),
                    "requires_checkpoint": bool(requires_checkpoint),
                    "pending_approval": bool(requires_manual)
                    and status in {"PENDING", "AWAITING_APPROVAL"},
                    "blocked_or_failed_reason": (
                        "POLICY_BLOCKED" if decision == "BLOCK" else failed_reason
                    ),
                    "ai_assessment": ai_assessment,
                    "recovery_facts": recovery_facts,
                    "latest_verified_activity": activities[0] if activities else None,
                    "recent_verified_activities": activities,
                    "recent_confirmed_result": confirmed,
                    "recent_changes": [
                        item for item in activities if item["type"] == "OBSERVED_CHANGE"
                    ],
                    "latest_checkpoint": latest_checkpoint,
                    "current_task": None,
                    "current_phase": None,
                    "current_action": None,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "observed_at": (
                        activities[0]["timestamp"] if activities else updated_at
                    ),
                    "action_ref": supervision.projected_action_ref(
                        connection, session_id
                    ),
                    "evidence_refs": sorted(set(refs)),
                }
            )
        agent_projection = self.agents()
        recovery_projection = self.recovery()
        protection_by_workspace: dict[str, dict[str, Any]] = {}
        for recovery_item in recovery_projection.get("items", []):
            recovery_workspace_id = _atom(recovery_item.get("workspace_id"))
            if recovery_workspace_id is None:
                continue
            protection_by_workspace[recovery_workspace_id] = {
                "storage_kind": recovery_item.get("storage_kind"),
                "protection_state": recovery_item.get("protection_state", "UNKNOWN"),
                "verification_state": recovery_item.get(
                    "verification_state", "NOT_VERIFIED"
                ),
                "latest_checkpoint": {
                    "checkpoint_id": recovery_item.get("checkpoint_id"),
                    "reason_code": recovery_item.get("reason_code"),
                    "evidence_refs": recovery_item.get("evidence_refs", []),
                },
                "latest_verified_change": recovery_item.get(
                    "latest_verified_change"
                ),
                "recovery_disposition": recovery_item.get(
                    "recovery_disposition", "UNKNOWN"
                ),
            }
        summaries = _agent_supervision_summaries(
            connection,
            agent_projection.get("items", []),
            items,
            session_agent_events,
            protection_by_workspace,
        )
        degraded = any(item["supervision_status"] == "UNKNOWN" for item in summaries)
        populated = bool(items or summaries)
        projection = _base(
            "supervision",
            "DEGRADED" if degraded else "AVAILABLE" if populated else "EMPTY",
            "R4_SUPERVISION_AGENT_BINDING_INCOMPLETE"
            if degraded
            else "R4_SUPERVISION_AVAILABLE"
            if populated
            else "R4_STATE_EMPTY",
            items,
        )
        projection["observed_agents"] = summaries
        projection["evidence_refs"] = sorted(
            {
                *projection["evidence_refs"],
                *(ref for item in summaries for ref in item["evidence_refs"]),
            }
        )
        observed = [
            item["observed_at"] for item in summaries if item.get("observed_at")
        ]
        if observed and not projection.get("observed_at"):
            projection["observed_at"] = max(observed)
        projection["recent_verified_activities"] = _verified_activities(
            connection,
            limit=50,
        )
        return projection

    def changes(
        self,
        *,
        limit: int = 100,
        before_sequence: int | None = None,
        include_process_activity: bool = False,
        workspace_id: str | None = None,
        checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        connection = self._verified_connection("changes")
        if isinstance(connection, dict):
            return connection
        items, next_cursor = _verified_activity_page(
            connection,
            limit=limit,
            before_sequence=before_sequence,
            include_process_activity=include_process_activity,
            workspace_id=workspace_id,
            checkpoint_id=checkpoint_id,
        )
        recovery_projection = self.recovery()
        recovery_by_checkpoint = {
            item["checkpoint_id"]: item
            for item in recovery_projection.get("items", [])
            if item.get("checkpoint_id") is not None
        }
        recovery_by_workspace = {
            item["workspace_id"]: item
            for item in recovery_projection.get("items", [])
            if item.get("workspace_id") is not None
        }
        agent_by_workspace = _current_workspace_agent_map(connection)
        for item in items:
            recovery_item = recovery_by_checkpoint.get(item.get("checkpoint_id"))
            if recovery_item is None:
                recovery_item = recovery_by_workspace.get(item.get("workspace_id"))
            if recovery_item is not None:
                item["workspace_id"] = item.get("workspace_id") or recovery_item.get(
                    "workspace_id"
                )
                item["storage_kind"] = recovery_item.get("storage_kind")
                item["protection_state"] = recovery_item.get(
                    "protection_state", "UNKNOWN"
                )
                item["verification_state"] = recovery_item.get(
                    "verification_state", "NOT_VERIFIED"
                )
            elif item["category"] == "CHANGE" and item.get("checkpoint_id"):
                item["storage_kind"] = None
                item["protection_state"] = "CHANGE_PROVEN"
                item["verification_state"] = "CHANGE_PROVEN"
            else:
                item["storage_kind"] = None
                item["protection_state"] = "UNKNOWN"
                item["verification_state"] = "NOT_VERIFIED"
            item["agent_ref"] = agent_by_workspace.get(item.get("workspace_id") or "")
        projection = _base(
            "changes",
            "AVAILABLE" if items else "EMPTY",
            "R4_CHANGES_AVAILABLE" if items else "R4_STATE_EMPTY",
            items,
        )
        projection.update(
            {
                "page_size": len(items),
                "next_cursor": next_cursor,
                "include_process_activity": include_process_activity,
                "workspace_id_filter": workspace_id,
                "checkpoint_id_filter": checkpoint_id,
            }
        )
        return projection

    def evidence(self, event_id: str) -> dict[str, Any]:
        if not isinstance(event_id, str) or not _SAFE_EVENT_ID.fullmatch(event_id):
            return {
                "schema_version": "r4-product-evidence-1",
                "status": "NOT_FOUND",
                "reason_code": "EVIDENCE_EVENT_NOT_FOUND",
                "event_id": event_id if isinstance(event_id, str) else "INVALID",
            }
        connection = self._verified_connection("evidence")
        if isinstance(connection, dict):
            return {
                "schema_version": "r4-product-evidence-1",
                "status": "DEGRADED",
                "reason_code": connection["reason_code"],
                "event_id": event_id,
            }
        row = connection.execute(
            """SELECT event_id, event_type, observed_at, recorded_at, source,
                      subject_ref, result, checkpoint_id, transaction_id,
                      evidence_refs_json, payload_safe_json, curr_hash,
                      execution_domain_id
               FROM evidence_ledger_events WHERE event_id = ?""",
            (event_id,),
        ).fetchone()
        if row is None:
            return {
                "schema_version": "r4-product-evidence-1",
                "status": "NOT_FOUND",
                "reason_code": "EVIDENCE_EVENT_NOT_FOUND",
                "event_id": event_id,
            }
        try:
            refs = json.loads(row[9])
            payload = json.loads(row[10])
        except (json.JSONDecodeError, TypeError):
            return {
                "schema_version": "r4-product-evidence-1",
                "status": "DEGRADED",
                "reason_code": "EVIDENCE_EVENT_INVALID",
                "event_id": event_id,
            }
        detail = _activity_detail(payload if isinstance(payload, dict) else {})
        sanitized_detail: dict[str, Any] = {
            "affected_objects": detail["affected_objects"]
        }
        if detail["verification"] is not None:
            sanitized_detail["verification"] = detail["verification"]
        for key in (
            "attribution",
            "change_kind",
            "coverage_after",
            "coverage_before",
            "recovery_disposition",
            "workspace_id",
        ):
            if detail[key] is not None:
                sanitized_detail[key] = detail[key]
        reason_code = (
            _atom(payload.get("reason_code")) if isinstance(payload, dict) else None
        )
        return {
            "schema_version": "r4-product-evidence-1",
            "status": "AVAILABLE",
            "reason_code": reason_code or row[1],
            "event_id": row[0],
            "event_type": row[1],
            "observed_at": row[2],
            "recorded_at": row[3],
            "source": row[4],
            "subject": row[5] if _SAFE_EVENT_ID.fullmatch(row[5] or "") else None,
            "result": row[6],
            "verification_summary": detail["verification"],
            "checkpoint_id": row[7],
            "change_id": row[8],
            "chain_ref": row[11],
            "execution_domain_id": row[12],
            "sanitized_detail": sanitized_detail,
            "related_evidence_refs": sorted(
                item for item in refs if isinstance(item, str) and item
            ),
        }

    def recovery(self) -> dict[str, Any]:
        connection = self._verified_connection("recovery")
        if isinstance(connection, dict):
            return _with_empty_recovery(connection)
        items: list[dict[str, Any]] = []
        for checkpoint in self._database.list_checkpoints(limit=100):
            checkpoint_id = str(checkpoint["id"])
            artifact, load_reason = self._snapshots.load_recovery_v3_with_status(
                checkpoint["snapshot_path"]
            )
            if artifact is None:
                items.append(_recovery_failure_item(checkpoint_id, load_reason))
                continue
            manifest = artifact.get("manifest")
            workspace = artifact.get("workspace")
            domains = (
                {
                    entry.get("domain")
                    for entry in manifest
                    if isinstance(entry, dict) and _atom(entry.get("domain"))
                }
                if isinstance(manifest, list)
                else set()
            )
            if isinstance(workspace, dict) and _atom(
                workspace.get("execution_domain_id")
            ):
                domains.add(workspace["execution_domain_id"])
            if len(domains) != 1:
                items.append(
                    _recovery_failure_item(checkpoint_id, "RECOVERY_DOMAIN_UNKNOWN")
                )
                continue
            domain = domains.pop()
            valid, reason_code, digest = validate_snapshot_v3(
                artifact, expected_domain=domain
            )
            if not valid or digest != checkpoint["hash_sha256"]:
                items.append(_recovery_failure_item(checkpoint_id, reason_code, domain))
                continue
            target_refs = tuple(
                entry["logical_path"]
                for entry in artifact["manifest"]
                if entry["classification"] == "restorable"
            )
            facts = RecoveryCoverageService(
                self._database, self._snapshots
            ).compute_with_connection(
                connection,
                checkpoint_id=checkpoint_id,
                target_refs=target_refs,
                execution_domain_id=domain,
            )
            actual_restore = _actual_restore_summary(connection, checkpoint_id, digest)
            workspace_summary = _workspace_recovery_summary(workspace)
            if (
                workspace_summary.get("workspace_id") is not None
                and workspace_summary.get("storage_kind") is None
            ):
                try:
                    authority = WorkspaceScopeService(self._database).resolve_authority(
                        workspace_summary["workspace_id"]
                    )
                except WorkspaceScopeError:
                    authority = None
                if authority is not None and authority.authority_state == "BOUND":
                    workspace_summary.update(
                        {
                            "storage_kind": authority.storage_kind,
                            "storage_resource_identity": authority.storage_resource_identity,
                            "logical_root": authority.logical_root,
                            "scope_kind": _scope_kind_for_storage(
                                authority.storage_kind
                            ),
                        }
                    )
            item_eligible, item_reason = self._recovery_action_eligibility(
                workspace_id=(
                    workspace_summary["workspace_id"]
                    if workspace_summary["scope_kind"]
                    in {"HOST_WORKSPACE", "DOCKER_NAMED_VOLUME"}
                    else None
                )
            )
            related_changes = [
                item
                for item in _verified_activities(
                    connection,
                    checkpoint_id=checkpoint_id,
                    include_process_activity=False,
                    limit=100,
                )
                if item["category"] == "CHANGE"
            ]
            related_change_count = connection.execute(
                """SELECT COUNT(*) FROM evidence_ledger_events
                   WHERE checkpoint_id = ? AND event_type = 'OBSERVED_CHANGE'""",
                (checkpoint_id,),
            ).fetchone()[0]
            recovery_item = {
                "checkpoint_id": checkpoint_id,
                "created_at": checkpoint["created_at"],
                "execution_domain_id": domain,
                "manifest_integrity": "VERIFIED",
                **facts.safe_summary(),
                "evidence_refs": list(facts.evidence_refs),
                **actual_restore,
                **workspace_summary,
                "action_eligible": item_eligible,
                "eligibility_reason_code": item_reason,
                "related_change_count": related_change_count,
                "related_change_event_ids": [
                    item["event_id"] for item in related_changes
                ],
                "related_changes_truncated": related_change_count
                > len(related_changes),
            }
            recovery_item.update(
                _recovery_item_protection_fields(
                    recovery_item,
                    latest_verified_change=(
                        related_changes[0] if related_changes else None
                    ),
                )
            )
            items.append(recovery_item)
        projection = _base(
            "recovery",
            "AVAILABLE" if items else "EMPTY",
            "R4_RECOVERY_AVAILABLE" if items else "R4_STATE_EMPTY",
            items,
        )
        if not items:
            eligible, reason_code = self._recovery_action_eligibility()
            scope_kind, workspace_id, capability_supported = (
                self._current_workspace_protection_summary()
            )
            return _with_empty_recovery(
                projection,
                action_eligible=eligible,
                eligibility_reason_code=reason_code,
                scope_kind=scope_kind,
                workspace_id=workspace_id,
                capability_supported=capability_supported,
            )
        latest = items[0]
        action_eligible = latest["action_eligible"]
        eligibility_reason_code = latest["eligibility_reason_code"]
        for key in (
            "recovery_level",
            "r1_verified",
            "r2_verified",
            "r3_verified",
            "test_restore_status",
            "trusted_baseline_status",
            "trusted_baseline_id",
        ):
            projection[key] = latest[key]
        projection.update(
            {
                "checkpoint_count": len(items),
                "latest_checkpoint": latest,
                "actual_restore_status": latest["actual_restore_status"],
                "recovery_verified": latest["actual_restore_status"] == "VERIFIED",
                "verified_at": latest["actual_restore_verified_at"],
                "scope_kind": latest["scope_kind"],
                "workspace_id": latest["workspace_id"],
                "storage_kind": latest.get("storage_kind"),
                "protection_state": latest.get("protection_state", "UNKNOWN"),
                "verification_state": latest.get(
                    "verification_state", "NOT_VERIFIED"
                ),
                "coverage": latest["coverage"],
                "capability_supported": True,
                "action_eligible": action_eligible,
                "eligibility_reason_code": eligibility_reason_code,
                "capabilities": {
                    "create_checkpoint": True,
                    "test_restore": True,
                    "restore": True,
                },
                "limitations": (
                    [
                        "NOT_WHOLE_HOST_BACKUP",
                        "COVERAGE_BASED_WORKSPACE_PROTECTION",
                        "EXPLICIT_RESTORE_CONFIRMATION_REQUIRED",
                    ]
                    if latest["scope_kind"]
                    in {"HOST_WORKSPACE", "DOCKER_NAMED_VOLUME"}
                    else [
                        "PRODUCT_CONFIG_TARGET_ONLY",
                        "EXPLICIT_RESTORE_CONFIRMATION_REQUIRED",
                    ]
                ),
            }
        )
        return projection

    def _recovery_action_eligibility(
        self,
        workspace_id: str | None = None,
    ) -> tuple[bool, str | None]:
        try:
            service = WorkspaceScopeService(self._database)
            if workspace_id is not None:
                authority = service.resolve_authority(workspace_id)
                return _authority_action_eligibility(authority)
            authorities = service.authority_results()
            latest = service.latest_result()
        except WorkspaceScopeError:
            return False, "WORKSPACE_SCOPE_BINDING_INVALID"
        if len(authorities) > 1:
            return False, "WORKSPACE_SCOPE_AMBIGUOUS"
        if len(authorities) == 1:
            return _authority_action_eligibility(authorities[0])
        if latest is None:
            return False, "NO_VERIFIED_WORKSPACE"
        if latest.status != "BOUND":
            return False, latest.reason_code
        return False, "WORKSPACE_SCOPE_BINDING_INVALID"

    def _current_workspace_protection_summary(
        self,
    ) -> tuple[str, str | None, bool]:
        try:
            authorities = WorkspaceScopeService(self._database).authority_results()
        except WorkspaceScopeError:
            return "UNKNOWN", None, False
        if len(authorities) != 1 or authorities[0].authority_state != "BOUND":
            # Product support is independent from current workspace action
            # eligibility. No/ambiguous authority remains ineligible, but it
            # does not erase the packaged recovery capability.
            return "UNKNOWN", None, True
        authority = authorities[0]
        return (
            _scope_kind_for_storage(authority.storage_kind),
            authority.workspace_id,
            authority.protection_capability == "SUPPORTED",
        )

    def _verified_connection(self, view: str) -> sqlite3.Connection | dict[str, Any]:
        connection = self._database._conn
        if connection is None:
            return self.unavailable(view)
        if verify_ledger(connection):
            return _base(view, "DEGRADED", "R4_LEDGER_INVALID", [])
        return connection


def _recovery_failure_item(
    checkpoint_id: str, reason_code: str, domain: str | None = None
) -> dict[str, Any]:
    return {
        "checkpoint_id": checkpoint_id,
        "execution_domain_id": domain,
        "status": "EVIDENCE_INSUFFICIENT",
        "reason_code": reason_code,
        "requested_targets": 0,
        "authorized_snapshot_targets": 0,
        "intact_manifest_blob_targets": 0,
        "authorized_snapshot_coverage": None,
        "manifest_blob_coverage": None,
        "test_restore_verified_targets": None,
        "test_restore_status": "NOT_RUN_P6",
        "recovery_level": "R0",
        "r1_verified": False,
        "r2_verified": False,
        "r3_verified": False,
        "trusted_baseline_status": "NONE",
        "trusted_baseline_id": None,
        "evidence_refs": [],
        "action_eligible": False,
        "eligibility_reason_code": reason_code,
        "actual_restore_status": "NOT_RUN",
        "actual_restore_verified_at": None,
        "actual_restore_evidence_refs": [],
        "scope_kind": "UNKNOWN",
        "workspace_id": None,
        "storage_kind": None,
        "protection_state": "UNKNOWN",
        "verification_state": "NOT_VERIFIED",
        "related_change_count": 0,
        "related_change_event_ids": [],
        "related_changes_truncated": False,
        "latest_verified_change": None,
        "recovery_disposition": "UNKNOWN",
        "coverage": None,
    }


def _with_empty_recovery(
    projection: dict[str, Any],
    *,
    action_eligible: bool = False,
    eligibility_reason_code: str | None = "NO_VERIFIED_WORKSPACE",
    scope_kind: str = "UNKNOWN",
    workspace_id: str | None = None,
    capability_supported: bool = True,
) -> dict[str, Any]:
    return {
        **projection,
        "recovery_level": "R0",
        "r1_verified": False,
        "r2_verified": False,
        "r3_verified": False,
        "test_restore_status": "NOT_RUN_P6",
        "trusted_baseline_status": "NONE",
        "trusted_baseline_id": None,
        "checkpoint_count": 0,
        "latest_checkpoint": None,
        "actual_restore_status": "NOT_RUN",
        "recovery_verified": False,
        "verified_at": None,
        "scope_kind": scope_kind,
        "workspace_id": workspace_id,
        "storage_kind": None,
        "protection_state": (
            "BOUND_NO_CHECKPOINT" if workspace_id is not None else "UNBOUND"
        ),
        "verification_state": "NOT_VERIFIED",
        "coverage": None,
        "capability_supported": capability_supported,
        "action_eligible": action_eligible,
        "eligibility_reason_code": eligibility_reason_code,
        "capabilities": {
            "create_checkpoint": True,
            "test_restore": True,
            "restore": True,
        },
        "limitations": [
            "PRODUCT_CONFIG_TARGET_ONLY",
            "EXPLICIT_RESTORE_CONFIRMATION_REQUIRED",
        ],
    }


def _actual_restore_summary(
    connection: sqlite3.Connection,
    checkpoint_id: str,
    manifest_digest: str,
) -> dict[str, Any]:
    rows = connection.execute(
        """SELECT event_id, event_type, result, recorded_at, payload_safe_json
           FROM evidence_ledger_events
           WHERE checkpoint_id = ?
             AND event_type IN ('FILE_RESTORED', 'VALIDATOR_PASSED')
           ORDER BY sequence""",
        (checkpoint_id,),
    ).fetchall()
    matched: dict[str, tuple[str, str]] = {}
    for event_id, event_type, result, recorded_at, payload_json in rows:
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if (
            result == "AVAILABLE"
            and payload.get("manifest_digest") == manifest_digest
            and payload.get("reason_code")
            in {"RECOVERY_RESTORED_AND_VERIFIED", "WORKSPACE_RESTORED_AND_VERIFIED"}
            and isinstance(payload.get("file_count"), int)
            and payload["file_count"] > 0
        ):
            matched[event_type] = (event_id, recorded_at)
    verified = set(matched) == {"FILE_RESTORED", "VALIDATOR_PASSED"}
    refs = sorted(value[0] for value in matched.values()) if verified else []
    verified_at = max(value[1] for value in matched.values()) if verified else None
    return {
        "actual_restore_status": "VERIFIED" if verified else "NOT_RUN",
        "actual_restore_verified_at": verified_at,
        "actual_restore_evidence_refs": refs,
    }


def _workspace_recovery_summary(workspace: object) -> dict[str, Any]:
    if not isinstance(workspace, dict):
        return {
            "scope_kind": "PRODUCT_CONFIG",
            "workspace_id": None,
            "coverage": None,
        }
    coverage = workspace.get("coverage")
    counts = workspace.get("coverage_counts")
    reason_counts: dict[str, int] = {}
    if isinstance(coverage, list):
        for entry in coverage:
            reason = _atom(entry.get("reason_code")) if isinstance(entry, dict) else None
            if reason is not None:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
    safe_counts = (
        {
            key: counts.get(key)
            for key in ("restorable", "audit_only", "excluded", "unreachable")
        }
        if isinstance(counts, dict)
        else None
    )
    return {
        "scope_kind": _scope_kind_for_storage(_atom(workspace.get("storage_kind"))),
        "workspace_id": _atom(workspace.get("workspace_id")),
        "storage_kind": _atom(workspace.get("storage_kind")),
        "storage_resource_identity": _atom(
            workspace.get("storage_resource_identity")
        ),
        "logical_root": _atom(workspace.get("logical_root")),
        "coverage": {
            "counts": safe_counts,
            "reason_counts": dict(sorted(reason_counts.items())),
            "scan_complete": workspace.get("scan_complete") is True,
            "scan_reason_code": _atom(workspace.get("scan_reason_code")),
        },
    }


def _scope_kind_for_storage(storage_kind: str | None) -> str:
    return (
        "DOCKER_NAMED_VOLUME"
        if storage_kind == "DOCKER_NAMED_VOLUME"
        else "HOST_WORKSPACE"
    )


def _authority_action_eligibility(authority: Any) -> tuple[bool, str | None]:
    if authority.authority_state != "BOUND":
        return False, authority.reason_code
    if authority.protection_capability != "SUPPORTED":
        return False, (
            authority.protection_reason_code
            or "WORKSPACE_STORAGE_BACKEND_UNSUPPORTED"
        )
    return True, None
