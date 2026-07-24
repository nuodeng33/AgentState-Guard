"""ASDL/1 Cryptographic Primitives — Pure Python, stdlib + hashlib.

Requirements:
- ECDSA P-256 + SHA-256 device identity
- SAS derivation from HMAC-SHA256(secret, canonical_transcript)
- Challenge/response with canonical context binding
- Replay cache with expiry
- Fingerprint = SHA-256 of DER-encoded SPKI

Test vectors are in docs/device-link/CRYPTO_TEST_VECTORS.md.
"""

import hashlib
import hmac
import os
import struct
import time as _time
from typing import Dict, List, Optional, Tuple

# ---- Canonical Serialization ----


def canonical_concat(*parts: bytes) -> bytes:
    """Concatenate pre-serialized canonical parts into one byte sequence."""
    return b"".join(parts)


def encode_uint16_be(value: int) -> bytes:
    return struct.pack(">H", value)


def encode_uint32_be(value: int) -> bytes:
    return struct.pack(">I", value)


def encode_uint64_be(value: int) -> bytes:
    return struct.pack(">Q", value)


def encode_utf8(text: str) -> bytes:
    return text.encode("utf-8")


def encode_hex(hex_string: str) -> bytes:
    return bytes.fromhex(hex_string)


# ---- Random Generators ----


def random_bytes(n: int = 32) -> bytes:
    """CSPRNG random bytes."""
    return os.urandom(n)


def random_hex(n_bytes: int = 16) -> str:
    return os.urandom(n_bytes).hex()


def random_session_id() -> str:
    return os.urandom(16).hex()


# ---- Fingerprint ----


def spki_fingerprint(public_key_der: bytes) -> str:
    """SHA-256 of DER-encoded SubjectPublicKeyInfo."""
    return hashlib.sha256(public_key_der).hexdigest()


def fingerprint_short(fingerprint: str) -> str:
    """First 8 hex chars for human display."""
    return fingerprint[:8]


# ---- SAS Derivation ----


def derive_sas(pairing_secret: bytes, canonical_transcript: bytes) -> str:
    """Derive 6-digit SAS from pairing secret and canonical transcript.

    SAS = to_6digit(HMAC-SHA256(secret, transcript))
    Both sides compute identically; mismatch = tampering or wrong device.
    """
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
    """Build canonical pairing transcript for SAS derivation."""
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


# ---- Challenge Authentication ----


def build_challenge_context(
    protocol_version: int,
    session_id: str,
    client_uuid: str,
    server_uuid: str,
    challenge_nonce: bytes,
    timestamp: int,
    tls_channel_hash: bytes = b"",
) -> bytes:
    """Build canonical challenge context for signing."""
    return canonical_concat(
        encode_uint16_be(protocol_version),
        encode_utf8(session_id),
        encode_utf8(client_uuid),
        encode_utf8(server_uuid),
        challenge_nonce,
        encode_uint64_be(timestamp),
        tls_channel_hash,
    )


def generate_challenge() -> bytes:
    """Generate random 32-byte challenge nonce."""
    return os.urandom(32)


# ---- Replay Cache ----


class ReplayCache:
    """In-memory replay cache with TTL expiry."""

    def __init__(self, ttl_seconds: int = 300):
        self._cache: Dict[str, float] = {}
        self._ttl = ttl_seconds

    def check_and_record(self, nonce_hex: str) -> bool:
        """Return True if nonce is NEW and record it.
        Return False if nonce was already seen (replay).
        """
        now = _time.monotonic()
        # Purge expired
        expired = [k for k, v in self._cache.items() if now - v > self._ttl]
        for k in expired:
            del self._cache[k]

        if nonce_hex in self._cache:
            return False
        self._cache[nonce_hex] = now
        return True

    def clear(self) -> None:
        self._cache.clear()


# ---- ECDSA P-256 Sign/Verify Wrapper ----


def generate_ecdsa_p256_keypair() -> Tuple[bytes, bytes]:
    """Generate ECDSA P-256 keypair.

    Returns (private_key_pem, public_key_pem).
    Uses cryptography library if available, otherwise falls back to
    openssl command or returns placeholder for non-runtime use.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization

        priv = ec.generate_private_key(ec.SECP256R1())
        priv_pem = priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pub_pem = priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return priv_pem, pub_pem
    except ImportError:
        # Standalone mode: return placeholder
        # In production, cryptography is a dependency
        return _generate_openssl_keypair()


def _generate_openssl_keypair() -> Tuple[bytes, bytes]:
    """Fallback: use openssl CLI to generate ECDSA P-256 keypair."""
    import subprocess, tempfile
    with tempfile.TemporaryDirectory(prefix="asdl-key-") as td:
        import pathlib
        key_path = pathlib.Path(td) / "key.pem"
        subprocess.run(
            ["openssl", "ecparam", "-genkey", "-name", "prime256v1",
             "-out", str(key_path), "-noout"],
            capture_output=True, check=True, timeout=10,
        )
        priv_pem = key_path.read_bytes()
        pub = subprocess.run(
            ["openssl", "ec", "-in", str(key_path), "-pubout"],
            capture_output=True, check=True, timeout=10,
        )
        # Delete private key temp file
        key_path.unlink()
        return priv_pem, pub.stdout


def sign_challenge(private_key_pem: bytes, data: bytes) -> bytes:
    """Sign data with ECDSA P-256 private key."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes, serialization

    priv = serialization.load_pem_private_key(private_key_pem, password=None)
    assert isinstance(priv, ec.EllipticCurvePrivateKey)
    sig = priv.sign(data, ec.ECDSA(hashes.SHA256()))
    return sig


def verify_signature(public_key_pem: bytes, data: bytes, signature: bytes) -> bool:
    """Verify ECDSA P-256 signature."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes, serialization

    pub = serialization.load_pem_public_key(public_key_pem)
    assert isinstance(pub, ec.EllipticCurvePublicKey)
    try:
        pub.verify(signature, data, ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False


def public_key_to_der(public_key_pem: bytes) -> bytes:
    """Convert PEM public key to DER-encoded SPKI bytes."""
    from cryptography.hazmat.primitives import serialization
    pub = serialization.load_pem_public_key(public_key_pem)
    return pub.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


# ---- SAS Display Helpers ----


def format_sas(sas: str) -> str:
    """Format SAS with grouping: '482 913'"""
    return f"{sas[:3]} {sas[3:]}"
