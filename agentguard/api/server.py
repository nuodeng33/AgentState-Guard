"""Local HTTP API server for AgentState Guard GUI."""

from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import Request

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent


def create_app(state_db_path: Path | None = None, config: dict | None = None):
    """Create a FastAPI application instance.

    Uses lazy imports so core modules don't depend on FastAPI.
    """
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    from ..commands.doctor import doctor as _doctor
    from ..commands.status import status as _status_ptr
    from ..core.config import Config
    from ..storage.db import StateDB
    from ..storage.snapshots import SnapshotStore
    from ..transactions.engine import TransactionEngine

    app = FastAPI(title="AgentState Guard API", version="0.9.0-dev")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Serve web_static if built (AFTER API routes are defined below)
    # We'll add a catch-all at the end via the @app.exception_handler

    # Session token
    session_token = secrets.token_hex(32)

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        # Allow static files and public API endpoints
        if (not request.url.path.startswith("/api/")) or request.url.path in ("/api/health", "/api/session"):
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
    cfg = cfg_o._data
    db_path = state_db_path or cfg_o.state_db()
    db = StateDB(db_path)
    snapshots_dir = cfg_o.snapshot_dir()

    def _get_db():
        db.connect()
        return db

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": "0.9.0.dev0"}

    @app.get("/api/session")
    async def get_session():
        return {"token": session_token}

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

    _ai_provider = None
    _ai_config = None

    @app.post("/api/ai/test")
    async def _ai_test(body: dict):
        """Test AI provider connection. API key stays in memory."""
        api_key = body.get("api_key", "")
        base_url = body.get("base_url", "")
        model = body.get("model", "")
        if not base_url:
            return {"ok": False, "error": "Base URL required"}
        cfg = ProviderConfig(base_url=base_url, api_key=api_key, model=model)
        provider = OpenAICompatibleProvider(cfg, timeout=10)
        return provider.test_connection()

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
    app = create_app(config=config)
    if not allow_remote:
        host = "127.0.0.1"
    uvicorn.run(app, host=host, port=port, log_level="info")
