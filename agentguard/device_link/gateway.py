"""ASDL/1 Device Link Gateway — MVP read-only API router.

Must NOT expose the full Core API. Separate security boundary.
"""

from __future__ import annotations

import hashlib
import struct
import time as _time

from .crypto import (
    generate_challenge,
    random_session_id,
    random_token,
    sign_challenge,
    spki_fingerprint,
    validate_ecdsa_p256_public_key_der,
    verify_signature,
)
from .errors import DeviceLinkError
from .pairing import PairingManager, PairState


def _length_prefix(value: bytes) -> bytes:
    return struct.pack(">I", len(value)) + value


def build_auth_message(
    *,
    protocol_version: int,
    desktop_uuid: str,
    device_uuid: str,
    challenge_id: str,
    challenge: bytes,
) -> bytes:
    """Build an unambiguous domain-separated mutual-auth message."""
    return b"".join(
        (
            b"ASDL\x00AUTH_RESPONSE\x00",
            struct.pack(">H", protocol_version),
            _length_prefix(desktop_uuid.encode("utf-8")),
            _length_prefix(device_uuid.encode("utf-8")),
            _length_prefix(bytes.fromhex(challenge_id)),
            _length_prefix(challenge),
        )
    )


class DeviceRegistry:
    """Stores bound device metadata. Non-sensitive fields only."""

    def __init__(self, single_device: bool = False):
        self._devices: dict[str, dict] = {}
        self._single_device = single_device

    def add(
        self,
        device_uuid: str,
        pubkey_der: bytes,
        display_name: str,
        permissions: list,
        protocol_version: int,
    ) -> None:
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

    def get(self, device_uuid: str) -> dict | None:
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

    def count(self) -> int:
        return len(self._devices)


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
        config: GatewayConfig | None = None,
        *,
        device_registry=None,
        single_device: bool | None = None,
    ):
        self.desktop_uuid = desktop_uuid
        self.desktop_pubkey_der = desktop_device_pubkey_der
        self.desktop_privkey_pem = desktop_device_privkey_pem
        self.desktop_tls_spki_fp = desktop_tls_spki_fp
        self.config = config or GatewayConfig()
        self.pairing_mgr = PairingManager(max_sessions=self.config.max_pair_sessions)
        enforce_single = (
            self.config.single_device if single_device is None else single_device
        )
        self.devices = device_registry or DeviceRegistry(single_device=enforce_single)
        self._sessions: dict[str, dict] = {}
        self._challenges: dict[str, dict] = {}
        self._pair_tickets: dict[bytes, dict] = {}
        self._pair_tokens: dict[bytes, dict] = {}
        self._product_tokens: dict[bytes, dict] = {}
        self._product_challenges: dict[str, dict] = {}
        self._pair_confirmations: dict[str, set[str]] = {}

    # ---- Pairing ----

    def pair_start(self) -> dict:
        try:
            session = self.pairing_mgr.create_session(
                self.desktop_uuid,
                self.desktop_pubkey_der,
                self.desktop_tls_spki_fp,
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

    def create_pairing_invitation(self, host: str, port: int = 8788) -> dict:
        """Create a QR-safe invitation with a one-time, memory-only ticket."""
        if not host or host in {"0.0.0.0", "127.0.0.1", "localhost"} or port != 8788:
            raise DeviceLinkError(
                409, "PAIR_ENDPOINT_INVALID", "A physical LAN endpoint is required"
            )
        started = self.pair_start()
        if "error" in started:
            raise DeviceLinkError(
                started.get("code", 423),
                "PAIRING_CAPACITY_EXHAUSTED",
                "No pairing session capacity is available",
            )
        session_id = started["session_id"]
        ticket = random_token()
        digest = hashlib.sha256(ticket.encode("ascii")).digest()
        self._pair_tickets[digest] = {
            "session_id": session_id,
            "expires": _time.monotonic() + self.config.pair_ttl,
            "used": False,
        }
        expires_at_epoch = int(_time.time()) + self.config.pair_ttl
        return {
            "protocol_version": 1,
            "session_id": session_id,
            "ticket": ticket,
            "endpoint": f"https://{host}:{port}",
            "expires_in_s": self.config.pair_ttl,
            "expires_at_epoch": expires_at_epoch,
            "desktop_uuid": self.desktop_uuid,
            "desktop_public_key_der": self.desktop_pubkey_der.hex(),
            "desktop_signing_fingerprint": spki_fingerprint(self.desktop_pubkey_der),
            "tls_spki_fingerprint": self.desktop_tls_spki_fp,
            "state": started["state"],
        }

    def accept_pairing_ticket(
        self,
        session_id: str,
        ticket: str,
        android_uuid: str,
        nonce_hex: str,
    ) -> dict:
        try:
            digest = hashlib.sha256(ticket.encode("ascii")).digest()
        except (UnicodeEncodeError, AttributeError):
            digest = b""
        record = self._pair_tickets.get(digest)
        if (
            record is None
            or record["used"]
            or record["session_id"] != session_id
            or _time.monotonic() >= record["expires"]
        ):
            raise DeviceLinkError(
                401, "PAIR_TICKET_INVALID", "Pairing ticket is invalid or expired"
            )
        record["used"] = True
        result = self.pair_first_connection(session_id, android_uuid, nonce_hex)
        if "error" in result:
            raise DeviceLinkError(
                result.get("code", 409), "PAIR_STATE_CONFLICT", result["error"]
            )
        pairing_token = random_token()
        self._pair_tokens[hashlib.sha256(pairing_token.encode("ascii")).digest()] = {
            "session_id": session_id,
            "expires": record["expires"],
        }
        return {**result, "pairing_token": pairing_token}

    def _require_pair_token(self, session_id: str, token: str) -> None:
        try:
            digest = hashlib.sha256(token.encode("ascii")).digest()
        except (UnicodeEncodeError, AttributeError):
            digest = b""
        record = self._pair_tokens.get(digest)
        if (
            record is None
            or record["session_id"] != session_id
            or _time.monotonic() >= record["expires"]
        ):
            raise DeviceLinkError(
                401, "PAIR_TOKEN_INVALID", "Pairing token is invalid or expired"
            )

    def pair_start_sas_scoped(
        self,
        session_id: str,
        pairing_token: str,
        android_pubkey_der_hex: str,
    ) -> dict:
        self._require_pair_token(session_id, pairing_token)
        result = self.pair_start_sas(session_id, android_pubkey_der_hex)
        if "error" in result:
            raise DeviceLinkError(
                result.get("code", 400), "PAIR_SAS_FAILED", result["error"]
            )
        return result

    def pair_android_confirm(
        self,
        session_id: str,
        pairing_token: str,
        confirm: bool,
    ) -> dict:
        self._require_pair_token(session_id, pairing_token)
        if not confirm:
            return self._product_reject(session_id)
        session = self.pairing_mgr.get(session_id)
        confirmations = self._pair_confirmations.get(session_id, set())
        # Android intentionally re-drives its authenticated confirmation while
        # waiting for the desktop. Once the desktop completes the same SAS,
        # that retry must reveal the settled state so Android can call
        # /complete and persist the binding. Other callers/states still fail
        # closed; the scoped pair token was validated above.
        if session is not None and session.state is PairState.CONFIRMED_BOTH and "android" in confirmations:
            return {"state": session.state.value}
        if session is None or session.state is not PairState.SAS_PENDING:
            raise DeviceLinkError(
                409, "PAIR_STATE_CONFLICT", "SAS confirmation is not expected"
            )
        self._pair_confirmations.setdefault(session_id, set()).add("android")
        return self._finish_dual_confirmation(session_id)

    def pair_desktop_confirm(self, session_id: str, confirm: bool) -> dict:
        if not confirm:
            return self._product_reject(session_id)
        session = self.pairing_mgr.get(session_id)
        if session is None or session.state is not PairState.SAS_PENDING:
            raise DeviceLinkError(
                409, "PAIR_STATE_CONFLICT", "SAS confirmation is not expected"
            )
        self._pair_confirmations.setdefault(session_id, set()).add("desktop")
        return self._finish_dual_confirmation(session_id)

    def _finish_dual_confirmation(self, session_id: str) -> dict:
        confirmed = self._pair_confirmations.get(session_id, set())
        if confirmed == {"android", "desktop"}:
            result = self.pair_confirm(session_id, True)
            if "error" in result:
                raise DeviceLinkError(
                    result.get("code", 409), "PAIR_STATE_CONFLICT", result["error"]
                )
            return result
        return {"state": "sas_pending", "confirmed_by": sorted(confirmed)}

    def _product_reject(self, session_id: str) -> dict:
        result = self.pair_confirm(session_id, False)
        self._invalidate_pairing(session_id)
        if "error" in result:
            raise DeviceLinkError(
                result.get("code", 409), "PAIR_STATE_CONFLICT", result["error"]
            )
        return result

    def cancel_pairing(self, session_id: str) -> dict:
        session = self.pairing_mgr.get(session_id)
        if session is None:
            raise DeviceLinkError(
                404, "PAIR_SESSION_NOT_FOUND", "Pairing session not found"
            )
        try:
            session.cancel()
        except ValueError as error:
            raise DeviceLinkError(409, "PAIR_STATE_CONFLICT", str(error)) from error
        self._invalidate_pairing(session_id)
        return {"session_id": session_id, "state": session.state.value}

    def _invalidate_pairing(self, session_id: str) -> None:
        for records in (self._pair_tickets, self._pair_tokens):
            for digest, record in list(records.items()):
                if record["session_id"] == session_id:
                    del records[digest]
        self._pair_confirmations.pop(session_id, None)

    def pair_poll(self, session_id: str) -> dict:
        session = self.pairing_mgr.get(session_id)
        if not session:
            return {"error": "Session not found", "code": 404}
        if session.is_expired and not session.is_terminal:
            session.expire_if_needed()
        result = {"session_id": session.session_id, "state": session.state.value}
        sas = session.sas_for_projection()
        if sas is not None:
            result["sas"] = sas
        return result

    def pair_first_connection(
        self, session_id: str, android_uuid: str, nonce_hex: str
    ) -> dict:
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

    def pair_complete(
        self,
        session_id: str,
        android_uuid: str,
        android_pubkey_der_hex: str,
        display_name: str,
    ) -> dict:
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
        if (
            android_uuid != session.android_uuid
            or submitted_pubkey != session.android_pubkey_der
        ):
            return {"error": "Pairing identity mismatch", "code": 409}

        bound_uuid = session.android_uuid
        bound_pubkey = session.android_pubkey_der
        try:
            session.consume()
        except ValueError as exc:
            return {"error": str(exc), "code": 409}
        try:
            self.devices.add(
                bound_uuid,
                bound_pubkey,
                display_name,
                permissions=["read", "approve_once", "reject", "self_unpair"],
                protocol_version=1,
            )
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

    def pair_complete_scoped(
        self,
        session_id: str,
        pairing_token: str,
        android_uuid: str,
        android_pubkey_der_hex: str,
        display_name: str,
    ) -> dict:
        self._require_pair_token(session_id, pairing_token)
        result = self.pair_complete(
            session_id, android_uuid, android_pubkey_der_hex, display_name
        )
        if "error" in result:
            raise DeviceLinkError(
                result.get("code", 409), "PAIR_COMPLETE_FAILED", result["error"]
            )
        legacy_token = result.pop("session_token", None)
        if legacy_token:
            self._sessions.pop(legacy_token, None)
        token = self._issue_product_token(android_uuid)
        self._invalidate_pairing(session_id)
        result["session_token"] = token
        result["permissions"] = ["read", "approve_once", "reject", "self_unpair"]
        return result

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

    def auth_challenge_scoped(
        self, device_uuid: str, protocol_version: int = 1
    ) -> dict:
        if protocol_version != 1:
            raise DeviceLinkError(
                400,
                "DEVICE_PROTOCOL_UNSUPPORTED",
                "Device Link protocol is unsupported",
            )
        if not self.devices.get(device_uuid):
            raise DeviceLinkError(403, "DEVICE_NOT_BOUND", "Device is not bound")
        challenge_id = random_session_id()
        challenge = generate_challenge()
        self._product_challenges[challenge_id] = {
            "device_uuid": device_uuid,
            "challenge": challenge,
            "expires": _time.monotonic() + min(self.config.challenge_ttl, 60),
            "used": False,
        }
        return {
            "challenge_id": challenge_id,
            "desktop_challenge": challenge.hex(),
            "desktop_uuid": self.desktop_uuid,
            "protocol_version": 1,
            "expires_in_s": min(self.config.challenge_ttl, 60),
        }

    def auth_response(
        self, device_uuid: str, challenge_resp_hex: str, nonce_hex: str
    ) -> dict:
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
            bytes.fromhex(dev["pubkey_der_hex"]),
            challenge["challenge"],
            signature,
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

    def auth_response_scoped(
        self,
        device_uuid: str,
        challenge_id: str,
        signature_hex: str,
        protocol_version: int = 1,
    ) -> dict:
        if protocol_version != 1:
            raise DeviceLinkError(
                400,
                "DEVICE_PROTOCOL_UNSUPPORTED",
                "Device Link protocol is unsupported",
            )
        device = self.devices.get(device_uuid)
        record = self._product_challenges.get(challenge_id)
        if device is None:
            raise DeviceLinkError(403, "DEVICE_NOT_BOUND", "Device is not bound")
        if record is None:
            raise DeviceLinkError(401, "AUTH_CHALLENGE_UNKNOWN", "Challenge is unknown")
        if record["used"]:
            raise DeviceLinkError(
                401, "AUTH_CHALLENGE_USED", "Challenge was already used"
            )
        if record["device_uuid"] != device_uuid:
            raise DeviceLinkError(
                401, "AUTH_CHALLENGE_MISMATCH", "Challenge device mismatch"
            )
        if _time.monotonic() >= record["expires"]:
            record["used"] = True
            raise DeviceLinkError(410, "AUTH_CHALLENGE_EXPIRED", "Challenge expired")
        message = build_auth_message(
            protocol_version=protocol_version,
            desktop_uuid=self.desktop_uuid,
            device_uuid=device_uuid,
            challenge_id=challenge_id,
            challenge=record["challenge"],
        )
        try:
            signature = bytes.fromhex(signature_hex)
        except ValueError as error:
            raise DeviceLinkError(
                400, "DEVICE_INVALID_REQUEST", "Signature is invalid"
            ) from error
        if not verify_signature(
            bytes.fromhex(device["pubkey_der_hex"]), message, signature
        ):
            raise DeviceLinkError(
                401, "AUTH_SIGNATURE_INVALID", "Signature verification failed"
            )
        record["used"] = True
        token = self._issue_product_token(device_uuid)
        self.devices.update_last_seen(device_uuid)
        return {
            "session_token": token,
            "expires_in_s": self.config.session_ttl,
            "desktop_signature": sign_challenge(
                self.desktop_privkey_pem, message
            ).hex(),
        }

    def _issue_product_token(self, device_uuid: str) -> str:
        for digest, record in list(self._product_tokens.items()):
            if record["device_uuid"] == device_uuid:
                del self._product_tokens[digest]
        token = random_token()
        self._product_tokens[hashlib.sha256(token.encode("ascii")).digest()] = {
            "device_uuid": device_uuid,
            "expires": _time.monotonic() + self.config.session_ttl,
        }
        return token

    def validate_token(self, token: str) -> str | None:
        try:
            digest = hashlib.sha256(token.encode("ascii")).digest()
        except (UnicodeEncodeError, AttributeError):
            digest = b""
        product = self._product_tokens.get(digest)
        if product is not None:
            if _time.monotonic() >= product["expires"]:
                del self._product_tokens[digest]
                return None
            return product["device_uuid"]
        session = self._sessions.get(token)
        if not session:
            return None
        if _time.monotonic() >= session["expires"]:
            del self._sessions[token]
            return None
        return session["device_uuid"]

    # ---- Device ----

    def revoke_device(self, device_uuid: str) -> dict:
        removed = self.devices.remove(device_uuid)
        for token, session in list(self._sessions.items()):
            if session["device_uuid"] == device_uuid:
                del self._sessions[token]
        for digest, record in list(self._product_tokens.items()):
            if record["device_uuid"] == device_uuid:
                del self._product_tokens[digest]
        for challenge_id, challenge in list(self._challenges.items()):
            if challenge["device_uuid"] == device_uuid:
                del self._challenges[challenge_id]
        for challenge_id, challenge in list(self._product_challenges.items()):
            if challenge["device_uuid"] == device_uuid:
                del self._product_challenges[challenge_id]
        if removed:
            return {"status": "revoked", "device_uuid": device_uuid}
        return {"error": "Device not found", "code": 404}

    def list_bound_devices(self) -> list:
        return self.devices.list()

    def invalidate_transient_authorizations(self) -> None:
        """Drop live credentials and pairing state without deleting trust bindings."""
        self.pairing_mgr.cancel_all()
        self._sessions.clear()
        self._challenges.clear()
        self._pair_tickets.clear()
        self._pair_tokens.clear()
        self._product_tokens.clear()
        self._product_challenges.clear()
        self._pair_confirmations.clear()

    # ---- Status ----

    def get_status(self) -> dict:
        return {
            "status": "active",
            "desktop_uuid": self.desktop_uuid,
            "bound_devices": self.devices.count(),
        }
