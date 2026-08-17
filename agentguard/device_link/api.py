"""Independent, allowlisted Device Link HTTP application for port 8788."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agentguard.api.r4_projection import R4ReadProjectionService
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore
from agentguard.supervision.service import SupervisionActionError, SupervisionService

from .errors import DeviceLinkError

_SESSION_ID = re.compile(r"^session-[0-9a-f-]{36}$")
_BODY_LIMIT = 1_048_576


class BodyLimitMiddleware:
    def __init__(self, app, max_bytes: int = _BODY_LIMIT) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        chunks = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.max_bytes:
                body = b'{"error":{"code":"DEVICE_PAYLOAD_TOO_LARGE","message":"Request body exceeds limit"}}'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("ascii")),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {
                "type": "http.request",
                "body": b"".join(chunks),
                "more_body": False,
            }

        await self.app(scope, replay, send)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PairConnect(_Strict):
    ticket: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    android_uuid: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    nonce: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class PairSas(_Strict):
    android_pubkey_der_hex: str = Field(
        min_length=2, max_length=1024, pattern=r"^[0-9a-f]+$"
    )


class PairConfirm(_Strict):
    confirm: bool


class PairComplete(_Strict):
    android_uuid: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    android_pubkey_der_hex: str = Field(
        min_length=2, max_length=1024, pattern=r"^[0-9a-f]+$"
    )
    display_name: str = Field(min_length=1, max_length=128)
    protocol_version: int = Field(ge=1, le=1)


class AuthChallenge(_Strict):
    device_uuid: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    protocol_version: int = Field(ge=1, le=1)


class AuthResponse(_Strict):
    device_uuid: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    challenge_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    signature: str = Field(min_length=2, max_length=1024, pattern=r"^[0-9a-f]+$")
    protocol_version: int = Field(ge=1, le=1)


class ApprovalIntent(_Strict):
    action_ref: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class EmptyRequest(_Strict):
    pass


def create_device_link_app(
    *,
    gateway,
    state_db_path: Path,
    snapshots_dir: Path,
    advisory_getter=None,
) -> FastAPI:
    app = FastAPI(
        title="AgentState Guard Device Link",
        version="1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(BodyLimitMiddleware)

    def error(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": code, "message": message}}, status_code=status
        )

    @app.exception_handler(DeviceLinkError)
    async def device_error(_request: Request, exc: DeviceLinkError):
        return error(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError):
        return error(400, "DEVICE_INVALID_REQUEST", "Invalid Device Link request")

    def database() -> StateDB:
        value = StateDB(state_db_path)
        value.connect()
        return value

    def projector(view: str):
        db = database()
        try:
            return getattr(
                R4ReadProjectionService(db, SnapshotStore(snapshots_dir)), view
            )()
        finally:
            db.close()

    def identity(authorization: str = Header(default="", alias="Authorization")) -> str:
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token:
            raise DeviceLinkError(
                401, "DEVICE_TOKEN_INVALID", "Session token is required"
            )
        device_uuid = gateway.validate_token(token)
        if not device_uuid:
            raise DeviceLinkError(
                401, "DEVICE_TOKEN_INVALID", "Session token is invalid or expired"
            )
        return device_uuid

    def pair_token(value: str = Header(default="", alias="X-Pairing-Token")) -> str:
        return value

    @app.post("/device/v1/pair/{session_id}/connect")
    async def pair_connect(session_id: str, body: PairConnect):
        return gateway.accept_pairing_ticket(
            session_id, body.ticket, body.android_uuid, body.nonce
        )

    @app.post("/device/v1/pair/{session_id}/sas")
    async def pair_sas(
        session_id: str, body: PairSas, token: str = Depends(pair_token)
    ):
        return gateway.pair_start_sas_scoped(
            session_id, token, body.android_pubkey_der_hex
        )

    @app.post("/device/v1/pair/{session_id}/confirm")
    async def pair_confirm(
        session_id: str, body: PairConfirm, token: str = Depends(pair_token)
    ):
        return gateway.pair_android_confirm(session_id, token, body.confirm)

    @app.post("/device/v1/pair/{session_id}/complete")
    async def pair_complete(
        session_id: str, body: PairComplete, token: str = Depends(pair_token)
    ):
        return gateway.pair_complete_scoped(
            session_id,
            token,
            body.android_uuid,
            body.android_pubkey_der_hex,
            body.display_name,
        )

    @app.post("/device/v1/auth/challenge")
    async def auth_challenge(body: AuthChallenge):
        return gateway.auth_challenge_scoped(body.device_uuid, body.protocol_version)

    @app.post("/device/v1/auth/response")
    async def auth_response(body: AuthResponse):
        return gateway.auth_response_scoped(
            body.device_uuid, body.challenge_id, body.signature, body.protocol_version
        )

    @app.post("/device/v1/self-unpair")
    async def self_unpair(
        _body: EmptyRequest,
        device_uuid: str = Depends(identity),
    ):
        result = gateway.revoke_device(device_uuid)
        if result.get("status") != "revoked" and result.get("code") != 404:
            raise DeviceLinkError(
                409, "DEVICE_SELF_UNPAIR_FAILED", "Device self-unpair failed"
            )
        return {
            "schema_version": "device-link-self-unpair-1",
            "action": "SELF_UNPAIR",
            "status": "UNPAIRED",
            "reason_code": "DEVICE_SELF_UNPAIRED",
        }

    @app.get("/device/v1/status")
    async def status(_device_uuid: str = Depends(identity)):
        return {
            "schema_version": "device-link-status-1",
            **gateway.get_status(),
            "permissions": ["read", "approve_once", "reject", "self_unpair"],
        }

    @app.get("/device/v1/environment")
    async def environment(_device_uuid: str = Depends(identity)):
        return projector("runtime")

    @app.get("/device/v1/agents")
    async def agents(_device_uuid: str = Depends(identity)):
        return projector("agents")

    @app.get("/device/v1/supervision")
    async def supervision(_device_uuid: str = Depends(identity)):
        return projector("supervision")

    @app.get("/device/v1/changes")
    async def changes(_device_uuid: str = Depends(identity)):
        return projector("changes")

    @app.get("/device/v1/checkpoints")
    @app.get("/device/v1/recovery")
    async def recovery(_device_uuid: str = Depends(identity)):
        return projector("recovery")

    @app.get("/device/v1/evidence/{event_id}")
    async def evidence(event_id: str, _device_uuid: str = Depends(identity)):
        db = database()
        try:
            result = R4ReadProjectionService(db, SnapshotStore(snapshots_dir)).evidence(
                event_id
            )
            return JSONResponse(
                result, status_code=404 if result["status"] == "NOT_FOUND" else 200
            )
        finally:
            db.close()

    @app.get("/device/v1/ai/advisory")
    async def advisory(_device_uuid: str = Depends(identity)):
        if advisory_getter is None:
            return {
                "schema_version": "product-ai-advisory-1",
                "status": "UNAVAILABLE",
                "reason_code": "AI_ADVISORY_NOT_RUN",
                "evidence_refs": [],
            }
        return advisory_getter()

    def supervision_action(session_id: str, body: ApprovalIntent, action: str):
        if not _SESSION_ID.fullmatch(session_id):
            raise DeviceLinkError(
                400, "SUPERVISION_ACTION_REQUEST_INVALID", "Invalid session"
            )
        db = database()
        try:
            service = SupervisionService(db)
            result = (
                service.approve_once(session_id, body.action_ref)
                if action == "APPROVE_ONCE"
                else service.reject_once(session_id, body.action_ref)
            )
            return result.to_dict()
        except SupervisionActionError as exc:
            status = 404 if exc.reason_code == "SUPERVISION_SESSION_NOT_FOUND" else 409
            raise DeviceLinkError(
                status, exc.reason_code, "Supervision action rejected"
            ) from exc
        finally:
            db.close()

    @app.post("/device/v1/supervision/{session_id}/approve-once")
    async def approve_once(
        session_id: str, body: ApprovalIntent, _device_uuid: str = Depends(identity)
    ):
        return supervision_action(session_id, body, "APPROVE_ONCE")

    @app.post("/device/v1/supervision/{session_id}/reject")
    async def reject(
        session_id: str, body: ApprovalIntent, _device_uuid: str = Depends(identity)
    ):
        return supervision_action(session_id, body, "REJECT")

    return app
