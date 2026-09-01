"""Bounded Docker CLI transport for named-volume protection operations."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from uuid import uuid4

from agentguard.core.docker import resolved_docker_executable
from agentguard.core.sanitizer import contains_sensitive_data

from .docker_volume_helper_artifact import BundledVolumeHelperArtifact
from .workspace_permissions import PermissionProof
from .workspace_policy import (
    _EXCLUDED_DIRECTORIES,
    _EXCLUDED_SUFFIXES,
    WorkspaceCoverageEntry,
    WorkspaceScan,
    WorkspaceScanLimits,
    _sensitive_name,
)
from .workspace_scope import DurableWorkspaceScope

_NO_WINDOW_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_SAFE_VOLUME = re.compile(r"[A-Za-z0-9_.-]{1,128}")
_CANARY_PREFIX = ".agentstate-guard-verification/"
_MAX_OUTPUT_BYTES = 192 * 1024 * 1024


class VolumeHelperArtifact(Protocol):
    image_reference: str

    def ensure_available(self, docker: str, run: Any) -> tuple[bool, str]: ...


@dataclass(frozen=True)
class RestorePlan:
    restore_paths: tuple[str, ...]
    remove_paths: tuple[str, ...]


class DockerCliVolumeTransport:
    """Use an integrity-pinned ASG helper image; never the supervised image."""

    def __init__(
        self,
        *,
        docker_executable: str | None = None,
        helper_artifact: VolumeHelperArtifact | None = None,
    ) -> None:
        self._docker = docker_executable or resolved_docker_executable()
        self._helper = helper_artifact or BundledVolumeHelperArtifact.default()

    def validate_resource(self, scope: DurableWorkspaceScope) -> tuple[bool, str]:
        locator = _locator(scope)
        if self._docker is None or locator is None:
            return False, "DOCKER_VOLUME_BACKEND_UNAVAILABLE"
        name, driver, volume_scope, created_at, mountpoint_ref, _target_image = locator
        if driver != "local":
            return False, "DOCKER_VOLUME_HELPER_RUNTIME_UNAVAILABLE"
        info = self._run([self._docker, "info", "--format", "{{.ID}}"], timeout=20)
        inspect = self._run(
            [
                self._docker,
                "volume",
                "inspect",
                name,
                "--format",
                "{{.Name}}\t{{.Driver}}\t{{.Scope}}\t{{.CreatedAt}}\t{{.Mountpoint}}",
            ],
            timeout=20,
        )
        if info.returncode != 0 or inspect.returncode != 0:
            return False, "DOCKER_VOLUME_UNREACHABLE"
        parts = inspect.stdout.strip().split("\t")
        if len(parts) != 5:
            return False, "DOCKER_VOLUME_IDENTITY_UNAVAILABLE"
        observed_mount_ref = "sha256:" + hashlib.sha256(parts[4].encode()).hexdigest()
        if (parts[0], parts[1], parts[2], parts[3], observed_mount_ref) != (
            name,
            driver,
            volume_scope,
            created_at,
            mountpoint_ref,
        ):
            return False, "WORKSPACE_STORAGE_IDENTITY_MISMATCH"
        material = (
            f"docker-volume-v1\x1f{info.stdout.strip()}\x1f{name}\x1f{driver}"
            f"\x1f{volume_scope}\x1f{created_at}\x1f{mountpoint_ref}"
        ).encode()
        if "sha256:" + hashlib.sha256(material).hexdigest() != scope.storage_resource_identity:
            return False, "WORKSPACE_STORAGE_IDENTITY_MISMATCH"
        if self._helper is None:
            return False, "DOCKER_VOLUME_HELPER_ARTIFACT_UNAVAILABLE"
        ready, helper_reason = self._helper.ensure_available(self._docker, self._run)
        if not ready:
            return False, helper_reason
        probe = self._run(
            [
                self._docker,
                "run",
                "--rm",
                f"--name=asg-volume-probe-{uuid4().hex[:12]}",
                "--network=none",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--read-only",
                self._helper.image_reference,
                "--probe",
            ],
            timeout=60,
        )
        if probe.returncode != 0 or probe.stdout.strip() != "ASG_VOLUME_HELPER_READY":
            return False, "DOCKER_VOLUME_HELPER_RUNTIME_UNAVAILABLE"
        return True, "DOCKER_VOLUME_RESOURCE_VERIFIED"

    def scan(self, scope: DurableWorkspaceScope, limits: WorkspaceScanLimits | None = None) -> WorkspaceScan:
        limits = limits or WorkspaceScanLimits()
        payload = self._invoke(scope, "scan", limits=limits, writable=False)
        if payload.get("status") != "OK":
            raise RuntimeError(str(payload.get("reason_code", "DOCKER_VOLUME_SCAN_FAILED")))
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            raise TypeError("DOCKER_VOLUME_SCAN_INVALID")
        entries: list[WorkspaceCoverageEntry] = []
        total_restorable = 0
        for raw in raw_entries:
            entry, content_size = _coverage_entry(raw, total_restorable, limits)
            entries.append(entry)
            total_restorable += content_size
        entries.sort(key=lambda item: (item.relative_path.casefold(), item.relative_path))
        return WorkspaceScan(
            root=Path("/opaque-docker-volume"),
            entries=tuple(entries),
            complete=bool(payload.get("complete")),
            reason_code=str(payload.get("reason_code", "WORKSPACE_SCAN_COMPLETE")),
        )

    def test_restore(self, scope: DurableWorkspaceScope, artifact: dict) -> tuple[bool, str, int]:
        return _restore_result(self._invoke(scope, "test_restore", artifact=artifact, writable=False))

    def restore(self, scope: DurableWorkspaceScope, artifact: dict) -> tuple[bool, str, int]:
        current = self.scan(scope)
        baseline = {item["relative_path"]: item for item in artifact["workspace"]["coverage"]}
        current_map = {item.relative_path: item.to_extension_dict() for item in current.entries}
        try:
            plan = _exact_restore_plan(baseline, current_map)
        except ValueError as exc:
            return False, str(exc), 0
        changed = set(plan.restore_paths) | set(plan.remove_paths)
        if self._has_active_writer(scope) and any(not path.startswith(_CANARY_PREFIX) for path in changed):
            return False, "WORKSPACE_RESTORE_ACTIVE_WRITER_CONFLICT", 0
        payload = self._invoke(
            scope,
            "restore",
            artifact=_restore_artifact_subset(artifact, plan.restore_paths),
            restore_paths=plan.restore_paths,
            remove_paths=plan.remove_paths,
            writable=True,
        )
        ok, reason, count = _restore_result(payload)
        if not ok:
            return ok, reason, count
        reread = self.scan(scope)
        mismatches = _convergence_mismatches(
            baseline,
            {item.relative_path: item.to_extension_dict() for item in reread.entries},
        )
        if any(mismatches.values()):
            return False, "WORKSPACE_RESTORE_CONVERGENCE_FAILED", count
        return True, "WORKSPACE_RESTORED_AND_VERIFIED", count

    def _has_active_writer(self, scope: DurableWorkspaceScope) -> bool:
        locator = _locator(scope)
        if self._docker is None or locator is None:
            return True
        result = self._run(
            [self._docker, "ps", "--filter", f"volume={locator[0]}", "--format", "{{.ID}}"],
            timeout=20,
        )
        return result.returncode != 0 or bool(result.stdout.strip())

    def _invoke(
        self,
        scope: DurableWorkspaceScope,
        operation: str,
        *,
        limits: WorkspaceScanLimits | None = None,
        artifact: dict | None = None,
        restore_paths: tuple[str, ...] = (),
        remove_paths: tuple[str, ...] = (),
        writable: bool,
    ) -> dict[str, Any]:
        valid, reason = self.validate_resource(scope)
        if not valid:
            raise RuntimeError(reason)
        locator = _locator(scope)
        assert locator is not None and self._docker is not None and self._helper is not None
        request: dict[str, Any] = {
            "operation": operation,
            "logical_root": scope.logical_root,
            "restore_paths": list(restore_paths),
            "remove_paths": list(remove_paths),
        }
        if limits is not None:
            request["limits"] = {
                "max_entries": limits.max_entries,
                "max_file_bytes": limits.max_file_bytes,
                "max_total_restorable_bytes": limits.max_total_restorable_bytes,
                "max_depth": limits.max_depth,
                "excluded_directories": sorted(_EXCLUDED_DIRECTORIES),
                "excluded_suffixes": sorted(_EXCLUDED_SUFFIXES),
            }
        if artifact is not None:
            request["artifact"] = _json_artifact(artifact)
        with tempfile.TemporaryDirectory(prefix="asg-volume-helper-") as temporary:
            request_path = Path(temporary) / "request.json"
            request_path.write_text(json.dumps(request, separators=(",", ":")), encoding="utf-8")
            volume_option = "" if writable else ",readonly"
            command = [
                self._docker,
                "run",
                "--rm",
                f"--name=asg-volume-{operation}-{uuid4().hex[:12]}",
                "--network=none",
                "--cap-drop=ALL",
            ]
            if operation == "scan":
                command.append("--cap-add=DAC_READ_SEARCH")
            elif operation == "test_restore":
                command.extend(
                    [
                        "--cap-add=CHOWN",
                        "--cap-add=FOWNER",
                        "--cap-add=DAC_READ_SEARCH",
                        "--cap-add=DAC_OVERRIDE",
                    ]
                )
            elif writable:
                command.extend(["--cap-add=CHOWN", "--cap-add=FOWNER", "--cap-add=DAC_OVERRIDE"])
            command.extend(
                [
                    "--security-opt=no-new-privileges",
                    "--read-only",
                    "--tmpfs=/tmp:rw,nosuid,nodev,size=256m",
                    "--mount",
                    f"type=volume,src={locator[0]},dst=/asg-volume{volume_option}",
                    "--mount",
                    f"type=bind,src={request_path},dst=/asg/request.json,readonly",
                    self._helper.image_reference,
                    "/asg/request.json",
                ]
            )
            result = self._run(command, timeout=180)
        if result.returncode != 0 or len(result.stdout.encode()) > _MAX_OUTPUT_BYTES:
            raise RuntimeError("DOCKER_VOLUME_HELPER_FAILED")
        try:
            payload = json.loads(result.stdout)
        except ValueError as exc:
            raise RuntimeError("DOCKER_VOLUME_HELPER_OUTPUT_INVALID") from exc
        if not isinstance(payload, dict):
            raise TypeError("DOCKER_VOLUME_HELPER_OUTPUT_INVALID")
        return payload

    @staticmethod
    def _run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_NO_WINDOW_FLAGS,
            check=False,
        )


def _locator(scope: DurableWorkspaceScope) -> tuple[str, str, str, str, str, str] | None:
    if not isinstance(scope.storage_locator, str):
        return None
    parts = tuple(scope.storage_locator.split("\x1f"))
    if len(parts) != 6 or _SAFE_VOLUME.fullmatch(parts[0]) is None:
        return None
    return parts  # type: ignore[return-value]


def _coverage_entry(raw: object, total_restorable: int, limits: WorkspaceScanLimits) -> tuple[WorkspaceCoverageEntry, int]:
    if not isinstance(raw, dict):
        raise TypeError("DOCKER_VOLUME_SCAN_INVALID")
    relative, kind = raw.get("relative_path"), raw.get("object_kind")
    category, reason = str(raw.get("category")), raw.get("reason_code")
    size, digest = raw.get("size"), raw.get("digest")
    raw_content_digest = raw.get("content_digest")
    mode, uid, gid = raw.get("mode"), raw.get("uid"), raw.get("gid")
    link_target = raw.get("link_target")
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or PurePosixPath(relative).is_absolute()
        or ".." in PurePosixPath(relative).parts
        or PurePosixPath(relative).as_posix() != relative
        or kind not in {"FILE", "DIRECTORY", "SYMLINK", "SPECIAL"}
        or not isinstance(reason, str)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or not isinstance(mode, str)
        or not isinstance(uid, int)
        or isinstance(uid, bool)
        or uid < 0
        or not isinstance(gid, int)
        or isinstance(gid, bool)
        or gid < 0
        or (link_target is not None and not isinstance(link_target, str))
        or (
            raw_content_digest is not None
            and (
                not isinstance(raw_content_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", raw_content_digest) is None
            )
        )
    ):
        raise ValueError("DOCKER_VOLUME_SCAN_INVALID")
    content: bytes | None = None
    proof: PermissionProof | None = None
    added = 0
    if category == "candidate":
        if kind == "FILE":
            encoded = raw.get("content")
            if not isinstance(encoded, str):
                raise ValueError("DOCKER_VOLUME_SCAN_INVALID")
            content = base64.b64decode(encoded, validate=True)
            if (
                len(content) != size
                or raw_content_digest is None
                or hashlib.sha256(content).hexdigest() != raw_content_digest
            ):
                raise ValueError("DOCKER_VOLUME_SCAN_INVALID")
            sensitive = _sensitive_name(Path(relative)) or contains_sensitive_data(content.decode("utf-8", errors="replace"))
            if sensitive:
                category, reason, content = "audit_only", "WORKSPACE_SENSITIVE_AUDIT_ONLY", None
            elif total_restorable + size > limits.max_total_restorable_bytes:
                category, reason, content = "audit_only", "WORKSPACE_TOTAL_SIZE_LIMIT_AUDIT_ONLY", None
            else:
                category, reason, added = "restorable", "WORKSPACE_RESTORABLE", size
        elif kind in {"DIRECTORY", "SYMLINK"}:
            if kind == "SYMLINK" and not link_target:
                raise ValueError("DOCKER_VOLUME_SCAN_INVALID")
            category, reason = "restorable", "WORKSPACE_RESTORABLE"
        else:
            raise ValueError("DOCKER_VOLUME_SCAN_INVALID")
        if category == "restorable":
            proof = PermissionProof(kind="POSIX_MODE_OWNER", values={"mode": mode, "uid": uid, "gid": gid})
    if category not in {"restorable", "audit_only", "excluded", "unreachable"}:
        raise ValueError("DOCKER_VOLUME_SCAN_INVALID")
    return (
        WorkspaceCoverageEntry(
            relative_path=relative,
            object_kind=kind,
            category=category,
            reason_code=str(reason),
            size=size,
            content_digest=raw_content_digest if content is not None else None,
            observation_digest=digest,
            permission_proof=proof,
            link_target=link_target,
            content=content,
        ),
        added,
    )


def _json_artifact(artifact: dict) -> dict:
    value = dict(artifact)
    value["blobs"] = {key: content.hex() if isinstance(content, bytes) else content for key, content in artifact["blobs"].items()}
    return value


def _restore_artifact_subset(artifact: dict, paths: tuple[str, ...]) -> dict:
    selected = {
        item["relative_path"]: item
        for item in artifact["workspace"]["coverage"]
        if item["relative_path"] in paths and item.get("category") == "restorable"
    }
    digests = {item["content_digest"] for item in selected.values() if isinstance(item.get("content_digest"), str)}
    return {
        "workspace": {"coverage": [selected[path] for path in sorted(selected)]},
        "blobs": {digest: artifact["blobs"][digest] for digest in sorted(digests)},
    }


def _restore_result(payload: dict[str, Any]) -> tuple[bool, str, int]:
    return (
        payload.get("status") == "OK",
        str(payload.get("reason_code", "DOCKER_VOLUME_RESTORE_FAILED")),
        int(payload.get("verified_targets", 0)),
    )


def _entry_changed(before: dict, after: dict) -> bool:
    if (
        before.get("object_kind") == after.get("object_kind")
        and before.get("observation_digest") == after.get("observation_digest")
    ):
        return False
    return any(
        before.get(field) != after.get(field)
        for field in ("object_kind", "category", "size", "content_digest", "observation_digest", "permission_proof", "link_target")
    )


def _exact_restore_plan(baseline: dict[str, dict], current: dict[str, dict]) -> RestorePlan:
    restore: set[str] = set()
    remove: set[str] = set()
    blocked_prefixes: set[str] = set()
    for path, after in current.items():
        if path not in baseline and after.get("object_kind") == "DIRECTORY":
            descendants = [item for name, item in current.items() if name.startswith(path + "/")]
            if any(item.get("category") != "restorable" for item in descendants):
                blocked_prefixes.add(path)
    for path in sorted(set(baseline) | set(current)):
        before, after = baseline.get(path), current.get(path)
        if before is not None and before.get("category") == "restorable":
            if after is None or _entry_changed(before, after):
                if after is not None and after.get("category") != "restorable":
                    raise ValueError("WORKSPACE_RESTORE_UNSAFE_OBJECT_CONFLICT")
                restore.add(path)
                if after is not None and before.get("object_kind") != after.get("object_kind"):
                    remove.add(path)
        elif (
            before is None
            and after is not None
            and after.get("category") == "restorable"
            and not any(
                path == blocked or path.startswith(blocked + "/")
                for blocked in blocked_prefixes
            )
        ):
            remove.add(path)
    remove_order = tuple(sorted(remove, key=lambda value: (-len(PurePosixPath(value).parts), value)))
    return RestorePlan(tuple(sorted(restore)), remove_order)


def _restorable(mapping: dict[str, dict]) -> dict[str, dict]:
    return {path: item for path, item in mapping.items() if item.get("category") == "restorable"}


def _convergence_mismatches(expected: dict[str, dict], actual: dict[str, dict]) -> dict[str, tuple[str, ...]]:
    wanted, observed = _restorable(expected), _restorable(actual)
    common = set(wanted) & set(observed)
    type_mismatch = {path for path in common if wanted[path].get("object_kind") != observed[path].get("object_kind")}
    content = {
        path
        for path in common - type_mismatch
        if any(wanted[path].get(field) != observed[path].get(field) for field in ("size", "content_digest", "link_target"))
    }
    metadata = {
        path
        for path in common - type_mismatch
        if wanted[path].get("permission_proof") != observed[path].get("permission_proof")
    }
    return {
        "missing": tuple(sorted(set(wanted) - set(observed))),
        "unexpected": tuple(sorted(set(observed) - set(wanted))),
        "type": tuple(sorted(type_mismatch)),
        "content": tuple(sorted(content)),
        "metadata": tuple(sorted(metadata)),
    }


__all__ = ["DockerCliVolumeTransport", "RestorePlan", "_convergence_mismatches", "_coverage_entry", "_exact_restore_plan"]
