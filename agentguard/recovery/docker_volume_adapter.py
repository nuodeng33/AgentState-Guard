"""Snapshot V3 adapter for one Docker named-volume workspace authority."""

from __future__ import annotations

from typing import Protocol

from agentguard.discovery.capabilities import CapabilityStatus
from agentguard.discovery.workspace_authority import storage_workspace_digest
from agentguard.evidence.canonical import canonical_json_unbounded

from .contracts import RecoveryOperation, RecoveryOperationResult, RecoveryRequest
from .manifest import validate_snapshot_v3
from .workspace_policy import WorkspaceScan, WorkspaceScanLimits
from .workspace_scope import DurableWorkspaceScope


class DockerVolumeTransport(Protocol):
    def validate_resource(
        self, scope: DurableWorkspaceScope
    ) -> tuple[bool, str]: ...

    def scan(
        self,
        scope: DurableWorkspaceScope,
        limits: WorkspaceScanLimits | None = None,
    ) -> WorkspaceScan: ...

    def test_restore(
        self, scope: DurableWorkspaceScope, artifact: dict
    ) -> tuple[bool, str, int]: ...

    def restore(
        self, scope: DurableWorkspaceScope, artifact: dict
    ) -> tuple[bool, str, int]: ...


class DockerVolumeRecoveryAdapter:
    """Storage-specific I/O; Core keeps authority and recovery truth."""

    def __init__(
        self,
        *,
        scope: DurableWorkspaceScope,
        transport: DockerVolumeTransport,
        scan_limits: WorkspaceScanLimits | None = None,
    ) -> None:
        self._scope = scope
        self._transport = transport
        self._scan_limits = scan_limits

    def snapshot(self, request: RecoveryRequest) -> RecoveryOperationResult:
        if (
            request.operation is not RecoveryOperation.SNAPSHOT
            or request.execution_domain_id != self._scope.execution_domain_id
            or request.target_path is not None
            or not request.user_approved
        ):
            return self._result(request, CapabilityStatus.ERROR, "WORKSPACE_SNAPSHOT_REQUEST_INVALID")
        valid, reason = self._validate_scope_and_resource()
        if not valid:
            return self._result(request, CapabilityStatus.UNREACHABLE, reason)
        try:
            scan = self._transport.scan(self._scope, self._scan_limits)
        except PermissionError:
            return self._result(request, CapabilityStatus.PERMISSION_DENIED, "DOCKER_VOLUME_SCAN_PERMISSION_DENIED")
        except (OSError, RuntimeError, ValueError):
            return self._result(request, CapabilityStatus.UNREACHABLE, "DOCKER_VOLUME_SCAN_UNREACHABLE")
        if not scan.complete:
            return self._result(request, CapabilityStatus.ERROR, scan.reason_code)
        artifact = _artifact_from_scan(self._scope, scan)
        verified, reason_code, digest = validate_snapshot_v3(
            artifact, expected_domain=request.execution_domain_id
        )
        if not verified or digest is None:
            return self._result(request, CapabilityStatus.ERROR, reason_code)
        return self._result(
            request,
            CapabilityStatus.AVAILABLE,
            reason_code,
            manifest_digest=digest,
            artifact=artifact,
            details={
                "scope_kind": "DOCKER_NAMED_VOLUME",
                "workspace_id": self._scope.workspace_id,
                "storage_kind": self._scope.storage_kind,
                "coverage": scan.counts,
                "coverage_reason_counts": scan.reason_counts,
                "coverage_digest": scan.coverage_digest,
                "scan_complete": scan.complete,
                "scan_reason_code": scan.reason_code,
                "restorable_targets": scan.counts["restorable"],
            },
        )

    def verify(self, request: RecoveryRequest) -> RecoveryOperationResult:
        valid, reason, digest = self._validated_artifact_request(
            request, RecoveryOperation.VERIFY
        )
        return self._result(
            request,
            CapabilityStatus.AVAILABLE if valid else CapabilityStatus.ERROR,
            reason,
            manifest_digest=digest,
        )

    def test_restore(self, request: RecoveryRequest) -> RecoveryOperationResult:
        valid, reason, digest = self._validated_artifact_request(
            request, RecoveryOperation.TEST_RESTORE
        )
        if not valid or digest is None:
            return self._result(request, CapabilityStatus.ERROR, reason)
        resource_ok, resource_reason = self._validate_scope_and_resource()
        if not resource_ok:
            return self._result(request, CapabilityStatus.UNREACHABLE, resource_reason)
        assert request.artifact is not None
        manifest_target_count = len(request.artifact["manifest"])
        try:
            ok, reason_code, verified = self._transport.test_restore(
                self._scope, request.artifact
            )
        except PermissionError:
            return self._result(request, CapabilityStatus.PERMISSION_DENIED, "DOCKER_VOLUME_TEST_RESTORE_PERMISSION_DENIED")
        except (OSError, RuntimeError, ValueError):
            return self._result(request, CapabilityStatus.ERROR, "DOCKER_VOLUME_TEST_RESTORE_FAILED")
        return self._result(
            request,
            CapabilityStatus.AVAILABLE if ok else CapabilityStatus.ERROR,
            reason_code,
            manifest_digest=digest,
            details={
                "scope_kind": "DOCKER_NAMED_VOLUME",
                "workspace_id": self._scope.workspace_id,
                "verified_targets": manifest_target_count,
                "verified_workspace_objects": verified,
            },
        )

    def restore(self, request: RecoveryRequest) -> RecoveryOperationResult:
        if not request.user_approved:
            return self._result(request, CapabilityStatus.ERROR, "RECOVERY_CONFIRMATION_REQUIRED")
        valid, reason, digest = self._validated_artifact_request(
            request, RecoveryOperation.RESTORE
        )
        if not valid or digest is None:
            return self._result(request, CapabilityStatus.ERROR, reason)
        resource_ok, resource_reason = self._validate_scope_and_resource()
        if not resource_ok:
            return self._result(request, CapabilityStatus.UNREACHABLE, resource_reason)
        assert request.artifact is not None
        manifest_target_count = len(request.artifact["manifest"])
        try:
            ok, reason_code, verified = self._transport.restore(
                self._scope, request.artifact
            )
        except PermissionError:
            return self._result(request, CapabilityStatus.PERMISSION_DENIED, "DOCKER_VOLUME_RESTORE_PERMISSION_DENIED")
        except (OSError, RuntimeError, ValueError):
            return self._result(request, CapabilityStatus.ERROR, "DOCKER_VOLUME_RESTORE_FAILED")
        return self._result(
            request,
            CapabilityStatus.AVAILABLE if ok else CapabilityStatus.ERROR,
            reason_code,
            manifest_digest=digest,
            details={
                "scope_kind": "DOCKER_NAMED_VOLUME",
                "workspace_id": self._scope.workspace_id,
                "verified_targets": manifest_target_count,
                "verified_workspace_objects": verified,
                "post_restore_status": (
                    "POST_RESTORE_VERIFIED" if ok else "POST_RESTORE_VERIFICATION_FAILED"
                ),
            },
        )

    def _validated_artifact_request(
        self,
        request: RecoveryRequest,
        operation: RecoveryOperation,
    ) -> tuple[bool, str, str | None]:
        if (
            request.operation is not operation
            or request.execution_domain_id != self._scope.execution_domain_id
            or request.target_path is not None
            or request.checkpoint_id is None
            or request.artifact is None
        ):
            return False, "WORKSPACE_RECOVERY_REQUEST_INVALID", None
        valid, reason, digest = validate_snapshot_v3(
            request.artifact, expected_domain=request.execution_domain_id
        )
        if not valid:
            return False, reason, None
        workspace = request.artifact.get("workspace")
        if not isinstance(workspace, dict):
            return False, "RECOVERY_WORKSPACE_EXTENSION_INVALID", None
        if workspace.get("storage_resource_identity") != self._scope.storage_resource_identity:
            return False, "WORKSPACE_STORAGE_IDENTITY_MISMATCH", None
        if (
            workspace.get("workspace_id") != self._scope.workspace_id
            or workspace.get("execution_domain_id") != self._scope.execution_domain_id
            or workspace.get("root_digest") != self._scope.root_digest
            or workspace.get("storage_kind") != self._scope.storage_kind
            or workspace.get("logical_root") != self._scope.logical_root
        ):
            return False, "WORKSPACE_SCOPE_BINDING_INVALID", None
        return True, reason, digest

    def _validate_scope_and_resource(self) -> tuple[bool, str]:
        if (
            self._scope.storage_kind != "DOCKER_NAMED_VOLUME"
            or self._scope.root_path is not None
            or self._scope.storage_resource_identity is None
            or self._scope.logical_root is None
            or storage_workspace_digest(
                self._scope.storage_resource_identity,
                self._scope.logical_root,
                self._scope.execution_domain_id,
            )
            != self._scope.root_digest
        ):
            return False, "WORKSPACE_SCOPE_BINDING_INVALID"
        if self._scope.protection_capability != "SUPPORTED":
            return False, self._scope.protection_reason_code
        return self._transport.validate_resource(self._scope)

    @staticmethod
    def _result(
        request: RecoveryRequest,
        status: CapabilityStatus,
        reason_code: str,
        *,
        manifest_digest: str | None = None,
        artifact: dict | None = None,
        details: dict | None = None,
    ) -> RecoveryOperationResult:
        return RecoveryOperationResult(
            operation=request.operation,
            status=status,
            reason_code=reason_code,
            execution_domain_id=request.execution_domain_id,
            checkpoint_id=request.checkpoint_id,
            manifest_digest=manifest_digest,
            details=details or {},
            artifact=artifact,
        )


