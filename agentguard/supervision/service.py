"""Transactional application service for local supervision sessions."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentguard.evidence.ledger import EvidenceLedger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.policy.models import Decision, PolicyDecision
from agentguard.storage.db import StateDB


class SupervisionSession:
    """Immutable projection of a stored supervision session."""

    def __init__(self, session_id: str, status: str) -> None:
        self.supervision_session_id = session_id
        self.status = status


class SupervisionService:
    """Owns session transitions and ledger writes in one StateDB transaction."""

    def __init__(self, database: StateDB) -> None:
        self._database = database
        self._ledger = EvidenceLedger()

    @classmethod
    def for_path(cls, path: Path) -> SupervisionService:
        database = StateDB(path)
        database.connect()
        return cls(database)

    def create(self, declared_intent: str, decision: PolicyDecision) -> SupervisionSession:
        session_id = f"session-{uuid4()}"
        status = "REJECTED" if decision.decision is Decision.BLOCK else (
            "AWAITING_APPROVAL" if decision.requires_manual_approval else "EVALUATED"
        )
        now = datetime.now(UTC)
        intent_digest = hashlib.sha256(declared_intent.encode("utf-8")).hexdigest()
        with self._database.transaction() as connection:
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
            self._append(connection, session_id, EventType.POLICY_EVALUATED, decision.decision.value, now)
        return SupervisionSession(session_id, status)

    def approve(self, session_id: str) -> SupervisionSession:
        return self._transition(session_id, "AWAITING_APPROVAL", "APPROVED", EventType.USER_APPROVED)

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
                or (requires_checkpoint and not checkpoint_id)
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
        return self._transition(session_id, "AWAITING_APPROVAL", "REJECTED", EventType.USER_REJECTED)

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

    def _append(self, connection, session_id: str, event_type: EventType, result: str, now: datetime) -> None:
        self._ledger.append(
            connection,
            EvidenceEvent(
                schema_version=1,
                event_id=f"session-event-{uuid4()}",
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
                evidence_refs=(session_id,),
                payload_safe={"status": result},
            ),
        )
