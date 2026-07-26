"""ASDL/1 Device Link gateway security boundary."""

from __future__ import annotations

import hashlib
import struct
import threading
import time as _time
from dataclasses import dataclass

from .crypto import (
    Clock,
    RandomSource,
    SecureRandom,
    SystemClock,
    generate_challenge,
    is_p256_public_key_der,
    spki_fingerprint,
    verify_signature_der,
)
from .errors import DeviceLinkError, invalid_request, state_conflict
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
    """Build the unambiguous, domain-separated authentication message."""
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
    """Thread-safe in-memory store for bound device metadata."""

    def __init__(self):
        self._devices: dict[str, dict] = {}
        self._lock = threading.RLock()

    def add(
        self,
        device_uuid: str,
        pubkey_der: bytes,
        display_name: str,
        permissions: list,
        protocol_version: int,
    ) -> None:
        with self._lock:
            self._devices[device_uuid] = {
                "uuid": device_uuid,
                "pubkey_der_hex": pubkey_der.hex(),
                "fingerprint": spki_fingerprint(pubkey_der),
                "display_name": display_name,
                "permissions": list(permissions),
                "created_at": int(_time.time()),
                "last_seen": None,
                "protocol_version": protocol_version,
            }

    def get(self, device_uuid: str) -> dict | None:
        with self._lock:
            device = self._devices.get(device_uuid)
            return dict(device) if device else None

    def remove(self, device_uuid: str) -> bool:
        with self._lock:
            return self._devices.pop(device_uuid, None) is not None

    def list(self) -> list:
        with self._lock:
            return [
                {
                    "uuid": device["uuid"],
                    "fingerprint": device["fingerprint"],
                    "display_name": device["display_name"],
                    "last_seen": device.get("last_seen"),
                }
                for device in self._devices.values()
            ]

    def update_last_seen(self, device_uuid: str) -> None:
        with self._lock:
            if device_uuid in self._devices:
                self._devices[device_uuid]["last_seen"] = int(_time.time())

    def count(self) -> int:
        with self._lock:
            return len(self._devices)


@dataclass
class _TokenRecord:
    device_uuid: str
    expires_at: float
    issued_at: float


class _TokenStore:
    """Bounded token store keyed only by SHA-256 token digests."""

    def __init__(
        self,
        *,
        ttl_seconds: int,
        max_tokens: int,
        clock: Clock,
        rng: RandomSource,
    ):
        self._ttl = ttl_seconds
        self._max = max_tokens
        self._clock = clock
        self._rng = rng
        self._records: dict[bytes, _TokenRecord] = {}
        self._latest_by_device: dict[str, bytes] = {}

    @staticmethod
    def _digest(token: str) -> bytes:
        return hashlib.sha256(token.encode("ascii")).digest()

    def issue(self, device_uuid: str) -> str:
        self._cleanup()
        self.revoke_device(device_uuid)
        if len(self._records) >= self._max:
            oldest = min(
                self._records,
                key=lambda digest: self._records[digest].issued_at,
            )
            self._remove(oldest)
        token = self._rng.bytes(32).hex()
        digest = self._digest(token)
        while digest in self._records:
            token = self._rng.bytes(32).hex()
            digest = self._digest(token)
        now = self._clock.now()
        self._records[digest] = _TokenRecord(
            device_uuid=device_uuid,
            expires_at=now + self._ttl,
            issued_at=now,
        )
        self._latest_by_device[device_uuid] = digest
        return token

    def validate(self, token: str) -> str | None:
        if (
            len(token) != 64
            or token.lower() != token
            or any(char not in "0123456789abcdef" for char in token)
        ):
            return None
        try:
            digest = self._digest(token)
        except (UnicodeEncodeError, ValueError):
            return None
        record = self._records.get(digest)
        if not record:
            return None
        if self._clock.now() >= record.expires_at:
            self._remove(digest)
            return None
        if self._latest_by_device.get(record.device_uuid) != digest:
            return None
        return record.device_uuid

    def revoke_device(self, device_uuid: str) -> None:
        for digest, record in list(self._records.items()):
            if record.device_uuid == device_uuid:
                self._remove(digest)

    def _cleanup(self) -> None:
        now = self._clock.now()
        for digest, record in list(self._records.items()):
            if now >= record.expires_at:
                self._remove(digest)

    def _remove(self, digest: bytes) -> None:
        record = self._records.pop(digest, None)
        if (
            record
            and self._latest_by_device.get(record.device_uuid) == digest
        ):
            del self._latest_by_device[record.device_uuid]


