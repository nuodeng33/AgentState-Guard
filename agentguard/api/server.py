"""Local HTTP API server for AgentState Guard GUI."""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent


def create_app(state_db_path: Optional[Path] = None, config: Optional[dict] = None):
    """Create a FastAPI application instance.

    Uses lazy imports so core modules don't depend on FastAPI.
    """
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse
    from fastapi.responses import FileResponse
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    import uvicorn

    from ..storage.db import StateDB
    from ..storage.snapshots import SnapshotStore
    from ..core.config import Config
    from ..commands.doctor import doctor as _doctor
    from ..commands.status import status as _status_ptr
    from ..transactions.engine import TransactionEngine
    from ..storage.gc import plan_gc
    from ..storage.blob import BlobStore

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

    @app.get("/api/handoff")
    async def api_handoff():
        db = _get_db()
        try:
            from ..commands.handoff import cmd_handoff
            result = cmd_handoff(Path("/tmp/agentguard-handoff"), db)
            return result
        finally:
            db.close()

    # --- Catch-all: serve SPA for non-API paths ---
    from fastapi.responses import HTMLResponse
    web_static = HERE.parent / "web_static"

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("_docs"):
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
               config: Optional[dict] = None):
    """Run the API server."""
    import uvicorn
    app = create_app(config=config)
    if not allow_remote:
        host = "127.0.0.1"
    uvicorn.run(app, host=host, port=port, log_level="info")
