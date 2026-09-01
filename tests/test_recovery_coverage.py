"""Authoritative P6 recovery coverage facts for policy and supervision."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

import pytest

from agentguard.discovery.domains import SelfRuntimeAdapter
from agentguard.discovery.workspace_authority import workspace_root_digest
from agentguard.evidence.canonical import flatten_bounded_digest_tree
from agentguard.evidence.ledger import EvidenceLedger, verify_ledger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.policy.models import Decision, PolicyInput
from agentguard.recovery.contracts import RecoveryOperation, RecoveryRequest
from agentguard.recovery.coverage import RecoveryCoverageService, RecoveryCoverageStatus
from agentguard.recovery.policy import RestorePolicy
from agentguard.recovery.service import RecoveryService, _target_ref_digest_payload
from agentguard.recovery.workspace_adapter import HostWorkspaceRecoveryAdapter
from agentguard.recovery.workspace_permissions import PosixPermissionBackend
from agentguard.recovery.workspace_scope import DurableWorkspaceScope
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore
from agentguard.supervision.service import SupervisionService


def _policy_input(target: str) -> PolicyInput:
    return PolicyInput(
        intent_kind="change",
        effect_kind="provider_config_change",
        target_refs=(target,),
        execution_domain_id="local-domain",
        declared_scope=(target,),
        requested_capabilities=(),
        network_effect=False,
        privilege_effect=False,
        destructive_effect=False,
        secret_access=False,
        evidence_refs=("evidence-1",),
    )


def _record_workspace_authority(database: StateDB) -> None:
    observed_at = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    common = {
        "schema_version": 1,
        "recorded_at": observed_at,
        "observed_at": observed_at,
        "event_family": EventFamily.DISCOVERY,
        "source": "test-discovery",
        "result": "available",
        "execution_domain_id": "local-domain",
        "supervision_session_id": None,
        "transaction_id": None,
        "checkpoint_id": None,
    }
    events = (
        EvidenceEvent(
            **common,
            event_id="coverage-runtime-event",
            event_type=EventType.RUNTIME_DETECTED,
            subject_ref="coverage-runtime",
            evidence_refs=("coverage-runtime-source",),
            payload_safe={
                "fact_type": "runtime.metadata",
                "snapshot_id": "coverage-snapshot",
                "runtime_id": "coverage-runtime",
                "value": {"runtime_kind": "SELF_RUNTIME"},
            },
        ),
        EvidenceEvent(
            **common,
            event_id="coverage-agent-event",
            event_type=EventType.AGENT_DETECTED,
            subject_ref="coverage-agent",
            evidence_refs=("coverage-agent-source",),
            payload_safe={
                "fact_type": "agent.metadata",
                "snapshot_id": "coverage-snapshot",
                "agent_id": "coverage-agent",
                "agent_type": "CLOUDCLI",
                "runtime_id": "coverage-runtime",
                "workspace_ids": ["coverage-workspace"],
                "confidence": 0.8,
                "value": {"agent_kind": "CLOUDCLI"},
            },
        ),
        EvidenceEvent(
            **common,
            event_id="coverage-workspace-binding",
            event_type=EventType.WORKSPACE_LINKED,
            subject_ref="coverage-agent",
            evidence_refs=("coverage-workspace-source",),
            payload_safe={
                "fact_type": "workspace.binding",
                "snapshot_id": "coverage-snapshot",
                "binding_id": "coverage-workspace-binding",
                "workspace_id": "coverage-workspace",
                "runtime_id": "coverage-runtime",
                "agent_id": "coverage-agent",
                "runtime_event_id": "coverage-runtime-event",
                "agent_event_id": "coverage-agent-event",
            },
        ),
    )
    with database.transaction() as connection:
        ledger = EvidenceLedger()
        for event in events:
            ledger.append(connection, event)


def _recovery(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    database = StateDB(tmp_path / "state.db")
    database.connect()
    snapshots = SnapshotStore(tmp_path / "snapshots")
    recovery = RecoveryService(
        database=database,
        snapshots=snapshots,
        adapters={
            "local-domain": SelfRuntimeAdapter(
                recovery_policy=RestorePolicy(
                    approved_paths={"local-domain": (target,)},
                    validators={"local-domain": "toml-parse"},
                )
            )
        },
    )
    created = recovery.snapshot(
        RecoveryRequest(
            operation=RecoveryOperation.SNAPSHOT,
            execution_domain_id="local-domain",
            target_path=target,
            user_approved=True,
        )
    )
    return target, database, snapshots, created


def test_recursive_target_ref_tree_preserves_and_validates_all_refs(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for index in range(65):
        (workspace / f"target-{index:03d}.txt").write_text(
            f"target-{index}", encoding="utf-8"
        )
    database = StateDB(tmp_path / "state.db")
    database.connect()
    snapshots = SnapshotStore(tmp_path / "snapshots")
    scope = DurableWorkspaceScope(
        observation_id="target-tree-observation",
        discovery_snapshot_id="target-tree-discovery",
        observed_at=datetime(2026, 8, 9, 10, 0, tzinfo=UTC),
        recorded_at=datetime(2026, 8, 9, 10, 0, tzinfo=UTC),
        workspace_id="workspace-target-tree",
        execution_domain_id="local-domain",
        root_path=workspace.resolve(),
        root_digest=workspace_root_digest(workspace.resolve(), "local-domain"),
    )
    recovery = RecoveryService(
        database=database,
        snapshots=snapshots,
        adapters={
            "local-domain": HostWorkspaceRecoveryAdapter(
                scope=scope,
                permission_backend=PosixPermissionBackend(),
                quarantine_root=tmp_path / "quarantine",
            )
        },
    )
    created = recovery.snapshot(
        RecoveryRequest(
            operation=RecoveryOperation.SNAPSHOT,
            execution_domain_id="local-domain",
            user_approved=True,
        )
    )
    assert created.ok
    assert created.checkpoint_id is not None
    assert created.manifest_digest is not None
    checkpoint = database.get_checkpoint(int(created.checkpoint_id))
    artifact = snapshots.load_recovery_v3(checkpoint["snapshot_path"])
    target_refs = recovery._target_ref_digests(artifact)
    assert len(target_refs) == 65
    tree = _target_ref_digest_payload(target_refs)
    assert len(tree) == 2
    assert sum(len(branch) for branch in tree) == 65
    assert sorted(item for branch in tree for item in branch) == sorted(target_refs)

    try:
        rows = database._conn.execute(
            """SELECT event_type, payload_safe_json
               FROM evidence_ledger_events
               WHERE checkpoint_id = ?
                 AND event_type IN ('CHECKPOINT_CREATED', 'MANIFEST_VERIFIED')
               ORDER BY sequence""",
            (created.checkpoint_id,),
        ).fetchall()
        assert [row[0] for row in rows] == [
            "CHECKPOINT_CREATED",
            "MANIFEST_VERIFIED",
        ]
        assert all(json.loads(row[1])["target_ref_digests"] == tree for row in rows)
        assert verify_ledger(database._conn) == []

        refs, authorized = RecoveryCoverageService._snapshot_evidence(
            database._conn,
            created.checkpoint_id,
            "local-domain",
            created.manifest_digest,
        )
        assert len(refs) == 2
        assert authorized == set(target_refs)

        tampered = _target_ref_digest_payload(("0" * 64, *target_refs[1:]))
        with database.transaction() as connection:
            EvidenceLedger().append(
                connection,
                EvidenceEvent(
                    schema_version=1,
                    event_id="tree-mismatch",
                    recorded_at=datetime(2026, 8, 9, 10, 1, tzinfo=UTC),
                    observed_at=None,
                    event_family=EventFamily.RECOVERY,
                    event_type=EventType.MANIFEST_VERIFIED,
                    source="test-recovery",
                    result="AVAILABLE",
                    execution_domain_id="local-domain",
                    supervision_session_id=None,
                    transaction_id=None,
                    checkpoint_id=created.checkpoint_id,
                    subject_ref=f"manifest:{created.manifest_digest}",
                    evidence_refs=(),
                    payload_safe={
                        "manifest_digest": created.manifest_digest,
                        "target_ref_digests": tampered,
                    },
                ),
            )
        refs, authorized = RecoveryCoverageService._snapshot_evidence(
            database._conn,
            created.checkpoint_id,
            "local-domain",
            created.manifest_digest,
        )
        assert refs == ()
        assert authorized == set()
        assert verify_ledger(database._conn) == []
    finally:
        database.close()


def test_target_ref_digest_tree_recurses_beyond_two_levels_without_truncation():
    target_refs = tuple(
        hashlib.sha256(f"deep-target-{index}".encode()).hexdigest()
        for index in range(4_097)
    )

    tree = _target_ref_digest_payload(target_refs)

    assert len(tree) == 2
    assert flatten_bounded_digest_tree(tree) == target_refs
    assert flatten_bounded_digest_tree([*tree, ["not-a-digest"]]) is None


def test_coverage_distinguishes_snapshot_integrity_and_unrun_test_restore(tmp_path):
    target, database, snapshots, created = _recovery(tmp_path)
    try:
        facts = RecoveryCoverageService(database, snapshots).compute(
            checkpoint_id=created.checkpoint_id,
            target_refs=(str(target),),
            execution_domain_id="local-domain",
        )

        assert facts.status is RecoveryCoverageStatus.COMPLETE
        assert facts.authorized_snapshot_targets == 1
        assert facts.intact_manifest_blob_targets == 1
        assert facts.test_restore_verified_targets is None
        assert facts.test_restore_status == "NOT_RUN_P6"
        assert facts.authorized_snapshot_coverage == 1.0
        assert facts.manifest_blob_coverage == 1.0
        event_types = {
            row[0]
            for row in database._conn.execute("SELECT event_type FROM evidence_ledger_events")
        }
        assert "RECOVERY_VERIFIED" not in event_types
        assert "TRUSTED_BASELINE" not in event_types
        assert verify_ledger(database._conn) == []
    finally:
        database.close()


def test_blob_corruption_preserves_snapshot_fact_but_invalidates_integrity(tmp_path):
    target, database, snapshots, created = _recovery(tmp_path)
    try:
        checkpoint = database.get_checkpoint(int(created.checkpoint_id))
        stored = snapshots.load(checkpoint["snapshot_path"])
        digest = next(iter(stored["blobs"]))
        stored["blobs"][digest] = "00"
        snapshots.save(int(created.checkpoint_id), stored)

        facts = RecoveryCoverageService(database, snapshots).compute(
            checkpoint_id=created.checkpoint_id,
            target_refs=(str(target),),
            execution_domain_id="local-domain",
        )

        assert facts.status is RecoveryCoverageStatus.EVIDENCE_INSUFFICIENT
        assert facts.authorized_snapshot_targets == 1
        assert facts.intact_manifest_blob_targets == 0
        assert facts.test_restore_verified_targets is None
        assert facts.reason_code == "RECOVERY_MANIFEST_INVALID"
    finally:
        database.close()


def test_coverage_remains_complete_after_manifest_reverification(tmp_path):
    target, database, snapshots, created = _recovery(tmp_path)
    recovery = RecoveryService(
        database=database,
        snapshots=snapshots,
        adapters={"local-domain": SelfRuntimeAdapter()},
    )
    try:
        verified = recovery.verify(
            RecoveryRequest(
                operation=RecoveryOperation.VERIFY,
                execution_domain_id="local-domain",
                checkpoint_id=created.checkpoint_id,
                target_path=tmp_path / "must-not-be-read.toml",
            )
        )
        facts = RecoveryCoverageService(database, snapshots).compute(
            checkpoint_id=created.checkpoint_id,
            target_refs=(str(target),),
            execution_domain_id="local-domain",
        )

        assert verified.reason_code == "RECOVERY_MANIFEST_VERIFIED"
        assert facts.status is RecoveryCoverageStatus.COMPLETE
        assert not (tmp_path / "must-not-be-read.toml").exists()
    finally:
        database.close()


def test_missing_checkpoint_and_unreachable_artifact_are_distinct(tmp_path):
    target, database, snapshots, created = _recovery(tmp_path)
    coverage = RecoveryCoverageService(database, snapshots)
    try:
        missing = coverage.compute(
            checkpoint_id="99999",
            target_refs=(str(target),),
            execution_domain_id="local-domain",
        )
        checkpoint = database.get_checkpoint(int(created.checkpoint_id))
        snapshots.delete(checkpoint["snapshot_path"])
        unreachable = coverage.compute(
            checkpoint_id=created.checkpoint_id,
            target_refs=(str(target),),
            execution_domain_id="local-domain",
        )

        assert missing.status is RecoveryCoverageStatus.MISSING
        assert missing.reason_code == "RECOVERY_CHECKPOINT_MISSING"
        assert unreachable.status is RecoveryCoverageStatus.UNREACHABLE
        assert unreachable.reason_code == "RECOVERY_ARTIFACT_NOT_FOUND"
    finally:
        database.close()


def test_authoritative_supervision_persists_safe_coverage_facts_atomically(tmp_path):
    target, database, snapshots, created = _recovery(tmp_path)
    _record_workspace_authority(database)
    sessions = SupervisionService(database, snapshots=snapshots)
    try:
        session, decision, facts = sessions.create_authoritative(
            "provider config change",
            _policy_input(str(target)),
            checkpoint_id=created.checkpoint_id,
        )

        assert decision.decision is Decision.REVIEW
        assert facts.status is RecoveryCoverageStatus.COMPLETE
        payload = database._conn.execute(
            "SELECT payload_safe_json FROM evidence_ledger_events "
            "WHERE supervision_session_id = ? AND event_type = 'POLICY_EVALUATED'",
            (session.supervision_session_id,),
        ).fetchone()[0]
        safe = json.loads(payload)["recovery_facts"]
        assert safe["authorized_snapshot_coverage"] == 1.0
        assert safe["manifest_blob_coverage"] == 1.0
        assert safe["test_restore_status"] == "NOT_RUN_P6"
        assert str(target) not in payload
        assert verify_ledger(database._conn) == []
    finally:
        database.close()


def test_test_restore_updates_authoritative_r2_coverage_facts(tmp_path):
    target, database, snapshots, created = _recovery(tmp_path)
    try:
        service = RecoveryService(
            database=database,
            snapshots=snapshots,
            adapters={"local-domain": SelfRuntimeAdapter()},
        )
        result = service.test_restore(
            RecoveryRequest(
                operation=RecoveryOperation.TEST_RESTORE,
                execution_domain_id="local-domain",
                checkpoint_id=created.checkpoint_id,
            )
        )
        assert result.reason_code == "TEST_RESTORE_VERIFIED"

        facts = RecoveryCoverageService(database, snapshots).compute(
            checkpoint_id=created.checkpoint_id,
            target_refs=(str(target),),
            execution_domain_id="local-domain",
        )
        assert facts.test_restore_verified_targets == 1
        assert facts.test_restore_status == "VERIFIED_R2"
        assert facts.safe_summary()["test_restore_verified_targets"] == 1
    finally:
        database.close()


def test_actual_restore_events_do_not_invalidate_verified_test_restore(tmp_path):
    target, database, snapshots, created = _recovery(tmp_path)
    try:
        service = RecoveryService(
            database=database,
            snapshots=snapshots,
            adapters={"local-domain": SelfRuntimeAdapter()},
        )
        tested = service.test_restore(
            RecoveryRequest(
                operation=RecoveryOperation.TEST_RESTORE,
                execution_domain_id="local-domain",
                checkpoint_id=created.checkpoint_id,
            )
        )
        assert tested.reason_code == "TEST_RESTORE_VERIFIED"

        common = {
            "schema_version": 1,
            "recorded_at": datetime(2026, 8, 9, 10, 2, tzinfo=UTC),
            "observed_at": None,
            "event_family": EventFamily.RECOVERY,
            "source": "test-actual-restore",
            "result": "AVAILABLE",
            "execution_domain_id": "local-domain",
            "supervision_session_id": None,
            "transaction_id": None,
            "checkpoint_id": created.checkpoint_id,
            "subject_ref": f"manifest:{created.manifest_digest}",
            "evidence_refs": (),
            "payload_safe": {
                "manifest_digest": created.manifest_digest,
                "reason_code": "RECOVERY_RESTORED_AND_VERIFIED",
                "file_count": 2,
            },
        }
        with database.transaction() as connection:
            ledger = EvidenceLedger()
            ledger.append(
                connection,
                EvidenceEvent(
                    **common,
                    event_id="actual-file-restored",
                    event_type=EventType.FILE_RESTORED,
                ),
            )
            ledger.append(
                connection,
                EvidenceEvent(
                    **common,
                    event_id="actual-validator-passed",
                    event_type=EventType.VALIDATOR_PASSED,
                ),
            )

        facts = RecoveryCoverageService(database, snapshots).compute(
            checkpoint_id=created.checkpoint_id,
            target_refs=(str(target),),
            execution_domain_id="local-domain",
        )

        assert facts.test_restore_verified_targets == 1
        assert facts.test_restore_status == "VERIFIED_R2"
    finally:
        database.close()


def test_authoritative_supervision_ledger_fault_rolls_back_session(tmp_path, monkeypatch):
    target, database, snapshots, created = _recovery(tmp_path)
    _record_workspace_authority(database)
    sessions = SupervisionService(database, snapshots=snapshots)
    before_events = database._conn.execute(
        "SELECT COUNT(*) FROM evidence_ledger_events"
    ).fetchone()[0]
    monkeypatch.setattr(
        sessions._ledger,
        "append",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.IntegrityError("raw-secret")),
    )
    try:
        with pytest.raises(RuntimeError) as raised:
            sessions.create_authoritative(
                "provider config change",
                _policy_input(str(target)),
                checkpoint_id=created.checkpoint_id,
            )

        assert str(raised.value) == "SUPERVISION_PERSISTENCE_FAILED"
        assert "raw-secret" not in str(raised.value)
        assert database._conn.execute("SELECT COUNT(*) FROM supervision_sessions").fetchone() == (0,)
        assert database._conn.execute(
            "SELECT COUNT(*) FROM evidence_ledger_events"
        ).fetchone() == (before_events,)
        assert not database._conn.in_transaction
    finally:
        database.close()
