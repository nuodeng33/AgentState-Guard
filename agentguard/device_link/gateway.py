"""ASDL/1 Device Link Gateway — MVP read-only API router.

Must NOT expose the full Core API. Separate security boundary.
"""

from __future__ import annotations

import json
import time as _time
from typing import Any, Dict, Optional

from .crypto import (
    generate_challenge, verify_signature, spki_fingerprint,
    random_token, validate_ecdsa_p256_public_key_der,
)
from .pairing import PairingManager, PairState


class DeviceRegistry:
    """Stores bound device metadata. Non-sensitive fields only."""

    def __init__(self, single_device: bool = False):
        self._devices: Dict[str, dict] = {}
        self._single_device = single_device

    def add(self, device_uuid: str, pubkey_der: bytes, display_name: str,
            permissions: list, protocol_version: int) -> None:
        if device_uuid in self._devices:
            raise ValueError("Device UUID already bound")
        if self._single_device and self._devices:
            raise ValueError("Maximum devices already bound")
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
        self.challenge_ttl = kwargs.get("challenge_ttl", 300)
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
        self.devices = DeviceRegistry(single_device=self.config.single_device)
        self._sessions: Dict[str, dict] = {}
        self._challenges: Dict[str, dict] = {}

    # ---- Pairing ----

    def pair_start(self) -> dict:
        try:
            session = self.pairing_mgr.create_session(
                self.desktop_uuid, self.desktop_pubkey_der, self.desktop_tls_spki_fp,
                expiry_seconds=self.config.pair_ttl,
                max_sas_attempts=self.config.max_sas_attempts,
            )
        except ValueError as exc:
            return {"error": str(exc), "code": 423}
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
            session.expire_if_needed()
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
        try:
            pubkey_der = bytes.fromhex(android_pubkey_der_hex)
            if not validate_ecdsa_p256_public_key_der(pubkey_der):
                return {"error": "Invalid P-256 DER public key", "code": 400}
            session.set_android_pubkey(pubkey_der)
            sas = session.start_sas()
            return {"sas": sas, "state": session.state.value}
        except ValueError as e:
            return {"error": str(e), "code": 400}

    def pair_confirm(self, session_id: str, confirm: bool) -> dict:
        session = self.pairing_mgr.get(session_id)
        if not session:
            return {"error": "Session not found", "code": 404}
        if not confirm:
            try:
                session.reject()
            except ValueError as e:
                return {"error": str(e), "code": 409}
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
        if not session.android_uuid or not session.android_pubkey_der:
            return {"error": "Pairing identity is incomplete", "code": 409}
        try:
            submitted_pubkey = bytes.fromhex(android_pubkey_der_hex)
            if not validate_ecdsa_p256_public_key_der(submitted_pubkey):
                return {"error": "Invalid P-256 DER public key", "code": 400}
        except ValueError as exc:
            return {"error": str(exc), "code": 400}
        if android_uuid != session.android_uuid or submitted_pubkey != session.android_pubkey_der:
            return {"error": "Pairing identity mismatch", "code": 409}

        bound_uuid = session.android_uuid
        bound_pubkey = session.android_pubkey_der
        try:
            session.consume()
        except ValueError as exc:
            return {"error": str(exc), "code": 409}
        try:
            self.devices.add(bound_uuid, bound_pubkey, display_name,
                             permissions=["read"], protocol_version=1)
        except ValueError as exc:
            return {"error": str(exc), "code": 409}
        token = random_token()
        self._sessions[token] = {
            "device_uuid": bound_uuid,
            "expires": _time.monotonic() + self.config.session_ttl,
        }
        self.devices.update_last_seen(bound_uuid)
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
        challenge_hex = challenge.hex()
        self._challenges[challenge_hex] = {
            "device_uuid": device_uuid,
            "challenge": challenge,
            "issued_at": _time.monotonic(),
            "expires_at": _time.monotonic() + self.config.challenge_ttl,
            "consumed": False,
        }
        return {"desktop_challenge": challenge_hex, "desktop_uuid": self.desktop_uuid}

    def auth_response(self, device_uuid: str, challenge_resp_hex: str,
                      nonce_hex: str) -> dict:
        dev = self.devices.get(device_uuid)
        if not dev:
            return {"error": "Device not bound", "code": 403}
        try:
            nonce = bytes.fromhex(nonce_hex)
            signature = bytes.fromhex(challenge_resp_hex)
        except ValueError as exc:
            return {"error": str(exc), "code": 400}

        challenge = self._challenges.get(nonce_hex)
        if not challenge:
            return {"error": "Challenge not found", "code": 401}
        if challenge["device_uuid"] != device_uuid:
            return {"error": "Challenge identity mismatch", "code": 403}
        if challenge["consumed"]:
            return {"error": "Challenge already consumed", "code": 401}
        if _time.monotonic() >= challenge["expires_at"]:
            challenge["consumed"] = True
            return {"error": "Challenge expired", "code": 401}
        if nonce != challenge["challenge"]:
            return {"error": "Challenge mismatch", "code": 401}

        verified = verify_signature(
            bytes.fromhex(dev["pubkey_der_hex"]), challenge["challenge"], signature,
        )
        if not verified:
            return {"error": "Signature verification failed", "code": 401}

        challenge["consumed"] = True
        token = random_token()
        self._sessions[token] = {
            "device_uuid": device_uuid,
            "expires": _time.monotonic() + self.config.session_ttl,
        }
        self.devices.update_last_seen(device_uuid)
        return {"session_token": token}

    def validate_token(self, token: str) -> Optional[str]:
        session = self._sessions.get(token)
        if not session:
            return None
        if _time.monotonic() >= session["expires"]:
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

    # ---- Status ----

    def get_status(self) -> dict:
        return {
            "status": "active",
            "desktop_uuid": self.desktop_uuid,
            "bound_devices": len(self.devices._devices),
        }
