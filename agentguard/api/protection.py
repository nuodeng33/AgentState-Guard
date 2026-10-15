"""Authenticated local routes for directory protection and durable operations."""

from __future__ import annotations

import sqlite3
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agentguard.recovery.protection import ProtectionError


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RegisterRequest(StrictRequest):
    source_id: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=4096)
    request_id: str = Field(min_length=1, max_length=128)
    replaces_scope_id: str | None = Field(default=None, max_length=128)


class ConfigureRequest(StrictRequest):
    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    enabled: bool
    retention_days: int = Field(default=30, ge=1, le=3650)
    quota_bytes: int = Field(default=2147483648, ge=1024, le=1099511627776)


class PreviewRequest(StrictRequest):
    scope_id: str = Field(min_length=1, max_length=128)
    mode: Literal["undo_batch", "restore_version", "undo_operation"]
    file_ids: list[str] = Field(min_length=1, max_length=1000)
    version_id: str | None = Field(default=None, max_length=128)
    batch_id: str | None = Field(default=None, max_length=128)
    operation_id: str | None = Field(default=None, max_length=128)


class ApplyRequest(StrictRequest):
    preview_id: str = Field(min_length=1, max_length=128)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(min_length=1, max_length=128)


class SourceRequest(StrictRequest):
    request_id: str = Field(min_length=1, max_length=128)


class PinRequest(StrictRequest):
    pinned: bool


class LinkRequest(StrictRequest):
    request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    kind: Literal["scope", "session", "batch"]
    object_id: str = Field(min_length=1, max_length=128)
    task_id: str | None = Field(default=None, max_length=128)