def _artifact_from_scan(scope: DurableWorkspaceScope, scan: WorkspaceScan) -> dict:
    manifest: list[dict[str, object]] = []
    blobs: dict[str, bytes] = {}
    for entry in scan.entries:
        if entry.category != "restorable" or entry.object_kind != "FILE":
            continue
        assert entry.content_digest is not None
        assert entry.content is not None
        assert entry.permission_proof is not None
        mode = str(entry.permission_proof.values["mode"])
        uid = entry.permission_proof.values.get("uid")
        gid = entry.permission_proof.values.get("gid")
        manifest.append(
            {
                "domain": scope.execution_domain_id,
                "logical_path": f"/workspace/{scope.workspace_id}/{entry.relative_path}",
                "classification": "restorable",
                "blob_sha256": entry.content_digest,
                "size": entry.size,
                "mode": mode,
                "uid": uid,
                "gid": gid,
                "validator": "workspace-hash-permission-v1",
                "sha256": entry.content_digest,
                "status": "WORKSPACE_RESTORABLE",
            }
        )
        blobs[entry.content_digest] = entry.content
    coverage = [item.to_extension_dict() for item in scan.entries]
    return {
        "format_version": 3,
        "manifest": manifest,
        "blobs": blobs,
        "workspace": {
            "schema_version": 3,
            "workspace_id": scope.workspace_id,
            "scope_observation_id": scope.observation_id,
            "execution_domain_id": scope.execution_domain_id,
            "root_digest": scope.root_digest,
            "storage_kind": scope.storage_kind,
            "storage_resource_identity": scope.storage_resource_identity,
            "logical_root": scope.logical_root,
            "durability": scope.durability,
            "protection_capability": scope.protection_capability,
            "coverage": coverage,
            "coverage_counts": scan.counts,
            "coverage_digest": __import__("hashlib").sha256(
                canonical_json_unbounded(coverage).encode()
            ).hexdigest(),
            "scan_complete": scan.complete,
            "scan_reason_code": scan.reason_code,
        },
    }


__all__ = ["DockerVolumeRecoveryAdapter", "DockerVolumeTransport"]
