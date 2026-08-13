"""Local HTTP API server for AgentState Guard GUI."""

from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
_SUPERVISION_SESSION_ID = re.compile(r"session-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


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


def create_app(
    state_db_path: Path | None = None,
    config: dict | None = None,
    assessment_provider=None,
    discovery_service=None,
    product_startup_discovery: bool = False,
):
    """Create a FastAPI application instance.

    Uses lazy imports so core modules don't depend on FastAPI.
    """
    from fastapi import FastAPI, HTTPException
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    from ..commands.doctor import doctor as _doctor
    from ..commands.status import status as _status_ptr
    from ..core.config import Config
    from ..discovery.product import (
        ProductDiscoveryService,
        unavailable_product_snapshot,
    )
    from ..evidence.discovery_adapter import record_discovery_snapshot
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
                    "runtime_count": 0,
                    "agent_count": 0,
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
            and (
                path == "/api/v1/supervision/changes"
                or path.endswith("/apply")
            )
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

    cfg_o = Config(Path(config.get("base_dir", "/workspace")) if config else PROJECT_ROOT)
    cfg_o.ensure_product_config()
    cfg = cfg_o._data
    db_path = state_db_path or cfg_o.state_db()
    db = StateDB(db_path)
    snapshots_dir = cfg_o.snapshot_dir()
    _ai_provider = assessment_provider
    _product_discovery = discovery_service or ProductDiscoveryService()

    def _get_db():
        db.connect()
        return db

    def _refresh_product_discovery() -> dict[str, object]:
        unavailable = False
        try:
            snapshot = _product_discovery.discover()
        except (OSError, RuntimeError, TypeError, ValueError):
            snapshot = unavailable_product_snapshot()
            unavailable = True
        current_db = _get_db()
        try:
            record_discovery_snapshot(
                current_db,
                snapshot,
                recorded_at=datetime.now(UTC),
            )
        finally:
            current_db.close()
        return {
            "schema_version": "product-discovery-1",
            "status": snapshot.status.value,
            "reason_code": (
                "DISCOVERY_REFRESH_UNAVAILABLE"
                if unavailable
                else "DISCOVERY_REFRESHED"
            ),
            "snapshot_id": snapshot.snapshot_id,
            "runtime_count": len(snapshot.runtimes),
            "agent_count": len(snapshot.agents),
        }

    if product_startup_discovery:
        try:
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
            return {"status": "ok", "db_integrity": integrity[0] if integrity else "unknown"}
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
            projector = R4ReadProjectionService(current_db, SnapshotStore(snapshots_dir))
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
        return _r4_projection("agents")

    @app.get("/api/v1/supervision")
    async def api_r4_supervision():
        return _r4_projection("supervision")

    @app.get("/api/v1/recovery")
    async def api_r4_recovery():
        return _r4_projection("recovery")

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
                    "runtime_count": 0,
                    "agent_count": 0,
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
    async def _ai_analyze(body: dict):
        """Run AI environment analysis. Uses sanitized context only."""
        api_key = body.get("api_key", "")
        base_url = body.get("base_url", "")
        model = body.get("model", "deepseek-chat")
        context = body.get("context", {})
        if not base_url:
            return {"status": "error", "summary": "AI provider not configured"}
        cfg = ProviderConfig(base_url=base_url, api_key=api_key, model=model)
        provider = OpenAICompatibleProvider(cfg, timeout=30)
        result = provider.analyze(context)
        return result.to_dict()

    # ---- Device Link Gateway (mounted at /device/v1/) ----

    from fastapi import APIRouter

    from ..device_link.crypto import (
        generate_ecdsa_p256_keypair,
        public_key_to_der,
        random_session_id,
    )
    from ..device_link.gateway import DeviceLinkGateway

    # Generate in-memory ECDSA P-256 keys for the desktop identity
    _dev_priv_pem, _dev_pub_pem = generate_ecdsa_p256_keypair()
    _dev_pub_der = public_key_to_der(_dev_pub_pem)
    _desktop_uuid = random_session_id()

    _gateway = DeviceLinkGateway(
        desktop_uuid=_desktop_uuid,
        desktop_device_pubkey_der=_dev_pub_der,
        desktop_device_privkey_pem=_dev_priv_pem,
    )

    _device_router = APIRouter(prefix="/device/v1")

    def _device_auth_token(request: Request) -> str | None:
        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token or " " in token:
            return None
        return _gateway.validate_token(token)

    def _device_result(result: dict):
        if "error" in result:
            raise HTTPException(status_code=result.get("code", 400), detail=result["error"])
        return result

    @_device_router.post("/pair/start")
    async def _device_pair_start():
        return _device_result(_gateway.pair_start())

    @_device_router.post("/pair/{sid}/connect")
    async def _device_pair_connect(sid: str, body: dict):
        return _device_result(_gateway.pair_first_connection(
            sid, body.get("android_uuid", ""), body.get("nonce", ""),
        ))

    @_device_router.post("/pair/{sid}/sas")
    async def _device_pair_sas(sid: str, body: dict):
        return _device_result(_gateway.pair_start_sas(
            sid, body.get("android_pubkey_der_hex", ""),
        ))

    @_device_router.post("/pair/{sid}/confirm")
    async def _device_pair_confirm(sid: str, body: dict):
        return _device_result(_gateway.pair_confirm(
            sid, body.get("confirm", False),
        ))

    @_device_router.post("/pair/{sid}/complete")
    async def _device_pair_complete(sid: str, body: dict):
        return _device_result(_gateway.pair_complete(
            sid, body.get("android_uuid", ""),
            body.get("android_pubkey_der_hex", ""),
            body.get("display_name", ""),
        ))

    @_device_router.post("/auth/challenge")
    async def _device_auth_challenge(body: dict):
        return _device_result(_gateway.auth_challenge(
            body.get("device_uuid", ""),
        ))

    @_device_router.post("/auth/response")
    async def _device_auth_response(body: dict):
        return _device_result(_gateway.auth_response(
            body.get("device_uuid", ""),
            body.get("challenge_response", ""),
            body.get("nonce", ""),
        ))

    @_device_router.get("/status")
    async def _device_status(request: Request):
        if _device_auth_token(request) is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return {
            "desktop_uuid": _gateway.desktop_uuid,
            "devices_bound": len(_gateway.devices.list()),
            "active_pair_sessions": _gateway.pairing_mgr.active_sessions(),
        }

    @_device_router.get("/checkpoints")
    async def _device_checkpoints(request: Request):
        if _device_auth_token(request) is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return {
            "checkpoints": [],
            "bound_devices": _gateway.devices.list(),
        }

    app.include_router(_device_router)

    # --- Catch-all: serve SPA for non-API paths ---
    from fastapi.responses import HTMLResponse
    web_static = HERE.parent / "web_static"

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
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
                if static_file.is_file() and str(static_file).startswith(str(web_static.resolve())):
                    content = static_file.read_bytes()
                    suffix = static_file.suffix.lower()
                    ext_map = {".js": "text/javascript", ".css": "text/css", ".json": "application/json",
                               ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon"}
                    media_type = ext_map.get(suffix, "application/octet-stream")
            return HTMLResponse(content=content, media_type=media_type)
        return JSONResponse({"error": "Not found"}, status_code=404)

    return app


def run_server(host: str = "127.0.0.1", port: int = 8787, allow_remote: bool = False,
               config: dict | None = None):
    """Run the API server."""
    import uvicorn
    app = create_app(config=config, product_startup_discovery=True)
    if not allow_remote:
        host = "127.0.0.1"
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
