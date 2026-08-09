"""Transactional application service for local supervision sessions."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import stat
import tomllib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentguard.core.versions import exact_product_sha, is_exact_git_sha
from agentguard.evidence.canonical import canonical_json
from agentguard.evidence.discovery_adapter import resolve_verified_workspace_binding
from agentguard.evidence.ledger import EvidenceLedger, verify_ledger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.policy.engine import evaluate
from agentguard.policy.models import (
    POLICY_VERSION,
    Decision,
    PolicyDecision,
    PolicyInput,
)
from agentguard.recovery.coverage import (
    RecoveryCoverageFacts,
    RecoveryCoverageService,
    RecoveryCoverageStatus,
)
from agentguard.recovery.manifest import validate_snapshot_v3
from agentguard.recovery.policy import RestorePolicy
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
class ControlledChangeResult:
    """Outcome of the single authority-bound MVP configuration change."""

    supervision_session_id: str
    status: str
    reason_code: str
    changed: bool = False
    before_digest: str | None = None
    after_digest: str | None = None
    verification: str = "NOT_RUN"
    rolled_back: bool = False
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ActionAuthority:
    action_ref: str
    status: str


def _handle_relative_paths_supported() -> bool:
    """Return whether the current OS can enforce the handle-relative path."""
    # CPython exposes ``os.replace`` with the rename dir-fd implementation but
    # lists ``os.rename`` (not ``os.replace``) in ``os.supports_dir_fd``.
    required = (os.open, os.stat, os.rename, os.unlink)
    return all(function in os.supports_dir_fd for function in required)


def _identity(file_stat: os.stat_result) -> tuple[int, int]:
    return file_stat.st_dev, file_stat.st_ino


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(file_stat, "st_file_attributes", 0)
    return bool(flag and attributes & flag)


def _portable_target_stat(target: Path) -> tuple[os.stat_result, os.stat_result]:
    """Reject symlink/reparse traversal where dir_fd is unavailable."""
    parent_stat = os.stat(target.parent, follow_symlinks=False)
    target_stat = os.stat(target, follow_symlinks=False)
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or _is_reparse_point(parent_stat)
        or not stat.S_ISREG(target_stat.st_mode)
        or _is_reparse_point(target_stat)
    ):
        raise OSError("CONTROLLED_CHANGE_TARGET_CHANGED")
    return parent_stat, target_stat


def _read_controlled_target_portable(target: Path) -> tuple[bytes, os.stat_result]:
    """Read and identity-check a target on platforms without dir_fd."""
    parent_stat, target_stat = _portable_target_stat(target)
    descriptor = os.open(
        target,
        os.O_RDONLY | getattr(os, "O_BINARY", 0),
    )
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        content = b"".join(chunks)
        opened_stat = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    current_parent, current_target = _portable_target_stat(target)
    if (
        _identity(parent_stat) != _identity(current_parent)
        or _identity(target_stat) != _identity(opened_stat)
        or _identity(opened_stat) != _identity(current_target)
        or not stat.S_ISREG(opened_stat.st_mode)
        or opened_stat.st_size != len(content)
    ):
        raise OSError("CONTROLLED_CHANGE_TARGET_CHANGED")
    return content, opened_stat


def _read_controlled_target(target: Path) -> tuple[bytes, os.stat_result]:
    """Read a regular target through a non-symlinked parent directory handle."""
    if not _handle_relative_paths_supported():
        return _read_controlled_target_portable(target)
    parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
        os,
        "O_NOFOLLOW",
        0,
    )
    parent_fd = os.open(target.parent, parent_flags)
    try:
        descriptor = os.open(
            target.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            content = b"".join(chunks)
            file_stat = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_fd)
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size != len(content):
        raise OSError("CONTROLLED_CHANGE_TARGET_CHANGED")
    return content, file_stat


def _replace_at(parent_fd: int, name: str, content: bytes, mode: int) -> None:
    temp_name = f".agentguard-restore-{name}-{uuid4().hex}"
    descriptor = -1
    try:
        descriptor = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=parent_fd,
        )
        remaining = memoryview(content)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("CONTROLLED_CHANGE_WRITE_INCOMPLETE")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, stat.S_IMODE(mode))
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temp_name,
            name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        os.fsync(parent_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temp_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


def _write_portable_temp(target: Path, content: bytes, mode: int) -> Path:
    temp = target.parent / f".agentguard-restore-{target.name}-{uuid4().hex}"
    descriptor = -1
    try:
        descriptor = os.open(
            temp,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
        remaining = memoryview(content)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("CONTROLLED_CHANGE_WRITE_INCOMPLETE")
            remaining = remaining[written:]
        os.fsync(descriptor)
        fchmod = getattr(os, "fchmod", None)
        chmod_by_path = fchmod is None
        if fchmod is not None:
            try:
                fchmod(descriptor, stat.S_IMODE(mode))
            except NotImplementedError:
                chmod_by_path = True
        os.close(descriptor)
        descriptor = -1
        if chmod_by_path:
            os.chmod(temp, stat.S_IMODE(mode))
        return temp
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise


def _portable_atomic_restore(
    target: Path,
    content: bytes,
    file_entry: dict,
) -> dict[str, object]:
    """Use full paths with repeated identity checks when dir_fd is unavailable."""
    temp: Path | None = None
    try:
        parent_stat, _target_stat = _portable_target_stat(target)
        current, opened_stat = _read_controlled_target_portable(target)
        current_digest = hashlib.sha256(current).hexdigest()
        expected_before = file_entry.get("_expected_before_sha256")
        expected_identity = file_entry.get("_expected_before_identity")
        if (
            not isinstance(expected_before, str)
            or current_digest != expected_before
            or expected_identity != _identity(opened_stat)
        ):
            return {
                "status": "authority_stale",
                "message": "Controlled target changed",
            }
        mode_value = file_entry.get("mode_oct", "0o644")
        try:
            mode = (
                int(mode_value, 8)
                if str(mode_value).startswith("0")
                else int(mode_value)
            )
        except (TypeError, ValueError):
            mode = 0o644
        temp = _write_portable_temp(target, content, mode)
        latest, latest_stat = _read_controlled_target_portable(target)
        latest_parent, _latest_target = _portable_target_stat(target)
        if (
            hashlib.sha256(latest).hexdigest() != expected_before
            or _identity(latest_stat) != expected_identity
            or _identity(latest_parent) != _identity(parent_stat)
        ):
            return {
                "status": "authority_stale",
                "message": "Controlled target changed",
            }
        os.replace(temp, target)
        temp = None
        written, _written_stat = _read_controlled_target_portable(target)
        actual = hashlib.sha256(written).hexdigest()
        expected = file_entry.get("sha256")
        current_parent, _current_target = _portable_target_stat(target)
        if not isinstance(expected, str) or actual != expected:
            return {"status": "hash_mismatch", "message": "Controlled hash mismatch"}
        if _identity(current_parent) != _identity(parent_stat):
            return {"status": "error", "message": "Controlled parent changed"}
        return {"status": "success", "message": "Controlled file replaced"}
    except (NotImplementedError, OSError):
        return {"status": "error", "message": "Controlled replace failed"}
    finally:
        if temp is not None:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass


def _digest_at(parent_fd: int, name: str) -> tuple[str, os.stat_result]:
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise OSError("CONTROLLED_CHANGE_TARGET_CHANGED")
        actual = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            actual.update(chunk)
    finally:
        os.close(descriptor)
    return actual.hexdigest(), file_stat


def _atomic_restore(target: Path, content: bytes, file_entry: dict) -> dict[str, object]:
    """Atomically replace one controlled file without following its parent."""
    if not _handle_relative_paths_supported():
        return _portable_atomic_restore(target, content, file_entry)
    parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
        os,
        "O_NOFOLLOW",
        0,
    )
    try:
        parent_fd = os.open(target.parent, parent_flags)
    except OSError:
        return {"status": "error", "message": "Controlled parent unavailable"}
    try:
        parent_stat = os.fstat(parent_fd)
        current_stat = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(current_stat.st_mode):
            return {"status": "error", "message": "Controlled target unavailable"}
        current_digest, opened_stat = _digest_at(parent_fd, target.name)
        expected_before = file_entry.get("_expected_before_sha256")
        expected_identity = file_entry.get("_expected_before_identity")
        if (
            not isinstance(expected_before, str)
            or current_digest != expected_before
            or expected_identity != (opened_stat.st_dev, opened_stat.st_ino)
        ):
            return {"status": "authority_stale", "message": "Controlled target changed"}
        mode_value = file_entry.get("mode_oct", "0o644")
        try:
            mode = int(mode_value, 8) if str(mode_value).startswith("0") else int(mode_value)
        except (TypeError, ValueError):
            mode = 0o644
        _replace_at(parent_fd, target.name, content, mode)
        actual, _written_stat = _digest_at(parent_fd, target.name)
        expected = file_entry.get("sha256")
        if not isinstance(expected, str) or actual != expected:
            return {"status": "hash_mismatch", "message": "Controlled hash mismatch"}
        try:
            current_parent = os.stat(target.parent, follow_symlinks=False)
        except OSError:
            current_parent = None
        if (
            current_parent is None
            or not stat.S_ISDIR(current_parent.st_mode)
            or (current_parent.st_dev, current_parent.st_ino)
            != (parent_stat.st_dev, parent_stat.st_ino)
        ):
            rollback_content = file_entry.get("_rollback_content")
            rollback_mode = file_entry.get("_rollback_mode", current_stat.st_mode)
            if isinstance(rollback_content, bytes):
                _replace_at(parent_fd, target.name, rollback_content, int(rollback_mode))
            return {"status": "error", "message": "Controlled parent changed"}
        return {"status": "success", "message": "Controlled file replaced"}
    except OSError:
        return {"status": "error", "message": "Controlled replace failed"}
    finally:
        os.close(parent_fd)


def _authoritative_diff(
    target: Path,
    *,
    before_digest: str,
    expected_after_digest: str,
) -> dict[str, object]:
    content, _file_stat = _read_controlled_target(target)
    after_digest = hashlib.sha256(content).hexdigest()
    if after_digest != expected_after_digest or after_digest == before_digest:
        raise ValueError("CONTROLLED_CHANGE_DIFF_INVALID")
    return {
        "before_digest": before_digest,
        "after_digest": after_digest,
        "changed": True,
    }


def _offline_verify(
    target: Path,
    *,
    validator: str,
    expected_digest: str,
    target_ref_digest: str,
) -> dict[str, object]:
    result = "FAIL"
    verified_digest: str | None = None
    try:
        content, _file_stat = _read_controlled_target(target)
        verified_digest = hashlib.sha256(content).hexdigest()
        if validator == "toml-parse" and verified_digest == expected_digest:
            tomllib.loads(content.decode("utf-8"))
            result = "PASS"
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        pass
    return {
        "method": "tomllib.loads",
        "network_used": False,
        "result": result,
        "target_ref_digest": target_ref_digest,
        "verified_digest": verified_digest,
    }


def _rollback_file(
    target: Path,
    content: bytes,
    before_stat,
) -> bool:
    digest = hashlib.sha256(content).hexdigest()
    try:
        current, current_stat = _read_controlled_target(target)
    except OSError:
        return False
    current_digest = hashlib.sha256(current).hexdigest()
    if current_digest == digest:
        return True
    result = _atomic_restore(
        target,
        content,
        {
            "mode_oct": oct(stat.S_IMODE(before_stat.st_mode)),
            "sha256": digest,
            "_expected_before_sha256": current_digest,
            "_expected_before_identity": (current_stat.st_dev, current_stat.st_ino),
        },
    )
    try:
        restored, _file_stat = _read_controlled_target(target)
    except OSError:
        return False
    return (
        result.get("status") == "success"
        or hashlib.sha256(restored).hexdigest() == digest
    )


class SupervisionService:
    """Owns session transitions and ledger writes in one StateDB transaction."""

    def __init__(
        self,
        database: StateDB,
        *,
        snapshots: SnapshotStore | None = None,
        product_sha: str | None = None,
    ) -> None:
        self._database = database
        self._snapshots = snapshots
        self._ledger = EvidenceLedger()
        self._product_sha = (
            product_sha if is_exact_git_sha(product_sha) else exact_product_sha()
        )

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
        if self._product_sha is None:
            raise RuntimeError("PRODUCT_PROVENANCE_UNAVAILABLE")
        try:
            with self._database.transaction() as connection:
                workspace = resolve_verified_workspace_binding(
                    connection,
                    execution_domain_id=policy_input.execution_domain_id,
                )
                if workspace["reason_code"] == "WORKSPACE_LEDGER_INVALID":
                    raise SupervisionActionError("WORKSPACE_AUTHORITY_UNAVAILABLE")
                authoritative_input = (
                    replace(
                        policy_input,
                        evidence_refs=(workspace["binding_ref"],),
                    )
                    if workspace["status"] == "BOUND"
                    else policy_input
                )
                facts = RecoveryCoverageService(
                    self._database,
                    self._snapshots,
                ).compute_with_connection(
                    connection,
                    checkpoint_id=checkpoint_id,
                    target_refs=authoritative_input.target_refs,
                    execution_domain_id=authoritative_input.execution_domain_id,
                )
                decision = evaluate(authoritative_input, recovery_facts=facts)
                session = self._create_in_transaction(
                    connection,
                    declared_intent,
                    decision,
                    recovery_facts=facts,
                    authority_context=(
                        {
                            "execution_domain_id": authoritative_input.execution_domain_id,
                            "workspace_id": workspace["workspace_id"],
                            "workspace_binding_ref": workspace["binding_ref"],
                            "approved_scope_digest": self._refs_digest(
                                authoritative_input.declared_scope
                            ),
                            "target_refs_digest": self._refs_digest(
                                authoritative_input.target_refs
                            ),
                            "product_sha": self._product_sha,
                        }
                        if workspace["status"] == "BOUND"
                        else None
                    ),
                    checkpoint_binding_required=(
                        authoritative_input.intent_kind == "change"
                    ),
                )
        except SupervisionActionError as exc:
            raise RuntimeError(exc.reason_code) from None
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
        authority_context: dict[str, object] | None = None,
        checkpoint_binding_required: bool = False,
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
            "checkpoint_binding_required": checkpoint_binding_required,
            **(
                {"activation_context": authority_context}
                if authority_context is not None
                else {}
            ),
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
            execution_domain_id=(
                str(authority_context["execution_domain_id"])
                if authority_context is not None
                else None
            ),
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
            ):
                return SupervisionSession(session_id, status)
            activation_context = None
            if requires_checkpoint or self._checkpoint_binding_required(
                connection,
                session_id,
            ):
                activation_context = self._checkpoint_activation_context(
                    connection,
                    session_id=session_id,
                    checkpoint_id=checkpoint_id,
                )
                if activation_context is None:
                    return SupervisionSession(session_id, status)
            updated = connection.execute(
                """UPDATE supervision_sessions SET status = ?, updated_at = ?
                   WHERE supervision_session_id = ? AND status = ?""",
                ("ACTIVE", now.isoformat(), session_id, status),
            ).rowcount
            if not updated:
                return self._read(session_id, connection)
            self._append(
                connection,
                session_id,
                EventType.SESSION_ACTIVATED,
                "ACTIVE",
                now,
                evidence_refs=(
                    tuple(activation_context["evidence_refs"])
                    if activation_context
                    else ()
                ),
                payload_extra=activation_context,
                execution_domain_id=(
                    str(activation_context["execution_domain_id"])
                    if activation_context
                    else None
                ),
                checkpoint_id=checkpoint_id if activation_context else None,
            )
            if verify_ledger(connection):
                raise RuntimeError("SUPERVISION_LEDGER_INVALID")
        return SupervisionSession(session_id, "ACTIVE")

    def apply_config_change(
        self,
        session_id: str,
        checkpoint_id: str,
        *,
        requested_target: Path,
        content: bytes,
    ) -> ControlledChangeResult:
        """Apply one approved TOML file change through the active R4 authority."""
        rollback: dict[str, object] = {}
        try:
            return self._apply_config_change(
                session_id,
                checkpoint_id,
                requested_target=requested_target,
                content=content,
                rollback=rollback,
            )
        except (OSError, RuntimeError, sqlite3.DatabaseError, ValueError):
            target = rollback.get("target")
            before = rollback.get("before")
            before_stat = rollback.get("before_stat")
            rolled_back = (
                _rollback_file(target, before, before_stat)
                if isinstance(target, Path)
                and isinstance(before, bytes)
                and before_stat is not None
                else False
            )
            current_digest = None
            if isinstance(target, Path):
                try:
                    current, _current_stat = _read_controlled_target(target)
                    current_digest = hashlib.sha256(current).hexdigest()
                except OSError:
                    pass
            changed = (
                isinstance(rollback.get("before_digest"), str)
                and current_digest is not None
                and current_digest != rollback["before_digest"]
            )
            result = ControlledChangeResult(
                session_id,
                "FAILED",
                "CONTROLLED_CHANGE_PERSISTENCE_FAILED",
                changed=changed,
                before_digest=rollback.get("before_digest"),
                after_digest=rollback.get("after_digest"),
                rolled_back=rolled_back,
            )
            try:
                with self._database.transaction() as connection:
                    if verify_ledger(connection):
                        connection.execute(
                            """UPDATE supervision_sessions SET status = ?, updated_at = ?
                               WHERE supervision_session_id = ? AND status = 'ACTIVE'""",
                            ("FAILED", datetime.now(UTC).isoformat(), session_id),
                        )
                    else:
                        context = rollback.get("context")
                        evidence_refs: tuple[str, ...] = ()
                        if (
                            changed
                            and isinstance(context, dict)
                            and isinstance(target, Path)
                        ):
                            effect_ref = self._append_change(
                                connection,
                                session_id=session_id,
                                checkpoint_id=checkpoint_id,
                                change_id=f"change-{uuid4()}",
                                event_type=EventType.EXTERNAL_EFFECT_UNKNOWN,
                                result="PERSISTENCE_FAILED_ROLLBACK_UNKNOWN",
                                context=context,
                                payload={
                                    "before_digest": rollback.get("before_digest"),
                                    "expected_after_digest": rollback.get("after_digest"),
                                    "observed_digest": current_digest,
                                    "rolled_back": False,
                                    "target_ref_digest": hashlib.sha256(
                                        str(target).encode("utf-8")
                                    ).hexdigest(),
                                },
                            )
                            evidence_refs = (effect_ref,)
                        result = self._fail_controlled_change(
                            connection,
                            session_id=session_id,
                            checkpoint_id=checkpoint_id,
                            reason_code="CONTROLLED_CHANGE_PERSISTENCE_FAILED",
                            context=context if isinstance(context, dict) else None,
                            before_digest=rollback.get("before_digest"),
                            after_digest=rollback.get("after_digest"),
                            changed=changed,
                            rolled_back=rolled_back,
                            evidence_refs=evidence_refs,
                        )
            except (OSError, RuntimeError, sqlite3.DatabaseError, ValueError):
                pass
            return result

    def _apply_config_change(
        self,
        session_id: str,
        checkpoint_id: str,
        *,
        requested_target: Path,
        content: bytes,
        rollback: dict[str, object],
    ) -> ControlledChangeResult:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM supervision_sessions WHERE supervision_session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError("SUPERVISION_SESSION_NOT_FOUND")
            if row[0] != "ACTIVE":
                return ControlledChangeResult(
                    session_id,
                    row[0],
                    "CONTROLLED_CHANGE_REPLAYED",
                )
            if verify_ledger(connection):
                connection.execute(
                    """UPDATE supervision_sessions SET status = ?, updated_at = ?
                       WHERE supervision_session_id = ? AND status = 'ACTIVE'""",
                    ("FAILED", datetime.now(UTC).isoformat(), session_id),
                )
                return ControlledChangeResult(
                    session_id,
                    "FAILED",
                    "CONTROLLED_CHANGE_LEDGER_INVALID",
                )
            context: dict[str, object] | None = None

            def fail(reason_code: str, **details) -> ControlledChangeResult:
                return self._fail_controlled_change(
                    connection,
                    session_id=session_id,
                    checkpoint_id=checkpoint_id,
                    reason_code=reason_code,
                    context=context,
                    **details,
                )

            context = self._controlled_change_context(
                connection,
                session_id=session_id,
                checkpoint_id=checkpoint_id,
            )
            if context is None:
                return fail("CONTROLLED_CHANGE_AUTHORITY_STALE")
            requested = Path(requested_target)
            if (
                not requested.is_absolute()
                or ".." in requested.parts
                or RestorePolicy._path_is_unsafe(requested)
            ):
                return fail("CONTROLLED_CHANGE_PATH_UNSAFE")
            entry = self._controlled_change_entry(checkpoint_id, context)
            if entry is None:
                return fail("CONTROLLED_CHANGE_AUTHORITY_STALE")
            if str(requested) != entry["logical_path"]:
                return fail("CONTROLLED_CHANGE_SCOPE_DRIFT")
            if not isinstance(content, bytes):
                return fail("CONTROLLED_CHANGE_CONTENT_INVALID")
            try:
                before, before_stat = _read_controlled_target(requested)
            except (OSError, ValueError):
                return fail("CONTROLLED_CHANGE_AUTHORITY_STALE")
            before_digest = hashlib.sha256(before).hexdigest()
            after_digest = hashlib.sha256(content).hexdigest()
            rollback.update(
                target=requested,
                before=before,
                before_stat=before_stat,
                before_digest=before_digest,
                after_digest=after_digest,
                context=context,
            )
            if before_digest != entry["sha256"]:
                if before_digest == after_digest:
                    target_ref_digest = hashlib.sha256(
                        str(requested).encode("utf-8")
                    ).hexdigest()
                    effect_ref = self._append_change(
                        connection,
                        session_id=session_id,
                        checkpoint_id=checkpoint_id,
                        change_id=f"change-{uuid4()}",
                        event_type=EventType.EXTERNAL_EFFECT_UNKNOWN,
                        result="APPROVED_CONTENT_PRESENT_WITHOUT_CHANGE_EVIDENCE",
                        context=context,
                        payload={
                            "expected_before_digest": entry["sha256"],
                            "observed_digest": before_digest,
                            "approved_after_digest": after_digest,
                            "rolled_back": False,
                            "target_ref_digest": target_ref_digest,
                        },
                    )
                    return fail(
                        "CONTROLLED_CHANGE_EXTERNAL_EFFECT_UNKNOWN",
                        before_digest=str(entry["sha256"]),
                        after_digest=before_digest,
                        changed=True,
                        evidence_refs=(effect_ref,),
                    )
                return fail(
                    "CONTROLLED_CHANGE_AUTHORITY_STALE",
                    before_digest=before_digest,
                )
            if before_digest == after_digest:
                return fail(
                    "CONTROLLED_CHANGE_NO_EFFECT",
                    before_digest=before_digest,
                    after_digest=after_digest,
                )
            target_ref_digest = hashlib.sha256(str(requested).encode("utf-8")).hexdigest()
            change_id = f"change-{uuid4()}"
            write_result = _atomic_restore(
                requested,
                content,
                {
                    "mode_oct": oct(stat.S_IMODE(before_stat.st_mode)),
                    "sha256": after_digest,
                    "_expected_before_sha256": before_digest,
                    "_expected_before_identity": (before_stat.st_dev, before_stat.st_ino),
                    "_rollback_content": before,
                    "_rollback_mode": before_stat.st_mode,
                },
            )
            if write_result.get("status") != "success":
                if write_result.get("status") == "authority_stale":
                    return fail(
                        "CONTROLLED_CHANGE_AUTHORITY_STALE",
                        before_digest=before_digest,
                        after_digest=after_digest,
                    )
                try:
                    current, _current_stat = _read_controlled_target(requested)
                    current_digest = hashlib.sha256(current).hexdigest()
                except OSError:
                    current_digest = None
                rolled_back = (
                    _rollback_file(requested, before, before_stat)
                    if current_digest != before_digest
                    else False
                )
                return fail(
                    "CONTROLLED_CHANGE_WRITE_FAILED",
                    before_digest=before_digest,
                    after_digest=after_digest,
                    rolled_back=rolled_back,
                )
            try:
                diff = _authoritative_diff(
                    requested,
                    before_digest=before_digest,
                    expected_after_digest=after_digest,
                )
            except (OSError, ValueError):
                rolled_back = _rollback_file(requested, before, before_stat)
                effect_ref = self._append_change(
                    connection,
                    session_id=session_id,
                    checkpoint_id=checkpoint_id,
                    change_id=change_id,
                    event_type=EventType.EXTERNAL_EFFECT_UNKNOWN,
                    result=(
                        "DIFF_FAILED_ROLLED_BACK"
                        if rolled_back
                        else "DIFF_FAILED_ROLLBACK_UNKNOWN"
                    ),
                    context=context,
                    payload={
                        "before_digest": before_digest,
                        "expected_after_digest": after_digest,
                        "rolled_back": rolled_back,
                        "target_ref_digest": target_ref_digest,
                    },
                )
                return fail(
                    "CONTROLLED_CHANGE_DIFF_FAILED",
                    before_digest=before_digest,
                    after_digest=after_digest,
                    rolled_back=rolled_back,
                    evidence_refs=(effect_ref,),
                )
            change_ref = self._append_change(
                connection,
                session_id=session_id,
                checkpoint_id=checkpoint_id,
                change_id=change_id,
                event_type=EventType.OBSERVED_CHANGE,
                result="CHANGED",
                context=context,
                payload={
                    **diff,
                    "workspace_id": context["workspace_id"],
                    "approved_scope_digest": context["approved_scope_digest"],
                    "manifest_digest": context["manifest_digest"],
                    "product_sha": context["product_sha"],
                    "target_ref_digest": target_ref_digest,
                },
            )
            verification = _offline_verify(
                requested,
                validator=str(entry["validator"]),
                expected_digest=after_digest,
                target_ref_digest=target_ref_digest,
            )
            if verification["result"] != "PASS":
                rolled_back = _rollback_file(requested, before, before_stat)
                return fail(
                    "CONTROLLED_CHANGE_VERIFY_FAILED",
                    before_digest=before_digest,
                    after_digest=after_digest,
                    verification=str(verification["result"]),
                    rolled_back=rolled_back,
                    evidence_refs=(change_ref,),
                    payload_extra={"verification": verification},
                )
            now = datetime.now(UTC)
            updated = connection.execute(
                """UPDATE supervision_sessions SET status = ?, updated_at = ?
                   WHERE supervision_session_id = ? AND status = 'ACTIVE'""",
                ("COMPLETED", now.isoformat(), session_id),
            ).rowcount
            if updated != 1:
                rolled_back = _rollback_file(requested, before, before_stat)
                return ControlledChangeResult(
                    session_id,
                    self._read(session_id, connection).status,
                    "CONTROLLED_CHANGE_REPLAYED",
                    False,
                    before_digest,
                    after_digest,
                    "PASS",
                    rolled_back,
                    (change_ref,),
                )
            completed_ref = self._append(
                connection,
                session_id,
                EventType.SESSION_COMPLETED,
                "COMPLETED",
                now,
                evidence_refs=(change_ref,),
                payload_extra={
                    "change_id": change_id,
                    "verification": verification,
                    "workspace_id": context["workspace_id"],
                    "approved_scope_digest": context["approved_scope_digest"],
                    "manifest_digest": context["manifest_digest"],
                    "product_sha": context["product_sha"],
                },
                execution_domain_id=str(context["execution_domain_id"]),
                checkpoint_id=checkpoint_id,
            )
            if verify_ledger(connection):
                raise RuntimeError("CONTROLLED_CHANGE_LEDGER_INVALID")
            return ControlledChangeResult(
                session_id,
                "COMPLETED",
                "CONTROLLED_CHANGE_COMPLETED",
                True,
                before_digest,
                after_digest,
                "PASS",
                False,
                (change_ref, completed_ref),
            )

    def _controlled_change_context(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        checkpoint_id: str,
    ) -> dict[str, object] | None:
        context = self._checkpoint_activation_context(
            connection,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
        )
        if context is None:
            return None
        events = connection.execute(
            """SELECT event_id, result, execution_domain_id, checkpoint_id,
                      evidence_refs_json, payload_safe_json
               FROM evidence_ledger_events
               WHERE supervision_session_id = ? AND event_type = 'SESSION_ACTIVATED'""",
            (session_id,),
        ).fetchall()
        if len(events) != 1:
            return None
        event = events[0]
        try:
            evidence_refs = json.loads(event[4])
            payload = self._json_payload(event[5])
        except (json.JSONDecodeError, SupervisionActionError):
            return None
        if (
            event[1] != "ACTIVE"
            or event[2] != context["execution_domain_id"]
            or event[3] != checkpoint_id
            or payload != {"status": "ACTIVE", **context}
            or not isinstance(evidence_refs, list)
            or set(evidence_refs) != {session_id, *context["evidence_refs"]}
        ):
            return None
        later = connection.execute(
            """SELECT COUNT(*) FROM evidence_ledger_events
               WHERE supervision_session_id = ?
                 AND event_type IN ('OBSERVED_CHANGE', 'SESSION_COMPLETED', 'SESSION_FAILED')""",
            (session_id,),
        ).fetchone()[0]
        if later:
            return None
        return {**context, "activation_event_id": event[0]}

    def _controlled_change_entry(
        self,
        checkpoint_id: str,
        context: dict[str, object],
    ) -> dict[str, object] | None:
        if self._snapshots is None or not checkpoint_id.isdecimal():
            return None
        checkpoint = self._database.get_checkpoint(int(checkpoint_id))
        if (
            checkpoint is None
            or checkpoint["git_commit"] != self._product_sha
            or checkpoint["hash_sha256"] != context["manifest_digest"]
        ):
            return None
        artifact, _reason = self._snapshots.load_recovery_v3_with_status(
            checkpoint["snapshot_path"]
        )
        if artifact is None:
            return None
        valid, _reason, digest = validate_snapshot_v3(
            artifact,
            expected_domain=str(context["execution_domain_id"]),
        )
        entries = [
            entry
            for entry in artifact.get("manifest", ())
            if entry.get("classification") == "restorable"
        ]
        if (
            not valid
            or digest != context["manifest_digest"]
            or len(entries) != 1
            or self._refs_digest((str(entries[0]["logical_path"]),))
            != context["approved_scope_digest"]
        ):
            return None
        return entries[0]

    def _fail_controlled_change(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        checkpoint_id: str,
        reason_code: str,
        context: dict[str, object] | None = None,
        before_digest: str | None = None,
        after_digest: str | None = None,
        verification: str = "NOT_RUN",
        changed: bool = False,
        rolled_back: bool = False,
        evidence_refs: tuple[str, ...] = (),
        payload_extra: dict[str, object] | None = None,
    ) -> ControlledChangeResult:
        now = datetime.now(UTC)
        connection.execute(
            """UPDATE supervision_sessions SET status = ?, updated_at = ?
               WHERE supervision_session_id = ? AND status = 'ACTIVE'""",
            ("FAILED", now.isoformat(), session_id),
        )
        event_id = self._append(
            connection,
            session_id,
            EventType.SESSION_FAILED,
            "FAILED",
            now,
            evidence_refs=evidence_refs,
            payload_extra={
                "reason_code": reason_code,
                "committed": False,
                "rolled_back": rolled_back,
                **(payload_extra or {}),
            },
            execution_domain_id=(
                str(context["execution_domain_id"]) if context else None
            ),
            checkpoint_id=checkpoint_id,
        )
        return ControlledChangeResult(
            session_id,
            "FAILED",
            reason_code,
            changed,
            before_digest,
            after_digest,
            verification,
            rolled_back,
            (*evidence_refs, event_id),
        )

    def _append_change(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        checkpoint_id: str,
        change_id: str,
        event_type: EventType,
        result: str,
        context: dict[str, object],
        payload: dict[str, object],
    ) -> str:
        event_id = f"change-event-{uuid4()}"
        self._ledger.append(
            connection,
            EvidenceEvent(
                schema_version=1,
                event_id=event_id,
                recorded_at=datetime.now(UTC),
                observed_at=None,
                event_family=EventFamily.CHANGE,
                event_type=event_type,
                source="r4-config-change",
                result=result,
                execution_domain_id=str(context["execution_domain_id"]),
                supervision_session_id=session_id,
                transaction_id=change_id,
                checkpoint_id=checkpoint_id,
                subject_ref=f"target:{payload['target_ref_digest']}",
                evidence_refs=(
                    session_id,
                    str(context["activation_event_id"]),
                    *tuple(str(item) for item in context["evidence_refs"]),
                ),
                payload_safe=payload,
            ),
        )
        return event_id

    def _checkpoint_activation_context(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        checkpoint_id: str | None,
    ) -> dict[str, object] | None:
        if (
            self._snapshots is None
            or self._product_sha is None
            or checkpoint_id is None
            or not checkpoint_id.isdecimal()
            or verify_ledger(connection)
        ):
            return None
        session_events = connection.execute(
            """SELECT sequence, event_id, event_type, result, payload_safe_json
               FROM evidence_ledger_events
               WHERE supervision_session_id = ? ORDER BY sequence""",
            (session_id,),
        ).fetchall()
        policy_events = [
            event for event in session_events
            if event[2] == EventType.POLICY_EVALUATED.value
        ]
        approvals = [
            event for event in session_events
            if event[2] == EventType.USER_APPROVED.value
        ]
        if len(policy_events) != 1 or len(approvals) != 1:
            return None
        policy_event = policy_events[0]
        approval_event = approvals[0]
        try:
            policy_payload = self._json_payload(policy_event[4])
        except SupervisionActionError:
            return None
        policy_authority = policy_payload.get("policy_authority")
        policy_digest = policy_payload.get("policy_binding_digest")
        if (
            not isinstance(policy_authority, dict)
            or not isinstance(policy_digest, str)
            or hashlib.sha256(
                canonical_json(policy_authority).encode("utf-8")
            ).hexdigest() != policy_digest
        ):
            return None
        context = policy_authority.get("activation_context")
        required_context = {
            "execution_domain_id",
            "workspace_id",
            "workspace_binding_ref",
            "approved_scope_digest",
            "target_refs_digest",
            "product_sha",
        }
        if not isinstance(context, dict) or set(context) != required_context:
            return None
        domain = context["execution_domain_id"]
        workspace = resolve_verified_workspace_binding(
            connection,
            execution_domain_id=domain if isinstance(domain, str) else None,
        )
        if (
            workspace["status"] != "BOUND"
            or workspace["workspace_id"] != context["workspace_id"]
            or context["product_sha"] != self._product_sha
            or context["approved_scope_digest"] != context["target_refs_digest"]
        ):
            return None
        checkpoint = self._database.get_checkpoint(int(checkpoint_id))
        if checkpoint is None or checkpoint["git_commit"] != self._product_sha:
            return None
        artifact, _reason = self._snapshots.load_recovery_v3_with_status(
            checkpoint["snapshot_path"]
        )
        if artifact is None:
            return None
        valid, _reason, manifest_digest = validate_snapshot_v3(
            artifact,
            expected_domain=domain,
        )
        if not valid or manifest_digest != checkpoint["hash_sha256"]:
            return None
        target_refs = tuple(
            sorted(
                entry["logical_path"]
                for entry in artifact["manifest"]
                if entry["classification"] == "restorable"
            )
        )
        if not target_refs or self._refs_digest(target_refs) != context["target_refs_digest"]:
            return None
        facts = RecoveryCoverageService(
            self._database,
            self._snapshots,
        ).compute_with_connection(
            connection,
            checkpoint_id=checkpoint_id,
            target_refs=target_refs,
            execution_domain_id=domain,
        )
        if (
            facts.status is not RecoveryCoverageStatus.COMPLETE
            or facts.authorized_snapshot_coverage != 1.0
            or facts.manifest_blob_coverage != 1.0
        ):
            return None
        checkpoint_events = connection.execute(
            """SELECT sequence, event_id, event_type, result, execution_domain_id,
                      supervision_session_id, subject_ref, payload_safe_json, curr_hash
               FROM evidence_ledger_events
               WHERE checkpoint_id = ?
                 AND event_type IN ('CHECKPOINT_CREATED', 'MANIFEST_VERIFIED')
               ORDER BY sequence""",
            (checkpoint_id,),
        ).fetchall()
        if len(checkpoint_events) != 2:
            return None
        created, manifested = checkpoint_events
        if created[2] != EventType.CHECKPOINT_CREATED.value:
            return None
        if manifested[2] != EventType.MANIFEST_VERIFIED.value:
            return None
        expected_target_digests = sorted(
            hashlib.sha256(value.encode("utf-8")).hexdigest()
            for value in target_refs
        )
        for event in checkpoint_events:
            try:
                payload = self._json_payload(event[7])
            except SupervisionActionError:
                return None
            if (
                event[3] != "AVAILABLE"
                or event[4] != domain
                or event[5] != session_id
                or event[6] != f"manifest:{manifest_digest}"
                or payload.get("manifest_digest") != manifest_digest
                or payload.get("product_sha") != self._product_sha
                or payload.get("target_ref_digests") != expected_target_digests
            ):
                return None
        if (
            not policy_event[0] < approval_event[0] < created[0] < manifested[0]
            or manifested[0] != created[0] + 1
        ):
            return None
        return {
            "execution_domain_id": domain,
            "workspace_id": context["workspace_id"],
            "workspace_binding_ref": workspace["binding_ref"],
            "approved_scope_digest": context["approved_scope_digest"],
            "manifest_digest": manifest_digest,
            "product_sha": self._product_sha,
            "evidence_refs": [
                workspace["binding_ref"],
                policy_event[1],
                approval_event[1],
                created[1],
                manifested[1],
            ],
        }

    def _checkpoint_binding_required(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> bool:
        rows = connection.execute(
            """SELECT payload_safe_json FROM evidence_ledger_events
               WHERE supervision_session_id = ? AND event_type = 'POLICY_EVALUATED'""",
            (session_id,),
        ).fetchall()
        if len(rows) != 1:
            return False
        try:
            payload = self._json_payload(rows[0][0])
        except SupervisionActionError:
            return False
        authority = payload.get("policy_authority")
        return (
            isinstance(authority, dict)
            and authority.get("checkpoint_binding_required") is True
        )

    @staticmethod
    def _refs_digest(values: tuple[str, ...]) -> str:
        return hashlib.sha256(
            canonical_json(sorted(set(values))).encode("utf-8")
        ).hexdigest()

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
        execution_domain_id: str | None = None,
        checkpoint_id: str | None = None,
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
                execution_domain_id=execution_domain_id,
                supervision_session_id=session_id,
                transaction_id=None,
                checkpoint_id=checkpoint_id,
                subject_ref=session_id,
                evidence_refs=(session_id, *evidence_refs),
                payload_safe=payload_safe,
            ),
        )
        return event_id