@dataclass
class _ChallengeRecord:
    challenge_id: str
    device_uuid: str
    challenge: bytes
    expires_at: float
    attempts: int = 0
    used: bool = False


class _ChallengeStore:
    def __init__(
        self,
        *,
        ttl_seconds: int,
        max_attempts: int,
        max_challenges: int,
        clock: Clock,
        rng: RandomSource,
    ):
        self._ttl = ttl_seconds
        self._max_attempts = max_attempts
        self._max = max_challenges
        self._clock = clock
        self._rng = rng
        self._records: dict[str, _ChallengeRecord] = {}

    def issue(self, device_uuid: str) -> _ChallengeRecord:
        if len(self._records) >= self._max:
            self._cleanup_consumed()
        if len(self._records) >= self._max:
            raise DeviceLinkError(
                429,
                "AUTH_CHALLENGE_CAPACITY",
                "Too many authentication challenges",
            )
        challenge_id = self._rng.bytes(16).hex()
        while challenge_id in self._records:
            challenge_id = self._rng.bytes(16).hex()
        record = _ChallengeRecord(
            challenge_id=challenge_id,
            device_uuid=device_uuid,
            challenge=generate_challenge(self._rng),
            expires_at=self._clock.now() + self._ttl,
        )
        self._records[challenge_id] = record
        return record

    def require(self, challenge_id: str, device_uuid: str) -> _ChallengeRecord:
        record = self._records.get(challenge_id)
        if not record:
            raise DeviceLinkError(
                401,
                "AUTH_CHALLENGE_UNKNOWN",
                "Authentication challenge is unknown",
            )
        if record.used:
            raise DeviceLinkError(
                401,
                "AUTH_CHALLENGE_USED",
                "Authentication challenge was already used",
            )
        if record.device_uuid != device_uuid:
            raise DeviceLinkError(
                401,
                "AUTH_CHALLENGE_MISMATCH",
                "Authentication challenge does not match the device",
            )
        if self._clock.now() >= record.expires_at:
            record.used = True
            raise DeviceLinkError(
                410,
                "AUTH_CHALLENGE_EXPIRED",
                "Authentication challenge expired",
            )
        return record

    def record_failure(self, record: _ChallengeRecord) -> None:
        record.attempts += 1
        if record.attempts >= self._max_attempts:
            record.used = True
            raise DeviceLinkError(
                401,
                "AUTH_ATTEMPTS_EXHAUSTED",
                "Authentication attempts exhausted",
            )
        raise DeviceLinkError(
            401,
            "AUTH_SIGNATURE_INVALID",
            "Signature verification failed",
        )

    @staticmethod
    def consume(record: _ChallengeRecord) -> None:
        record.used = True

    def _cleanup_consumed(self) -> None:
        expired_or_used = [
            challenge_id
            for challenge_id, record in self._records.items()
            if record.used or self._clock.now() >= record.expires_at
        ]
        for challenge_id in expired_or_used:
            del self._records[challenge_id]


class GatewayConfig:
    def __init__(self, **kwargs):
        self.max_pair_sessions = kwargs.get("max_pair_sessions", 5)
        self.pair_ttl = kwargs.get("pair_ttl", 120)
        self.max_sas_attempts = kwargs.get("max_sas_attempts", 3)
        self.session_ttl = kwargs.get("session_ttl", 3600)
        self.single_device = kwargs.get("single_device", False)
        self.challenge_ttl = kwargs.get("challenge_ttl", 60)
        self.max_auth_attempts = kwargs.get("max_auth_attempts", 3)
        self.max_tokens = kwargs.get("max_tokens", 1024)
        self.max_challenges = kwargs.get("max_challenges", 1024)


