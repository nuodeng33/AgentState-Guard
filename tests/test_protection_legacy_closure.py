"""Legacy recovery entry points must not write live workspace files."""

import hashlib
from datetime import UTC, datetime

from agentguard.commands.restore import cmd_restore
from agentguard.core.snapshot import create_file_snapshot, create_snapshot
from agentguard.core.whitelist import Whitelist
from agentguard.discovery.capabilities import CapabilityStatus
from agentguard.discovery.workspace_authority import workspace_root_digest
from agentguard.recovery.contracts import RecoveryOperation, RecoveryRequest
from agentguard.recovery.service import RecoveryService
from agentguard.recovery.workspace_adapter import HostWorkspaceRecoveryAdapter
from agentguard.recovery.workspace_permissions import current_user_permission_backend
from agentguard.recovery.workspace_scope import DurableWorkspaceScope
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore


def test_legacy_cli_restore_requires_preview_without_writing(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "config.txt"
    target.write_text("before\n", encoding="utf-8")
    state = tmp_path / "state"
    snapshots = SnapshotStore(state / "snapshots")
    database = StateDB(state / "state.db")
    database.connect()
    try:
        entry = create_file_snapshot(target, "restorable")
        snapshot = create_snapshot("before", {str(target): entry}, {}, {}, {}, {}, {})
        relative = snapshots.save(1, snapshot)
        snapshot_hash = hashlib.sha256((state / relative).read_bytes()).hexdigest()
        checkpoint_id = database.insert_checkpoint(
            "before", relative, snapshot_hash, 1, {}, None, None
        )
        assert snapshots.load(database.get_checkpoint(checkpoint_id)["snapshot_path"])

        target.write_text("current\n", encoding="utf-8")
        monkeypatch.setattr("builtins.input", lambda *_args: (_ for _ in ()).throw(AssertionError("prompted")))
        for approved in (False, True):
            result = cmd_restore(
                target_path=str(target), checkpoint_id=checkpoint_id,
                db=database, snapshots=snapshots, whitelist=Whitelist([str(target)]),
                restorable_paths=[str(target)], yes=approved,
            )
            assert result["status"] == "error"
            assert result["reason_code"] == "PREVIEW_REQUIRED"
            assert target.read_text(encoding="utf-8") == "current\n"
        assert database._conn.execute("SELECT COUNT(*) FROM restore_log").fetchone()[0] == 0
    finally:
        database.close()


def test_host_workspace_checkpoint_remains_readable_and_testable_but_not_live_restorable(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.txt"
    target.write_text("before\n", encoding="utf-8")
    domain_id = "windows-current"
    timestamp = datetime.now(UTC)
    scope = DurableWorkspaceScope(
        observation_id="workspace-observation-test",
        discovery_snapshot_id="discovery-test",
        observed_at=timestamp,
        recorded_at=timestamp,
        workspace_id="workspace-test",
        execution_domain_id=domain_id,
        root_path=project.resolve(),
        root_digest=workspace_root_digest(project.resolve(), domain_id),
    )
    adapter = HostWorkspaceRecoveryAdapter(
        scope=scope,
        permission_backend=current_user_permission_backend(),
        quarantine_root=tmp_path / "quarantine",
    )
    state = tmp_path / "state"
    snapshots = SnapshotStore(state / "snapshots")
    database = StateDB(state / "state.db")
    database.connect()
    try:
        service = RecoveryService(
            database=database, snapshots=snapshots, adapters={domain_id: adapter}
        )
        created = service.snapshot(RecoveryRequest(
            operation=RecoveryOperation.SNAPSHOT,
            execution_domain_id=domain_id,
            user_approved=True,
        ))
        assert created.status is CapabilityStatus.AVAILABLE, created
        checkpoint = database.get_checkpoint(int(created.checkpoint_id))
        artifact = snapshots.load_recovery_v3(checkpoint["snapshot_path"])
        assert artifact is not None

        target.write_text("current\n", encoding="utf-8")
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        tested = adapter.test_restore(RecoveryRequest(
            operation=RecoveryOperation.TEST_RESTORE,
            execution_domain_id=domain_id,
            checkpoint_id=created.checkpoint_id,
            artifact=artifact,
            sandbox_path=sandbox,
        ))
        assert tested.reason_code == "TEST_RESTORE_VERIFIED", tested
        assert (sandbox / f"workspace-{scope.workspace_id}" / "a.txt").read_text(encoding="utf-8") == "before\n"
        assert target.read_text(encoding="utf-8") == "current\n"

        request = RecoveryRequest(
            operation=RecoveryOperation.RESTORE,
            execution_domain_id=domain_id,
            checkpoint_id=created.checkpoint_id,
            artifact=artifact,
            user_approved=True,
        )
        direct = adapter.restore(request)
        internal = service.restore(request)
        assert direct.status is CapabilityStatus.ERROR
        assert direct.reason_code == "PREVIEW_REQUIRED"
        assert internal.status is CapabilityStatus.ERROR
        assert internal.reason_code == "PREVIEW_REQUIRED"
        assert target.read_text(encoding="utf-8") == "current\n"
        assert not (tmp_path / "quarantine").exists()
    finally:
        database.close()
