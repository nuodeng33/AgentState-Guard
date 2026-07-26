"""Local HTTP API server for AgentState Guard GUI."""

from __future__ import annotations

import ipaddress
import json
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from ..device_link.errors import DeviceLinkError, invalid_request
from ..device_link.models import (
    AuthChallengeRequest,
    AuthResponseRequest,
    EmptyRequest,
    PairCompleteRequest,
    PairConfirmRequest,
    PairConnectRequest,
    PairSasRequest,
    validate_session_id,
)

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
DEVICE_LINK_BODY_LIMIT = 1024 * 1024


def _is_loopback_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def _is_device_link_path(path: str) -> bool:
    return path == "/device/v1" or path.startswith("/device/v1/")


class DeviceLinkBoundaryMiddleware:
    """Enforce the Device Link peer, origin, and streaming body boundary."""

    def __init__(self, app, max_body_bytes: int = DEVICE_LINK_BODY_LIMIT):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or not _is_device_link_path(scope.get("path", ""))
        ):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        peer = client[0] if client else ""
        if not _is_loopback_host(peer):
            await self._reject(
                send,
                403,
                "DEVICE_LOOPBACK_REQUIRED",
                "Device Link currently accepts loopback peers only",
            )
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        origin = headers.get("origin")
        if origin:
            try:
                origin_host = urlsplit(origin).hostname or ""
            except (UnicodeError, ValueError):
                origin_host = ""
            if not _is_loopback_host(origin_host):
                await self._reject(
                    send,
                    403,
                    "DEVICE_ORIGIN_FORBIDDEN",
                    "Device Link does not accept remote browser origins",
                )
                return

        chunks = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                await self._reject(
                    send,
                    400,
                    "DEVICE_INVALID_REQUEST",
                    "Client disconnected while sending the request",
                )
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.max_body_bytes:
                await self._reject(
                    send,
                    413,
                    "DEVICE_PAYLOAD_TOO_LARGE",
                    "Device Link request body exceeds 1 MiB",
                )
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        delivered = False
        body = b"".join(chunks)

        async def replay_receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(send, status: int, code: str, message: str):
        body = json.dumps(
            {"error": {"code": code, "message": message}},
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def create_app(
    state_db_path: Path | None = None,
    config: dict | None = None,
    *,
    device_link_gateway=None,
):
    """Create a FastAPI application instance.

    Uses lazy imports so core modules don't depend on FastAPI.
    """
    from fastapi import Depends, FastAPI, Request
    from fastapi.exception_handlers import request_validation_exception_handler
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    from ..commands.doctor import doctor as _doctor
    from ..commands.status import status as _status_ptr
    from ..core.config import Config
    from ..storage.db import StateDB
    from ..storage.snapshots import SnapshotStore
    from ..transactions.engine import TransactionEngine

    app = FastAPI(title="AgentState Guard API", version="0.9.0-dev")

    def _device_error(status: int, code: str, message: str):
        return JSONResponse(
            {
                "error": {
                    "code": code,
                    "message": message,
                }
            },
            status_code=status,
        )

    @app.exception_handler(DeviceLinkError)
    async def _device_link_error_handler(
        request: Request,
        exc: DeviceLinkError,
    ):
        return _device_error(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ):
        if request.url.path.startswith("/device/v1/"):
            return _device_error(
                400,
                "DEVICE_INVALID_REQUEST",
                "Invalid Device Link request",
            )
        return await request_validation_exception_handler(request, exc)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(DeviceLinkBoundaryMiddleware)

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

    from fastapi import APIRouter, Header

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

    _gateway = device_link_gateway or DeviceLinkGateway(
        desktop_uuid=_desktop_uuid,
        desktop_device_pubkey_der=_dev_pub_der,
        desktop_device_privkey_pem=_dev_priv_pem,
    )
    app.state.device_link_gateway = _gateway

    _device_router = APIRouter(prefix="/device/v1")

    @_device_router.post("/pair/start")
    async def _device_pair_start(body: EmptyRequest | None = None):
        return _gateway.pair_start()

    def _session_id(value: str) -> str:
        try:
            return validate_session_id(value)
        except ValueError:
            raise invalid_request("Invalid pairing session ID")

    @_device_router.get("/pair/{sid}")
    async def _device_pair_poll(sid: str):
        return _gateway.pair_poll(_session_id(sid))

    @_device_router.post("/pair/{sid}/connect")
    async def _device_pair_connect(sid: str, body: PairConnectRequest):
        return _gateway.pair_first_connection(
            _session_id(sid),
            body.android_uuid,
            body.nonce,
        )

    @_device_router.post("/pair/{sid}/sas")
    async def _device_pair_sas(sid: str, body: PairSasRequest):
        return _gateway.pair_start_sas(
            _session_id(sid),
            body.android_pubkey_der_hex,
        )

    @_device_router.post("/pair/{sid}/confirm")
    async def _device_pair_confirm(sid: str, body: PairConfirmRequest):
        return _gateway.pair_confirm(_session_id(sid), body.confirm)

    @_device_router.post("/pair/{sid}/complete")
    async def _device_pair_complete(sid: str, body: PairCompleteRequest):
        return _gateway.pair_complete(
            _session_id(sid),
            body.android_uuid,
            body.android_pubkey_der_hex,
            body.display_name,
            body.protocol_version,
        )

    @_device_router.post("/auth/challenge")
    async def _device_auth_challenge(body: AuthChallengeRequest):
        return _gateway.auth_challenge(
            body.device_uuid,
            body.protocol_version,
        )

    @_device_router.post("/auth/response")
    async def _device_auth_response(body: AuthResponseRequest):
        return _gateway.auth_response(
            body.device_uuid,
            body.challenge_id,
            body.signature,
            body.protocol_version,
        )

    async def _device_identity(
        x_session_token: str = Header(default="", alias="X-Session-Token"),
    ) -> str:
        device_uuid = _gateway.validate_token(x_session_token)
        if not device_uuid:
            raise DeviceLinkError(
                401,
                "DEVICE_TOKEN_INVALID",
                "Device session token is missing, invalid, or expired",
            )
        return device_uuid

    @_device_router.get("/status")
    async def _device_status(
        _device_uuid: str = Depends(_device_identity),
    ):
        return {
            "desktop_uuid": _gateway.desktop_uuid,
            "devices_bound": len(_gateway.devices.list()),
            "active_pair_sessions": _gateway.pairing_mgr.active_sessions(),
        }

    @_device_router.get("/checkpoints")
    async def _device_checkpoints(
        _device_uuid: str = Depends(_device_identity),
    ):
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
        if full_path == "device/v1" or full_path.startswith("device/v1/"):
            return _device_error(
                404,
                "DEVICE_ROUTE_NOT_FOUND",
                "Device Link route not found",
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
    if allow_remote or not _is_loopback_host(host):
        raise ValueError(
            "Device Link server is loopback-only until secure transport exists"
        )
    import uvicorn
    app = create_app(config=config)
    uvicorn.run(app, host=host, port=port, log_level="info")