class DeviceLinkGateway:
    """In-memory Device Link service with one mutation lock."""

    def __init__(
        self,
        desktop_uuid: str,
        desktop_device_pubkey_der: bytes,
        desktop_device_privkey_pem: bytes,
        desktop_tls_spki_fp: str = "headless-dev",
        config: GatewayConfig | None = None,
        *,
        clock: Clock = None,
        rng: RandomSource = None,
    ):
        self.desktop_uuid = desktop_uuid
        self.desktop_pubkey_der = desktop_device_pubkey_der
        self.desktop_privkey_pem = desktop_device_privkey_pem
        self.desktop_tls_spki_fp = desktop_tls_spki_fp
        self.config = config or GatewayConfig()
        self._clock = clock or SystemClock()
        self._rng = rng or SecureRandom()
        self._lock = threading.RLock()
        self.pairing_mgr = PairingManager(
            max_sessions=self.config.max_pair_sessions,
            expiry_seconds=self.config.pair_ttl,
            max_sas_attempts=self.config.max_sas_attempts,
            clock=self._clock,
            rng=self._rng,
        )
        self.devices = DeviceRegistry()
        self._tokens = _TokenStore(
            ttl_seconds=self.config.session_ttl,
            max_tokens=self.config.max_tokens,
            clock=self._clock,
            rng=self._rng,
        )
        self._challenges = _ChallengeStore(
            ttl_seconds=self.config.challenge_ttl,
            max_attempts=self.config.max_auth_attempts,
            max_challenges=self.config.max_challenges,
            clock=self._clock,
            rng=self._rng,
        )

    def _session(self, session_id: str):
        session = self.pairing_mgr.get(session_id)
        if not session:
            raise DeviceLinkError(
                404,
                "PAIR_SESSION_NOT_FOUND",
                "Pairing session not found",
            )
        if session.state == PairState.EXPIRED:
            raise DeviceLinkError(
                410,
                "PAIR_SESSION_EXPIRED",
                "Pairing session expired",
            )
        return session

    # ---- Pairing ----

    def pair_start(self) -> dict:
        with self._lock:
            session = self.pairing_mgr.create_session(
                self.desktop_uuid,
                self.desktop_pubkey_der,
                self.desktop_tls_spki_fp,
            )
            return {
                "session_id": session.session_id,
                "expires_in_s": session.expiry_seconds,
                "desktop_uuid": self.desktop_uuid,
                "desktop_pubkey_fingerprint": spki_fingerprint(
                    self.desktop_pubkey_der
                )[:8],
                "state": session.state.value,
            }

    def pair_poll(self, session_id: str) -> dict:
        with self._lock:
            session = self._session(session_id)
            return {
                "session_id": session.session_id,
                "state": session.state.value,
            }

    def pair_first_connection(
        self,
        session_id: str,
        android_uuid: str,
        nonce_hex: str,
    ) -> dict:
        with self._lock:
            session = self._session(session_id)
            session.first_connection(android_uuid, bytes.fromhex(nonce_hex))
            return {"state": session.state.value}

    def pair_start_sas(
        self,
        session_id: str,
        android_pubkey_der_hex: str,
    ) -> dict:
        with self._lock:
            session = self._session(session_id)
            public_key_der = bytes.fromhex(android_pubkey_der_hex)
            if not is_p256_public_key_der(public_key_der):
                raise invalid_request("Public key must be DER SPKI P-256")
            session.set_android_pubkey(public_key_der)
            sas = session.start_sas()
            return {"sas": sas, "state": session.state.value}

    def pair_confirm(self, session_id: str, confirm: bool) -> dict:
        with self._lock:
            session = self._session(session_id)
            if confirm:
                session.confirm()
            else:
                session.reject()
            return {"state": session.state.value}

    def pair_complete(
        self,
        session_id: str,
        android_uuid: str,
        android_pubkey_der_hex: str,
        display_name: str,
        protocol_version: int = 1,
    ) -> dict:
        with self._lock:
            session = self._session(session_id)
            if session.state != PairState.CONFIRMED_BOTH:
                raise state_conflict(
                    f"Pairing session is {session.state.value}, not confirmed"
                )
            public_key_der = bytes.fromhex(android_pubkey_der_hex)
            if (
                session.android_uuid != android_uuid
                or session.android_pubkey_der != public_key_der
            ):
                raise DeviceLinkError(
                    409,
                    "PAIR_IDENTITY_MISMATCH",
                    "Completion identity does not match the pairing session",
                )
            if (
                self.config.single_device
                and self.devices.count()
                and not self.devices.get(android_uuid)
            ):
                raise state_conflict("Only one bound device is allowed")
            session.consume()
            self.devices.add(
                android_uuid,
                public_key_der,
                display_name,
                permissions=["read"],
                protocol_version=protocol_version,
            )
            token = self._tokens.issue(android_uuid)
            self.devices.update_last_seen(android_uuid)
            return {
                "status": "bound",
                "session_token": token,
                "desktop_uuid": self.desktop_uuid,
                "permissions": ["read"],
            }

    # ---- Authentication ----

    def auth_challenge(
        self,
        device_uuid: str,
        protocol_version: int = 1,
    ) -> dict:
        with self._lock:
            if protocol_version != 1:
                raise invalid_request("Unsupported protocol version")
            if not self.devices.get(device_uuid):
                raise DeviceLinkError(
                    403,
                    "DEVICE_NOT_BOUND",
                    "Device is not bound",
                )
            record = self._challenges.issue(device_uuid)
            return {
                "challenge_id": record.challenge_id,
                "desktop_challenge": record.challenge.hex(),
                "desktop_uuid": self.desktop_uuid,
                "expires_in_s": self.config.challenge_ttl,
            }

    def auth_response(
        self,
        device_uuid: str,
        challenge_id: str,
        signature_hex: str,
        protocol_version: int = 1,
    ) -> dict:
        with self._lock:
            if protocol_version != 1:
                raise invalid_request("Unsupported protocol version")
            device = self.devices.get(device_uuid)
            if not device:
                raise DeviceLinkError(
                    403,
                    "DEVICE_NOT_BOUND",
                    "Device is not bound",
                )
            record = self._challenges.require(challenge_id, device_uuid)
            message = build_auth_message(
                protocol_version=protocol_version,
                desktop_uuid=self.desktop_uuid,
                device_uuid=device_uuid,
                challenge_id=challenge_id,
                challenge=record.challenge,
            )
            verified = verify_signature_der(
                bytes.fromhex(device["pubkey_der_hex"]),
                message,
                bytes.fromhex(signature_hex),
            )
            if not verified:
                self._challenges.record_failure(record)
            self._challenges.consume(record)
            token = self._tokens.issue(device_uuid)
            self.devices.update_last_seen(device_uuid)
            return {"session_token": token}

    def validate_token(self, token: str) -> str | None:
        with self._lock:
            return self._tokens.validate(token)

    # ---- Device ----

    def revoke_device(self, device_uuid: str) -> dict:
        with self._lock:
            if not self.devices.remove(device_uuid):
                raise DeviceLinkError(
                    404,
                    "DEVICE_NOT_FOUND",
                    "Device not found",
                )
            self._tokens.revoke_device(device_uuid)
            return {"status": "revoked", "device_uuid": device_uuid}

    def list_bound_devices(self) -> list:
        return self.devices.list()

    def get_status(self) -> dict:
        return {
            "status": "active",
            "desktop_uuid": self.desktop_uuid,
            "bound_devices": self.devices.count(),
        }
