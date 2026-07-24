"""ASDL/1 Device Link Gateway — MVP read-only API router.

Must NOT expose the full Core API. Separate security boundary.
"""

from __future__ import annotations

import json
import time as _time
from typing import Any, Dict, Optional

from .crypto import (
    generate_challenge, verify_signature, spki_fingerprint,
    random_session_id,
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
            "pubkey_der_hex": pubkey_der.hex(),
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
            {"uuid": d["uuid"], "fingerprint": d["fingerprint"],
             "display_name": d["display_name"], "last_seen": d.get("last_seen")}
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
        self.single_device = kwargs.get("single_device", False)


class DeviceLinkGateway:
    """Minimal Device Link API router — MVP read-only client."""

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
        self._sessions: Dict[str, dict] = {}

    # ---- Pairing ----

    def pair_start(self) -> dict:
        session = self.pairing_mgr.create_session(
            self.desktop_uuid, self.desktop_pubkey_der, self.desktop_tls_spki_fp,
        )
        return {
            "session_id": session.session_id,
            "expires_in_s": session.expiry_seconds,
            "desktop_uuid": self.desktop_uuid,
            "desktop_pubkey_fingerprint": spki_fingerprint(self.desktop_pubkey_der)[:8],
            "state": session.state.value,
        }

    def pair_poll(self, session_id: str) -> dict:
        session = self.pairing_mgr.get(session_id)
        if not session:
            return {"error": "Session not found", "code": 404}
        if session.is_expired and not session.is_terminal:
            session.set_state(PairState.EXPIRED)
        return {"session_id": session.session_id, "state": session.state.value}

    def pair_first_connection(self, session_id: str, android_uuid: str,
                              nonce_hex: str) -> dict:
        session = self.pairing_mgr.get(session_id)
        if not session:
            return {"error": "Session not found", "code": 404}
        try:
            session.first_connection(android_uuid, bytes.fromhex(nonce_hex))
            return {"state": session.state.value}
        except ValueError as e:
            return {"error": str(e), "code": 409}

    def pair_start_sas(self, session_id: str, android_pubkey_der_hex: str) -> dict:
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
        session = self.pairing_mgr.get(session_id)
        if not session:
            return {"error": "Session not found", "code": 404}
        if not confirm:
            session.reject()
            return {"state": session.state.value}
        try:
            session.confirm()
        except ValueError as e:
            return {"error": str(e), "code": 409}
        return {"state": session.state.value}

    def pair_complete(self, session_id: str, android_uuid: str,
                      android_pubkey_der_hex: str, display_name: str) -> dict:
        session = self.pairing_mgr.get(session_id)
        if not session:
            return {"error": "Session not found", "code": 404}
        if session.state != PairState.CONFIRMED_BOTH:
            return {"error": f"Not confirmed: {session.state.value}", "code": 409}
        pubkey_der = bytes.fromhex(android_pubkey_der_hex)
        self.devices.add(android_uuid, pubkey_der, display_name,
                         permissions=["read"], protocol_version=1)
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
            "permissions": ["read"],
        }

    # ---- Auth ----

    def auth_challenge(self, device_uuid: str) -> dict:
        dev = self.devices.get(device_uuid)
        if not dev:
            return {"error": "Device not bound", "code": 403}
        challenge = generate_challenge()
        return {"desktop_challenge": challenge.hex(), "desktop_uuid": self.desktop_uuid}

    def auth_response(self, device_uuid: str, challenge_resp_hex: str,
                      nonce_hex: str) -> dict:
        dev = self.devices.get(device_uuid)
        if not dev:
            return {"error": "Device not bound", "code": 403}
        verified = verify_signature(
            bytes.fromhex(dev["pubkey_der_hex"]), bytes.fromhex(nonce_hex),
            bytes.fromhex(challenge_resp_hex),
        )
        if not verified:
            return {"error": "Signature verification failed", "code": 401}
        token = random_session_id()
        self._sessions[token] = {
            "device_uuid": device_uuid,
            "expires": int(_time.time()) + self.config.session_ttl,
        }
        self.devices.update_last_seen(device_uuid)
        return {"session_token": token}

    def validate_token(self, token: str) -> Optional[str]:
        session = self._sessions.get(token)
        if not session:
            return None
        if int(_time.time()) > session["expires"]:
            del self._sessions[token]
            return None
        return session["device_uuid"]

    # ---- Device ----

    def revoke_device(self, device_uuid: str) -> dict:
        if self.devices.remove(device_uuid):
            to_del = [t for t, s in self._sessions.items()
                       if s["device_uuid"] == device_uuid]
            for t in to_del:
                del self._sessions[t]
            return {"status": "revoked", "device_uuid": device_uuid}
        return {"error": "Device not found", "code": 404}

    def list_bound_devices(self) -> list:
        return self.devices.list()
