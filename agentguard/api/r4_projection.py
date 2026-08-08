"""Read-only, fail-closed R4 API projections from durable server state."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from agentguard.evidence.ledger import verify_ledger
from agentguard.recovery.coverage import RecoveryCoverageService
from agentguard.recovery.manifest import validate_snapshot_v3
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore

_SAFE_ATOM = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_RECOVERY_FIELDS = {
    "status", "reason_code", "requested_targets", "authorized_snapshot_targets",
    "intact_manifest_blob_targets", "authorized_snapshot_coverage",
    "manifest_blob_coverage", "test_restore_verified_targets",
    "test_restore_status", "recovery_level", "r1_verified", "r2_verified",
    "r3_verified", "trusted_baseline_status", "trusted_baseline_id",
}


def _atom(value: object) -> str | None:
    return value if isinstance(value, str) and _SAFE_ATOM.fullmatch(value) else None


def _json_object(value: object) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value) if isinstance(value, str) else None
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _base(view: str, status: str, reason_code: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    refs = sorted({ref for item in items for ref in item.get("evidence_refs", [])})
    return {
        "schema_version": "r4-p8-1",
        "view": view,
        "status": status,
        "reason_code": reason_code,
        "evidence_refs": refs,
        "items": items,
    }


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
            """SELECT event_id, event_type, result, execution_domain_id, payload_safe_json
               FROM evidence_ledger_events
               WHERE event_type IN ('RUNTIME_DETECTED', 'PROBE_UNREACHABLE')
               ORDER BY sequence"""
        ).fetchall()
        items: list[dict[str, Any]] = []
        for event_id, event_type, result, event_domain, payload_json in rows:
            payload = _json_object(payload_json)
            value = payload.get("value") if payload else None
            if not isinstance(value, dict):
                continue
            domain = _atom(event_domain) or _atom(value.get("execution_domain_id"))
            available = result.casefold() == "available"
            if event_type == "RUNTIME_DETECTED" and payload.get("fact_type") == "runtime.metadata":
                runtime_type = _atom(value.get("runtime_kind"))
                if runtime_type is None:
                    continue
                capabilities = value.get("capabilities", [])
                safe_capabilities = sorted({item for item in capabilities if _atom(item)}) if isinstance(capabilities, list) else []
                items.append({
                    "runtime_type": runtime_type,
                    "execution_domain_id": domain,
                    "availability": "AVAILABLE" if available else "UNKNOWN",
                    "capabilities": safe_capabilities,
                    "reason_code": "RUNTIME_DETECTED" if available else "RUNTIME_STATE_UNCERTAIN",
                    "uncertainty": not available or domain is None,
                    "evidence_refs": [event_id],
                })
            elif event_type == "PROBE_UNREACHABLE" and payload.get("fact_type") == "probe.unreachable":
                items.append({
                    "runtime_type": None,
                    "execution_domain_id": domain,
                    "availability": "UNREACHABLE",
                    "capabilities": [],
                    "reason_code": "DISCOVERY_PROBE_UNREACHABLE",
                    "uncertainty": True,
                    "evidence_refs": [event_id],
                })
        items.sort(key=lambda item: (item["execution_domain_id"] or "", item["runtime_type"] or "", item["evidence_refs"][0]))
        return _base(
            "runtime", "AVAILABLE" if items else "EMPTY",
            "R4_RUNTIME_AVAILABLE" if items else "R4_STATE_EMPTY", items,
        )

    def agents(self) -> dict[str, Any]:
        connection = self._verified_connection("agents")
        if isinstance(connection, dict):
            return connection
        rows = connection.execute(
            """SELECT event_id, result, execution_domain_id, payload_safe_json
               FROM evidence_ledger_events WHERE event_type = 'AGENT_DETECTED'
               ORDER BY sequence"""
        ).fetchall()
        items: list[dict[str, Any]] = []
        for event_id, result, event_domain, payload_json in rows:
            payload = _json_object(payload_json)
            value = payload.get("value") if payload else None
            if not isinstance(value, dict) or payload.get("fact_type") != "agent.metadata":
                continue
            identity = _atom(value.get("agent_kind"))
            if identity is None:
                continue
            confidence = payload.get("confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
                confidence = 0.0
            available = result.casefold() == "available"
            domain = _atom(event_domain) or _atom(value.get("execution_domain_id"))
            items.append({
                "detected_identity": identity,
                "role": _atom(value.get("role")) or "detected",
                "lifecycle": "DETECTED" if available else "UNKNOWN",
                "confidence": confidence,
                "execution_domain_id": domain,
                "workspace": {"status": "UNKNOWN", "binding_ref": None},
                "reason_code": "AGENT_DETECTED" if available else "AGENT_STATE_UNCERTAIN",
                "uncertainty": not available or domain is None,
                "evidence_refs": [event_id],
            })
        items.sort(key=lambda item: (item["execution_domain_id"] or "", item["detected_identity"], item["evidence_refs"][0]))
        return _base(
            "agents", "AVAILABLE" if items else "EMPTY",
            "R4_AGENTS_AVAILABLE" if items else "R4_STATE_EMPTY", items,
        )

    def supervision(self) -> dict[str, Any]:
        connection = self._verified_connection("supervision")
        if isinstance(connection, dict):
            return connection
        sessions = connection.execute(
            """SELECT supervision_session_id, status, decision,
                      requires_checkpoint, requires_manual_approval
               FROM supervision_sessions ORDER BY created_at, supervision_session_id"""
        ).fetchall()
        items: list[dict[str, Any]] = []
        for session_id, status, decision, requires_checkpoint, requires_manual in sessions:
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
                if event_type == "POLICY_EVALUATED" and isinstance(payload.get("recovery_facts"), dict):
                    recovery_facts = {
                        key: payload["recovery_facts"].get(key)
                        for key in sorted(_RECOVERY_FIELDS)
                    }
                if event_type == "AI_ASSESSED":
                    ai_assessment = {
                        "decision": _atom(payload.get("decision")) or _atom(result),
                        "severity": _atom(payload.get("severity")),
                    }
            items.append({
                "supervision_session_id": session_id,
                "status": status,
                "policy_decision": decision,
                "manual_approval": status == "APPROVED",
                "requires_manual_approval": bool(requires_manual),
                "requires_checkpoint": bool(requires_checkpoint),
                "ai_assessment": ai_assessment,
                "recovery_facts": recovery_facts,
                "evidence_refs": sorted(set(refs)),
            })
        return _base(
            "supervision", "AVAILABLE" if items else "EMPTY",
            "R4_SUPERVISION_AVAILABLE" if items else "R4_STATE_EMPTY", items,
        )

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
            domains = {
                entry.get("domain") for entry in manifest
                if isinstance(entry, dict) and _atom(entry.get("domain"))
            } if isinstance(manifest, list) else set()
            if len(domains) != 1:
                items.append(_recovery_failure_item(checkpoint_id, "RECOVERY_DOMAIN_UNKNOWN"))
                continue
            domain = domains.pop()
            valid, reason_code, digest = validate_snapshot_v3(artifact, expected_domain=domain)
            if not valid or digest != checkpoint["hash_sha256"]:
                items.append(_recovery_failure_item(checkpoint_id, reason_code, domain))
                continue
            target_refs = tuple(
                entry["logical_path"] for entry in artifact["manifest"]
                if entry["classification"] == "restorable"
            )
            facts = RecoveryCoverageService(self._database, self._snapshots).compute_with_connection(
                connection,
                checkpoint_id=checkpoint_id,
                target_refs=target_refs,
                execution_domain_id=domain,
            )
            items.append({
                "checkpoint_id": checkpoint_id,
                "execution_domain_id": domain,
                **facts.safe_summary(),
                "evidence_refs": list(facts.evidence_refs),
            })
        projection = _base(
            "recovery", "AVAILABLE" if items else "EMPTY",
            "R4_RECOVERY_AVAILABLE" if items else "R4_STATE_EMPTY", items,
        )
        if not items:
            return _with_empty_recovery(projection)
        latest = items[0]
        for key in (
            "recovery_level", "r1_verified", "r2_verified", "r3_verified",
            "test_restore_status", "trusted_baseline_status", "trusted_baseline_id",
        ):
            projection[key] = latest[key]
        return projection

    def _verified_connection(self, view: str) -> sqlite3.Connection | dict[str, Any]:
        connection = self._database._conn
        if connection is None:
            return self.unavailable(view)
        if verify_ledger(connection):
            return _base(view, "DEGRADED", "R4_LEDGER_INVALID", [])
        return connection


def _recovery_failure_item(checkpoint_id: str, reason_code: str, domain: str | None = None) -> dict[str, Any]:
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
    }
