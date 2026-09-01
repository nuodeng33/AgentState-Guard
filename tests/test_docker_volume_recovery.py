"""Docker named-volume recovery uses workspace authority without a host path."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentguard.api import product_recovery
from agentguard.discovery.workspace_authority import (
    ResolvedWorkspaceAuthority,
    storage_workspace_digest,
)
from agentguard.recovery.contracts import RecoveryOperation, RecoveryRequest
from agentguard.recovery.docker_volume_adapter import DockerVolumeRecoveryAdapter
from agentguard.recovery.docker_volume_helper import materialize, scan_root
from agentguard.recovery.docker_volume_helper_artifact import (
    BundledVolumeHelperArtifact,
)
from agentguard.recovery.docker_volume_transport import (
    DockerCliVolumeTransport,
    RestorePlan,
    _convergence_mismatches,
    _coverage_entry,
    _exact_restore_plan,
)
from agentguard.recovery.workspace_changes import (
    WorkspaceChangeObserver,
    _workspace_diff,
)
from agentguard.recovery.workspace_permissions import PermissionProof
from agentguard.recovery.workspace_policy import (
    WorkspaceCoverageEntry,
    WorkspaceScan,
    WorkspaceScanLimits,
)
from agentguard.recovery.workspace_scope import (
    DurableWorkspaceScope,
    WorkspaceScopeService,
)
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def _scope(identity: str = "sha256:" + "1" * 64) -> DurableWorkspaceScope:
    digest = storage_workspace_digest(identity, "/", "host-native")
    return DurableWorkspaceScope(
        observation_id="workspace-observation-volume",
        discovery_snapshot_id="snapshot-volume",
        observed_at=NOW,
        recorded_at=NOW,
        workspace_id=f"workspace-{digest.split(':', 1)[1][:24]}",
        execution_domain_id="host-native",
        root_path=None,
        root_digest=digest,
        storage_kind="DOCKER_NAMED_VOLUME",
        storage_resource_identity=identity,
        storage_locator=(
            "agent-k3-workspace\x1flocal\x1flocal\x1f2026-07-28T13:33:50Z\x1f"
            + "sha256:"
            + "2" * 64
            + "\x1fsha256:"
            + "3" * 64
        ),
        logical_root="/",
        durability="DURABLE",
        current_reachability="AVAILABLE",
        protection_capability="SUPPORTED",
        protection_reason_code="DOCKER_VOLUME_BACKEND_SUPPORTED",
        agent_mutation_capability="READ_WRITE",
        evidence_refs=("volume-evidence",),
        ledger_event_id="workspace-ledger-volume",
    )


def _scan(content: bytes = b"before") -> WorkspaceScan:
    import hashlib

    digest = hashlib.sha256(content).hexdigest()
    return WorkspaceScan(
        root=Path("/opaque-docker-volume"),
        entries=(
            WorkspaceCoverageEntry(
                relative_path=".agentstate-guard-verification/canary/value.txt",
                object_kind="FILE",
                category="restorable",
                reason_code="WORKSPACE_RESTORABLE",
                size=len(content),
                content_digest=digest,
                observation_digest=digest,
                permission_proof=PermissionProof(
                    kind="POSIX_MODE", values={"mode": "0o640"}
                ),
                content=content,
            ),
        ),
    )


class _FakeTransport:
    def __init__(self) -> None:
        self.current = _scan()
        self.test_restored = False
        self.restored = False

    def validate_resource(self, scope):
        return True, "DOCKER_VOLUME_RESOURCE_VERIFIED"

    def scan(self, scope, limits=None):
        return self.current

    def test_restore(self, scope, artifact):
        self.test_restored = True
        return True, "TEST_RESTORE_VERIFIED", 1

    def restore(self, scope, artifact):
        self.restored = True
        self.current = _scan(b"before")
        return True, "WORKSPACE_RESTORED_AND_VERIFIED", 1


def _snapshot(adapter):
    return adapter.snapshot(
        RecoveryRequest(
            operation=RecoveryOperation.SNAPSHOT,
            execution_domain_id="host-native",
            user_approved=True,
        )
    )


def test_named_volume_snapshot_binds_storage_identity_and_manifest():
    adapter = DockerVolumeRecoveryAdapter(scope=_scope(), transport=_FakeTransport())

    result = _snapshot(adapter)

    assert result.ok
    assert result.artifact is not None
    workspace = result.artifact["workspace"]
    assert workspace["storage_kind"] == "DOCKER_NAMED_VOLUME"
    assert workspace["storage_resource_identity"] == _scope().storage_resource_identity
    assert workspace["logical_root"] == "/"
    assert result.details["scope_kind"] == "DOCKER_NAMED_VOLUME"


def test_named_volume_test_restore_and_actual_restore_reread():
    transport = _FakeTransport()
    adapter = DockerVolumeRecoveryAdapter(scope=_scope(), transport=transport)
    snapshot = _snapshot(adapter)
    assert snapshot.artifact is not None
    transport.current = _scan(b"after")

    tested = adapter.test_restore(
        RecoveryRequest(
            operation=RecoveryOperation.TEST_RESTORE,
            execution_domain_id="host-native",
            checkpoint_id="1",
            artifact=snapshot.artifact,
            sandbox_path=Path("/ignored-by-volume-backend"),
        )
    )
    restored = adapter.restore(
        RecoveryRequest(
            operation=RecoveryOperation.RESTORE,
            execution_domain_id="host-native",
            checkpoint_id="1",
            artifact=snapshot.artifact,
            user_approved=True,
        )
    )

    assert tested.ok and tested.reason_code == "TEST_RESTORE_VERIFIED"
    assert restored.ok and restored.reason_code == "WORKSPACE_RESTORED_AND_VERIFIED"
    assert transport.test_restored is True
    assert transport.restored is True
    assert transport.current.entries[0].content == b"before"


def test_named_volume_public_verified_target_count_remains_flat_manifest_count():
    class _ObjectCountingTransport(_FakeTransport):
        def test_restore(self, scope, artifact):
            return True, "TEST_RESTORE_VERIFIED", 3

        def restore(self, scope, artifact):
            return True, "WORKSPACE_RESTORED_AND_VERIFIED", 3

    transport = _ObjectCountingTransport()
    adapter = DockerVolumeRecoveryAdapter(scope=_scope(), transport=transport)
    snapshot = _snapshot(adapter)
    assert snapshot.artifact is not None

    tested = adapter.test_restore(
        RecoveryRequest(
            operation=RecoveryOperation.TEST_RESTORE,
            execution_domain_id="host-native",
            checkpoint_id="1",
            artifact=snapshot.artifact,
            sandbox_path=Path("/ignored-by-volume-backend"),
        )
    )
    restored = adapter.restore(
        RecoveryRequest(
            operation=RecoveryOperation.RESTORE,
            execution_domain_id="host-native",
            checkpoint_id="1",
            artifact=snapshot.artifact,
            user_approved=True,
        )
    )

    assert tested.details["verified_targets"] == 1
    assert tested.details["verified_workspace_objects"] == 3
    assert restored.details["verified_targets"] == 1
    assert restored.details["verified_workspace_objects"] == 3


def test_named_volume_artifact_cannot_cross_recreated_volume_identity():
    first = DockerVolumeRecoveryAdapter(scope=_scope(), transport=_FakeTransport())
    snapshot = _snapshot(first)
    assert snapshot.artifact is not None
    recreated = DockerVolumeRecoveryAdapter(
        scope=_scope("sha256:" + "9" * 64),
        transport=_FakeTransport(),
    )

    result = recreated.verify(
        RecoveryRequest(
            operation=RecoveryOperation.VERIFY,
            execution_domain_id="host-native",
            checkpoint_id="1",
            artifact=snapshot.artifact,
        )
    )

    assert not result.ok
    assert result.reason_code == "WORKSPACE_STORAGE_IDENTITY_MISMATCH"


def test_product_recovery_routes_named_volume_through_storage_backend(
    tmp_path, monkeypatch
):
    scope = _scope()
    database = StateDB(tmp_path / "state.db")
    database.connect()
    snapshots = SnapshotStore(tmp_path / "snapshots")
    transport = _FakeTransport()
    monkeypatch.setattr(
        product_recovery,
        "DockerCliVolumeTransport",
        lambda: transport,
    )
    WorkspaceScopeService(database).bind(
        ResolvedWorkspaceAuthority(
            status="BOUND",
            reason_code="WORKSPACE_SCOPE_VERIFIED",
            root_path=None,
            workspace_id=scope.workspace_id,
            root_digest=scope.root_digest,
            execution_domain_id=scope.execution_domain_id,
            agent_ids=("agent-kimi",),
            process_instance_ids=("process-kimi",),
            evidence_refs=("volume-evidence",),
            storage_kind=scope.storage_kind,
            storage_resource_identity=scope.storage_resource_identity,
            storage_locator=scope.storage_locator,
            logical_root=scope.logical_root,
            durability=scope.durability,
            current_reachability=scope.current_reachability,
            protection_capability=scope.protection_capability,
            protection_reason_code=scope.protection_reason_code,
            agent_mutation_capability=scope.agent_mutation_capability,
        ),
        recorded_at=NOW,
        discovery_snapshot_id="snapshot-volume",
    )

    created = product_recovery.run_product_recovery(
        database,
        snapshots,
        target=tmp_path / "unused.toml",
        operation=RecoveryOperation.SNAPSHOT,
    )
    checkpoint_id = str(created["checkpoint_id"])
    transport.current = _scan(b"after")
    active = WorkspaceScopeService(database).resolve_authority(scope.workspace_id)
    assert active.scope is not None
    change = WorkspaceChangeObserver(
        database=database,
        snapshots=snapshots,
        permission_backend=None,
        storage_scanner=transport.scan,
    ).observe(active.scope, observed_at=NOW)
    assert change.status == "AVAILABLE"
    assert change.change_count == 1
    assert change.checkpoint_id == checkpoint_id
    with pytest.raises(product_recovery.ProductRecoveryError) as blocked:
        product_recovery.run_product_recovery(
            database,
            snapshots,
            target=tmp_path / "unused.toml",
            operation=RecoveryOperation.RESTORE,
            checkpoint_id=checkpoint_id,
            confirmed=True,
        )
    assert blocked.value.reason_code == "RECOVERY_TEST_RESTORE_REQUIRED"
    tested = product_recovery.run_product_recovery(
        database,
        snapshots,
        target=tmp_path / "unused.toml",
        operation=RecoveryOperation.TEST_RESTORE,
        checkpoint_id=checkpoint_id,
    )
    restored = product_recovery.run_product_recovery(
        database,
        snapshots,
        target=tmp_path / "unused.toml",
        operation=RecoveryOperation.RESTORE,
        checkpoint_id=checkpoint_id,
        confirmed=True,
    )

    assert created["scope_kind"] == "DOCKER_NAMED_VOLUME"
    assert created["workspace_id"] == scope.workspace_id
    assert tested["reason_code"] == "TEST_RESTORE_VERIFIED"
    assert restored["reason_code"] == "WORKSPACE_RESTORED_AND_VERIFIED"
    assert transport.current.entries[0].content == b"before"
    assert database._conn is not None
    event_types = {
        row[0]
        for row in database._conn.execute(
            "SELECT event_type FROM evidence_ledger_events WHERE checkpoint_id = ?",
            (checkpoint_id,),
        )
    }
    assert {
        "CHECKPOINT_CREATED",
        "MANIFEST_VERIFIED",
        "TEST_RESTORE_STARTED",
        "FILE_RESTORED",
        "VALIDATOR_PASSED",
    }.issubset(event_types)
    database.close()


def _transport_scope() -> DurableWorkspaceScope:
    engine = "engine-fixture"
    name = "agent-k3-workspace"
    driver = "local"
    volume_scope = "local"
    created = "2026-07-28T13:33:50Z"
    mountpoint = "/var/lib/docker/volumes/agent-k3-workspace/_data"
    mountpoint_ref = "sha256:" + hashlib.sha256(mountpoint.encode()).hexdigest()
    identity = "sha256:" + hashlib.sha256(
        (
            f"docker-volume-v1\x1f{engine}\x1f{name}\x1f{driver}"
            f"\x1f{volume_scope}\x1f{created}\x1f{mountpoint_ref}"
        ).encode()
    ).hexdigest()
    image = "sha256:" + "3" * 64
    return replace(
        _scope(identity),
        storage_locator=(
            f"{name}\x1f{driver}\x1f{volume_scope}\x1f{created}"
            f"\x1f{mountpoint_ref}\x1f{image}"
        ),
    )


class _ReadyHelper:
    image_reference = "agentstate-guard/volume-helper:v1"

    def ensure_available(self, _docker, _run):
        return True, "DOCKER_VOLUME_HELPER_VERIFIED"


def test_cli_transport_revalidates_volume_and_product_helper(monkeypatch):
    transport = DockerCliVolumeTransport(
        docker_executable="/docker", helper_artifact=_ReadyHelper()
    )
    calls: list[list[str]] = []

    def run(command, *, timeout):
        import subprocess

        calls.append(command)
        if command[1] == "info":
            return subprocess.CompletedProcess(command, 0, "engine-fixture\n", "")
        if command[1:3] == ["volume", "inspect"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "agent-k3-workspace\tlocal\tlocal\t"
                "2026-07-28T13:33:50Z\t"
                "/var/lib/docker/volumes/agent-k3-workspace/_data\n",
                "",
            )
        assert command[1] == "run"
        return subprocess.CompletedProcess(
            command, 0, "ASG_VOLUME_HELPER_READY\n", ""
        )

    monkeypatch.setattr(transport, "_run", run)

    assert transport.validate_resource(_transport_scope()) == (
        True,
        "DOCKER_VOLUME_RESOURCE_VERIFIED",
    )
    assert [command[1] for command in calls] == [
        "info",
        "volume",
        "run",
    ]
    assert _ReadyHelper.image_reference in calls[-1]
    assert "python3" not in calls[-1]


def test_cli_transport_missing_volume_and_helper_fail_closed(monkeypatch):
    import subprocess

    transport = DockerCliVolumeTransport(
        docker_executable="/docker", helper_artifact=_ReadyHelper()
    )
    monkeypatch.setattr(
        transport,
        "_run",
        lambda command, *, timeout: subprocess.CompletedProcess(
            command,
            0 if command[1] == "info" else 1,
            "engine-fixture\n" if command[1] == "info" else "",
            "unavailable",
        ),
    )

    assert transport.validate_resource(_transport_scope()) == (
        False,
        "DOCKER_VOLUME_UNREACHABLE",
    )
    locator = _transport_scope().storage_locator
    assert locator is not None
    unsupported = replace(
        _transport_scope(),
        storage_locator=locator.replace(
            "\x1flocal\x1flocal\x1f", "\x1fnfs\x1flocal\x1f"
        ),
    )
    assert transport.validate_resource(unsupported) == (
        False,
        "DOCKER_VOLUME_HELPER_RUNTIME_UNAVAILABLE",
    )


def test_volume_scan_decoder_rejects_escape_and_corrupt_content():
    limits = WorkspaceScanLimits()
    digest = hashlib.sha256(b"safe").hexdigest()
    with pytest.raises(ValueError, match="DOCKER_VOLUME_SCAN_INVALID"):
        _coverage_entry(
            {
                "relative_path": "../escape",
                "object_kind": "FILE",
                "category": "candidate",
                "reason_code": "WORKSPACE_CONTENT_CAPTURED",
                "size": 4,
                "mode": "0o640",
                "uid": 1001,
                "gid": 1001,
                "digest": digest,
                "content_digest": digest,
                "content": "c2FmZQ==",
            },
            0,
            limits,
        )
    with pytest.raises(ValueError, match="DOCKER_VOLUME_SCAN_INVALID"):
        _coverage_entry(
            {
                "relative_path": "safe.txt",
                "object_kind": "FILE",
                "category": "candidate",
                "reason_code": "WORKSPACE_CONTENT_CAPTURED",
                "size": 4,
                "mode": "0o640",
                "uid": 1001,
                "gid": 1001,
                "digest": digest,
                "content_digest": digest,
                "content": "YmFkIQ==",
            },
            0,
            limits,
        )
    special, added = _coverage_entry(
        {
            "relative_path": "pipe",
            "object_kind": "SPECIAL",
            "category": "excluded",
            "reason_code": "WORKSPACE_OBJECT_UNSUPPORTED",
            "size": 0,
            "mode": "0o600",
            "uid": 1001,
            "gid": 1001,
            "digest": "0" * 64,
            "content_digest": None,
        },
        0,
        limits,
    )
    assert special.category == "excluded"
    assert special.content is None
    assert added == 0


def test_product_volume_helper_source_is_importable():
    assert callable(scan_root)
    assert callable(materialize)


def test_active_volume_restore_only_dispatches_changed_canary(monkeypatch):
    scope = _transport_scope()
    snapshot = _snapshot(
        DockerVolumeRecoveryAdapter(scope=scope, transport=_FakeTransport())
    )
    assert snapshot.artifact is not None
    transport = DockerCliVolumeTransport(docker_executable="/docker")
    captured = {}
    scans = iter((_scan(b"after"), _scan(b"before")))
    monkeypatch.setattr(transport, "scan", lambda _scope: next(scans))
    monkeypatch.setattr(transport, "_has_active_writer", lambda _scope: True)

    def invoke(_scope, operation, **kwargs):
        captured.update(kwargs["artifact"])
        assert operation == "restore"
        return {
            "status": "OK",
            "reason_code": "WORKSPACE_RESTORED_AND_VERIFIED",
            "verified_targets": 1,
        }

    monkeypatch.setattr(transport, "_invoke", invoke)

    result = transport.restore(scope, snapshot.artifact)

    assert result == (True, "WORKSPACE_RESTORED_AND_VERIFIED", 1)
    assert len(captured["workspace"]["coverage"]) == 1
    assert captured["workspace"]["coverage"][0]["relative_path"].startswith(
        ".agentstate-guard-verification/"
    )
    assert len(captured["blobs"]) == 1


def test_active_volume_restore_rejects_any_non_canary_change(monkeypatch):
    scope = _transport_scope()
    snapshot = _snapshot(
        DockerVolumeRecoveryAdapter(scope=scope, transport=_FakeTransport())
    )
    assert snapshot.artifact is not None
    transport = DockerCliVolumeTransport(
        docker_executable="/docker", helper_artifact=_ReadyHelper()
    )
    non_canary = WorkspaceScan(
        root=Path("/opaque-docker-volume"),
        entries=(replace(_scan(b"after").entries[0], relative_path="source.txt"),),
    )
    monkeypatch.setattr(transport, "scan", lambda _scope: non_canary)
    monkeypatch.setattr(transport, "_has_active_writer", lambda _scope: True)
    monkeypatch.setattr(
        transport,
        "_invoke",
        lambda *_args, **_kwargs: pytest.fail("restore helper must not run"),
    )

    assert transport.restore(scope, snapshot.artifact) == (
        False,
        "WORKSPACE_RESTORE_ACTIVE_WRITER_CONFLICT",
        0,
    )


def test_cli_transport_uses_valid_writable_mount_syntax(monkeypatch):
    import subprocess

    transport = DockerCliVolumeTransport(
        docker_executable="/docker", helper_artifact=_ReadyHelper()
    )
    captured: list[str] = []
    monkeypatch.setattr(
        transport,
        "validate_resource",
        lambda _scope: (True, "DOCKER_VOLUME_RESOURCE_VERIFIED"),
    )

    def run(command, *, timeout):
        captured.extend(command)
        return subprocess.CompletedProcess(
            command,
            0,
            '{"status":"OK","reason_code":"WORKSPACE_RESTORED_AND_VERIFIED",'
            '"verified_targets":0}',
            "",
        )

    monkeypatch.setattr(transport, "_run", run)

    result = transport._invoke(
        _transport_scope(),
        "restore",
        artifact={"workspace": {"coverage": []}, "blobs": {}},
        writable=True,
    )

    assert result["status"] == "OK"
    volume_mount = next(
        part for part in captured if part.startswith("type=volume,")
    )
    assert volume_mount == (
        "type=volume,src=agent-k3-workspace,dst=/asg-volume"
    )
    assert "--network=none" in captured
    assert "--cap-drop=ALL" in captured
    assert "--cap-add=CHOWN" in captured
    assert "--cap-add=FOWNER" in captured
    assert "--cap-add=DAC_OVERRIDE" in captured
    assert "--security-opt=no-new-privileges" in captured
    assert "--read-only" in captured
    assert "--privileged" not in captured
    assert _transport_scope().storage_locator.split("\x1f")[-1] not in captured
    assert _ReadyHelper.image_reference in captured


def test_helper_artifact_integrity_mismatch_fails_before_docker_load(tmp_path):
    archive = tmp_path / "volume-helper.tar"
    archive.write_bytes(b"tampered")
    helper = BundledVolumeHelperArtifact(
        archive_path=archive,
        archive_sha256="0" * 64,
        image_reference="agentstate-guard/volume-helper:v1",
        image_id="sha256:" + "1" * 64,
    )
    calls = []

    assert helper.ensure_available("/docker", lambda *args, **kwargs: calls.append(args)) == (
        False,
        "DOCKER_VOLUME_HELPER_INTEGRITY_MISMATCH",
    )
    assert calls == []


def test_helper_artifact_wrong_loaded_identity_fails_without_pull(tmp_path):
    import subprocess

    archive = tmp_path / "volume-helper.tar"
    archive.write_bytes(b"valid archive")
    helper = BundledVolumeHelperArtifact(
        archive_path=archive,
        archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        image_reference="agentstate-guard/volume-helper:v1",
        image_id="sha256:" + "1" * 64,
    )
    calls = []

    def run(command, *, timeout):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 0, "sha256:" + "2" * 64 + "\twrong-label\n", ""
        )

    assert helper.ensure_available("/docker", run) == (
        False,
        "DOCKER_VOLUME_HELPER_IDENTITY_MISMATCH",
    )
    assert all("pull" not in command for command in calls)


def _directory_entry(path: str, digest: str = "a" * 64) -> dict:
    return {
        "relative_path": path,
        "object_kind": "DIRECTORY",
        "category": "restorable",
        "reason_code": "WORKSPACE_RESTORABLE",
        "size": 0,
        "content_digest": None,
        "observation_digest": digest,
        "permission_proof": PermissionProof(
            kind="POSIX_MODE_OWNER",
            values={"mode": "0o755", "uid": 1001, "gid": 1001},
        ).to_dict(),
        "link_target": None,
    }


def test_exact_restore_plan_covers_added_deleted_and_type_changed_objects():
    baseline = {
        "keep.txt": replace(_scan(b"before").entries[0], relative_path="keep.txt").to_extension_dict(),
        "deleted.txt": replace(_scan(b"deleted").entries[0], relative_path="deleted.txt").to_extension_dict(),
        "type-path": replace(_scan(b"file").entries[0], relative_path="type-path").to_extension_dict(),
    }
    current = {
        "keep.txt": replace(_scan(b"after").entries[0], relative_path="keep.txt").to_extension_dict(),
        "new.txt": replace(_scan(b"new").entries[0], relative_path="new.txt").to_extension_dict(),
        "new-dir": _directory_entry("new-dir"),
        "new-dir/child": replace(_scan(b"child").entries[0], relative_path="new-dir/child").to_extension_dict(),
        "type-path": _directory_entry("type-path", "b" * 64),
    }

    assert _exact_restore_plan(baseline, current) == RestorePlan(
        restore_paths=("deleted.txt", "keep.txt", "type-path"),
        remove_paths=("new-dir/child", "new-dir", "new.txt", "type-path"),
    )


def test_exact_restore_plan_never_deletes_non_restorable_objects():
    base = _scan().entries[0].to_extension_dict()
    current = {
        "excluded": {**base, "relative_path": "excluded", "category": "excluded", "permission_proof": None},
        "audit": {**base, "relative_path": "audit", "category": "audit_only", "permission_proof": None},
        "special": {**base, "relative_path": "special", "object_kind": "SPECIAL", "category": "excluded", "permission_proof": None},
    }

    assert _exact_restore_plan({}, current) == RestorePlan((), ())


def test_convergence_detects_unexpected_missing_and_content_mismatch():
    expected = {
        "value.txt": replace(_scan(b"before").entries[0], relative_path="value.txt").to_extension_dict(),
        "missing.txt": replace(_scan(b"missing").entries[0], relative_path="missing.txt").to_extension_dict(),
    }
    actual = {
        "value.txt": replace(_scan(b"after").entries[0], relative_path="value.txt").to_extension_dict(),
        "unexpected.txt": replace(_scan(b"new").entries[0], relative_path="unexpected.txt").to_extension_dict(),
    }

    mismatches = _convergence_mismatches(expected, actual)

    assert mismatches["missing"] == ("missing.txt",)
    assert mismatches["unexpected"] == ("unexpected.txt",)
    assert mismatches["content"] == ("value.txt",)


def test_named_volume_scan_captures_owner_directory_and_safe_symlink(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    directory = root / "dir"
    directory.mkdir(mode=0o750)
    value = directory / "value.txt"
    value.write_bytes(b"value")
    link = root / "link"
    link.symlink_to("dir/value.txt")
    limits = {
        "max_entries": 100,
        "max_file_bytes": 1024,
        "max_total_restorable_bytes": 4096,
        "max_depth": 8,
        "excluded_directories": [],
        "excluded_suffixes": [],
    }

    payload = scan_root(root, limits)
    entries = {item["relative_path"]: item for item in payload["entries"]}

    assert entries["dir"]["object_kind"] == "DIRECTORY"
    assert entries["dir"]["category"] == "candidate"
    assert entries["dir"]["uid"] == directory.lstat().st_uid
    assert entries["dir/value.txt"]["gid"] == value.lstat().st_gid
    assert entries["link"]["object_kind"] == "SYMLINK"
    assert entries["link"]["link_target"] == "dir/value.txt"


def test_checkpoint_relative_diff_distinguishes_all_recovery_relevant_kinds():
    unchanged = replace(_scan(b"same").entries[0], relative_path="same").to_extension_dict()
    baseline = {
        "modified": replace(_scan(b"old").entries[0], relative_path="modified").to_extension_dict(),
        "deleted": replace(_scan(b"gone").entries[0], relative_path="deleted").to_extension_dict(),
        "typed": replace(_scan(b"file").entries[0], relative_path="typed").to_extension_dict(),
        "metadata": replace(_scan(b"same").entries[0], relative_path="metadata").to_extension_dict(),
        "same": unchanged,
    }
    current = {
        "added": replace(_scan(b"new").entries[0], relative_path="added").to_extension_dict(),
        "modified": replace(_scan(b"new").entries[0], relative_path="modified").to_extension_dict(),
        "typed": _directory_entry("typed"),
        "metadata": {
            **baseline["metadata"],
            "permission_proof": PermissionProof(kind="POSIX_MODE", values={"mode": "0o600"}).to_dict(),
        },
        "same": unchanged,
    }

    changes = _workspace_diff(
        workspace_id="workspace-test",
        checkpoint_id="checkpoint-test",
        baseline=baseline,
        current=current,
    )

    assert {item["change_kind"] for item in changes} == {
        "ADDED",
        "MODIFIED",
        "DELETED",
        "TYPE_CHANGED",
        "METADATA_CHANGED",
    }


def test_helper_materializes_exact_tree_with_mode_owner_and_symlink(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    uid, gid = root.stat().st_uid, root.stat().st_gid
    digest = hashlib.sha256(b"before").hexdigest()
    owner = lambda mode: PermissionProof(
        kind="POSIX_MODE_OWNER", values={"mode": mode, "uid": uid, "gid": gid}
    ).to_dict()
    artifact = {
        "workspace": {
            "coverage": [
                {**_directory_entry("dir"), "permission_proof": owner("0o750")},
                {
                    "relative_path": "dir/value.txt",
                    "object_kind": "FILE",
                    "category": "restorable",
                    "size": 6,
                    "content_digest": digest,
                    "permission_proof": owner("0o640"),
                    "link_target": None,
                },
                {
                    "relative_path": "link",
                    "object_kind": "SYMLINK",
                    "category": "restorable",
                    "size": len("dir/value.txt"),
                    "content_digest": None,
                    "permission_proof": owner("0o777"),
                    "link_target": "dir/value.txt",
                },
            ]
        },
        "blobs": {digest: b"before".hex()},
    }
    (root / "added").mkdir()
    (root / "added/new.txt").write_text("new", encoding="utf-8")
    (root / "dir").write_text("wrong type", encoding="utf-8")

    count = materialize(
        root,
        artifact,
        restore_paths=("dir", "dir/value.txt", "link"),
        remove_paths=("added/new.txt", "added", "dir"),
    )

    assert count == 3
    assert (root / "dir/value.txt").read_bytes() == b"before"
    assert oct((root / "dir").stat().st_mode & 0o777) == "0o750"
    assert oct((root / "dir/value.txt").stat().st_mode & 0o777) == "0o640"
    assert ((root / "dir/value.txt").stat().st_uid, (root / "dir/value.txt").stat().st_gid) == (uid, gid)
    assert (root / "link").is_symlink()
    assert (root / "link").readlink().as_posix() == "dir/value.txt"
    assert not (root / "added").exists()