def protection_router(runtime):
    router = APIRouter(prefix="/api/v1")

    def invoke(method, *args, **kwargs):
        try:
            with runtime.service() as service:
                result = getattr(service, method)(*args, **kwargs)
            if method in {"register", "configure", "queue_restore", "source_action"}:
                runtime.notify()
            return result
        except ProtectionError as exc:
            return JSONResponse({"status": "UNCHANGED", "reason_code": exc.reason_code, **exc.details}, status_code=exc.status_code)
        except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError) as exc:
            return JSONResponse({"status": "UNAVAILABLE", "reason_code": getattr(exc, "reason_code", "PROTECTION_UNAVAILABLE")}, status_code=503)

    @router.get("/protection/scopes")
    def scopes():
        result = invoke("scopes")
        if isinstance(result, dict):
            result["collector"] = {"running": bool(runtime.thread and runtime.thread.is_alive()),
                "reason_code": runtime.last_error, "metadata_interval_seconds": 2,
                "content_interval_seconds": 30, "stability_seconds": 2, "retention_interval_seconds": 300}
        return result

    @router.post("/protection/scopes", status_code=201)
    def register(body: RegisterRequest):
        return invoke("register", body.source_id, body.path, body.request_id, replaces_scope_id=body.replaces_scope_id)

    @router.get("/protection/scopes/candidates")
    def candidates(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        # Enumerating source metadata never starts a stopped environment.
        return invoke("candidates", limit, offset)

    @router.post("/protection/scopes/{scope_id}/configure")
    def configure(scope_id: str, body: ConfigureRequest):
        return invoke("configure", scope_id, body.request_id, body.expected_revision,
            enabled=body.enabled, retention_days=body.retention_days, quota_bytes=body.quota_bytes)

    @router.post("/protection/scopes/{scope_id}/capture")
    def capture(scope_id: str, body: StrictRequest):
        return invoke("capture", scope_id, force=True)

    @router.post("/protection/scopes/{scope_id}/collect")
    def collect(scope_id: str, body: StrictRequest):
        return invoke("collect", scope_id)

    @router.get("/protection/scopes/{scope_id}/versions")
    def versions(scope_id: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), view: Literal["full", "summary"] = "full"):
        return invoke("versions", scope_id, limit, offset, view=view)

    @router.get("/protection/scopes/{scope_id}/changes")
    def changes(scope_id: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), view: Literal["full", "summary"] = "full"):
        return invoke("changes", scope_id, limit, offset, view=view)

    @router.post("/protection/scopes/{scope_id}/versions/{version_id}/pin")
    def pin(scope_id: str, version_id: str, body: PinRequest):
        return invoke("pin", scope_id, version_id, body.pinned)

    @router.get("/protection/scopes/{scope_id}/files/{file_id}/diff")
    def diff(scope_id: str, file_id: str, before: str, after: str):
        return invoke("diff", scope_id, file_id, before, after)

    @router.get("/protection/scopes/{scope_id}/files/{file_id}/history")
    def history(scope_id: str, file_id: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return invoke("file_history", scope_id, file_id, limit, offset)

    @router.get("/protection/scopes/{scope_id}/versions/{version_id}/files/{file_id}")
    def content(scope_id: str, version_id: str, file_id: str):
        return invoke("file_content", scope_id, version_id, file_id)

    @router.post("/protection/scopes/{scope_id}/checkpoints/{checkpoint_id}/import")
    def import_checkpoint(scope_id: str, checkpoint_id: int, body: StrictRequest):
        from agentguard.storage.snapshots import SnapshotStore
        snapshots = getattr(runtime, "snapshots", None) or SnapshotStore(runtime.state_dir / "snapshots")
        return invoke("import_checkpoint", scope_id, checkpoint_id, snapshots)

    @router.post("/recovery/previews")
    def preview(body: PreviewRequest):
        return invoke("preview", body.scope_id, body.mode, body.file_ids,
            version_id=body.version_id, batch_id=body.batch_id, operation_id=body.operation_id)

    @router.get("/recovery/previews/{preview_id}/files/{file_id}/diff")
    def preview_diff(preview_id: str, file_id: str):
        return invoke("preview_diff", preview_id, file_id)

    @router.post("/recovery/operations", status_code=202)
    def apply(body: ApplyRequest):
        return invoke("queue_restore", body.preview_id, body.digest, body.request_id)

    @router.get("/recovery/operations")
    def operations(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), view: Literal["full", "summary"] = "full"):
        return invoke("operations", limit, offset, view=view)

    @router.get("/recovery/operations/{operation_id}")
    def operation(operation_id: str):
        return invoke("operation", operation_id)

    @router.get("/recovery/operations/{operation_id}/steps/{step_id}/retained")
    def retained(operation_id: str, step_id: str):
        return invoke("retained_preimage", operation_id, step_id)

    @router.get("/recovery/operations/{operation_id}/events")
    def operation_events(operation_id: str, limit: int = Query(100, ge=1, le=100), after: int = Query(0, ge=0)):
        return invoke("operation_events", operation_id, limit, after)

    @router.post("/recovery/operations/{operation_id}/reconcile")
    def reconcile(operation_id: str, body: StrictRequest):
        return invoke("reconcile", operation_id)

    @router.get("/works/{work_id}/protection-links")
    def links(work_id: UUID):
        return invoke("links", str(work_id))

    @router.get("/works/{work_id}/protection-changes")
    def work_changes(work_id: UUID, limit: int = Query(50, ge=1, le=100)):
        return invoke("work_changes", str(work_id), limit)

    @router.post("/works/{work_id}/protection-links")
    def link(work_id: UUID, body: LinkRequest):
        return invoke("link", str(work_id), body.kind, body.object_id, body.task_id,
            body.request_id, body.expected_revision)

    @router.post("/works/{work_id}/protection-links/unlink")
    def unlink(work_id: UUID, body: LinkRequest):
        return invoke("unlink", str(work_id), body.kind, body.object_id, body.task_id,
            body.request_id, body.expected_revision)

    @router.post("/discovery/sources/{source_id}/start-and-connect", status_code=202)
    def start_connect(source_id: str, body: SourceRequest):
        return invoke("source_action", source_id, "start-and-connect", body.request_id)

    @router.post("/discovery/sources/{source_id}/connect", status_code=202)
    def connect(source_id: str, body: SourceRequest):
        return invoke("source_action", source_id, "connect", body.request_id)

    @router.post("/discovery/sources/{source_id}/disconnect", status_code=202)
    def disconnect(source_id: str, body: SourceRequest):
        return invoke("source_action", source_id, "disconnect", body.request_id)

    return router
