"""Fail-closed P9 checkpoint activation binding over existing R4 authority."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

import agentguard.supervision.service as supervision_module
from agentguard.discovery import (
    AgentDescriptor,
    AgentLifecycleStatus,
    CapabilityStatus,
    DiscoverySnapshot,
    ExecutionDomainDescriptor,
    ExecutionDomainKind,
    ProbeEvidence,
    RuntimeDescriptor,
    WorkspaceDescriptor,
)
from agentguard.discovery.domains import SelfRuntimeAdapter
from agentguard.evidence.discovery_adapter import record_discovery_snapshot
from agentguard.evidence.ledger import EvidenceLedger, verify_ledger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.policy.models import Decision, PolicyDecision, PolicyInput
from agentguard.recovery.contracts import RecoveryOperation, RecoveryRequest
from agentguard.recovery.policy import RestorePolicy
from agentguard.recovery.service import RecoveryService
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore
from agentguard.supervision.service import SupervisionService

PRODUCT_SHA = "3f21c91dd42fd2cbddd114f34f2f970228001e86"
OTHER_SHA = "1" * 40
OBSERVED_AT = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)


def _evidence(
    evidence_id: str,
    fact_type: str,
    value: dict[str, object],
    observed_at: datetime,
) -> ProbeEvidence:
    return ProbeEvidence(
        evidence_id=evidence_id,
        collector="server-discovery",
        source="local",
        observed_at=observed_at,
        fact_type=fact_type,
        value=value,
        status=CapabilityStatus.AVAILABLE,
        sanitized=True,
    )


def _snapshot(
    *,
    snapshot_id: str = "activation-snapshot-1",
    domain_id: str = "local-domain",
    workspace_id: str = "workspace-one",
    observed_at: datetime = OBSERVED_AT,
    include_workspace: bool = True,
) -> DiscoverySnapshot:
    runtime_id = "agent-dev-runtime"
    agent_id = "cloudcli-agent"
    prefix = snapshot_id
    workspace_evidence_id = f"{prefix}-workspace"
    return DiscoverySnapshot(
        snapshot_id=snapshot_id,
        observed_at=observed_at,
        domains=(
            ExecutionDomainDescriptor(
                domain_id=domain_id,
                kind=ExecutionDomainKind.CONTAINER,
                evidence_ids=(f"{prefix}-domain",),
                confidence=0.8,
            ),
        ),
        runtimes=(
            RuntimeDescriptor(
                runtime_id=runtime_id,
                runtime_type="DOCKER_DESKTOP_WSL2",
                domain_id=domain_id,
                status=CapabilityStatus.AVAILABLE,
                evidence_ids=(f"{prefix}-runtime",),
                confidence=0.8,
            ),
        ),
        agents=(
            AgentDescriptor(
                agent_id=agent_id,
                agent_type="CLOUDCLI",
                lifecycle=AgentLifecycleStatus.RUNNING,
                domain_id=domain_id,
                runtime_id=runtime_id,
                workspace_ids=(workspace_id,),
                evidence_ids=(f"{prefix}-agent",),
                confidence=0.8,
            ),
        ),
        workspaces=(
            (
                WorkspaceDescriptor(
                    workspace_id=workspace_id,
                    domain_id=domain_id,
                    runtime_ids=(runtime_id,),
                    agent_ids=(agent_id,),
                    evidence_ids=(workspace_evidence_id,),
                    confidence=0.8,
                ),
            )
            if include_workspace
            else ()
        ),
        evidence=(
            _evidence(f"{prefix}-domain", "domain.container", {"kind": "CONTAINER"}, observed_at),
            _evidence(
                f"{prefix}-runtime",
                "runtime.metadata",
                {"runtime_kind": "DOCKER_DESKTOP_WSL2"},
                observed_at,
            ),
            _evidence(
                f"{prefix}-agent",
                "agent.metadata",
                {"agent_kind": "CLOUDCLI", "role": "AGENT_HOST"},
                observed_at,
            ),
            _evidence(
                workspace_evidence_id,
                "workspace.present",
                {"workspace_kind": "PROJECT"},
                observed_at,
            ),
        ),
        status=CapabilityStatus.AVAILABLE,
    )


@pytest.fixture
def authority(tmp_path):
    database = StateDB(tmp_path / "state.db")
    database.connect()
    snapshots = SnapshotStore(tmp_path / "snapshots")
    try:
        yield database, snapshots
    finally:
        database.close()


def _policy_input(target, *, domain_id: str = "local-domain", evidence_refs=()) -> PolicyInput:
    target_ref = str(target)
    return PolicyInput(
        intent_kind="change",
        effect_kind="provider_config_change",
        target_refs=(target_ref,),
        execution_domain_id=domain_id,
        declared_scope=(target_ref,),
        requested_capabilities=(),
        network_effect=False,
        privilege_effect=False,
        destructive_effect=False,
        secret_access=False,
        evidence_refs=tuple(evidence_refs),
    )


def _record_workspace(database: StateDB, **overrides):
    snapshot = _snapshot(**overrides)
    return record_discovery_snapshot(database, snapshot, recorded_at=snapshot.observed_at)


def _sessions(database: StateDB, snapshots: SnapshotStore, *, product_sha=PRODUCT_SHA):
    return SupervisionService(database, snapshots=snapshots, product_sha=product_sha)


def _recovery(
    database: StateDB,
    snapshots: SnapshotStore,
    approved_paths,
    *,
    product_sha=PRODUCT_SHA,
):
    policy = RestorePolicy(
        approved_paths={domain: tuple(paths) for domain, paths in approved_paths.items()},
        validators={domain: "toml-parse" for domain in approved_paths},
    )
    return RecoveryService(
        database=database,
        snapshots=snapshots,
        adapters={
            domain: SelfRuntimeAdapter(recovery_policy=policy)
            for domain in approved_paths
        },
        product_sha=product_sha,
    )


def _approved_session(database, snapshots, target, *, domain_id="local-domain"):
    refs = _record_workspace(database)
    sessions = _sessions(database, snapshots)
    session, decision, _facts = sessions.create_authoritative(
        "provider config change",
        _policy_input(target, domain_id=domain_id, evidence_refs=(item.event_id for item in refs)),
        checkpoint_id=None,
    )
    assert decision.decision is Decision.REVIEW
    assert decision.requires_checkpoint is True
    action_ref = sessions.action_ref(session.supervision_session_id)
    assert action_ref is not None
    sessions.approve_once(session.supervision_session_id, action_ref)
    return sessions, session.supervision_session_id


def _bound_checkpoint(
    database,
    snapshots,
    target,
    session_id,
    *,
    domain_id="local-domain",
    product_sha=PRODUCT_SHA,
):
    recovery = _recovery(
        database,
        snapshots,
        {domain_id: (target,)},
        product_sha=product_sha,
    )
    return recovery.snapshot(
        RecoveryRequest(
            operation=RecoveryOperation.SNAPSHOT,
            execution_domain_id=domain_id,
            target_path=target,
            user_approved=True,
            supervision_session_id=session_id,
        )
    )


def _active_session(database, snapshots, target):
    sessions, session_id = _approved_session(database, snapshots, target)
    created = _bound_checkpoint(database, snapshots, target, session_id)
    assert sessions.activate(session_id, created.checkpoint_id).status == "ACTIVE"
    return sessions, session_id, created.checkpoint_id


def _activation_count(database: StateDB) -> int:
    return database._conn.execute(
        "SELECT COUNT(*) FROM evidence_ledger_events "
        "WHERE event_type = 'SESSION_ACTIVATED' AND result = 'ACTIVE'"
    ).fetchone()[0]


def _session_status(database: StateDB, session_id: str) -> str:
    return database._conn.execute(
        "SELECT status FROM supervision_sessions WHERE supervision_session_id = ?",
        (session_id,),
    ).fetchone()[0]


def test_valid_authority_chain_allows_activation_without_executing_change(authority, tmp_path):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    original = b"safe=true\n"
    target.write_bytes(original)
    sessions, session_id = _approved_session(database, snapshots, target)
    created = _bound_checkpoint(database, snapshots, target, session_id)

    activated = sessions.activate(session_id, created.checkpoint_id)

    assert activated.status == "ACTIVE"
    assert target.read_bytes() == original
    assert _activation_count(database) == 1
    assert verify_ledger(database._conn) == []
    event = database._conn.execute(
        """SELECT supervision_session_id, checkpoint_id, execution_domain_id,
                  payload_safe_json
           FROM evidence_ledger_events
           WHERE event_type = 'SESSION_ACTIVATED' AND result = 'ACTIVE'"""
    ).fetchone()
    assert event[:3] == (session_id, created.checkpoint_id, "local-domain")
    payload = json.loads(event[3])
    assert payload["product_sha"] == PRODUCT_SHA
    assert payload["workspace_id"] == "workspace-one"
    assert str(target) not in event[3]


def test_active_authority_changes_one_config_then_diffs_and_verifies_offline(
    authority,
    tmp_path,
):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    dirty = tmp_path / "unrelated-dirty.txt"
    before = b"safe = false\n"
    after = b"safe = true\n"
    dirty_before = b"leave me alone\n"
    target.write_bytes(before)
    dirty.write_bytes(dirty_before)
    sessions, session_id, checkpoint_id = _active_session(
        database,
        snapshots,
        target,
    )

    result = sessions.apply_config_change(
        session_id,
        checkpoint_id,
        requested_target=target,
        content=after,
    )

    assert result.status == "COMPLETED"
    assert result.reason_code == "CONTROLLED_CHANGE_COMPLETED"
    assert result.changed is True
    assert result.before_digest == hashlib.sha256(before).hexdigest()
    assert result.after_digest == hashlib.sha256(after).hexdigest()
    assert result.verification == "PASS"
    assert target.read_bytes() == after
    assert dirty.read_bytes() == dirty_before
    assert verify_ledger(database._conn) == []
    events = database._conn.execute(
        """SELECT event_type, result, checkpoint_id, execution_domain_id,
                  payload_safe_json
           FROM evidence_ledger_events
           WHERE supervision_session_id = ?
             AND event_type IN ('OBSERVED_CHANGE', 'SESSION_COMPLETED')
           ORDER BY sequence""",
        (session_id,),
    ).fetchall()
    assert [(event[0], event[1]) for event in events] == [
        ("OBSERVED_CHANGE", "CHANGED"),
        ("SESSION_COMPLETED", "COMPLETED"),
    ]
    change = json.loads(events[0][4])
    assert events[0][2:4] == (checkpoint_id, "local-domain")
    assert change["before_digest"] == hashlib.sha256(before).hexdigest()
    assert change["after_digest"] == hashlib.sha256(after).hexdigest()
    assert change["changed"] is True
    assert change["workspace_id"] == "workspace-one"
    assert change["approved_scope_digest"]
    assert change["target_ref_digest"]
    completed = json.loads(events[1][4])
    assert completed["verification"] == {
        "method": "tomllib.loads",
        "network_used": False,
        "result": "PASS",
        "target_ref_digest": change["target_ref_digest"],
        "verified_digest": hashlib.sha256(after).hexdigest(),
    }
    assert str(target) not in events[0][4]
    assert str(target) not in events[1][4]


@pytest.mark.parametrize(
    ("requested", "reason_code"),
    [
        ("other", "CONTROLLED_CHANGE_SCOPE_DRIFT"),
        ("traversal", "CONTROLLED_CHANGE_PATH_UNSAFE"),
    ],
)
def test_unapproved_or_traversal_target_fails_without_mutation(
    authority,
    tmp_path,
    requested,
    reason_code,
):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    other = tmp_path / "other.toml"
    before = b"safe = false\n"
    target.write_bytes(before)
    other.write_bytes(b"other = true\n")
    sessions, session_id, checkpoint_id = _active_session(database, snapshots, target)
    requested_target = (
        other
        if requested == "other"
        else target.parent / "nested" / ".." / target.name
    )

    result = sessions.apply_config_change(
        session_id,
        checkpoint_id,
        requested_target=requested_target,
        content=b"safe = true\n",
    )

    assert result.status == "FAILED"
    assert result.reason_code == reason_code
    assert result.changed is False
    assert target.read_bytes() == before
    assert other.read_bytes() == b"other = true\n"
    assert _session_status(database, session_id) == "FAILED"
    assert verify_ledger(database._conn) == []


def test_symlink_escape_fails_without_touching_link_target(authority, tmp_path):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    outside = tmp_path / "outside.toml"
    target.write_bytes(b"safe = false\n")
    sessions, session_id, checkpoint_id = _active_session(database, snapshots, target)
    target.unlink()
    outside.write_bytes(b"outside = true\n")
    target.symlink_to(outside)

    result = sessions.apply_config_change(
        session_id,
        checkpoint_id,
        requested_target=target,
        content=b"safe = true\n",
    )

    assert result.status == "FAILED"
    assert result.reason_code == "CONTROLLED_CHANGE_PATH_UNSAFE"
    assert outside.read_bytes() == b"outside = true\n"
    assert target.is_symlink()
    assert verify_ledger(database._conn) == []


def test_atomic_write_failure_never_records_observed_change(
    authority,
    tmp_path,
    monkeypatch,
):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    before = b"safe = false\n"
    target.write_bytes(before)
    sessions, session_id, checkpoint_id = _active_session(database, snapshots, target)
    def partial_write(path, content, _entry):
        path.write_bytes(content)
        return {"status": "error", "message": "injected after write"}

    monkeypatch.setattr(supervision_module, "_atomic_restore", partial_write)

    result = sessions.apply_config_change(
        session_id,
        checkpoint_id,
        requested_target=target,
        content=b"safe = true\n",
    )

    assert result.status == "FAILED"
    assert result.reason_code == "CONTROLLED_CHANGE_WRITE_FAILED"
    assert result.rolled_back is True
    assert target.read_bytes() == before
    assert database._conn.execute(
        "SELECT COUNT(*) FROM evidence_ledger_events WHERE event_type = 'OBSERVED_CHANGE'"
    ).fetchone()[0] == 0
    assert verify_ledger(database._conn) == []


def test_diff_failure_rolls_back_and_records_failed_external_effect(
    authority,
    tmp_path,
    monkeypatch,
):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    before = b"safe = false\n"
    target.write_bytes(before)
    sessions, session_id, checkpoint_id = _active_session(database, snapshots, target)

    def fail_diff(*_args, **_kwargs):
        raise OSError("injected diff failure")

    monkeypatch.setattr(supervision_module, "_authoritative_diff", fail_diff)

    result = sessions.apply_config_change(
        session_id,
        checkpoint_id,
        requested_target=target,
        content=b"safe = true\n",
    )

    assert result.status == "FAILED"
    assert result.reason_code == "CONTROLLED_CHANGE_DIFF_FAILED"
    assert result.rolled_back is True
    assert target.read_bytes() == before
    assert database._conn.execute(
        "SELECT result FROM evidence_ledger_events WHERE event_type = 'EXTERNAL_EFFECT_UNKNOWN'"
    ).fetchone()[0] == "DIFF_FAILED_ROLLED_BACK"
    assert verify_ledger(database._conn) == []


def test_persistence_commit_failure_rolls_back_filesystem_change(authority, tmp_path):
    class CommitFailConnection(sqlite3.Connection):
        fail_next_commit = False

        def commit(self):
            if self.fail_next_commit:
                self.fail_next_commit = False
                raise sqlite3.OperationalError("injected commit failure")
            return super().commit()

    database, snapshots = authority
    target = tmp_path / "config.toml"
    before = b"safe = false\n"
    target.write_bytes(before)
    sessions, session_id, checkpoint_id = _active_session(database, snapshots, target)
    database._conn.close()
    database._conn = sqlite3.connect(
        str(database.db_path),
        factory=CommitFailConnection,
    )
    database._conn.fail_next_commit = True

    result = sessions.apply_config_change(
        session_id,
        checkpoint_id,
        requested_target=target,
        content=b"safe = true\n",
    )

    assert result.status == "FAILED"
    assert result.reason_code == "CONTROLLED_CHANGE_PERSISTENCE_FAILED"
    assert result.rolled_back is True
    assert target.read_bytes() == before
    assert _session_status(database, session_id) == "FAILED"
    assert database._conn.execute(
        "SELECT COUNT(*) FROM evidence_ledger_events WHERE event_type = 'OBSERVED_CHANGE'"
    ).fetchone()[0] == 0
    assert verify_ledger(database._conn) == []


def test_offline_verify_failure_rolls_back_and_cannot_complete(authority, tmp_path):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    before = b"safe = false\n"
    invalid = b"safe = [\n"
    target.write_bytes(before)
    sessions, session_id, checkpoint_id = _active_session(database, snapshots, target)

    result = sessions.apply_config_change(
        session_id,
        checkpoint_id,
        requested_target=target,
        content=invalid,
    )

    assert result.status == "FAILED"
    assert result.reason_code == "CONTROLLED_CHANGE_VERIFY_FAILED"
    assert result.verification == "FAIL"
    assert result.rolled_back is True
    assert target.read_bytes() == before
    assert _session_status(database, session_id) == "FAILED"
    types = [
        row[0]
        for row in database._conn.execute(
            "SELECT event_type FROM evidence_ledger_events WHERE supervision_session_id = ?",
            (session_id,),
        )
    ]
    assert "OBSERVED_CHANGE" in types
    assert "SESSION_COMPLETED" not in types
    assert types[-1] == "SESSION_FAILED"
    assert verify_ledger(database._conn) == []


def test_corrupt_ledger_fails_closed_before_filesystem_change(authority, tmp_path):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    before = b"safe = false\n"
    target.write_bytes(before)
    sessions, session_id, checkpoint_id = _active_session(database, snapshots, target)
    database._conn.execute("DROP TRIGGER evidence_ledger_events_no_update")
    database._conn.execute(
        "UPDATE evidence_ledger_events SET result = 'forged' WHERE event_type = 'SESSION_ACTIVATED'"
    )
    database._conn.commit()

    result = sessions.apply_config_change(
        session_id,
        checkpoint_id,
        requested_target=target,
        content=b"safe = true\n",
    )

    assert result.status == "FAILED"
    assert result.reason_code == "CONTROLLED_CHANGE_LEDGER_INVALID"
    assert result.evidence_refs == ()
    assert target.read_bytes() == before
    assert _session_status(database, session_id) == "FAILED"
    assert verify_ledger(database._conn)


def test_completed_change_cannot_be_replayed(authority, tmp_path):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    target.write_bytes(b"safe = false\n")
    sessions, session_id, checkpoint_id = _active_session(database, snapshots, target)
    first = sessions.apply_config_change(
        session_id,
        checkpoint_id,
        requested_target=target,
        content=b"safe = true\n",
    )

    replay = sessions.apply_config_change(
        session_id,
        checkpoint_id,
        requested_target=target,
        content=b"safe = false\n",
    )

    assert first.status == "COMPLETED"
    assert replay.status == "COMPLETED"
    assert replay.reason_code == "CONTROLLED_CHANGE_REPLAYED"
    assert replay.changed is False
    assert target.read_bytes() == b"safe = true\n"
    assert database._conn.execute(
        "SELECT COUNT(*) FROM evidence_ledger_events WHERE event_type = 'OBSERVED_CHANGE'"
    ).fetchone()[0] == 1
    assert verify_ledger(database._conn) == []


@pytest.mark.parametrize(
    "authority_change",
    ["stale-target", "wrong-checkpoint", "cross-workspace", "wrong-product-sha"],
)
def test_active_authority_is_revalidated_immediately_before_change(
    authority,
    tmp_path,
    authority_change,
):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    before = b"safe = false\n"
    target.write_bytes(before)
    sessions, session_id, checkpoint_id = _active_session(database, snapshots, target)
    requested_checkpoint = checkpoint_id
    expected_bytes = before
    if authority_change == "stale-target":
        expected_bytes = b"external = true\n"
        target.write_bytes(expected_bytes)
    elif authority_change == "wrong-checkpoint":
        requested_checkpoint = "999999"
    elif authority_change == "cross-workspace":
        later = OBSERVED_AT + timedelta(minutes=1)
        _record_workspace(
            database,
            snapshot_id="post-activation-workspace",
            workspace_id="workspace-two",
            observed_at=later,
        )
    else:
        sessions = _sessions(database, snapshots, product_sha=OTHER_SHA)

    result = sessions.apply_config_change(
        session_id,
        requested_checkpoint,
        requested_target=target,
        content=b"safe = true\n",
    )

    assert result.status == "FAILED"
    assert result.reason_code.startswith("CONTROLLED_CHANGE_")
    assert result.changed is False
    assert target.read_bytes() == expected_bytes
    assert _session_status(database, session_id) == "FAILED"
    assert verify_ledger(database._conn) == []


def test_required_checkpoint_missing_never_activates(authority, tmp_path):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    sessions, session_id = _approved_session(database, snapshots, target)

    assert sessions.activate(session_id, None).status == "APPROVED"
    assert _activation_count(database) == 0


def test_checkpoint_bound_to_another_session_never_activates(authority, tmp_path):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    sessions, first_id = _approved_session(database, snapshots, target)
    created = _bound_checkpoint(database, snapshots, target, first_id)
    second, _decision_value, _facts = sessions.create_authoritative(
        "second provider config change",
        _policy_input(target, evidence_refs=("second-evidence",)),
        checkpoint_id=None,
    )
    second_ref = sessions.action_ref(second.supervision_session_id)
    assert second_ref is not None
    sessions.approve_once(second.supervision_session_id, second_ref)

    assert sessions.activate(second.supervision_session_id, created.checkpoint_id).status == "APPROVED"
    assert _activation_count(database) == 0


@pytest.mark.parametrize(
    ("checkpoint_domain", "checkpoint_target", "checkpoint_sha"),
    [
        ("other-domain", "same", PRODUCT_SHA),
        ("local-domain", "other", PRODUCT_SHA),
        ("local-domain", "same", OTHER_SHA),
    ],
    ids=("wrong-domain", "wrong-scope", "wrong-product-sha"),
)
def test_mismatched_checkpoint_authority_never_activates(
    authority,
    tmp_path,
    checkpoint_domain,
    checkpoint_target,
    checkpoint_sha,
):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    other = tmp_path / "other.toml"
    target.write_text("safe=true")
    other.write_text("other=true")
    sessions, session_id = _approved_session(database, snapshots, target)
    chosen = target if checkpoint_target == "same" else other
    created = _bound_checkpoint(
        database,
        snapshots,
        chosen,
        session_id,
        domain_id=checkpoint_domain,
        product_sha=checkpoint_sha,
    )

    assert sessions.activate(session_id, created.checkpoint_id).status == "APPROVED"
    assert _activation_count(database) == 0


def test_checkpoint_created_before_approval_is_stale_for_activation(authority, tmp_path):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    refs = _record_workspace(database)
    sessions = _sessions(database, snapshots)
    session, _decision_value, _facts = sessions.create_authoritative(
        "provider config change",
        _policy_input(target, evidence_refs=(item.event_id for item in refs)),
        checkpoint_id=None,
    )
    created = _bound_checkpoint(database, snapshots, target, session.supervision_session_id)
    action_ref = sessions.action_ref(session.supervision_session_id)
    assert action_ref is not None
    sessions.approve_once(session.supervision_session_id, action_ref)

    assert sessions.activate(session.supervision_session_id, created.checkpoint_id).status == "APPROVED"
    assert _activation_count(database) == 0


def test_preexisting_unbound_checkpoint_cannot_bypass_activation_binding(authority, tmp_path):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    refs = _record_workspace(database)
    unbound = _recovery(
        database,
        snapshots,
        {"local-domain": (target,)},
    ).snapshot(
        RecoveryRequest(
            operation=RecoveryOperation.SNAPSHOT,
            execution_domain_id="local-domain",
            target_path=target,
            user_approved=True,
        )
    )
    sessions = _sessions(database, snapshots)
    session, decision, _facts = sessions.create_authoritative(
        "provider config change",
        _policy_input(target, evidence_refs=(item.event_id for item in refs)),
        checkpoint_id=unbound.checkpoint_id,
    )
    assert decision.requires_checkpoint is False
    action_ref = sessions.action_ref(session.supervision_session_id)
    assert action_ref is not None
    sessions.approve_once(session.supervision_session_id, action_ref)

    assert sessions.activate(
        session.supervision_session_id,
        unbound.checkpoint_id,
    ).status == "APPROVED"
    assert _activation_count(database) == 0


def test_corrupt_checkpoint_manifest_never_activates(authority, tmp_path):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    sessions, session_id = _approved_session(database, snapshots, target)
    created = _bound_checkpoint(database, snapshots, target, session_id)
    checkpoint = database.get_checkpoint(int(created.checkpoint_id))
    artifact = snapshots.load(checkpoint["snapshot_path"])
    blob = next(iter(artifact["blobs"]))
    artifact["blobs"][blob] = b"corrupt"
    snapshots.save(int(created.checkpoint_id), artifact)

    assert sessions.activate(session_id, created.checkpoint_id).status == "APPROVED"
    assert _activation_count(database) == 0


def test_unrelated_ledger_change_does_not_replace_bound_checkpoint_authority(
    authority,
    tmp_path,
):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    sessions, session_id = _approved_session(database, snapshots, target)
    created = _bound_checkpoint(database, snapshots, target, session_id)
    with database.transaction() as connection:
        EvidenceLedger().append(
            connection,
            EvidenceEvent(
                schema_version=1,
                event_id="unrelated-ledger-change",
                recorded_at=OBSERVED_AT + timedelta(minutes=2),
                observed_at=OBSERVED_AT + timedelta(minutes=2),
                event_family=EventFamily.DISCOVERY,
                event_type=EventType.PROBE_UNREACHABLE,
                source="unrelated-probe",
                result="unreachable",
                execution_domain_id="unrelated-domain",
                supervision_session_id=None,
                transaction_id=None,
                checkpoint_id=None,
                subject_ref="unrelated-probe",
                evidence_refs=("unrelated-probe",),
                payload_safe={"fact_type": "probe.unreachable", "value": {}},
            ),
        )

    assert sessions.activate(session_id, created.checkpoint_id).status == "ACTIVE"
    assert _activation_count(database) == 1


def test_missing_or_cross_domain_workspace_authority_never_activates(authority, tmp_path):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    sessions = _sessions(database, snapshots)

    missing, _decision_value, _facts = sessions.create_authoritative(
        "provider config change",
        _policy_input(target, evidence_refs=("caller-only",)),
        checkpoint_id=None,
    )
    missing_ref = sessions.action_ref(missing.supervision_session_id)
    assert missing_ref is not None
    sessions.approve_once(missing.supervision_session_id, missing_ref)
    assert sessions.activate(missing.supervision_session_id, None).status == "APPROVED"

    _record_workspace(database)
    cross_domain, _decision_value, _facts = sessions.create_authoritative(
        "provider config change",
        _policy_input(target, domain_id="other-domain", evidence_refs=("caller-only",)),
        checkpoint_id=None,
    )
    cross_ref = sessions.action_ref(cross_domain.supervision_session_id)
    assert cross_ref is not None
    sessions.approve_once(cross_domain.supervision_session_id, cross_ref)
    assert sessions.activate(cross_domain.supervision_session_id, None).status == "APPROVED"
    assert _activation_count(database) == 0


def test_stale_or_conflicting_workspace_authority_cannot_create_activation_chain(authority, tmp_path):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    first = _snapshot()
    record_discovery_snapshot(database, first, recorded_at=first.observed_at)
    later = first.observed_at + timedelta(minutes=1)
    stale = _snapshot(
        snapshot_id="activation-snapshot-2",
        observed_at=later,
        include_workspace=False,
    )
    record_discovery_snapshot(database, stale, recorded_at=later)

    sessions = _sessions(database, snapshots)
    session, _decision_value, _facts = sessions.create_authoritative(
        "provider config change",
        _policy_input(target, evidence_refs=("caller-only",)),
        checkpoint_id=None,
    )
    action_ref = sessions.action_ref(session.supervision_session_id)
    assert action_ref is not None
    sessions.approve_once(session.supervision_session_id, action_ref)
    assert sessions.activate(session.supervision_session_id, None).status == "APPROVED"
    assert _activation_count(database) == 0


def test_cross_workspace_binding_after_policy_never_activates(authority, tmp_path):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    refs = _record_workspace(database)
    sessions = _sessions(database, snapshots)
    session, _decision_value, _facts = sessions.create_authoritative(
        "provider config change",
        _policy_input(target, evidence_refs=(item.event_id for item in refs)),
        checkpoint_id=None,
    )
    later = OBSERVED_AT + timedelta(minutes=1)
    _record_workspace(
        database,
        snapshot_id="activation-snapshot-cross-workspace",
        workspace_id="workspace-two",
        observed_at=later,
    )
    action_ref = sessions.action_ref(session.supervision_session_id)
    assert action_ref is not None
    sessions.approve_once(session.supervision_session_id, action_ref)
    created = _bound_checkpoint(
        database,
        snapshots,
        target,
        session.supervision_session_id,
    )

    assert sessions.activate(
        session.supervision_session_id,
        created.checkpoint_id,
    ).status == "APPROVED"
    assert _activation_count(database) == 0


def test_corrupt_ledger_cannot_create_or_activate_authority(authority, tmp_path):
    database, snapshots = authority
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    sessions, session_id = _approved_session(database, snapshots, target)
    created = _bound_checkpoint(database, snapshots, target, session_id)
    database._conn.execute("DROP TRIGGER evidence_ledger_events_no_update")
    database._conn.execute(
        "UPDATE evidence_ledger_events SET result = 'forged' WHERE event_id = ?",
        (created.checkpoint_id,),
    )
    database._conn.execute(
        "UPDATE evidence_ledger_events SET result = 'forged' WHERE event_type = 'MANIFEST_VERIFIED'"
    )
    database._conn.commit()

    assert sessions.activate(session_id, created.checkpoint_id).status == "APPROVED"
    assert _activation_count(database) == 0


@pytest.mark.parametrize("decision", [Decision.BLOCK, Decision.UNKNOWN, Decision.REVIEW])
def test_policy_non_allowing_states_never_activate(authority, decision):
    database, snapshots = authority
    sessions = _sessions(database, snapshots)
    policy = PolicyDecision(
        decision=decision,
        severity="HIGH" if decision is Decision.BLOCK else "UNKNOWN",
        matched_rule_ids=("test-policy-gate",),
        summary_code="TEST_POLICY_GATE",
        evidence_refs=("evidence-1",),
        uncertainties=(),
        required_checks=(),
        requires_checkpoint=True,
        requires_manual_approval=decision is Decision.REVIEW,
    )
    session = sessions.create("policy gate", policy)

    assert sessions.activate(session.supervision_session_id, "1").status != "ACTIVE"
    assert _activation_count(database) == 0
