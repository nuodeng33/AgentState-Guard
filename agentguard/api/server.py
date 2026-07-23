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
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn

    from ..storage.db import StateDB
    from ..storage.snapshots import SnapshotStore
    from ..core.config import Config
    from ..commands.doctor import doctor as _doctor
    from ..commands.status import status as _status_ptr
    from ..transactions.engine import TransactionEngine
    from ..storage.gc import plan_gc
    from ..storage.blob import BlobStore

    app = FastAPI(title="AgentState Guard API", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Session token
    session_token = secrets.token_hex(32)

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if request.url.path in ("/api/health", "/api/session"):
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
        return {"status": "ok", "version": "1.0.0"}

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

    return app


def run_server(host: str = "127.0.0.1", port: int = 8787, allow_remote: bool = False,
               config: Optional[dict] = None):
    """Run the API server."""
    import uvicorn
    app = create_app(config=config)
    if not allow_remote:
        host = "127.0.0.1"
    uvicorn.run(app, host=host, port=port, log_level="info")
