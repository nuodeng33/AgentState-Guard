"""Local HTTP API server for AgentState Guard GUI."""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Query, Request
from pydantic import BaseModel, ConfigDict, Field

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
_SUPERVISION_SESSION_ID = re.compile(
    r"session-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


class SupervisionActionRequest(BaseModel):
    """Only an opaque server-issued stale binding may cross the UI boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)

    action_ref: str = Field(pattern=r"^[0-9a-f]{64}$")


class ControlledChangeRequest(BaseModel):
    """Caller intent contains content only; target and authority stay server-owned."""

    model_config = ConfigDict(extra="forbid", strict=True)

    content: str = Field(min_length=1, max_length=1_048_576)


class DiscoveryRefreshRequest(BaseModel):
    """Manual refresh accepts no caller-selected discovery authority."""

    model_config = ConfigDict(extra="forbid", strict=True)


class AnalyzeCurrentEnvironmentRequest(BaseModel):
    """AI analysis accepts intent only; Core constructs every authority fact."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ProductRecoveryEmptyRequest(BaseModel):
    """Checkpoint and test-restore targets are entirely server-owned."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ProductRestoreRequest(BaseModel):
    """A real restore requires one explicit confirmation and no caller target."""

    model_config = ConfigDict(extra="forbid", strict=True)

    confirm: bool


class DeviceLinkEmptyRequest(BaseModel):
    """Device Link lifecycle actions have no caller-selected network scope."""

    model_config = ConfigDict(extra="forbid", strict=True)


class DeviceLinkConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    confirm: bool


def create_app(
    state_db_path: Path | None = None,
    config: dict | None = None,
    assessment_provider=None,
    discovery_service=None,
    product_startup_discovery: bool = False,
    device_link_controller=None,
    host_observer=None,
):
    """Create a FastAPI application instance.

    Uses lazy imports so core modules don't depend on FastAPI.
    """
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    from ..commands.doctor import doctor as _doctor
    from ..commands.status import status as _status_ptr
    from ..core.config import Config
    from ..discovery.product import (
        ProductDiscoveryService,
        host_reconciliation_basenames,
        unavailable_product_snapshot,
    )
    from ..discovery.workspace_authority import (
        ResolvedWorkspaceAuthority,
        resolve_workspace_authorities,
    )
    from ..evidence.discovery_adapter import record_discovery_snapshot
    from ..host_observer import (
        HostAgentObservationTarget,
        HostNativeObservationPlan,
        HostNativeObserver,
        HostWorkspaceObservationTarget,
    )
    from ..recovery.docker_volume_transport import DockerCliVolumeTransport
    from ..recovery.workspace_changes import WorkspaceChangeObserver
    from ..recovery.workspace_permissions import (
        PermissionCapabilityError,
        current_user_permission_backend,
    )
    from ..recovery.workspace_scope import WorkspaceScopeService
    from ..storage.db import StateDB
    from ..storage.snapshots import SnapshotStore
    from ..transactions.engine import TransactionEngine

    app = FastAPI(title="AgentState Guard API", version="0.9.0-dev")

    def _action_failure(
        action: str,
        session_id: str,
        reason_code: str,
    ) -> dict[str, object]:
        return {
            "schema_version": "r4-p8-action-1",
            "action": action,
            "supervision_session_id": session_id,
            "status": "UNCHANGED",
            "reason_code": reason_code,
            "consumed": False,
            "evidence_refs": [],
        }

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/discovery/refresh":
            return JSONResponse(
                {
                    "schema_version": "product-discovery-1",
                    "status": "UNCHANGED",
                    "reason_code": "DISCOVERY_REFRESH_REQUEST_INVALID",
                    "snapshot_id": None,
                    "observed_at": None,
                    "affected_views": [],
                    "runtime_count": 0,
                    "agent_count": 0,
                    "evidence_refs": [],
                },
                status_code=422,
            )
        if request.method == "POST" and path == "/api/ai/analyze":
            return JSONResponse(
                {
                    "schema_version": "product-ai-advisory-1",
                    "status": "UNCHANGED",
                    "reason_code": "AI_ANALYZE_REQUEST_INVALID",
                    "severity": "UNKNOWN",
                    "summary": None,
                    "uncertainties": [],
                    "recommended_checks": [],
                    "evidence_refs": [],
                    "provider": None,
                    "model": None,
                    "analyzed_at": None,
                },
                status_code=422,
            )
        if request.method == "POST" and (
            path == "/api/v1/recovery/checkpoints"
            or path.startswith("/api/v1/recovery/")
        ):
            checkpoint_id = None
            parts = path.rstrip("/").split("/")
            if len(parts) >= 2 and parts[-1] in {"test", "restore"}:
                checkpoint_id = parts[-2]
            return JSONResponse(
                {
                    "schema_version": "product-recovery-action-1",
                    "status": "UNCHANGED",
                    "reason_code": "RECOVERY_REQUEST_INVALID",
                    "checkpoint_id": checkpoint_id,
                    "evidence_refs": [],
                },
                status_code=422,
            )
        if request.method == "POST" and path.startswith("/api/v1/device-link"):
            return JSONResponse(
                {
                    "schema_version": "device-link-lifecycle-1",
                    "status": "UNCHANGED",
                    "reason_code": "DEVICE_LINK_REQUEST_INVALID",
                },
                status_code=422,
            )
        if (
            path.startswith("/api/v1/supervision/")
            and request.method == "POST"
            and path.endswith(("/approve-once", "/reject"))
        ):
            parts = path.rstrip("/").split("/")
            raw_session_id = parts[-2] if len(parts) >= 2 else ""
            session_id = (
                raw_session_id
                if _SUPERVISION_SESSION_ID.fullmatch(raw_session_id)
                else "INVALID"
            )
            action = "APPROVE_ONCE" if path.endswith("/approve-once") else "REJECT"
            return JSONResponse(
                _action_failure(
                    action,
                    session_id,
                    "SUPERVISION_ACTION_REQUEST_INVALID",
                ),
                status_code=422,
            )
        if (
            request.method == "POST"
            and path.startswith("/api/v1/supervision/")
            and (path == "/api/v1/supervision/changes" or path.endswith("/apply"))
        ):
            from .r4_controlled_change import controlled_change_failure

            return JSONResponse(
                controlled_change_failure("CONTROLLED_CHANGE_REQUEST_INVALID"),
                status_code=422,
            )
        return JSONResponse({"detail": exc.errors()}, status_code=422)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "X-Session-Token",
        ],
    )

    # Serve web_static if built (AFTER API routes are defined below)
    # We'll add a catch-all at the end via the @app.exception_handler

    # Session token
    session_token = secrets.token_hex(32)

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        # CORS preflight carries no authority token. Let the strict CORS
        # middleware accept or reject the requested origin/method/headers;
        # the subsequent real API request remains token-protected below.
        if (
            request.method == "OPTIONS"
            or (not request.url.path.startswith("/api/"))
            or request.url.path in ("/api/health", "/api/session")
        ):
            response = await call_next(request)
            return response
        token = request.headers.get("X-Session-Token", "")
        if token != session_token:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        return response

    cfg_o = Config(
        Path(config.get("base_dir") or PROJECT_ROOT) if config else PROJECT_ROOT
    )
    product_config_target = cfg_o.ensure_product_config()
    cfg = cfg_o._data
    db_path = state_db_path or cfg_o.state_db()
    db = StateDB(db_path)
    snapshots_dir = cfg_o.snapshot_dir()
    _ai_provider = assessment_provider
    _latest_ai_advisory: dict[str, object] = {
        "schema_version": "product-ai-advisory-1",
        "status": "UNAVAILABLE",
        "reason_code": "AI_ADVISORY_NOT_RUN",
        "evidence_refs": [],
    }
    _product_discovery = discovery_service or ProductDiscoveryService()
    _host_observer = host_observer or HostNativeObserver(db_path)

    def _get_db():
        db.connect()
        return db

    def _refresh_product_discovery() -> dict[str, object]:
        unavailable = False
        authority_report = None
        supports_workspace_authority = callable(
            getattr(_product_discovery, "discover_with_authority", None)
        )
        try:
            if supports_workspace_authority:
                authority_report = _product_discovery.discover_with_authority()
                snapshot = authority_report.snapshot
            else:
                snapshot = _product_discovery.discover()
        except (OSError, RuntimeError, TypeError, ValueError):
            snapshot = unavailable_product_snapshot()
            unavailable = True
        scope_result = None
        scope_results = []
        change_result = None
        active_scope = None
        current_db = _get_db()
        try:
            receipts = record_discovery_snapshot(
                current_db,
                snapshot,
                recorded_at=datetime.now(UTC),
            )
            if supports_workspace_authority:
                resolved_scopes = (
                    resolve_workspace_authorities(authority_report)
                    if authority_report is not None
                    else (
                        ResolvedWorkspaceAuthority(
                            status="UNAVAILABLE",
                            reason_code="WORKSPACE_SCOPE_DISCOVERY_UNAVAILABLE",
                        ),
                    )
                )
                if not resolved_scopes:
                    resolved_scopes = (
                        ResolvedWorkspaceAuthority(
                            status="NOT_OBSERVED",
                            reason_code="WORKSPACE_SCOPE_NOT_OBSERVED",
                        ),
                    )
                scope_service = WorkspaceScopeService(current_db)
                for resolved_scope in resolved_scopes:
                    scope_results.append(
                        scope_service.bind(
                            resolved_scope,
                            recorded_at=datetime.now(UTC),
                            discovery_snapshot_id=snapshot.snapshot_id,
                        )
                    )
                scope_result = next(
                    (item for item in scope_results if item.status == "BOUND"),
                    scope_results[-1],
                )
                bound_results = [
                    item for item in scope_results if item.status == "BOUND"
                ]
                if len(bound_results) == 1:
                    authority = scope_service.resolve_authority(
                        bound_results[0].workspace_id
                    )
                    active_scope = authority.scope
                    if active_scope is not None:
                        if active_scope.storage_kind == "DOCKER_NAMED_VOLUME":
                            volume_transport = DockerCliVolumeTransport()
                            change_result = WorkspaceChangeObserver(
                                database=current_db,
                                snapshots=SnapshotStore(snapshots_dir),
                                permission_backend=None,
                                storage_scanner=volume_transport.scan,
                            ).observe(active_scope, observed_at=datetime.now(UTC))
                        else:
                            try:
                                permission_backend = current_user_permission_backend()
                            except PermissionCapabilityError as exc:
                                change_result = {
                                    "status": "UNREACHABLE",
                                    "reason_code": exc.reason_code,
                                    "change_count": 0,
                                    "evidence_refs": (),
                                }
                            else:
                                change_result = WorkspaceChangeObserver(
                                    database=current_db,
                                    snapshots=SnapshotStore(snapshots_dir),
                                    permission_backend=permission_backend,
                                ).observe(active_scope, observed_at=datetime.now(UTC))
            target_events: dict[str, str] = {}
            if authority_report is not None:
                rows = current_db._conn.execute(
                    """SELECT event_id, subject_ref, execution_domain_id,
                              payload_safe_json
                       FROM evidence_ledger_events
                       WHERE event_type = 'AGENT_DETECTED'"""
                ).fetchall()
                for event_id, subject_ref, domain_id, payload_json in rows:
                    try:
                        payload = json.loads(payload_json)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if (
                        isinstance(payload, dict)
                        and payload.get("snapshot_id") == snapshot.snapshot_id
                        and payload.get("agent_id") == subject_ref
                    ):
                        target_events[f"{subject_ref}\x1f{domain_id}"] = event_id
            targets = tuple(
                HostAgentObservationTarget(
                    agent_ref=item.agent_id,
                    agent_event_id=target_events[
                        f"{item.agent_id}\x1f{item.execution_domain_id}"
                    ],
                    process_instance_id=item.process_instance_id,
                    pid=item.pid,
                    create_time=item.create_time,
                    execution_domain_id=item.execution_domain_id,
                )
                for item in (
                    authority_report.host_agent_process_authorities
                    if authority_report is not None
                    else ()
                )
                if f"{item.agent_id}\x1f{item.execution_domain_id}" in target_events
            )
            workspace_target = None
            if active_scope is not None:
                target_refs = {item.agent_ref for item in targets}
                bound_agents = tuple(
                    sorted(target_refs.intersection(active_scope.agent_ids))
                )
                if bound_agents:
                    workspace_target = HostWorkspaceObservationTarget(
                        workspace_id=active_scope.workspace_id,
                        execution_domain_id=active_scope.execution_domain_id,
                        root_digest=active_scope.root_digest,
                        binding_event_id=active_scope.ledger_event_id,
                        root_path=active_scope.root_path,
                        agent_refs=bound_agents,
                    )
            _host_observer.update(
                HostNativeObservationPlan(
                    targets=targets,
                    workspace=workspace_target,
                )
            )
        finally:
            current_db.close()
        affected_views = ["runtime", "agents", "supervision"]
        evidence_refs = [receipt.event_id for receipt in receipts]
        if scope_results:
            affected_views.extend(("changes", "recovery"))
            evidence_refs.extend(
                item.ledger_event_id
                for item in scope_results
                if item.ledger_event_id is not None
            )
        if change_result is not None:
            change_refs = (
                change_result.get("evidence_refs", ())
                if isinstance(change_result, dict)
                else change_result.evidence_refs
            )
            evidence_refs.extend(change_refs)
        return {
            "schema_version": "product-discovery-1",
            "status": snapshot.status.value,
            "reason_code": (
                "DISCOVERY_REFRESH_UNAVAILABLE"
                if unavailable
                else "DISCOVERY_REFRESHED"
            ),
            "snapshot_id": snapshot.snapshot_id,
            "observed_at": snapshot.observed_at.isoformat(),
            "affected_views": affected_views,
            "runtime_count": len(snapshot.runtimes),
            "agent_count": len(snapshot.agents),
            "workspace_scope_status": (
                scope_result.status if scope_result is not None else "NOT_SUPPORTED"
            ),
            "workspace_scope_reason_code": (
                scope_result.reason_code
                if scope_result is not None
                else "WORKSPACE_SCOPE_NOT_OBSERVED"
            ),
            "workspace_change_status": (
                change_result.get("status")
                if isinstance(change_result, dict)
                else change_result.status
                if change_result is not None
                else "NOT_RUN"
            ),
            "workspace_change_reason_code": (
                change_result.get("reason_code")
                if isinstance(change_result, dict)
                else change_result.reason_code
                if change_result is not None
                else "WORKSPACE_CHANGE_NOT_RUN"
            ),
            "workspace_change_count": (
                change_result.get("change_count", 0)
                if isinstance(change_result, dict)
                else change_result.change_count
                if change_result is not None
                else 0
            ),
            "evidence_refs": evidence_refs,
        }

    configure_reconciliation = getattr(
        _host_observer, "configure_reconciliation", None
    )
    if callable(configure_reconciliation):
        configure_reconciliation(
            _refresh_product_discovery,
            basenames=host_reconciliation_basenames(),
        )

    if product_startup_discovery:
        try:
            _refresh_product_discovery()
        except (OSError, RuntimeError, sqlite3.DatabaseError, TypeError, ValueError):
            pass

    @app.on_event("startup")
    async def _start_host_observer() -> None:
        _host_observer.start()

    @app.on_event("shutdown")
    async def _stop_host_observer() -> None:
        _host_observer.stop()

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": "0.9.0.dev0"}

    @app.get("/api/session")
    async def get_session():
        return {"token": session_token}

    @app.get("/api/readiness")
    async def readiness():
        try:
            current_db = _get_db()
            current_db._conn.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()
        except (OSError, sqlite3.DatabaseError, RuntimeError):
            return JSONResponse(
                {
                    "status": "degraded",
                    "database": "unreachable",
                    "reason_code": "RUNTIME_DATABASE_UNREACHABLE",
                },
                status_code=503,
            )
        finally:
            db.close()
        return {
            "status": "ready",
            "database": "available",
            "reason_code": "RUNTIME_READY",
        }

    @app.get("/api/status")
    async def api_status():
        db = _get_db()
        try:
            s = _status_ptr(cfg, db)
            return s
        finally:
            db.close()

    @app.get("/api/doctor")
    async def api_doctor():
        return _doctor(cfg)

    @app.get("/api/checkpoints")
    async def api_checkpoints(limit: int = 20):
        db = _get_db()
        try:
            return {"checkpoints": db.list_checkpoints(limit=limit)}
        finally:
            db.close()

    @app.get("/api/transactions")
    async def api_transactions():
        db = _get_db()
        try:
            eng = TransactionEngine(db, SnapshotStore(snapshots_dir), cfg)
            return {"transactions": eng.list_transactions()}
        finally:
            db.close()

    @app.get("/api/verify")
    async def api_verify():
        db = _get_db()
        try:
            integrity = db._conn.execute("PRAGMA integrity_check").fetchone()
            return {
                "status": "ok",
                "db_integrity": integrity[0] if integrity else "unknown",
            }
        finally:
            db.close()

    @app.get("/api/versions")
    async def api_versions():
        from ..core.versions import all_versions

        return {"versions": all_versions()}

    # ---- R4 P8 authoritative read projections ----

    from ..supervision.service import SupervisionActionError, SupervisionService
    from .r4_projection import R4ReadProjectionService

    def _r4_projection(view: str, **projection_args: Any) -> dict[str, Any]:
        try:
            current_db = _get_db()
        except (OSError, sqlite3.DatabaseError, RuntimeError):
            return R4ReadProjectionService.unavailable(view)
        try:
            projector = R4ReadProjectionService(
                current_db, SnapshotStore(snapshots_dir)
            )
            return getattr(projector, view)(**projection_args)
        except (OSError, sqlite3.DatabaseError, RuntimeError, TypeError, ValueError):
            return R4ReadProjectionService.unavailable(view)
        finally:
            current_db.close()

    @app.get("/api/v1/runtime")
    async def api_r4_runtime():
        return _r4_projection("runtime")

    @app.get("/api/v1/agents")
    async def api_r4_agents():
        return _r4_projection("agents")

    @app.get("/api/v1/supervision")
    async def api_r4_supervision():
        return _r4_projection("supervision")

    @app.get("/api/v1/recovery")
    async def api_r4_recovery():
        return _r4_projection("recovery")

    @app.post("/api/v1/recovery/checkpoints")
    async def api_recovery_checkpoint(_request: ProductRecoveryEmptyRequest):
        from ..recovery.contracts import RecoveryOperation
        from .product_recovery import (
            ProductRecoveryError,
            recovery_failure,
            run_product_recovery,
        )

        current_db = _get_db()
        try:
            return run_product_recovery(
                current_db,
                SnapshotStore(snapshots_dir),
                target=product_config_target,
                operation=RecoveryOperation.SNAPSHOT,
                discovery_service=_product_discovery,
            )
        except ProductRecoveryError as error:
            return JSONResponse(
                recovery_failure(error.reason_code), status_code=error.status_code
            )
        finally:
            current_db.close()

    @app.post("/api/v1/recovery/{checkpoint_id}/test")
    async def api_recovery_test(
        checkpoint_id: str,
        _request: ProductRecoveryEmptyRequest,
    ):
        from ..recovery.contracts import RecoveryOperation
        from .product_recovery import (
            ProductRecoveryError,
            recovery_failure,
            run_product_recovery,
        )

        current_db = _get_db()
        try:
            return run_product_recovery(
                current_db,
                SnapshotStore(snapshots_dir),
                target=product_config_target,
                operation=RecoveryOperation.TEST_RESTORE,
                checkpoint_id=checkpoint_id,
                discovery_service=_product_discovery,
            )
        except ProductRecoveryError as error:
            return JSONResponse(
                recovery_failure(error.reason_code, checkpoint_id),
                status_code=error.status_code,
            )
        finally:
            current_db.close()

    @app.post("/api/v1/recovery/{checkpoint_id}/restore")
    async def api_recovery_restore(
        checkpoint_id: str,
        request: ProductRestoreRequest,
    ):
        from ..recovery.contracts import RecoveryOperation
        from .product_recovery import (
            ProductRecoveryError,
            recovery_failure,
            run_product_recovery,
        )

        current_db = _get_db()
        try:
            return run_product_recovery(
                current_db,
                SnapshotStore(snapshots_dir),
                target=product_config_target,
                operation=RecoveryOperation.RESTORE,
                checkpoint_id=checkpoint_id,
                confirmed=request.confirm,
                discovery_service=_product_discovery,
            )
        except ProductRecoveryError as error:
            return JSONResponse(
                recovery_failure(error.reason_code, checkpoint_id),
                status_code=error.status_code,
            )
        finally:
            current_db.close()

    @app.get("/api/v1/changes")
    async def api_r4_changes(
        limit: int = Query(default=100, ge=1, le=100),
        before_sequence: int | None = Query(default=None, ge=1),
        include_process_activity: bool = False,
        workspace_id: str | None = Query(
            default=None,
            pattern=r"^[A-Za-z0-9_.:-]{1,64}$",
        ),
        checkpoint_id: str | None = Query(
            default=None,
            pattern=r"^[A-Za-z0-9_.:-]{1,64}$",
        ),
    ):
        return _r4_projection(
            "changes",
            limit=limit,
            before_sequence=before_sequence,
            include_process_activity=include_process_activity,
            workspace_id=workspace_id,
            checkpoint_id=checkpoint_id,
        )

    @app.get("/api/v1/evidence/{event_id}")
    async def api_r4_evidence(event_id: str):
        try:
            current_db = _get_db()
        except (OSError, sqlite3.DatabaseError, RuntimeError):
            return JSONResponse(
                {
                    "schema_version": "r4-product-evidence-1",
                    "status": "DEGRADED",
                    "reason_code": "R4_DATABASE_UNREACHABLE",
                    "event_id": event_id,
                },
                status_code=503,
            )
        try:
            result = R4ReadProjectionService(
                current_db,
                SnapshotStore(snapshots_dir),
            ).evidence(event_id)
            status_code = 404 if result["status"] == "NOT_FOUND" else 200
            return JSONResponse(result, status_code=status_code)
        except (OSError, sqlite3.DatabaseError, RuntimeError, TypeError, ValueError):
            return JSONResponse(
                {
                    "schema_version": "r4-product-evidence-1",
                    "status": "DEGRADED",
                    "reason_code": "EVIDENCE_DETAIL_UNAVAILABLE",
                    "event_id": event_id,
                },
                status_code=503,
            )
        finally:
            current_db.close()

    @app.post("/api/v1/discovery/refresh")
    async def api_product_discovery_refresh(_body: DiscoveryRefreshRequest):
        try:
            result = _refresh_product_discovery()
            if result["reason_code"] == "DISCOVERY_REFRESH_UNAVAILABLE":
                return JSONResponse(result, status_code=503)
            return result
        except (OSError, RuntimeError, sqlite3.DatabaseError, TypeError, ValueError):
            return JSONResponse(
                {
                    "schema_version": "product-discovery-1",
                    "status": "DEGRADED",
                    "reason_code": "DISCOVERY_REFRESH_UNAVAILABLE",
                    "snapshot_id": None,
                    "observed_at": None,
                    "affected_views": [],
                    "runtime_count": 0,
                    "agent_count": 0,
                    "evidence_refs": [],
                },
                status_code=503,
            )

    def _supervision_action(
        session_id: str,
        body: SupervisionActionRequest,
        *,
        action: str,
    ):
        if not _SUPERVISION_SESSION_ID.fullmatch(session_id):
            return JSONResponse(
                _action_failure(
                    action,
                    "INVALID",
                    "SUPERVISION_ACTION_REQUEST_INVALID",
                ),
                status_code=422,
            )
        try:
            current_db = _get_db()
        except (OSError, sqlite3.DatabaseError, RuntimeError):
            return JSONResponse(
                _action_failure(
                    action,
                    session_id,
                    "SUPERVISION_AUTHORITY_UNAVAILABLE",
                ),
                status_code=503,
            )
        try:
            service = SupervisionService(current_db)
            result = (
                service.approve_once(session_id, body.action_ref)
                if action == "APPROVE_ONCE"
                else service.reject_once(session_id, body.action_ref)
            )
            return result.to_dict()
        except SupervisionActionError as exc:
            if exc.reason_code == "SUPERVISION_SESSION_NOT_FOUND":
                status_code = 404
            elif exc.reason_code in {
                "SUPERVISION_AUTHORITY_UNAVAILABLE",
                "SUPERVISION_LEDGER_INVALID",
                "SUPERVISION_POLICY_EVIDENCE_INVALID",
            }:
                status_code = 503
            else:
                status_code = 409
            return JSONResponse(
                _action_failure(action, session_id, exc.reason_code),
                status_code=status_code,
            )
        except (OSError, sqlite3.DatabaseError, RuntimeError):
            return JSONResponse(
                _action_failure(
                    action,
                    session_id,
                    "SUPERVISION_AUTHORITY_UNAVAILABLE",
                ),
                status_code=503,
            )
        finally:
            current_db.close()

    @app.post("/api/v1/supervision/{session_id}/approve-once")
    async def api_r4_supervision_approve_once(
        session_id: str,
        body: SupervisionActionRequest,
    ):
        return _supervision_action(session_id, body, action="APPROVE_ONCE")

    @app.post("/api/v1/supervision/{session_id}/reject")
    async def api_r4_supervision_reject(
        session_id: str,
        body: SupervisionActionRequest,
    ):
        return _supervision_action(session_id, body, action="REJECT")

    # ---- R4 P9 bounded controlled configuration change ----

    from .r4_controlled_change import (
        ControlledChangeError,
        apply_controlled_change,
        controlled_change_failure,
        prepare_controlled_change,
    )

    @app.post("/api/v1/supervision/changes")
    async def api_r4_controlled_change_prepare(body: ControlledChangeRequest):
        try:
            current_db = _get_db()
        except (OSError, sqlite3.DatabaseError, RuntimeError):
            return JSONResponse(
                controlled_change_failure("CONTROLLED_CHANGE_AUTHORITY_UNAVAILABLE"),
                status_code=503,
            )
        try:
            return prepare_controlled_change(
                current_db,
                SnapshotStore(snapshots_dir),
                base_dir=cfg_o.base_dir,
                content=body.content.encode("utf-8"),
                assessment_provider=_ai_provider,
                discovery_service=_product_discovery,
            )
        except ControlledChangeError as exc:
            return JSONResponse(
                controlled_change_failure(exc.reason_code),
                status_code=exc.status_code,
            )
        finally:
            current_db.close()

    @app.post("/api/v1/supervision/{session_id}/apply")
    async def api_r4_controlled_change_apply(
        session_id: str,
        body: ControlledChangeRequest,
    ):
        if not _SUPERVISION_SESSION_ID.fullmatch(session_id):
            return JSONResponse(
                controlled_change_failure("CONTROLLED_CHANGE_REQUEST_INVALID"),
                status_code=422,
            )
        try:
            current_db = _get_db()
        except (OSError, sqlite3.DatabaseError, RuntimeError):
            return JSONResponse(
                controlled_change_failure(
                    "CONTROLLED_CHANGE_AUTHORITY_UNAVAILABLE",
                    session_id=session_id,
                ),
                status_code=503,
            )
        try:
            return apply_controlled_change(
                current_db,
                SnapshotStore(snapshots_dir),
                base_dir=cfg_o.base_dir,
                session_id=session_id,
                content=body.content.encode("utf-8"),
            )
        except ControlledChangeError as exc:
            return JSONResponse(
                controlled_change_failure(exc.reason_code, session_id=session_id),
                status_code=exc.status_code,
            )
        finally:
            current_db.close()

    @app.get("/api/handoff")
    async def api_handoff():
        db = _get_db()
        try:
            from ..commands.handoff import cmd_handoff

            result = cmd_handoff(Path("/tmp/agentguard-handoff"), db)
            return result
        finally:
            db.close()

    # ---- AI Provider (backend proxy, no CORS) ----

    from ..ai.provider import (
        OpenAICompatibleProvider,
        ProviderConfig,
    )

    _ai_config = None

    @app.post("/api/ai/test")
    async def _ai_test(body: dict):
        """Test AI provider connection. API key stays in memory."""
        nonlocal _ai_provider
        api_key = body.get("api_key", "")
        base_url = body.get("base_url", "")
        model = body.get("model", "")
        if not base_url:
            return {"ok": False, "error": "Base URL required"}
        cfg = ProviderConfig(base_url=base_url, api_key=api_key, model=model)
        provider = OpenAICompatibleProvider(cfg, timeout=10)
        result = provider.test_connection()
        if result.get("ok") is True:
            _ai_provider = provider
        return result

    @app.post("/api/ai/models")
    async def _ai_models(body: dict):
        """List models from AI provider."""
        api_key = body.get("api_key", "")
        base_url = body.get("base_url", "")
        if not base_url:
            return {"models": [], "error": "Base URL required"}
        cfg = ProviderConfig(base_url=base_url, api_key=api_key)
        provider = OpenAICompatibleProvider(cfg, timeout=10)
        models = provider.list_models()
        return {"models": [m.to_dict() for m in models]}

    @app.post("/api/ai/analyze")
    async def _ai_analyze(_body: AnalyzeCurrentEnvironmentRequest):
        """Analyze only current, verified, server-projected environment state."""
        from ..core.sanitizer import sanitize_text

        nonlocal _latest_ai_advisory

        unavailable = {
            "schema_version": "product-ai-advisory-1",
            "status": "UNAVAILABLE",
            "reason_code": "AI_PROVIDER_UNAVAILABLE",
            "severity": "UNKNOWN",
            "summary": "AI provider is unavailable.",
            "uncertainties": ["AI_ASSESSMENT_UNAVAILABLE"],
            "recommended_checks": ["Configure and test an AI provider"],
            "evidence_refs": [],
            "provider": None,
            "model": None,
            "analyzed_at": None,
        }
        if _ai_provider is None or not callable(getattr(_ai_provider, "analyze", None)):
            return JSONResponse(unavailable, status_code=503)
        try:
            current_db = _get_db()
            projector = R4ReadProjectionService(
                current_db,
                SnapshotStore(snapshots_dir),
            )
            context = {
                "runtime": projector.runtime(),
                "agents": projector.agents(),
                "supervision": projector.supervision(),
                "recovery": projector.recovery(),
                "changes": projector.changes(),
            }
        except (OSError, sqlite3.DatabaseError, RuntimeError, TypeError, ValueError):
            return JSONResponse(
                {**unavailable, "reason_code": "AI_AUTHORITY_UNAVAILABLE"},
                status_code=503,
            )
        finally:
            db.close()
        try:
            result = _ai_provider.analyze(context)
        except Exception:  # noqa: BLE001 - external provider boundary fails closed.
            return JSONResponse(unavailable, status_code=503)
        status = str(getattr(result, "status", "error")).casefold()
        if status not in {"ok", "warn", "attention"}:
            return JSONResponse(unavailable, status_code=503)
        severity = {
            "low": "LOW",
            "medium": "MEDIUM",
            "high": "HIGH",
            "critical": "CRITICAL",
        }.get(str(getattr(result, "severity", "")).casefold(), "UNKNOWN")

        def bounded_strings(value: object) -> list[str]:
            if not isinstance(value, list):
                return []
            return [
                sanitize_text(item)[:500]
                for item in value[:5]
                if isinstance(item, str) and item
            ]

        refs = sorted(
            {
                ref
                for projection in context.values()
                for ref in projection.get("evidence_refs", [])
                if isinstance(ref, str) and ref
            }
        )
        provider_name = str(
            getattr(result, "provider", "")
            or getattr(_ai_provider, "provider_name", "unknown")
        )[:128]
        model = str(
            getattr(result, "model", "") or getattr(_ai_provider, "model", "unknown")
        )[:128]
        advisory = {
            "schema_version": "product-ai-advisory-1",
            "status": "AVAILABLE",
            "reason_code": "AI_ADVISORY_AVAILABLE",
            "severity": severity,
            "summary": sanitize_text(str(getattr(result, "summary", "")))[:500],
            "uncertainties": bounded_strings(getattr(result, "possible_causes", [])),
            "recommended_checks": bounded_strings(
                getattr(result, "recommended_checks", [])
            ),
            "evidence_refs": refs,
            "provider": provider_name,
            "model": model,
            "analyzed_at": str(getattr(result, "analyzed_at", ""))[:64],
        }
        _latest_ai_advisory = advisory
        return advisory

    # ---- Device Link control plane (Core-only; data plane is independent 8788) ----

    if device_link_controller is None:
        from ..device_link.product import (
            UnavailableDeviceLinkController,
            build_device_link_product,
        )

        try:
            device_link_controller = build_device_link_product(
                state_db_path=db_path,
                snapshots_dir=snapshots_dir,
                state_dir=cfg_o.state_dir(),
                advisory_getter=lambda: dict(_latest_ai_advisory),
            )
        except (OSError, RuntimeError, sqlite3.DatabaseError, TypeError, ValueError):
            device_link_controller = UnavailableDeviceLinkController()
    app.state.device_link_controller = device_link_controller

    @app.on_event("shutdown")
    async def shutdown_device_link():
        device_link_controller.disable()

    def _device_link_error(error):
        return JSONResponse(
            {
                "schema_version": "device-link-lifecycle-1",
                "status": "UNCHANGED",
                "reason_code": error.code,
            },
            status_code=error.status,
        )

    @app.get("/api/v1/devices")
    async def api_devices():
        return device_link_controller.product_status()

    @app.post("/api/v1/device-link/enable")
    async def api_device_link_enable(_body: DeviceLinkEmptyRequest):
        from ..device_link.errors import DeviceLinkError

        try:
            return device_link_controller.enable()
        except DeviceLinkError as error:
            return _device_link_error(error)

    @app.post("/api/v1/device-link/disable")
    async def api_device_link_disable(_body: DeviceLinkEmptyRequest):
        from ..device_link.errors import DeviceLinkError

        try:
            return device_link_controller.disable()
        except DeviceLinkError as error:
            return _device_link_error(error)

    @app.post("/api/v1/device-link/network/refresh")
    async def api_device_link_network_refresh(_body: DeviceLinkEmptyRequest):
        from ..device_link.errors import DeviceLinkError

        try:
            return device_link_controller.refresh_network()
        except DeviceLinkError as error:
            return _device_link_error(error)

    @app.post("/api/v1/device-link/pairings")
    async def api_device_link_pairing(_body: DeviceLinkEmptyRequest):
        from ..device_link.errors import DeviceLinkError

        try:
            return device_link_controller.create_pairing_invitation()
        except DeviceLinkError as error:
            return _device_link_error(error)

    @app.get("/api/v1/device-link/pairings/{session_id}")
    async def api_device_link_pairing_status(session_id: str):
        if device_link_controller.gateway is None:
            return JSONResponse(
                {
                    "schema_version": "device-link-pairing-1",
                    "status": "UNCHANGED",
                    "reason_code": "DEVICE_LINK_AUTHORITY_UNAVAILABLE",
                },
                status_code=503,
            )
        result = device_link_controller.gateway.pair_poll(session_id)
        if "error" in result:
            return JSONResponse(
                {
                    "schema_version": "device-link-pairing-1",
                    "status": "UNCHANGED",
                    "reason_code": "PAIR_SESSION_NOT_FOUND",
                },
                status_code=result.get("code", 404),
            )
        return result

    @app.post("/api/v1/device-link/pairings/{session_id}/confirm")
    async def api_device_link_pairing_confirm(
        session_id: str, body: DeviceLinkConfirmRequest
    ):
        from ..device_link.errors import DeviceLinkError

        if device_link_controller.gateway is None:
            return _device_link_error(
                DeviceLinkError(
                    503, "DEVICE_LINK_AUTHORITY_UNAVAILABLE", "Device Link unavailable"
                )
            )
        try:
            return device_link_controller.gateway.pair_desktop_confirm(
                session_id, body.confirm
            )
        except DeviceLinkError as error:
            return _device_link_error(error)

    @app.post("/api/v1/device-link/pairings/{session_id}/cancel")
    async def api_device_link_pairing_cancel(
        session_id: str, _body: DeviceLinkEmptyRequest
    ):
        from ..device_link.errors import DeviceLinkError

        if device_link_controller.gateway is None:
            return _device_link_error(
                DeviceLinkError(
                    503, "DEVICE_LINK_AUTHORITY_UNAVAILABLE", "Device Link unavailable"
                )
            )
        try:
            return device_link_controller.gateway.cancel_pairing(session_id)
        except DeviceLinkError as error:
            return _device_link_error(error)

    @app.post("/api/v1/device-link/devices/{device_uuid}/revoke")
    async def api_device_link_revoke(device_uuid: str, _body: DeviceLinkEmptyRequest):
        if device_link_controller.gateway is None:
            return JSONResponse(
                {
                    "schema_version": "device-link-device-action-1",
                    "status": "UNCHANGED",
                    "reason_code": "DEVICE_LINK_AUTHORITY_UNAVAILABLE",
                },
                status_code=503,
            )
        result = device_link_controller.gateway.revoke_device(device_uuid)
        if "error" in result:
            return JSONResponse(
                {
                    "schema_version": "device-link-device-action-1",
                    "status": "UNCHANGED",
                    "reason_code": "DEVICE_NOT_FOUND",
                },
                status_code=result.get("code", 404),
            )
        return {"schema_version": "device-link-device-action-1", **result}

    # --- Catch-all: serve SPA for non-API paths ---
    from fastapi.responses import HTMLResponse

    web_static = HERE.parent / "web_static"

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path == "device/v1" or full_path.startswith("device/v1/"):
            return JSONResponse(
                {
                    "error": {
                        "code": "DEVICE_ROUTE_NOT_FOUND",
                        "message": "Device Link is served only by the independent 8788 listener",
                    }
                },
                status_code=404,
            )
        if full_path.startswith(("api/", "_docs")):
            return JSONResponse({"error": "Not found"}, status_code=404)
        if not web_static.is_dir():
            return JSONResponse({"error": "Frontend not built"}, status_code=404)
        html = web_static / "index.html"
        if html.is_file():
            content = html.read_bytes()
            media_type = "text/html; charset=utf-8"
            if full_path and not full_path.startswith("api/"):
                # Try exact static file match
                static_file = (web_static / full_path).resolve()
                if static_file.is_file() and str(static_file).startswith(
                    str(web_static.resolve())
                ):
                    content = static_file.read_bytes()
                    suffix = static_file.suffix.lower()
                    ext_map = {
                        ".js": "text/javascript",
                        ".css": "text/css",
                        ".json": "application/json",
                        ".png": "image/png",
                        ".svg": "image/svg+xml",
                        ".ico": "image/x-icon",
                    }
                    media_type = ext_map.get(suffix, "application/octet-stream")
            return HTMLResponse(content=content, media_type=media_type)
        return JSONResponse({"error": "Not found"}, status_code=404)

    return app


def run_server(
    host: str = "127.0.0.1",
    port: int = 8787,
    allow_remote: bool = False,
    config: dict | None = None,
):
    """Run the API server."""
    import uvicorn

    if allow_remote or host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("CORE_API_LOOPBACK_ONLY")
    app = create_app(config=config, product_startup_discovery=True)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )
