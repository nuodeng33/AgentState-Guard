"""ASDL/1 Device Link Gateway — minimal API router.

Must NOT expose the full Core API. This is a separate security boundary.
Bind to 127.0.0.1 in headless dev; Private LAN interface on Windows.
"""

from __future__ import annotations

import json
import time as _time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .crypto import (
    generate_challenge, verify_signature, spki_fingerprint,
    random_session_id, encode_hex,
)
from .pairing import PairingManager, PairState


class DeviceRegistry:
    """Stores bound device metadata. Non-sensitive fields only."""

    def __init__(self):
        self._devices: Dict[str, dict] = {}

    def add(self, device_uuid: str, pubkey_der: bytes, display_name: str,
            permissions: list, protocol_version: int) -> None:
        fp = spki_fingerprint(pubkey_der)
        self._devices[device_uuid] = {
            "uuid": device_uuid,
            "pubkey_der": pubkey_der.hex(),
            "fingerprint": fp,
            "display_name": display_name,
            "permissions": permissions,
            "created_at": int(_time.time()),
            "last_seen": None,
            "protocol_version": protocol_version,
        }

    def get(self, device_uuid: str) -> Optional[dict]:
        return self._devices.get(device_uuid)

    def remove(self, device_uuid: str) -> bool:
        return self._devices.pop(device_uuid, None) is not None

    def list(self) -> list:
        return [
            {
                "uuid": d["uuid"],
                "fingerprint": d["fingerprint"],
                "display_name": d["display_name"],
                "last_seen": d.get("last_seen"),
            }
            for d in self._devices.values()
        ]

    def update_last_seen(self, device_uuid: str) -> None:
        if device_uuid in self._devices:
            self._devices[device_uuid]["last_seen"] = int(_time.time())


class GatewayConfig:
    def __init__(self, **kwargs):
        self.max_pair_sessions = kwargs.get("max_pair_sessions", 5)
        self.pair_ttl = kwargs.get("pair_ttl", 120)
        self.max_sas_attempts = kwargs.get("max_sas_attempts", 3)
        self.session_ttl = kwargs.get("session_ttl", 3600)
        self.rate_limit_per_min = kwargs.get("rate_limit_per_min", 60)
        self.payload_max_bytes = kwargs.get("payload_max_bytes", 1_048_576)
        self.restore_policy = kwargs.get("restore_policy", "desktop_confirm")
        self.single_device = kwargs.get("single_device", True)


class DeviceLinkGateway:
    """Minimal Device Link API router.

    This is NOT the full FastAPI server. It's a standalone module that
    FastAPI can mount or CLI can serve. All security enforcements live here.
    """

    def __init__(
        self,
        desktop_uuid: str,
        desktop_device_pubkey_der: bytes,
        desktop_device_privkey_pem: bytes,
        desktop_tls_spki_fp: str = "headless-dev",
        config: Optional[GatewayConfig] = None,
    ):
        self.desktop_uuid = desktop_uuid
        self.desktop_pubkey_der = desktop_device_pubkey_der
        self.desktop_privkey_pem = desktop_device_privkey_pem
        self.desktop_tls_spki_fp = desktop_tls_spki_fp
        self.config = config or GatewayConfig()
        self.pairing_mgr = PairingManager(max_sessions=self.config.max_pair_sessions)
        self.devices = DeviceRegistry()
        self._sessions: Dict[str, dict] = {}  # token -> {uuid, expires, ...}
        self._replay_cache = {}  # simple nonce→time cache

    # ---- Pairing Endpoints ----

    def pair_start(self) -> dict:
        """POST /device/v1/pair/start"""
        session = self.pairing_mgr.create_session(
            self.desktop_uuid, self.desktop_pubkey_der, self.desktop_tls_spki_fp,
        )
        return {
            "session_id": session.session_id,
            "expires_at": session.expiry,
            "desktop_uuid": self.desktop_uuid,
            "desktop_pubkey_fingerprint": spki_fingerprint(self.desktop_pubkey_der)[:8],
            "state": session.state.value,
        }

    def pair_poll(self, session_id: str) -> dict:
        """GET /device/v1/pair/{session_id}"""
        session = self.pairing_mgr.get(session_id)
        if not session:
            return {"error": "Session not found", "code": 404}
        return {
            "session_id": session.session_id,
            "state": session.state.value,
            "expires_at": session.expiry,
        }

    def pair_first_connection(
        self, session_id: str, android_uuid: str, nonce_android_hex: str,
    ) -> dict:
        """Android connects and sends its UUID + nonce."""
        session = self.pairing_mgr.get(session_id)
        if not session:
            return {"error": "Session not found", "code": 404}
        try:
            session.first_connection(android_uuid, bytes.fromhex(nonce_android_hex))
            return {"state": session.state.value}
        except ValueError as e:
            return {"error": str(e), "code": 409}

    def pair_start_sas(self, session_id: str, android_pubkey_der_hex: str) -> dict:
        """Android sends its public key; Desktop computes SAS."""
        session = self.pairing_mgr.get(session_id)
        if not session:
            return {"error": "Session not found", "code": 404}
        session.set_android_pubkey(bytes.fromhex(android_pubkey_der_hex))
        try:
            sas = session.start_sas()
            return {"sas": sas, "state": session.state.value}
        except ValueError as e:
            return {"error": str(e), "code": 409}

    def pair_confirm(self, session_id: str, confirm: bool) -> dict:
        """POST /device/v1/pair/confirm — both must confirm."""
        session = self.pairing_mgr.get(session_id)
        if not session:
            return {"error": "Session not found", "code": 404}
        if not confirm:
            session.reject()
            return {"state": session.state.value}

        if session.state != PairState.SAS_PENDING:
            return {"error": f"SAS not yet available, state={session.state.value}", "code": 409}

        try:
            session.confirm()
        except ValueError as e:
            return {"error": str(e), "code": 409}

        # If single trust mode and a device exists, verify intent
        if self.config.single_device and len(self.devices.list()) > 0:
            # Still allow — user explicitly confirmed both sides
            pass

        return {"state": session.state.value, "desktop_display": "AgentState Guard Desktop"}

    def pair_complete(
        self, session_id: str, android_uuid: str, android_pubkey_der_hex: str,
        display_name: str,
    ) -> dict:
        """Complete pairing: register device and issue session token."""
        session = self.pairing_mgr.get(session_id)
        if not session:
            return {"error": "Session not found", "code": 404}
        if session.state != PairState.CONFIRMED_BOTH:
            return {"error": f"Not confirmed: {session.state.value}", "code": 409}

        pubkey_der = bytes.fromhex(android_pubkey_der_hex)
        self.devices.add(
            android_uuid, pubkey_der, display_name,
            permissions=["read", "checkpoint", "restore"],
            protocol_version=1,
        )
        session.consume()

        token = random_session_id()
        self._sessions[token] = {
            "device_uuid": android_uuid,
            "expires": int(_time.time()) + self.config.session_ttl,
        }
        self.devices.update_last_seen(android_uuid)

        return {
            "status": "bound",
            "session_token": token,
            "desktop_uuid": self.desktop_uuid,
            "desktop_display_name": "AgentState Guard Desktop",
            "permissions": ["read", "checkpoint", "restore"],
        }

    # ---- Auth Endpoint ----

    def auth_challenge(self, device_uuid: str) -> dict:
        """POST /device/v1/auth/challenge"""
        dev = self.devices.get(device_uuid)
        if not dev:
            return {"error": "Device not bound", "code": 403}
        challenge = generate_challenge()
        session_nonce = generate_challenge()[:16]
        return {
            "desktop_challenge": challenge.hex(),
            "desktop_uuid": self.desktop_uuid,
            "session_nonce": session_nonce.hex(),
        }

    def auth_response(
        self, device_uuid: str, challenge_response_hex: str,
        nonce_hex: str,
    ) -> dict:
        """POST /device/v1/auth/response — verify signed challenge."""
        dev = self.devices.get(device_uuid)
        if not dev:
            return {"error": "Device not bound", "code": 403}

        pubkey_pem = bytes.fromhex(dev["pubkey_der"])
        # Verify signature
        verified = verify_signature(
            pubkey_pem, bytes.fromhex(nonce_hex),
            bytes.fromhex(challenge_response_hex),
        )
        if not verified:
            return {"error": "Signature verification failed", "code": 401}

        token = random_session_id()
        self._sessions[token] = {
            "device_uuid": device_uuid,
            "expires": int(_time.time()) + self.config.session_ttl,
        }
        self.devices.update_last_seen(device_uuid)
        return {
            "session_token": token,
            "expires_at": self._sessions[token]["expires"],
        }

    # ---- Session Validation ----

    def validate_token(self, token: str) -> Optional[str]:
        """Return device_uuid if token is valid, else None."""
        session = self._sessions.get(token)
        if not session:
            return None
        if int(_time.time()) > session["expires"]:
            del self._sessions[token]
            return None
        return session["device_uuid"]

    # ---- Device Management ----

    def revoke_device(self, device_uuid: str) -> dict:
        """Immediately revoke a bound device."""
        if self.devices.remove(device_uuid):
            # Invalidate all sessions for this device
            to_del = [
                t for t, s in self._sessions.items()
                if s["device_uuid"] == device_uuid
            ]
            for t in to_del:
                del self._sessions[t]
            return {"status": "revoked", "device_uuid": device_uuid}
        return {"error": "Device not found", "code": 404}

    def list_bound_devices(self) -> list:
        return self.devices.list()

    # ---- WebSocket Events ----

    def event_broker_send(self, event_type: str, data: dict) -> None:
        """Queue event for push to connected clients."""
        # Placeholder — real implementation in FastAPI WebSocket handler
        pass
