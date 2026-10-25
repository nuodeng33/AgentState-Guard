"""Local HTTP API server for AgentState Guard GUI."""

from __future__ import annotations

import logging
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
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


class DiscoverySourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
    expected_revision: int = Field(ge=0)


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
    protection_domains=None,
):
    """Create a FastAPI application instance.

    Uses lazy imports so core modules don't depend on FastAPI.
    """
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    from ..commands.doctor import doctor as _doctor
    from ..commands.status import status as _status_ptr
    from ..core.config import Config
    from ..discovery.product import (
        ProductDiscoveryService,
        unavailable_product_snapshot,
    )
    from ..discovery.workspace_authority import (
        ResolvedWorkspaceAuthority,
        resolve_host_workspace,
    )
    from ..evidence.discovery_adapter import record_discovery_snapshot
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
        if path.startswith("/api/v1/protection/") or path in {"/api/v1/recovery/previews", "/api/v1/recovery/operations"}:
            return JSONResponse({"reason_code": "PROTECTION_REQUEST_INVALID", "fields": [
                {"location": error["loc"], "type": error["type"]} for error in exc.errors()
            ]}, status_code=422)
        if path == "/api/v1/works" or path.startswith("/api/v1/works/"):
            return JSONResponse({
                "reason_code": "WORK_REQUEST_INVALID",
                "fields": [{"location": error["loc"], "type": error["type"]}
                           for error in exc.errors()],
            }, status_code=422)
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
        allow_methods=["GET", "POST", "PATCH", "PUT"],
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
        Path(config.get("base_dir", "/workspace")) if config else PROJECT_ROOT
    )
    product_config_target = cfg_o.ensure_product_config()
    cfg = cfg_o._data
    db_path = state_db_path or cfg_o.state_db()
    from .work import work_router

    app.include_router(work_router(db_path, agent_enricher=lambda result: enrich_agents(result)))
    from .work_appearance import appearance_router
    app.include_router(appearance_router(db_path))
    snapshots_dir = cfg_o.snapshot_dir()
    from ..recovery.protection import ProtectionRuntime
    from ..recovery.protection_domains import DomainRegistry

    domains = protection_domains or DomainRegistry(db_path.parent)
    protection = ProtectionRuntime(db_path, db_path.parent, domains)
    protection.snapshots = SnapshotStore(snapshots_dir)
    app.state.protection = protection
    _ai_provider = assessment_provider
    _latest_ai_advisory: dict[str, object] = {
        "schema_version": "product-ai-advisory-1",
        "status": "UNAVAILABLE",
        "reason_code": "AI_ADVISORY_NOT_RUN",
        "evidence_refs": [],
    }
    from ..discovery.domains.docker_host import DockerHostProbe
    from ..discovery.managed import ManagedDiscoveryService
    from ..discovery.source_settings import SourceSettings

    source_settings = SourceSettings(db_path)
    _product_discovery = discovery_service or ManagedDiscoveryService(
        native=ProductDiscoveryService(), docker=DockerHostProbe(),
        preferences=source_settings.read, domain_registry=domains,
    )
    # Serialize collection, persistence and configuration as one generation.
    # Reuse the manager lock so configure/refresh cannot acquire locks inversely.
    discovery_lock = (
        _product_discovery.lock if isinstance(_product_discovery, ManagedDiscoveryService)
        else threading.RLock()
    )

    def _get_db():
        current = StateDB(db_path)
        current.connect()
        return current

    from ..discovery.catalog import (
        DiscoveryCatalog,
        SessionPageCache,
        include_historical_sources,
        record_sources,
    )
    from ..discovery.source_identity import resolve_source
    from .discovery import discovery_router

    session_pages = SessionPageCache()

    @contextmanager
    def catalog_context():
        database = _get_db()
        try:
            yield DiscoveryCatalog(database, domains, _product_discovery, session_pages)
        finally:
            database.close()

    def canonical_source(source_id):
        database = _get_db()
        try:
            return resolve_source(database._conn, source_id)
        finally:
            database.close()

    def registered_paths():
        database = _get_db()
        try:
            return tuple(row[0] for row in database._conn.execute(
                "SELECT path FROM discovery_applications WHERE source_id='local' AND registered=1 LIMIT 256"))
        finally:
            database.close()

    def registered_programs():
        database = _get_db()
        try:
            values = {}
            for source_id, path in database._conn.execute("SELECT source_id,path FROM discovery_applications WHERE registered=1"):
                values.setdefault(source_id, set()).add(path)
            return values
        finally:
            database.close()

    def enrich_agents(result):
        with catalog_context() as catalog:
            return catalog.enrich_agents(result)

    domains.source_resolver = canonical_source
    if isinstance(_product_discovery, ManagedDiscoveryService):
        _product_discovery.catalog_context = catalog_context
        _product_discovery.native._registered_paths = registered_paths
        _product_discovery.registered_programs = registered_programs
    app.include_router(discovery_router(catalog_context, discovery_lock))

    def _refresh_product_discovery() -> dict[str, object]:
        with discovery_lock:
            return _refresh_product_discovery_locked()

    def _refresh_product_discovery_locked() -> dict[str, object]:
        unavailable = False
        authority_report = None
        if isinstance(_product_discovery, ManagedDiscoveryService):
            with catalog_context() as catalog:
                catalog.reconcile()
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
        change_result = None
        current_db = StateDB(db_path)
        current_db.connect()
        try:
            if isinstance(_product_discovery, ManagedDiscoveryService) and authority_report is not None:
                catalog = DiscoveryCatalog(current_db, domains, _product_discovery)
                catalog.observe(authority_report)
                record_sources(current_db, _product_discovery.sources())
                entry_key = frozenset((item.get("source_id"), item.get("agent_id"))
                    for item in authority_report.application_observations if item.get("agent_id"))
                new_entries = entry_key - getattr(_product_discovery, "inventory_entries", frozenset())
                if new_entries or time.monotonic() - getattr(_product_discovery, "inventory_at", -60) >= 30:
                    _product_discovery.inventory_coverage = catalog.refresh_inventory()
                    _product_discovery.inventory_at = time.monotonic()
                    _product_discovery.inventory_entries = entry_key
            publication_key = (snapshot.status.value,
                tuple(sorted((item.agent_id, item.agent_type, item.domain_id, item.lifecycle.value, item.workspace_ids) for item in snapshot.agents)),
                tuple(sorted((item.runtime_id, item.status.value) for item in snapshot.runtimes)),
                tuple(sorted((item["source_id"], item["state"], item.get("reason_code")) for item in _product_discovery.sources()["items"]))
                    if isinstance(_product_discovery, ManagedDiscoveryService) else ())
            publish = (not isinstance(_product_discovery, ManagedDiscoveryService) or
                getattr(_product_discovery, "publication_key", None) != publication_key or
                time.monotonic() - getattr(_product_discovery, "publication_at", -60) >= 30)
            receipts = record_discovery_snapshot(current_db, snapshot, recorded_at=datetime.now(UTC)) if publish else []
            if publish and isinstance(_product_discovery, ManagedDiscoveryService):
                _product_discovery.publication_key = publication_key
                _product_discovery.publication_at = time.monotonic()
                _product_discovery.published_snapshot_id = snapshot.snapshot_id
            if supports_workspace_authority and publish:
                resolved_scope = (
                    resolve_host_workspace(authority_report)
                    if authority_report is not None
                    else ResolvedWorkspaceAuthority(
                        status="UNAVAILABLE",
                        reason_code="WORKSPACE_SCOPE_DISCOVERY_UNAVAILABLE",
                    )
                )
                scope_service = WorkspaceScopeService(current_db)
                scope_result = scope_service.bind(
                    resolved_scope,
                    recorded_at=datetime.now(UTC),
                    discovery_snapshot_id=snapshot.snapshot_id,
                )
                if scope_result.status == "BOUND":
                    active_scope = scope_service.active_scope()
                    if active_scope is not None:
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
        finally:
            current_db.close()
        affected_views = ["runtime", "agents", "supervision"]
        evidence_refs = [receipt.event_id for receipt in receipts]
        if scope_result is not None:
            affected_views.extend(("changes", "recovery"))
            if scope_result.ledger_event_id is not None:
                evidence_refs.append(scope_result.ledger_event_id)
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
            "snapshot_id": getattr(_product_discovery, "published_snapshot_id", snapshot.snapshot_id),
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

    from .protection import protection_router

    app.include_router(protection_router(protection))
    from ..execution.runtime import ExecutionRuntime
    from .execution import delivery_router, execution_router

    execution = ExecutionRuntime(db_path, domains, catalog_context, protection)
    app.state.execution = execution
    app.include_router(execution_router(execution))
    app.include_router(delivery_router(execution))
    protection.discovery_refresh = _refresh_product_discovery
    protection.background_discovery = product_startup_discovery

    @app.on_event("startup")
    async def startup_protection():
        # Initialize WAL and schema before concurrent workers open connections.
        # Keep the existing safe degraded HTTP diagnostics on storage failure.
        database = StateDB(db_path)
        try:
            database.connect()
        except (OSError, RuntimeError, sqlite3.DatabaseError):
            logging.getLogger(__name__).warning("CORE_DATABASE_INITIALIZATION_UNAVAILABLE")
        finally:
            database.close()
        execution.start()
        protection.start()

    @app.on_event("shutdown")
    async def shutdown_protection():
        try:
            await run_in_threadpool(execution.close)
        finally:
            await run_in_threadpool(protection.close)

    if product_startup_discovery:
        try:
            if isinstance(_product_discovery, ManagedDiscoveryService):
                with catalog_context() as catalog:
                    catalog.reconcile()
            with protection.service() as service:
                service.restore_disconnections()
            _refresh_product_discovery()
        except (OSError, RuntimeError, sqlite3.DatabaseError, TypeError, ValueError):
            pass

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": "0.9.0.dev0"}

    @app.get("/api/session")
    async def get_session():
        return {"token": session_token}

    @app.get("/api/readiness")
    async def readiness():
        current_db = None
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
            if current_db is not None:
                current_db.close()
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

    def _r4_projection(view: str) -> dict[str, Any]:
        try:
            current_db = _get_db()
        except (OSError, sqlite3.DatabaseError, RuntimeError):
            return R4ReadProjectionService.unavailable(view)
        try:
            projector = R4ReadProjectionService(
                current_db, SnapshotStore(snapshots_dir)
            )
            return getattr(projector, view)()
        except (OSError, sqlite3.DatabaseError, RuntimeError, TypeError, ValueError):
            return R4ReadProjectionService.unavailable(view)
        finally:
            current_db.close()

    @app.get("/api/v1/runtime")
    async def api_r4_runtime():
        return _r4_projection("runtime")

    @app.get("/api/v1/agents")
    async def api_r4_agents():
        result = _r4_projection("agents")
        return enrich_agents(result) if isinstance(_product_discovery, ManagedDiscoveryService) else result

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
    async def api_r4_changes():
        return _r4_projection("changes")

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
            result = await run_in_threadpool(_refresh_product_discovery)
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

    @app.get("/api/v1/discovery/sources")
    def api_discovery_sources():
        if not isinstance(_product_discovery, ManagedDiscoveryService):
            return {"schema_version": "discovery-sources-1", "items": [],
                    "reason_code": "SOURCE_MANAGEMENT_UNSUPPORTED"}
        result = _product_discovery.sources()
        with catalog_context() as catalog:
            return include_historical_sources(catalog.db, result)

    @app.post("/api/v1/discovery/sources/{source_id}/configure")
    def api_configure_discovery_source(source_id: str, body: DiscoverySourceRequest):
        if not isinstance(_product_discovery, ManagedDiscoveryService):
            return JSONResponse({"reason_code": "SOURCE_MANAGEMENT_UNSUPPORTED"}, status_code=409)
        with _product_discovery.lock:
            source_id = canonical_source(source_id)
            source = next((item for item in _product_discovery.sources()["items"]
                           if item["source_id"] == source_id), None)
            if source is None:
                return JSONResponse({"reason_code": "DISCOVERY_SOURCE_NOT_FOUND"}, status_code=404)
            if not source["configurable"]:
                return JSONResponse({"reason_code": "DISCOVERY_SOURCE_NOT_CONFIGURABLE"}, status_code=409)
            try:
                setting = source_settings.set_enabled(source_id, body.enabled, body.expected_revision)
            except ValueError:
                return JSONResponse({"reason_code": "DISCOVERY_SOURCE_REVISION_CONFLICT"}, status_code=409)
            except sqlite3.DatabaseError:
                return JSONResponse({"reason_code": "DISCOVERY_SOURCE_SAVE_FAILED"}, status_code=503)
            # The setting remains durable even when its subsequent observation fails.
            try:
                refresh = _refresh_product_discovery()
            except (OSError, RuntimeError, sqlite3.DatabaseError, TypeError, ValueError):
                refresh = {"status": "UNAVAILABLE", "reason_code": "DISCOVERY_REFRESH_UNAVAILABLE"}
            return {**setting, "refresh": refresh}

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

    from ..ai.provider import AIProviderError, OpenAICompatibleProvider, ProviderConfig

    def _candidate_ai_provider(body: dict) -> OpenAICompatibleProvider:
        values = {name: body.get(name, "") for name in ("base_url", "api_key", "model")}
        if not all(isinstance(value, str) for value in values.values()):
            raise AIProviderError("AI_PROVIDER_CONFIG_INVALID")
        values["base_url"] = values["base_url"].strip()
        values["model"] = values["model"].strip()
        return OpenAICompatibleProvider(ProviderConfig(**values), timeout=10)

    @app.post("/api/ai/test")
    async def _ai_test(body: dict):
        """Validate connection and advertised model; no generation is performed."""
        nonlocal _ai_provider
        try:
            provider = _candidate_ai_provider(body)
            result = await run_in_threadpool(provider.test_connection)
        except AIProviderError as error:
            return {"ok": False, "error": error.code}
        if result.get("ok") is True:
            _ai_provider = provider
        return result

    @app.post("/api/ai/models")
    async def _ai_models(body: dict):
        """List models or report a bounded, explicit provider failure."""
        try:
            provider = _candidate_ai_provider(body)
            models = await run_in_threadpool(provider.list_models)
        except AIProviderError as error:
            return {"models": [], "error": error.code}
        return {"models": [model.to_dict() for model in models],
                **({"error": "AI_MODELS_EMPTY"} if not models else {})}

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
        provider = _ai_provider
        if provider is None or not callable(getattr(provider, "analyze", None)):
            return JSONResponse(unavailable, status_code=503)
        current_db = None
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
            if current_db is not None:
                current_db.close()
        try:
            result = await run_in_threadpool(provider.analyze, context)
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
        provider_name = sanitize_text(str(getattr(provider, "provider_name", "unknown")))[:128]
        model = sanitize_text(str(getattr(provider, "model", "unknown")))[:128]
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
