"""Read-only, fail-closed R4 API projections from durable server state."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from agentguard.evidence.discovery_adapter import resolve_verified_workspace_binding
from agentguard.evidence.ledger import verify_ledger
from agentguard.recovery.coverage import RecoveryCoverageService
from agentguard.recovery.manifest import validate_snapshot_v3
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore
from agentguard.supervision.service import SupervisionService

_SAFE_ATOM = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_SAFE_EVENT_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
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
        "TRUSTED_BASELINE_CREATED",
        "TRUSTED_BASELINE_RETIRED",
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


def _atom(value: object) -> str | None:
    return value if isinstance(value, str) and _SAFE_ATOM.fullmatch(value) else None


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
    if isinstance(multiple, list):
        affected.extend(
            item
            for item in multiple
            if isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item)
        )
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
        "change_kind": _atom(payload.get("change_kind")),
        "coverage_after": _atom(payload.get("coverage_after")),
        "coverage_before": _atom(payload.get("coverage_before")),
        "recovery_disposition": _atom(payload.get("recovery_disposition")),
        "workspace_id": _atom(payload.get("workspace_id")),
    }


def _verified_activities(
    connection: sqlite3.Connection,
    *,
    session_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    where = "event_type IN ({})".format(",".join("?" for _item in _ACTIVITY_TYPES))
    parameters: list[object] = sorted(_ACTIVITY_TYPES)
    if session_id is not None:
        where += " AND supervision_session_id = ?"
        parameters.append(session_id)
    parameters.append(limit)
    rows = connection.execute(
        f"""SELECT event_id, recorded_at, observed_at, event_family, event_type,
                   source, result, supervision_session_id, transaction_id,
                   checkpoint_id, subject_ref, evidence_refs_json, payload_safe_json,
                   execution_domain_id
            FROM evidence_ledger_events
            WHERE {where}
            ORDER BY sequence DESC LIMIT ?""",
        tuple(parameters),
    ).fetchall()
    activities: list[dict[str, Any]] = []
    for row in rows:
        try:
            evidence_refs = json.loads(row[11])
            payload = json.loads(row[12])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(evidence_refs, list) or not isinstance(payload, dict):
            continue
        detail = _activity_detail(payload)
        reason_code = _atom(payload.get("reason_code"))
        activities.append(
            {
                "event_id": row[0],
                "timestamp": row[2] or row[1],
                "observed_at": row[2],
                "recorded_at": row[1],
                "actor": row[5],
                "subject": row[10] if _SAFE_EVENT_ID.fullmatch(row[10] or "") else None,
                "type": row[4],
                "result": row[6],
                "affected_objects": detail["affected_objects"],
                "checkpoint_id": row[9],
                "supervision_session_id": row[7],
                "change_id": row[8],
                "policy_summary": (row[6] if row[4] == "POLICY_EVALUATED" else None),
                "approval_summary": (
                    row[6] if row[4] in {"USER_APPROVED", "USER_REJECTED"} else None
                ),
                "verification_summary": detail["verification"],
                "reason_code": reason_code or row[4],
                "execution_domain_id": row[13],
                "attribution": detail["attribution"],
                "change_kind": detail["change_kind"],
                "coverage_after": detail["coverage_after"],
                "coverage_before": detail["coverage_before"],
                "recovery_disposition": detail["recovery_disposition"],
                "workspace_id": detail["workspace_id"],
                "evidence_refs": sorted(
                    {
                        row[0],
                        *(
                            item
                            for item in evidence_refs
                            if isinstance(item, str) and item
                        ),
                    }
                ),
            }
        )
    return activities


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
            workspace = resolve_verified_workspace_binding(
                connection,
                agent_event_id=event_id,
            )
            instance_label = _instance_label(value.get("instance_label"))
            items.append(
                {
                    "detected_identity": instance_label or identity,
                    "instance_label": instance_label,
                    "role": _atom(value.get("role")) or "detected",
                    "lifecycle": lifecycle if available else "UNKNOWN",
                    "confidence": confidence,
                    "execution_domain_id": domain,
                    "workspace": workspace,
                    "reason_code": "AGENT_DETECTED"
                    if available
                    else "AGENT_STATE_UNCERTAIN",
                    "uncertainty": not available
                    or domain is None
                    or workspace["status"] != "BOUND",
                    "observed_at": observed_at,
                    "evidence_refs": [
                        event_id,
                        *(
                            [workspace["binding_ref"]]
                            if workspace["binding_ref"] is not None
                            else []
                        ),
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
        complete = bool(items) and all(not item["uncertainty"] for item in items)
        projection = _base(
            "agents",
            "AVAILABLE"
            if complete
            else "DEGRADED"
            if items or failure_refs
            else "EMPTY",
            "R4_AGENTS_AVAILABLE"
            if complete
            else "R4_AGENT_DISCOVERY_UNREACHABLE"
            if failure_refs
            else "R4_WORKSPACE_BINDING_INCOMPLETE"
            if items
            else "R4_STATE_EMPTY",
            items,
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
                      created_at, updated_at
               FROM supervision_sessions ORDER BY created_at, supervision_session_id"""
        ).fetchall()
        items: list[dict[str, Any]] = []
        supervision = SupervisionService(self._database)
        for (
            session_id,
            status,
            decision,
            requires_checkpoint,
            requires_manual,
            created_at,
            updated_at,
        ) in sessions:
            events = connection.execute(
                """SELECT event_id, event_type, result, payload_safe_json
                   FROM evidence_ledger_events
                   WHERE supervision_session_id = ? ORDER BY sequence""",
                (session_id,),
            ).fetchall()
            recovery_facts = None
            ai_assessment = None
            refs: list[str] = []
            for event_id, event_type, result, payload_json in events:
                refs.append(event_id)
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
        projection = _base(
            "supervision",
            "AVAILABLE" if items else "EMPTY",
            "R4_SUPERVISION_AVAILABLE" if items else "R4_STATE_EMPTY",
            items,
        )
        agent_projection = self.agents()
        projection["observed_agents"] = agent_projection.get("items", [])
        projection["recent_verified_activities"] = _verified_activities(
            connection,
            limit=50,
        )
        return projection

    def changes(self) -> dict[str, Any]:
        connection = self._verified_connection("changes")
        if isinstance(connection, dict):
            return connection
        items = _verified_activities(connection, limit=100)
        return _base(
            "changes",
            "AVAILABLE" if items else "EMPTY",
            "R4_CHANGES_AVAILABLE" if items else "R4_STATE_EMPTY",
            items,
        )

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
            items.append(
                {
                    "checkpoint_id": checkpoint_id,
                    "created_at": checkpoint["created_at"],
                    "execution_domain_id": domain,
                    "manifest_integrity": "VERIFIED",
                    **facts.safe_summary(),
                    "evidence_refs": list(facts.evidence_refs),
                    **actual_restore,
                    **workspace_summary,
                }
            )
        projection = _base(
            "recovery",
            "AVAILABLE" if items else "EMPTY",
            "R4_RECOVERY_AVAILABLE" if items else "R4_STATE_EMPTY",
            items,
        )
        if not items:
            return _with_empty_recovery(projection)
        latest = items[0]
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
                "coverage": latest["coverage"],
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
                    if latest["scope_kind"] == "HOST_WORKSPACE"
                    else [
                        "PRODUCT_CONFIG_TARGET_ONLY",
                        "EXPLICIT_RESTORE_CONFIRMATION_REQUIRED",
                    ]
                ),
            }
        )
        return projection

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
        "actual_restore_status": "NOT_RUN",
        "actual_restore_verified_at": None,
        "actual_restore_evidence_refs": [],
        "scope_kind": "UNKNOWN",
        "workspace_id": None,
        "coverage": None,
    }


def _with_empty_recovery(projection: dict[str, Any]) -> dict[str, Any]:
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
        "scope_kind": "UNKNOWN",
        "workspace_id": None,
        "coverage": None,
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
        "scope_kind": "HOST_WORKSPACE",
        "workspace_id": _atom(workspace.get("workspace_id")),
        "coverage": {
            "counts": safe_counts,
            "reason_counts": dict(sorted(reason_counts.items())),
            "scan_complete": workspace.get("scan_complete") is True,
            "scan_reason_code": _atom(workspace.get("scan_reason_code")),
        },
    }
