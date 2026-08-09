"""Narrow production composition for the R4 MVP controlled configuration change."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentguard.ai.supervisor import AISupervisor, AssessmentProvider
from agentguard.discovery import (
    AgentDescriptor,
    AgentLifecycleStatus,
    CapabilityStatus,
    DiscoverySnapshot,
    ExecutionDomainDescriptor,
    ExecutionDomainKind,
    RuntimeDescriptor,
    WorkspaceDescriptor,
)
from agentguard.discovery.agents import (
    ProcessCollector,
    ProcessState,
    PsutilProcessBackend,
    WorkspaceSource,
    workspace_candidate_from_path,
)
from agentguard.discovery.domains import SelfRuntimeAdapter
from agentguard.evidence.canonical import canonical_json
from agentguard.evidence.discovery_adapter import record_discovery_snapshot
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
) -> dict[str, object]:
    """Discover authority and create one write-before REVIEW session."""
    target = _server_target(base_dir)
    _validate_content(content)
    snapshot, domain_id = _production_snapshot(base_dir)
    try:
        receipts = record_discovery_snapshot(
            database,
            snapshot,
            recorded_at=datetime.now(UTC),
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
        evidence_refs=tuple(receipt.event_id for receipt in receipts),
    )
    try:
        session, decision, _facts = SupervisionService(
            database,
            snapshots=snapshots,
        ).create_authoritative(
            intent,
            policy_input,
            checkpoint_id=None,
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
    return {
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


def _production_snapshot(base_dir: Path) -> tuple[DiscoverySnapshot, str]:
    runtime_snapshot = SelfRuntimeAdapter().discover()
    domain = _current_domain(runtime_snapshot.domains)
    if domain is None or domain.kind is ExecutionDomainKind.UNKNOWN:
        raise ControlledChangeError("CONTROLLED_CHANGE_DISCOVERY_UNAVAILABLE", 503)
    observed_at = runtime_snapshot.observed_at
    processes = ProcessCollector(
        backend=PsutilProcessBackend(),
        execution_domain_id=domain.domain_id,
        collector="r4-production-process",
        clock=lambda: observed_at,
        home_path=str(Path.home()),
    ).collect()
    current = [item for item in processes.facts if item.pid == os.getpid()]
    if (
        len(current) != 1
        or current[0].current_state is not ProcessState.RUNNING
        or current[0].access_status is not CapabilityStatus.AVAILABLE
    ):
        raise ControlledChangeError("CONTROLLED_CHANGE_DISCOVERY_UNAVAILABLE", 503)
    fact = current[0]
    process_evidence = next(
        (item for item in processes.evidence if item.evidence_id in fact.evidence_refs),
        None,
    )
    if process_evidence is None:
        raise ControlledChangeError("CONTROLLED_CHANGE_DISCOVERY_UNAVAILABLE", 503)
    workspace = workspace_candidate_from_path(
        path=str(Path(base_dir).resolve()),
        source=WorkspaceSource.KNOWN_LOGICAL_PATH,
        execution_domain_id=domain.domain_id,
        evidence_refs=fact.evidence_refs,
        home_path=str(Path.home()),
        git_root_candidate=False,
    )
    nonce = uuid4().hex
    runtime_id = f"agentguard-runtime-{hashlib.sha256((domain.domain_id + fact.process_instance_id).encode()).hexdigest()[:24]}"
    agent_id = f"agentguard-agent-{hashlib.sha256(fact.process_instance_id.encode()).hexdigest()[:24]}"
    runtime_evidence_id = f"r4-runtime-{nonce}"
    agent_evidence_id = f"r4-agent-{nonce}"
    workspace_evidence_id = f"r4-workspace-{nonce}"
    runtime_evidence = replace(
        process_evidence,
        evidence_id=runtime_evidence_id,
        fact_type="runtime.metadata",
        value={"runtime_kind": "SELF_RUNTIME"},
        summary=None,
        error=None,
    )
    agent_evidence = replace(
        process_evidence,
        evidence_id=agent_evidence_id,
        fact_type="agent.metadata",
        value={"agent_kind": "AGENTSTATE_GUARD", "role": "AGENT_HOST"},
        summary=None,
        error=None,
    )
    workspace_evidence = replace(
        process_evidence,
        evidence_id=workspace_evidence_id,
        fact_type="workspace.present",
        value={"workspace_kind": "PROJECT", "candidate_id": workspace.candidate_id},
        summary=None,
        error=None,
    )
    snapshot = DiscoverySnapshot(
        snapshot_id=f"r4-production-{nonce}",
        observed_at=observed_at,
        domains=(domain,),
        runtimes=(
            RuntimeDescriptor(
                runtime_id=runtime_id,
                runtime_type="SELF_RUNTIME",
                domain_id=domain.domain_id,
                status=CapabilityStatus.AVAILABLE,
                evidence_ids=(runtime_evidence_id,),
                confidence=0.8,
            ),
        ),
        agents=(
            AgentDescriptor(
                agent_id=agent_id,
                agent_type="AGENTSTATE_GUARD",
                lifecycle=AgentLifecycleStatus.RUNNING,
                domain_id=domain.domain_id,
                runtime_id=runtime_id,
                workspace_ids=(workspace.candidate_id,),
                evidence_ids=(agent_evidence_id,),
                confidence=0.8,
            ),
        ),
        workspaces=(
            WorkspaceDescriptor(
                workspace_id=workspace.candidate_id,
                domain_id=domain.domain_id,
                runtime_ids=(runtime_id,),
                agent_ids=(agent_id,),
                evidence_ids=(workspace_evidence_id,),
                confidence=workspace.confidence,
            ),
        ),
        evidence=(*runtime_snapshot.evidence, runtime_evidence, agent_evidence, workspace_evidence),
        errors=runtime_snapshot.errors,
        status=runtime_snapshot.status,
    )
    return snapshot, domain.domain_id


def _current_domain(
    domains: tuple[ExecutionDomainDescriptor, ...],
) -> ExecutionDomainDescriptor | None:
    candidates: list[tuple[int, ExecutionDomainDescriptor]] = []

    def visit(domain: ExecutionDomainDescriptor, depth: int) -> None:
        self_visible = domain.capabilities.assessments.get("self_visible")
        if self_visible is not None and self_visible.status is CapabilityStatus.AVAILABLE:
            candidates.append((depth, domain))
        for child in domain.children:
            visit(child, depth + 1)

    for item in domains:
        visit(item, 0)
    if not candidates:
        return None
    deepest = max(depth for depth, _item in candidates)
    matches = [item for depth, item in candidates if depth == deepest]
    return matches[0] if len(matches) == 1 else None


__all__ = [
    "SCHEMA_VERSION",
    "ControlledChangeError",
    "apply_controlled_change",
    "controlled_change_failure",
    "prepare_controlled_change",
]
