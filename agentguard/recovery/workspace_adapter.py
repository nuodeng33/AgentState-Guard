"""Snapshot V3 adapter for one durable Host-native workspace scope."""

from __future__ import annotations

from pathlib import Path

from agentguard.discovery.capabilities import CapabilityStatus
from agentguard.discovery.workspace_authority import validate_workspace_root_binding

from .contracts import RecoveryOperation, RecoveryOperationResult, RecoveryRequest
from .manifest import validate_snapshot_v3
from .workspace_permissions import PermissionBackend
from .workspace_policy import WorkspaceScanLimits, scan_workspace
from .workspace_scope import DurableWorkspaceScope


class HostWorkspaceRecoveryAdapter:
    """Own a bounded workspace context; requests never supply filesystem scope."""

    def __init__(
        self,
        *,
        scope: DurableWorkspaceScope,
        permission_backend: PermissionBackend,
        quarantine_root: Path,
        scan_limits: WorkspaceScanLimits | None = None,
    ) -> None:
        self._scope = scope
        self._permission_backend = permission_backend
        self._quarantine_root = Path(quarantine_root)
        self._scan_limits = scan_limits

    def snapshot(self, request: RecoveryRequest) -> RecoveryOperationResult:
        if (
            request.operation is not RecoveryOperation.SNAPSHOT
            or request.execution_domain_id != self._scope.execution_domain_id
            or request.target_path is not None
            or not request.user_approved
        ):
            return self._result(
                request,
                CapabilityStatus.ERROR,
                "WORKSPACE_SNAPSHOT_REQUEST_INVALID",
            )
        root = self._validated_root()
        if root is None:
            return self._result(
                request,
                CapabilityStatus.UNREACHABLE,
                "WORKSPACE_SCOPE_BINDING_INVALID",
            )
        try:
            scan = scan_workspace(
                root,
                permission_backend=self._permission_backend,
                limits=self._scan_limits,
            )
        except PermissionError:
            return self._result(
                request,
                CapabilityStatus.PERMISSION_DENIED,
                "WORKSPACE_SCAN_PERMISSION_DENIED",
            )
        except (OSError, RuntimeError, ValueError):
            return self._result(
                request,
                CapabilityStatus.UNREACHABLE,
                "WORKSPACE_SCAN_UNREACHABLE",
            )
        if not scan.complete:
            return self._result(request, CapabilityStatus.ERROR, scan.reason_code)

        manifest: list[dict[str, object]] = []
        blobs: dict[str, bytes] = {}
        for entry in scan.entries:
            if entry.category != "restorable":
                continue
            assert entry.content_digest is not None
            assert entry.content is not None
            assert entry.permission_proof is not None
            mode = (
                str(entry.permission_proof.values["mode"])
                if entry.permission_proof.kind == "POSIX_MODE"
                else "0o0"
            )
            manifest.append(
                {
                    "domain": self._scope.execution_domain_id,
                    "logical_path": (
                        f"/workspace/{self._scope.workspace_id}/{entry.relative_path}"
                    ),
                    "classification": "restorable",
                    "blob_sha256": entry.content_digest,
                    "size": entry.size,
                    "mode": mode,
                    "uid": None,
                    "gid": None,
                    "validator": "workspace-hash-permission-v1",
                    "sha256": entry.content_digest,
                    "status": "WORKSPACE_RESTORABLE",
                }
            )
            blobs[entry.content_digest] = entry.content
        extension = {
            "schema_version": 1,
            "workspace_id": self._scope.workspace_id,
            "scope_observation_id": self._scope.observation_id,
            "execution_domain_id": self._scope.execution_domain_id,
            "root_digest": self._scope.root_digest,
            "coverage": [item.to_extension_dict() for item in scan.entries],
            "coverage_counts": scan.counts,
            "coverage_digest": scan.coverage_digest,
            "scan_complete": scan.complete,
            "scan_reason_code": scan.reason_code,
        }
        artifact = {
            "format_version": 3,
            "manifest": manifest,
            "blobs": blobs,
            "workspace": extension,
        }
        valid, reason_code, digest = validate_snapshot_v3(
            artifact,
            expected_domain=request.execution_domain_id,
        )
        if not valid or digest is None:
            return self._result(request, CapabilityStatus.ERROR, reason_code)
        return self._result(
            request,
            CapabilityStatus.AVAILABLE,
            reason_code,
            manifest_digest=digest,
            artifact=artifact,
            details={
                "scope_kind": "HOST_WORKSPACE",
                "workspace_id": self._scope.workspace_id,
                "coverage": scan.counts,
                "coverage_reason_counts": scan.reason_counts,
                "coverage_digest": scan.coverage_digest,
                "scan_complete": scan.complete,
                "scan_reason_code": scan.reason_code,
                "restorable_targets": scan.counts["restorable"],
            },
        )

    def verify(self, request: RecoveryRequest) -> RecoveryOperationResult:
        valid, reason_code, digest = validate_snapshot_v3(
            request.artifact,
            expected_domain=request.execution_domain_id,
        )
        if valid and not self._artifact_matches_scope(request.artifact):
            valid = False
            reason_code = "WORKSPACE_SCOPE_BINDING_INVALID"
            digest = None
        return self._result(
            request,
            CapabilityStatus.AVAILABLE if valid else CapabilityStatus.ERROR,
            reason_code,
            manifest_digest=digest,
        )

    def test_restore(self, request: RecoveryRequest) -> RecoveryOperationResult:
        return self._result(
            request,
            CapabilityStatus.UNSUPPORTED,
            "WORKSPACE_TEST_RESTORE_NOT_IMPLEMENTED",
        )

    def restore(self, request: RecoveryRequest) -> RecoveryOperationResult:
        return self._result(
            request,
            CapabilityStatus.UNSUPPORTED,
            "WORKSPACE_RESTORE_NOT_IMPLEMENTED",
        )

    def _validated_root(self) -> Path | None:
        return validate_workspace_root_binding(
            self._scope.root_path,
            execution_domain_id=self._scope.execution_domain_id,
            expected_digest=self._scope.root_digest,
        )

    def _artifact_matches_scope(self, artifact: object) -> bool:
        if not isinstance(artifact, dict):
            return False
        workspace = artifact.get("workspace")
        return bool(
            isinstance(workspace, dict)
            and workspace.get("workspace_id") == self._scope.workspace_id
            and workspace.get("execution_domain_id")
            == self._scope.execution_domain_id
            and workspace.get("root_digest") == self._scope.root_digest
        )

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


__all__ = ["HostWorkspaceRecoveryAdapter"]
