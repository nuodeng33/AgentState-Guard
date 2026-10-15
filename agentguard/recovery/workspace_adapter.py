"""Snapshot V3 adapter for one durable Host-native workspace scope."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path, PurePosixPath

from agentguard.discovery.capabilities import CapabilityStatus
from agentguard.discovery.workspace_authority import validate_workspace_root_binding

from .contracts import RecoveryOperation, RecoveryOperationResult, RecoveryRequest
from .manifest import validate_snapshot_v3
from .workspace_permissions import (
    PermissionBackend,
    PermissionCapabilityError,
    PermissionProof,
)
from .workspace_policy import WorkspaceScanLimits, scan_workspace
from .workspace_scope import DurableWorkspaceScope

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


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
        valid, reason_code, digest = self._validated_artifact_request(
            request, RecoveryOperation.TEST_RESTORE
        )
        if not valid or digest is None:
            return self._result(request, CapabilityStatus.ERROR, reason_code)
        sandbox = _validated_directory(request.sandbox_path)
        if sandbox is None:
            return self._result(
                request,
                CapabilityStatus.ERROR,
                "WORKSPACE_TEST_RESTORE_SANDBOX_INVALID",
            )
        target_root = sandbox / f"workspace-{self._scope.workspace_id}"
        try:
            target_root.mkdir(mode=0o700)
            verified = self._materialize_restorable_set(
                target_root,
                request.artifact,
            )
        except PermissionCapabilityError as exc:
            return self._permission_failure(request, exc.reason_code)
        except PermissionError:
            return self._result(
                request,
                CapabilityStatus.PERMISSION_DENIED,
                "WORKSPACE_TEST_RESTORE_PERMISSION_DENIED",
            )
        except (OSError, RuntimeError, ValueError):
            return self._result(
                request,
                CapabilityStatus.ERROR,
                "WORKSPACE_TEST_RESTORE_FAILED",
            )
        return self._result(
            request,
            CapabilityStatus.AVAILABLE,
            "TEST_RESTORE_VERIFIED",
            manifest_digest=digest,
            details={
                "scope_kind": "HOST_WORKSPACE",
                "workspace_id": self._scope.workspace_id,
                "verified_targets": verified,
            },
        )

    def restore(self, request: RecoveryRequest) -> RecoveryOperationResult:
        return self._result(request, CapabilityStatus.ERROR, "PREVIEW_REQUIRED")

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
        valid, reason_code, digest = validate_snapshot_v3(
            request.artifact,
            expected_domain=request.execution_domain_id,
        )
        if valid and not self._artifact_matches_scope(request.artifact):
            return False, "WORKSPACE_SCOPE_BINDING_INVALID", None
        return valid, reason_code, digest

    def _materialize_restorable_set(
        self,
        root: Path,
        artifact: dict,
    ) -> int:
        workspace = artifact["workspace"]
        blobs = artifact["blobs"]
        prepared: list[tuple[Path, Path | None, str, int, PermissionProof]] = []
        try:
            for entry in workspace["coverage"]:
                if entry["category"] != "restorable":
                    continue
                target = _safe_target(
                    root, entry["relative_path"], create_parents=True
                )
                if target is None:
                    raise RuntimeError("WORKSPACE_RESTORE_TARGET_UNSAFE")
                proof = PermissionProof.from_dict(entry["permission_proof"])
                content = blobs[entry["content_digest"]]
                temporary = _prepare_verified_temp(
                    target,
                    content,
                    proof,
                    permission_backend=self._permission_backend,
                )
                prepared.append(
                    (target, temporary, entry["content_digest"], len(content), proof)
                )
            for index, (target, temporary, digest, size, proof) in enumerate(prepared):
                assert temporary is not None
                os.replace(temporary, target)
                prepared[index] = (target, None, digest, size, proof)
            for target, _temporary, digest, size, proof in prepared:
                if not _verify_file(
                    target,
                    expected_digest=digest,
                    expected_size=size,
                    proof=proof,
                    permission_backend=self._permission_backend,
                ):
                    raise RuntimeError("WORKSPACE_RESTORE_POST_WRITE_VERIFY_FAILED")
            return len(prepared)
        finally:
            for _target, temporary, _digest, _size, _proof in prepared:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass

    def _permission_failure(
        self,
        request: RecoveryRequest,
        reason_code: str,
    ) -> RecoveryOperationResult:
        return self._result(request, _permission_status(reason_code), reason_code)

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


def _validated_directory(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if path.is_symlink() or _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
        return None
    return resolved


def _safe_target(
    root: Path,
    relative: str,
    *,
    create_parents: bool = False,
) -> Path | None:
    value = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or value.is_absolute()
        or value.as_posix() != relative
        or ".." in value.parts
    ):
        return None
    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        return None
    current = resolved_root
    for part in value.parts[:-1]:
        current = current / part
        if not current.exists():
            if not create_parents:
                continue
            try:
                current.mkdir()
            except OSError:
                return None
        try:
            info = current.lstat()
        except OSError:
            return None
        if current.is_symlink() or _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
            return None
    target = resolved_root.joinpath(*value.parts)
    try:
        target.relative_to(resolved_root)
    except ValueError:
        return None
    return target


def _prepare_verified_temp(
    target: Path,
    content: bytes,
    proof: PermissionProof,
    *,
    permission_backend: PermissionBackend,
) -> Path:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent,
            prefix=".agentguard-restore-",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        permission_backend.apply(temporary_path, proof)
        if not _verify_file(
            temporary_path,
            expected_digest=hashlib.sha256(content).hexdigest(),
            expected_size=len(content),
            proof=proof,
            permission_backend=permission_backend,
        ):
            raise RuntimeError("WORKSPACE_RESTORE_TEMP_VERIFY_FAILED")
        result = temporary_path
        temporary_path = None
        return result
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _verify_file(
    path: Path,
    *,
    expected_digest: str,
    expected_size: int,
    proof: PermissionProof,
    permission_backend: PermissionBackend,
) -> bool:
    if not _regular_non_reparse(path):
        return False
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            content = source.read()
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    return bool(
        before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and before.st_size == after.st_size == expected_size == len(content)
        and hashlib.sha256(content).hexdigest() == expected_digest
        and permission_backend.verify(path, proof)
    )


def _regular_non_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return bool(
        not path.is_symlink()
        and not _is_reparse(info)
        and stat.S_ISREG(info.st_mode)
    )


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        _REPARSE_POINT
        and getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _permission_status(reason_code: str) -> CapabilityStatus:
    return (
        CapabilityStatus.PERMISSION_DENIED
        if reason_code.endswith("_DENIED")
        else CapabilityStatus.ERROR
    )


__all__ = ["HostWorkspaceRecoveryAdapter"]
