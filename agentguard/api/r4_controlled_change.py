"""Narrow production composition for the R4 MVP controlled configuration change."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from agentguard.ai.supervisor import AISupervisor, AssessmentProvider
from agentguard.discovery import CapabilityStatus, DiscoverySnapshot
from agentguard.discovery.domains import SelfRuntimeAdapter
from agentguard.discovery.product import ProductDiscoveryService
from agentguard.evidence.canonical import canonical_json
from agentguard.evidence.discovery_adapter import record_discovery_snapshot
from agentguard.evidence.ledger import verify_ledger
from agentguard.evidence.product_target import record_product_target_binding
from agentguard.policy.models import Decision, PolicyInput
from agentguard.recovery.contracts import RecoveryOperation, RecoveryRequest
from agentguard.recovery.policy import RestorePolicy
from agentguard.recovery.service import RecoveryService
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore
from agentguard.supervision.service import SupervisionService

SCHEMA_VERSION = "r4-p9-controlled-change-1"
_MAX_CONTENT_BYTES = 1_048_576


class ControlledChangeError(RuntimeError):
    """Stable API-facing failure from the bounded production composition."""

    def __init__(self, reason_code: str, status_code: int = 409) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code


def controlled_change_failure(
    reason_code: str,
    *,
    session_id: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "supervision_session_id": session_id,
        "status": "UNCHANGED",
        "reason_code": reason_code,
        "changed": False,
    }


def prepare_controlled_change(
    database: StateDB,
    snapshots: SnapshotStore,
    *,
    base_dir: Path,
    content: bytes,
    assessment_provider: AssessmentProvider | None,
    discovery_service=None,
) -> dict[str, object]:
    """Discover authority and create one write-before REVIEW session."""
    target = _server_target(base_dir)
    _validate_content(content)
    snapshot, domain_id = _production_snapshot(discovery_service)
    recorded_at = datetime.now(UTC)
    try:
        receipts = record_discovery_snapshot(
            database,
            snapshot,
            recorded_at=recorded_at,
        )
        target_binding = record_product_target_binding(
            database,
            snapshot_id=snapshot.snapshot_id,
            execution_domain_id=domain_id,
            target_refs=(str(target),),
            recorded_at=recorded_at,
        )
    except (OSError, RuntimeError, sqlite3.DatabaseError, ValueError):
        raise ControlledChangeError("CONTROLLED_CHANGE_DISCOVERY_UNAVAILABLE", 503) from None

    intent = _intent(target, content)
    policy_input = PolicyInput(
        intent_kind="change",
        effect_kind="provider_config_change",
        target_refs=(str(target),),
        execution_domain_id=domain_id,
        declared_scope=(str(target),),
        requested_capabilities=(),
        network_effect=False,
        privilege_effect=False,
        destructive_effect=False,
        secret_access=False,
        evidence_refs=(
            *[receipt.event_id for receipt in receipts],
            target_binding.event_id,
        ),
    )
    try:
        supervision = SupervisionService(
            database,
            snapshots=snapshots,
        )
        session, decision, _facts = supervision.create_authoritative(
            intent,
            policy_input,
            checkpoint_id=None,
            product_target_binding_ref=target_binding.event_id,
        )
    except (OSError, RuntimeError, sqlite3.DatabaseError, ValueError):
        raise ControlledChangeError("CONTROLLED_CHANGE_AUTHORITY_UNAVAILABLE", 503) from None

    ai_advisory = "BYPASSED"
    if decision.decision is Decision.REVIEW:
        try:
            assessment = AISupervisor(
                database,
                assessment_provider if assessment_provider is not None else object(),
            ).assess(session.supervision_session_id)
        except (OSError, RuntimeError, sqlite3.DatabaseError, ValueError):
            ai_advisory = "UNAVAILABLE"
        else:
            ai_advisory = (
                "UNAVAILABLE"
                if "AI_ASSESSMENT_UNAVAILABLE" in assessment.uncertainties
                else assessment.decision
            )

    reason_code = {
        Decision.REVIEW: "CONTROLLED_CHANGE_PREPARED",
        Decision.ALLOW: "CONTROLLED_CHANGE_PREPARED",
        Decision.BLOCK: "CONTROLLED_CHANGE_POLICY_BLOCKED",
        Decision.UNKNOWN: "CONTROLLED_CHANGE_POLICY_UNKNOWN",
    }[decision.decision]
    return {
        "schema_version": SCHEMA_VERSION,
        "supervision_session_id": session.supervision_session_id,
        "status": session.status,
        "decision": decision.decision.value,
        "reason_code": reason_code,
        "requires_manual_approval": decision.requires_manual_approval,
        "requires_checkpoint": decision.requires_checkpoint,
        "ai_advisory": ai_advisory,
        "action_ref": supervision.action_ref(session.supervision_session_id),
    }


def apply_controlled_change(
    database: StateDB,
    snapshots: SnapshotStore,
    *,
    base_dir: Path,
    session_id: str,
    content: bytes,
) -> dict[str, object]:
    """Resolve server-owned authority, checkpoint, activate, and apply once."""
    target = _server_target(base_dir)
    _validate_content(content)
    connection = database._conn
    if connection is None:
        raise ControlledChangeError("CONTROLLED_CHANGE_AUTHORITY_UNAVAILABLE", 503)
    if verify_ledger(connection):
        raise ControlledChangeError("CONTROLLED_CHANGE_LEDGER_INVALID", 503)
    row = connection.execute(
        """SELECT status, declared_intent_digest FROM supervision_sessions
           WHERE supervision_session_id = ?""",
        (session_id,),
    ).fetchone()
    if row is None:
        raise ControlledChangeError("SUPERVISION_SESSION_NOT_FOUND", 404)
    status, expected_intent_digest = row
    actual_intent_digest = hashlib.sha256(
        _intent(target, content).encode("utf-8")
    ).hexdigest()
    if not hmac.compare_digest(str(expected_intent_digest), actual_intent_digest):
        raise ControlledChangeError("CONTROLLED_CHANGE_INTENT_MISMATCH")
    if status not in {"APPROVED", "ACTIVE"}:
        raise ControlledChangeError("CONTROLLED_CHANGE_APPROVAL_REQUIRED")
    domain_id = _session_domain(connection, session_id)
    checkpoint_id = _existing_checkpoint(connection, session_id)
    if checkpoint_id is None and status == "ACTIVE":
        raise ControlledChangeError("CONTROLLED_CHANGE_CHECKPOINT_STALE")
    if checkpoint_id is None:
        policy = RestorePolicy(
            approved_paths={domain_id: (target,)},
            validators={domain_id: "toml-parse"},
        )
        recovery = RecoveryService(
            database=database,
            snapshots=snapshots,
            adapters={domain_id: SelfRuntimeAdapter(recovery_policy=policy)},
        )
        checkpoint = recovery.snapshot(
            RecoveryRequest(
                operation=RecoveryOperation.SNAPSHOT,
                execution_domain_id=domain_id,
                target_path=target,
                supervision_session_id=session_id,
                user_approved=True,
            )
        )
        if not checkpoint.ok or checkpoint.checkpoint_id is None:
            raise ControlledChangeError(checkpoint.reason_code)
        checkpoint_id = checkpoint.checkpoint_id

    sessions = SupervisionService(database, snapshots=snapshots)
    if status == "APPROVED":
        activated = sessions.activate(session_id, checkpoint_id)
        if activated.status != "ACTIVE":
            raise ControlledChangeError("CONTROLLED_CHANGE_ACTIVATION_DENIED")
    result = sessions.apply_config_change(
        session_id,
        checkpoint_id,
        requested_target=target,
        content=content,
    )
    response: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "supervision_session_id": session_id,
        "status": result.status,
        "reason_code": result.reason_code,
        "changed": result.changed,
        "verification": result.verification,
        "rolled_back": result.rolled_back,
        "before_digest": result.before_digest,
        "after_digest": result.after_digest,
        "checkpoint_id": checkpoint_id,
        "evidence_refs": list(result.evidence_refs),
    }
    if result.status == "COMPLETED":
        response.update(_controlled_change_receipt(connection, session_id))
    return response


def _controlled_change_receipt(
    connection: sqlite3.Connection,
    session_id: str,
) -> dict[str, object]:
    """Project the completed action receipt only from verified Ledger facts."""
    rows = connection.execute(
        """SELECT event_id, event_type, result, transaction_id, payload_safe_json
           FROM evidence_ledger_events
           WHERE supervision_session_id = ? ORDER BY sequence""",
        (session_id,),
    ).fetchall()
    by_type: dict[str, list[tuple[object, ...]]] = {}
    for row in rows:
        by_type.setdefault(str(row[1]), []).append(row)
    required = {
        "POLICY_EVALUATED",
        "USER_APPROVED",
        "OBSERVED_CHANGE",
        "SESSION_COMPLETED",
    }
    if any(len(by_type.get(event_type, ())) != 1 for event_type in required):
        raise ControlledChangeError("CONTROLLED_CHANGE_RECEIPT_UNAVAILABLE", 503)
    policy = by_type["POLICY_EVALUATED"][0]
    approval = by_type["USER_APPROVED"][0]
    change = by_type["OBSERVED_CHANGE"][0]
    completed = by_type["SESSION_COMPLETED"][0]
    try:
        change_payload = json.loads(str(change[4]))
        completed_payload = json.loads(str(completed[4]))
    except json.JSONDecodeError:
        raise ControlledChangeError("CONTROLLED_CHANGE_RECEIPT_UNAVAILABLE", 503) from None
    transaction_id = change[3]
    workspace_id = completed_payload.get("workspace_id")
    changed_object = change_payload.get("target_ref_digest")
    if (
        policy[2] not in {"ALLOW", "REVIEW", "BLOCK", "UNKNOWN"}
        or approval[2] != "APPROVED"
        or not isinstance(transaction_id, str)
        or completed_payload.get("change_id") != transaction_id
        or not isinstance(workspace_id, str)
        or change_payload.get("workspace_id") != workspace_id
        or not isinstance(changed_object, str)
        or len(changed_object) != 64
        or any(character not in "0123456789abcdef" for character in changed_object)
    ):
        raise ControlledChangeError("CONTROLLED_CHANGE_RECEIPT_UNAVAILABLE", 503)
    return {
        "action": "replace_agentguard_toml",
        "transaction_id": transaction_id,
        "workspace_id": workspace_id,
        "policy_result": policy[2],
        "approval_evidence_refs": [approval[0]],
        "changed_objects": [changed_object],
        "evidence_refs": [row[0] for row in rows],
    }


def _server_target(base_dir: Path) -> Path:
    workspace = Path(base_dir).resolve()
    target = workspace / "config" / "agentguard.toml"
    if (
        not workspace.is_dir()
        or not target.is_file()
        or RestorePolicy._path_is_unsafe(target)
    ):
        raise ControlledChangeError("CONTROLLED_CHANGE_TARGET_UNAVAILABLE")
    return target


def _validate_content(content: bytes) -> None:
    if not isinstance(content, bytes) or not content or len(content) > _MAX_CONTENT_BYTES:
        raise ControlledChangeError("CONTROLLED_CHANGE_CONTENT_INVALID", 422)


def _intent(target: Path, content: bytes) -> str:
    return canonical_json(
        {
            "schema_version": SCHEMA_VERSION,
            "operation": "replace_agentguard_toml",
            "target_ref_digest": hashlib.sha256(str(target).encode("utf-8")).hexdigest(),
            "content_digest": hashlib.sha256(content).hexdigest(),
        }
    )


def _session_domain(connection: sqlite3.Connection, session_id: str) -> str:
    rows = connection.execute(
        """SELECT payload_safe_json FROM evidence_ledger_events
           WHERE supervision_session_id = ? AND event_type = 'POLICY_EVALUATED'""",
        (session_id,),
    ).fetchall()
    if len(rows) != 1:
        raise ControlledChangeError("CONTROLLED_CHANGE_AUTHORITY_UNAVAILABLE", 503)
    try:
        payload = json.loads(rows[0][0])
        authority = payload["policy_authority"]["activation_context"]
        domain_id = authority["execution_domain_id"]
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ControlledChangeError("CONTROLLED_CHANGE_AUTHORITY_UNAVAILABLE", 503) from None
    if not isinstance(domain_id, str) or not domain_id:
        raise ControlledChangeError("CONTROLLED_CHANGE_AUTHORITY_UNAVAILABLE", 503)
    return domain_id


def _existing_checkpoint(
    connection: sqlite3.Connection,
    session_id: str,
) -> str | None:
    rows = connection.execute(
        """SELECT event_type, checkpoint_id FROM evidence_ledger_events
           WHERE supervision_session_id = ?
             AND event_type IN ('CHECKPOINT_CREATED', 'MANIFEST_VERIFIED')
           ORDER BY sequence""",
        (session_id,),
    ).fetchall()
    if not rows:
        return None
    if (
        len(rows) != 2
        or rows[0][0] != "CHECKPOINT_CREATED"
        or rows[1][0] != "MANIFEST_VERIFIED"
        or not isinstance(rows[0][1], str)
        or rows[0][1] != rows[1][1]
    ):
        raise ControlledChangeError("CONTROLLED_CHANGE_CHECKPOINT_STALE")
    return rows[0][1]


def _production_snapshot(discovery_service=None) -> tuple[DiscoverySnapshot, str]:
    try:
        snapshot = (discovery_service or ProductDiscoveryService()).discover()
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ControlledChangeError("CONTROLLED_CHANGE_DISCOVERY_UNAVAILABLE", 503) from None
    domains = {
        runtime.domain_id
        for runtime in snapshot.runtimes
        if runtime.status is CapabilityStatus.AVAILABLE
    }
    if len(domains) != 1:
        raise ControlledChangeError("CONTROLLED_CHANGE_DISCOVERY_UNAVAILABLE", 503)
    return snapshot, domains.pop()


__all__ = [
    "SCHEMA_VERSION",
    "ControlledChangeError",
    "apply_controlled_change",
    "controlled_change_failure",
    "prepare_controlled_change",
]
