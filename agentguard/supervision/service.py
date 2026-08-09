"""Transactional application service for local supervision sessions."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentguard.evidence.canonical import canonical_json
from agentguard.evidence.ledger import EvidenceLedger, verify_ledger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.policy.engine import evaluate
from agentguard.policy.models import (
    POLICY_VERSION,
    Decision,
    PolicyDecision,
    PolicyInput,
)
from agentguard.recovery.coverage import RecoveryCoverageFacts, RecoveryCoverageService
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore


class SupervisionSession:
    """Immutable projection of a stored supervision session."""

    def __init__(self, session_id: str, status: str) -> None:
        self.supervision_session_id = session_id
        self.status = status


class SupervisionActionError(RuntimeError):
    """Stable fail-closed action failure without raw persistence details."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SupervisionActionResult:
    """Stable result for one consumed UI supervision action."""

    action: str
    supervision_session_id: str
    status: str
    reason_code: str
    evidence_refs: tuple[str, ...]
    consumed: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "r4-p8-action-1",
            "action": self.action,
            "supervision_session_id": self.supervision_session_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "consumed": self.consumed,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class _ActionAuthority:
    action_ref: str
    status: str


class SupervisionService:
    """Owns session transitions and ledger writes in one StateDB transaction."""

    def __init__(self, database: StateDB, *, snapshots: SnapshotStore | None = None) -> None:
        self._database = database
        self._snapshots = snapshots
        self._ledger = EvidenceLedger()

    @classmethod
    def for_path(cls, path: Path) -> SupervisionService:
        database = StateDB(path)
        database.connect()
        return cls(database)

    def create_recovery_approval_session(
        self,
        connection: sqlite3.Connection,
        *,
        operation_kind: str,
    ) -> SupervisionSession:
        """Create a local REVIEW session for one recovery authorization."""
        decision = PolicyDecision(
            decision=Decision.REVIEW,
            severity="HIGH",
            matched_rule_ids=("recovery-authorization",),
            summary_code="RECOVERY_APPROVAL_REQUIRED",
            evidence_refs=(),
            uncertainties=(),
            required_checks=(),
            requires_checkpoint=True,
            requires_manual_approval=True,
        )
        return self._create_in_transaction(
            connection,
            f"recovery:{operation_kind}",
            decision,
        )

    def approve_recovery_authorization(
        self,
        session_id: str,
        authorization: dict[str, str],
    ) -> SupervisionSession:
        """Approve exactly one durable, bound recovery authorization."""
        with self._database.transaction() as connection:
            return self._approve_recovery_authorization_in_transaction(
                connection,
                session_id,
                authorization,
            )

    def _approve_recovery_authorization_in_transaction(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        authorization: dict[str, str],
    ) -> SupervisionSession:
        """Persist a bound authorization with its supervision approval atomically."""
        required = {
            "authorization_id", "subject_id", "session_identity_digest",
            "checkpoint_id", "execution_domain_id", "manifest_digest",
            "operation_kind", "target_refs_digest", "drill_fingerprint",
            "policy_version", "binding_digest", "issued_at", "expires_at", "nonce",
        }
        if set(authorization) != required or authorization["operation_kind"] not in {
            "SELF_RUNTIME_R3_DRILL", "TRUSTED_BASELINE_CONFIRM",
        }:
            raise ValueError("RECOVERY_AUTHORIZATION_INVALID")
        try:
            issued_at = datetime.fromisoformat(authorization["issued_at"])
            expires_at = datetime.fromisoformat(authorization["expires_at"])
        except ValueError as exc:
            raise ValueError("RECOVERY_AUTHORIZATION_INVALID") from exc
        if (
            issued_at.tzinfo is None
            or expires_at.tzinfo is None
            or expires_at <= issued_at
            or not authorization["drill_fingerprint"]
        ):
            raise ValueError("RECOVERY_AUTHORIZATION_INVALID")
        now = datetime.now(UTC)
        row = connection.execute(
            """SELECT status, decision, declared_intent_digest FROM supervision_sessions
               WHERE supervision_session_id = ?""",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError("SUPERVISION_SESSION_NOT_FOUND")
        if (
            row[0] != "AWAITING_APPROVAL"
            or row[1] in {Decision.BLOCK.value, Decision.UNKNOWN.value}
            or authorization["session_identity_digest"] != row[2]
        ):
            return SupervisionSession(session_id, row[0])
        updated = connection.execute(
            """UPDATE supervision_sessions SET status = ?, updated_at = ?
               WHERE supervision_session_id = ? AND status = 'AWAITING_APPROVAL'""",
            ("APPROVED", now.isoformat(), session_id),
        ).rowcount
        if updated != 1:
            return self._read(session_id, connection)
        self._append(
            connection,
            session_id,
            EventType.USER_APPROVED,
            "APPROVED",
            now,
            payload_extra={
                "authorization_binding_digest": authorization["binding_digest"],
                "operation_kind": authorization["operation_kind"],
                "policy_version": authorization["policy_version"],
            },
        )
        connection.execute(
            """INSERT INTO recovery_authorizations (
                   authorization_id, subject_id, supervision_session_id,
                   session_identity_digest, checkpoint_id, execution_domain_id,
                   manifest_digest, operation_kind, target_refs_digest,
                   drill_fingerprint, policy_version, binding_digest, issued_at,
                   expires_at, nonce
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                authorization["authorization_id"], authorization["subject_id"],
                session_id, authorization["session_identity_digest"],
                authorization["checkpoint_id"], authorization["execution_domain_id"],
                authorization["manifest_digest"], authorization["operation_kind"],
                authorization["target_refs_digest"], authorization["drill_fingerprint"],
                authorization["policy_version"], authorization["binding_digest"],
                authorization["issued_at"], authorization["expires_at"],
                authorization["nonce"],
            ),
        )
        return SupervisionSession(session_id, "APPROVED")

    def create(self, declared_intent: str, decision: PolicyDecision) -> SupervisionSession:
        with self._database.transaction() as connection:
            return self._create_in_transaction(connection, declared_intent, decision)

    def create_authoritative(
        self,
        declared_intent: str,
        policy_input: PolicyInput,
        *,
        checkpoint_id: str | None,
    ) -> tuple[SupervisionSession, PolicyDecision, RecoveryCoverageFacts]:
        if self._snapshots is None:
            raise RuntimeError("RECOVERY_FACTS_UNAVAILABLE")
        try:
            with self._database.transaction() as connection:
                facts = RecoveryCoverageService(
                    self._database,
                    self._snapshots,
                ).compute_with_connection(
                    connection,
                    checkpoint_id=checkpoint_id,
                    target_refs=policy_input.target_refs,
                    execution_domain_id=policy_input.execution_domain_id,
                )
                decision = evaluate(policy_input, recovery_facts=facts)
                session = self._create_in_transaction(
                    connection,
                    declared_intent,
                    decision,
                    recovery_facts=facts,
                )
        except (OSError, sqlite3.DatabaseError, ValueError):
            raise RuntimeError("SUPERVISION_PERSISTENCE_FAILED") from None
        return session, decision, facts

    def _create_in_transaction(
        self,
        connection,
        declared_intent: str,
        decision: PolicyDecision,
        *,
        recovery_facts: RecoveryCoverageFacts | None = None,
    ) -> SupervisionSession:
        session_id = f"session-{uuid4()}"
        status = "REJECTED" if decision.decision is Decision.BLOCK else (
            "AWAITING_APPROVAL" if decision.requires_manual_approval else "EVALUATED"
        )
        now = datetime.now(UTC)
        intent_digest = hashlib.sha256(declared_intent.encode("utf-8")).hexdigest()
        policy_authority = {
            "declared_intent_digest": intent_digest,
            "decision": decision.decision.value,
            "severity": decision.severity,
            "matched_rule_ids": list(decision.matched_rule_ids),
            "summary_code": decision.summary_code,
            "evidence_refs": list(decision.evidence_refs),
            "uncertainties": list(decision.uncertainties),
            "required_checks": list(decision.required_checks),
            "requires_checkpoint": decision.requires_checkpoint,
            "requires_manual_approval": decision.requires_manual_approval,
            "policy_version": decision.policy_version,
        }
        policy_binding_digest = hashlib.sha256(
            canonical_json(policy_authority).encode("utf-8")
        ).hexdigest()
        connection.execute(
            """INSERT INTO supervision_sessions (
                   supervision_session_id, status, declared_intent_digest, decision,
                   requires_checkpoint, requires_manual_approval, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                status,
                intent_digest,
                decision.decision.value,
                int(decision.requires_checkpoint),
                int(decision.requires_manual_approval),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        self._append(connection, session_id, EventType.SESSION_CREATED, status, now)
        self._append(
            connection,
            session_id,
            EventType.POLICY_EVALUATED,
            decision.decision.value,
            now,
            evidence_refs=(recovery_facts.evidence_refs if recovery_facts else ()),
            payload_extra={
                "policy_authority": policy_authority,
                "policy_binding_digest": policy_binding_digest,
                **(
                    {"recovery_facts": recovery_facts.safe_summary()}
                    if recovery_facts
                    else {}
                ),
            },
        )
        return SupervisionSession(session_id, status)

    def action_ref(self, session_id: str) -> str | None:
        """Return the current opaque action binding for a server-owned REVIEW session."""
        try:
            with self._database.transaction() as connection:
                authority = self._action_authority(connection, session_id)
        except SupervisionActionError:
            return None
        except (OSError, sqlite3.DatabaseError, RuntimeError):
            return None
        return authority.action_ref

    def projected_action_ref(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> str | None:
        """Project a binding after the caller has verified the shared Ledger."""
        try:
            return self._action_authority(
                connection,
                session_id,
                ledger_verified=True,
            ).action_ref
        except SupervisionActionError:
            return None

    def approve_once(
        self,
        session_id: str,
        action_ref: str,
    ) -> SupervisionActionResult:
        return self._perform_action(
            session_id,
            action_ref,
            action="APPROVE_ONCE",
            target="APPROVED",
            event_type=EventType.USER_APPROVED,
            reason_code="SUPERVISION_APPROVED_ONCE",
        )

    def reject_once(
        self,
        session_id: str,
        action_ref: str,
    ) -> SupervisionActionResult:
        return self._perform_action(
            session_id,
            action_ref,
            action="REJECT",
            target="REJECTED",
            event_type=EventType.USER_REJECTED,
            reason_code="SUPERVISION_REJECTED",
        )

    def approve(self, session_id: str) -> SupervisionSession:
        """Preserve the trusted local CLI behavior using a fresh server binding."""
        action_ref = self.action_ref(session_id)
        if action_ref is None:
            return self._read(session_id)
        result = self.approve_once(session_id, action_ref)
        return SupervisionSession(session_id, result.status)

    def activate(self, session_id: str, checkpoint_id: str | None = None) -> SupervisionSession:
        now = datetime.now(UTC)
        with self._database.transaction() as connection:
            row = connection.execute(
                """SELECT status, decision, requires_checkpoint FROM supervision_sessions
                   WHERE supervision_session_id = ?""",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError("SUPERVISION_SESSION_NOT_FOUND")
            status, decision, requires_checkpoint = row
            if (
                status not in {"APPROVED", "EVALUATED"}
                or decision in {Decision.BLOCK.value, Decision.UNKNOWN.value}
                or requires_checkpoint
            ):
                return SupervisionSession(session_id, status)
            updated = connection.execute(
                """UPDATE supervision_sessions SET status = ?, updated_at = ?
                   WHERE supervision_session_id = ? AND status = ?""",
                ("ACTIVE", now.isoformat(), session_id, status),
            ).rowcount
            if not updated:
                return self._read(session_id, connection)
            self._append(connection, session_id, EventType.OBSERVED_CHANGE, "ACTIVE", now)
        return SupervisionSession(session_id, "ACTIVE")

    def complete(self, session_id: str) -> SupervisionSession:
        return self._transition(session_id, "ACTIVE", "COMPLETED", EventType.SESSION_COMPLETED)

    def fail(self, session_id: str) -> SupervisionSession:
        return self._transition(session_id, "ACTIVE", "FAILED", EventType.SESSION_FAILED)

    def reject(self, session_id: str) -> SupervisionSession:
        """Preserve the trusted local CLI behavior using a fresh server binding."""
        action_ref = self.action_ref(session_id)
        if action_ref is None:
            return self._read(session_id)
        result = self.reject_once(session_id, action_ref)
        return SupervisionSession(session_id, result.status)

    def _perform_action(
        self,
        session_id: str,
        action_ref: str,
        *,
        action: str,
        target: str,
        event_type: EventType,
        reason_code: str,
    ) -> SupervisionActionResult:
        try:
            with self._database.transaction() as connection:
                authority = self._action_authority(connection, session_id)
                if not hmac.compare_digest(authority.action_ref, action_ref):
                    raise SupervisionActionError("SUPERVISION_ACTION_STALE")
                now = datetime.now(UTC)
                updated = connection.execute(
                    """UPDATE supervision_sessions SET status = ?, updated_at = ?
                       WHERE supervision_session_id = ?
                         AND status = 'AWAITING_APPROVAL'
                         AND decision = 'REVIEW'
                         AND requires_manual_approval = 1""",
                    (target, now.isoformat(), session_id),
                ).rowcount
                if updated != 1:
                    raise SupervisionActionError("SUPERVISION_ACTION_REPLAYED")
                event_id = self._append(
                    connection,
                    session_id,
                    event_type,
                    target,
                    now,
                    payload_extra={
                        "action_binding_digest": authority.action_ref,
                        "action": action,
                        "consumed": True,
                    },
                )
        except SupervisionActionError:
            raise
        except (OSError, sqlite3.DatabaseError, RuntimeError):
            raise SupervisionActionError("SUPERVISION_AUTHORITY_UNAVAILABLE") from None
        return SupervisionActionResult(
            action=action,
            supervision_session_id=session_id,
            status=target,
            reason_code=reason_code,
            evidence_refs=(event_id,),
        )

    def _action_authority(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        *,
        ledger_verified: bool = False,
    ) -> _ActionAuthority:
        if not ledger_verified and verify_ledger(connection):
            raise SupervisionActionError("SUPERVISION_LEDGER_INVALID")
        row = connection.execute(
            """SELECT status, declared_intent_digest, decision,
                      requires_checkpoint, requires_manual_approval
               FROM supervision_sessions WHERE supervision_session_id = ?""",
            (session_id,),
        ).fetchone()
        if row is None:
            raise SupervisionActionError("SUPERVISION_SESSION_NOT_FOUND")
        status, intent_digest, decision, requires_checkpoint, requires_manual = row
        events = connection.execute(
            """SELECT sequence, event_id, event_type, source, result,
                      payload_safe_json, payload_digest, curr_hash
               FROM evidence_ledger_events
               WHERE supervision_session_id = ? ORDER BY sequence""",
            (session_id,),
        ).fetchall()
        action_events = [
            event for event in events
            if event[2] in {EventType.USER_APPROVED.value, EventType.USER_REJECTED.value}
        ]
        if decision != Decision.REVIEW.value or not bool(requires_manual):
            raise SupervisionActionError("SUPERVISION_POLICY_NOT_APPROVABLE")
        if action_events or status in {"APPROVED", "REJECTED", "COMPLETED", "FAILED"}:
            raise SupervisionActionError("SUPERVISION_ACTION_REPLAYED")
        if status != "AWAITING_APPROVAL":
            raise SupervisionActionError("SUPERVISION_ACTION_INVALID_STATE")
        recovery_bound = connection.execute(
            """SELECT 1 FROM recovery_drill_bindings
               WHERE supervision_session_id = ?
               UNION ALL
               SELECT 1 FROM trusted_baseline_candidate_bindings
               WHERE supervision_session_id = ? LIMIT 1""",
            (session_id, session_id),
        ).fetchone()
        if recovery_bound is not None:
            raise SupervisionActionError("SUPERVISION_RECOVERY_AUTHORIZATION_REQUIRED")
        created = [event for event in events if event[2] == EventType.SESSION_CREATED.value]
        policy = [event for event in events if event[2] == EventType.POLICY_EVALUATED.value]
        if len(created) != 1 or len(policy) != 1 or created[0][0] >= policy[0][0]:
            raise SupervisionActionError("SUPERVISION_POLICY_EVIDENCE_INVALID")
        created_payload = self._json_payload(created[0][5])
        policy_payload = self._json_payload(policy[0][5])
        policy_authority = policy_payload.get("policy_authority")
        policy_digest = policy_payload.get("policy_binding_digest")
        if (
            created[0][3] != "supervision-service"
            or created[0][4] != "AWAITING_APPROVAL"
            or created_payload.get("status") != "AWAITING_APPROVAL"
            or policy[0][3] != "supervision-service"
            or policy[0][4] != decision
            or policy_payload.get("status") != decision
            or not isinstance(policy_authority, dict)
            or not isinstance(policy_digest, str)
            or hashlib.sha256(
                canonical_json(policy_authority).encode("utf-8")
            ).hexdigest() != policy_digest
            or policy_authority.get("declared_intent_digest") != intent_digest
            or policy_authority.get("decision") != decision
            or policy_authority.get("requires_checkpoint") != bool(requires_checkpoint)
            or policy_authority.get("requires_manual_approval") is not True
            or policy_authority.get("policy_version") != POLICY_VERSION
        ):
            raise SupervisionActionError("SUPERVISION_POLICY_EVIDENCE_INVALID")
        binding = {
            "schema_version": "r4-p8-action-binding-1",
            "supervision_session_id": session_id,
            "status": status,
            "declared_intent_digest": intent_digest,
            "decision": decision,
            "requires_checkpoint": bool(requires_checkpoint),
            "requires_manual_approval": bool(requires_manual),
            "session_created_event_id": created[0][1],
            "session_created_payload_digest": created[0][6],
            "session_created_curr_hash": created[0][7],
            "policy_event_id": policy[0][1],
            "policy_payload_digest": policy[0][6],
            "policy_curr_hash": policy[0][7],
            "policy_binding_digest": policy_digest,
            "session_context_sequence": events[-1][0],
            "session_context_event_id": events[-1][1],
            "session_context_event_type": events[-1][2],
            "session_context_payload_digest": events[-1][6],
            "session_context_curr_hash": events[-1][7],
        }
        return _ActionAuthority(
            action_ref=hashlib.sha256(
                canonical_json(binding).encode("utf-8")
            ).hexdigest(),
            status=status,
        )

    @staticmethod
    def _json_payload(raw: str) -> dict:
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            raise SupervisionActionError("SUPERVISION_POLICY_EVIDENCE_INVALID") from None
        if not isinstance(payload, dict):
            raise SupervisionActionError("SUPERVISION_POLICY_EVIDENCE_INVALID")
        return payload

    def _transition(
        self,
        session_id: str,
        expected: str,
        target: str,
        event_type: EventType,
    ) -> SupervisionSession:
        now = datetime.now(UTC)
        with self._database.transaction() as connection:
            updated = connection.execute(
                """UPDATE supervision_sessions SET status = ?, updated_at = ?
                   WHERE supervision_session_id = ? AND status = ?""",
                (target, now.isoformat(), session_id, expected),
            ).rowcount
            if not updated:
                return self._read(session_id, connection)
            self._append(connection, session_id, event_type, target, now)
        return SupervisionSession(session_id, target)

    def _read(self, session_id: str, connection=None) -> SupervisionSession:
        connection = connection or self._database._conn
        row = connection.execute(
            "SELECT supervision_session_id, status FROM supervision_sessions WHERE supervision_session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError("SUPERVISION_SESSION_NOT_FOUND")
        return SupervisionSession(row[0], row[1])

    def _append(
        self,
        connection,
        session_id: str,
        event_type: EventType,
        result: str,
        now: datetime,
        *,
        evidence_refs: tuple[str, ...] = (),
        payload_extra: dict | None = None,
    ) -> str:
        payload_safe = {"status": result}
        if payload_extra:
            payload_safe.update(payload_extra)
        event_id = f"session-event-{uuid4()}"
        self._ledger.append(
            connection,
            EvidenceEvent(
                schema_version=1,
                event_id=event_id,
                recorded_at=now,
                observed_at=None,
                event_family=EventFamily.SUPERVISION,
                event_type=event_type,
                source="supervision-service",
                result=result,
                execution_domain_id=None,
                supervision_session_id=session_id,
                transaction_id=None,
                checkpoint_id=None,
                subject_ref=session_id,
                evidence_refs=(session_id, *evidence_refs),
                payload_safe=payload_safe,
            ),
        )
        return event_id
