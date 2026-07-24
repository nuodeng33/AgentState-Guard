"""ASDL/1 Cryptographic Primitives — ECDSA P-256, SAS, challenge, replay.

All time and randomness are injectable for testing.
Production defaults: SystemClock + SecureRandom.
"""

import hashlib
import hmac
import os
import secrets
import struct
import time
from abc import ABC, abstractmethod, abstractproperty
from typing import Dict, Tuple


# ---- Injectables ----


class Clock(ABC):
    @abstractmethod
    def now(self) -> float: ...


class SystemClock(Clock):
    def now(self) -> float:
        return time.monotonic()


class FakeClock(Clock):
    def __init__(self, start: float = 0.0):
        self._t = start

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


class RandomSource(ABC):
    @abstractmethod
    def bytes(self, n: int) -> bytes: ...


class SecureRandom(RandomSource):
    def bytes(self, n: int) -> bytes:
        return secrets.token_bytes(n)


class DeterministicRandom(RandomSource):
    def __init__(self, seed: int = 42):
        self._counter = seed

    def bytes(self, n: int) -> bytes:
        result = bytearray(n)
        for i in range(n):
            self._counter = (self._counter * 1103515245 + 12345) & 0x7FFFFFFF
            result[i] = self._counter % 256
        return bytes(result)


# ---- Canonical Serialization ----


def canonical_concat(*parts: bytes) -> bytes:
    return b"".join(parts)


def encode_uint16_be(value: int) -> bytes:
    return struct.pack(">H", value)


def encode_uint64_be(value: int) -> bytes:
    return struct.pack(">Q", value)


def encode_utf8(text: str) -> bytes:
    return text.encode("utf-8")


def encode_hex(hex_string: str) -> bytes:
    return bytes.fromhex(hex_string)


# ---- Random Generation ----


def random_bytes(n: int = 32, rng: RandomSource = None) -> bytes:
    return (rng or SecureRandom()).bytes(n)


def random_session_id(rng: RandomSource = None) -> str:
    return (rng or SecureRandom()).bytes(16).hex()


# ---- Fingerprint ----


def spki_fingerprint(public_key_der: bytes) -> str:
    return hashlib.sha256(public_key_der).hexdigest()


def fingerprint_short(fingerprint: str) -> str:
    return fingerprint[:8]


# ---- SAS Derivation ----


def derive_sas(pairing_secret: bytes, canonical_transcript: bytes) -> str:
    raw = hmac.new(pairing_secret, canonical_transcript, hashlib.sha256).digest()
    num = int.from_bytes(raw[:3], "big") % 1_000_000
    return f"{num:06d}"


def build_pairing_transcript(
    protocol_version: int,
    session_id: str,
    desktop_uuid: str,
    android_uuid: str,
    desktop_pubkey_der: bytes,
    android_pubkey_der: bytes,
    tls_spki_fingerprint: str,
    nonce_desktop: bytes,
    nonce_android: bytes,
    expiry: int,
) -> bytes:
    return canonical_concat(
        encode_uint16_be(protocol_version),
        encode_utf8(session_id),
        encode_utf8(desktop_uuid),
        encode_utf8(android_uuid),
        desktop_pubkey_der,
        android_pubkey_der,
        encode_utf8(tls_spki_fingerprint),
        nonce_desktop,
        nonce_android,
        encode_uint64_be(expiry),
    )


def format_sas(sas: str) -> str:
    return f"{sas[:3]} {sas[3:]}"


# ---- Challenge / Response ----


def generate_challenge(rng: RandomSource = None) -> bytes:
    return (rng or SecureRandom()).bytes(32)


# ---- Replay Cache ----


class ReplayCache:
    def __init__(self, ttl_seconds: float = 300.0, clock: Clock = None):
        self._cache: Dict[str, float] = {}
        self._ttl = ttl_seconds
        self._clock = clock or SystemClock()

    def check_and_record(self, nonce_hex: str) -> bool:
        now = self._clock.now()
        expired = [k for k, v in self._cache.items() if now - v > self._ttl]
        for k in expired:
            del self._cache[k]
        if nonce_hex in self._cache:
            return False
        self._cache[nonce_hex] = now
        return True

    def clear(self) -> None:
        self._cache.clear()

    def size(self) -> int:
        self.check_and_record("__sweep__")
        return len(self._cache)


# ---- ECDSA P-256 ----


def generate_ecdsa_p256_keypair() -> Tuple[bytes, bytes]:
    """Returns (private_key_pem, public_key_pem)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization as _ser

    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(
        encoding=_ser.Encoding.PEM,
        format=_ser.PrivateFormat.PKCS8,
        encryption_algorithm=_ser.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=_ser.Encoding.PEM,
        format=_ser.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


def sign_challenge(private_key_pem: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes, serialization as _ser

    priv = _ser.load_pem_private_key(private_key_pem, password=None)
    assert isinstance(priv, ec.EllipticCurvePrivateKey)
    return priv.sign(data, ec.ECDSA(hashes.SHA256()))


def verify_signature(public_key_pem: bytes, data: bytes, signature: bytes) -> bool:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes, serialization as _ser

    try:
        pub = _ser.load_pem_public_key(public_key_pem)
        assert isinstance(pub, ec.EllipticCurvePublicKey)
        pub.verify(signature, data, ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False


def public_key_to_der(public_key_pem: bytes) -> bytes:
    from cryptography.hazmat.primitives import serialization as _ser
    pub = _ser.load_pem_public_key(public_key_pem)
    return pub.public_bytes(
        encoding=_ser.Encoding.DER,
        format=_ser.PublicFormat.SubjectPublicKeyInfo,
    )
