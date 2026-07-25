"""Reference Crypto Implementation — independent of agentguard.device_link.crypto.

Uses ONLY stdlib hashlib + hmac + struct. No agentguard imports.
This is the TRUTH oracle against which production crypto.py is verified.
"""

import hashlib
import hmac
import struct


def ref_encode_uint16_be(value: int) -> bytes:
    return struct.pack(">H", value)


def ref_encode_uint64_be(value: int) -> bytes:
    return struct.pack(">Q", value)


def ref_encode_utf8(text: str) -> bytes:
    return text.encode("utf-8")


def ref_encode_hex(hex_string: str) -> bytes:
    return bytes.fromhex(hex_string)


def ref_canonical_concat(*parts: bytes) -> bytes:
    return b"".join(parts)


def ref_build_pairing_transcript(
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
    """Build canonical pairing transcript from spec.
    Order defined in ASDL/1 protocol:
    uint16_be(protocol) + utf8(sid) + utf8(d_uuid) + utf8(a_uuid) +
    d_pubkey + a_pubkey + utf8(tls_fp) + n_desk + n_andr + uint64_be(expiry)
    """
    return ref_canonical_concat(
        ref_encode_uint16_be(protocol_version),
        ref_encode_utf8(session_id),
        ref_encode_utf8(desktop_uuid),
        ref_encode_utf8(android_uuid),
        desktop_pubkey_der,
        android_pubkey_der,
        ref_encode_utf8(tls_spki_fingerprint),
        nonce_desktop,
        nonce_android,
        ref_encode_uint64_be(expiry),
    )


def ref_derive_sas(pairing_secret: bytes, canonical_transcript: bytes) -> str:
    """Derive 6-digit SAS: HMAC-SHA256(secret, transcript) → first 3 bytes → mod 1M."""
    raw = hmac.new(pairing_secret, canonical_transcript, hashlib.sha256).digest()
    num = int.from_bytes(raw[:3], "big") % 1_000_000
    return f"{num:06d}"


def ref_spki_fingerprint(public_key_der: bytes) -> str:
    return hashlib.sha256(public_key_der).hexdigest()


# ---- GOLDEN VECTORS ----

# Transcript A: base case
TRANSCRIPT_A_PARAMS = {
    "protocol_version": 1,
    "session_id": "a1b2c3d4e5f6a7b8",
    "desktop_uuid": "d81a3bc2-1111-4aaa-bbbb-222222222222",
    "android_uuid": "e92b4cd3-3333-4ccc-dddd-444444444444",
    "desktop_pubkey_der": b"\x04" + b"d" * 64,
    "android_pubkey_der": b"\x04" + b"a" * 64,
    "tls_spki_fingerprint": "a1b2c3d4e5f6a7b8",
    "nonce_desktop": b"n1" * 8,
    "nonce_android": b"n2" * 8,
    "expiry": 9999999999,
}


def compute_golden_vector_a():
    params = dict(TRANSCRIPT_A_PARAMS)
    _secret = params.pop("pairing_secret")
    t = ref_build_pairing_transcript(**params)
    sas = ref_derive_sas(_secret, t)
    return t, sas


def compute_all_one_field_changes():
    """For every field in TRANSCRIPT_A, change one byte → transcript must change."""
    base = TRANSCRIPT_A_PARAMS
    base_transcript, base_sas = compute_golden_vector_a()

    results = []
    fields_to_mutate = [
        ("protocol_version", 2),
        ("session_id", "b1b2c3d4e5f6a7b8"),
        ("desktop_uuid", "d81a3bc2-2222-4aaa-bbbb-222222222222"),
        ("android_uuid", "e92b4cd3-4444-4ccc-dddd-444444444444"),
        ("desktop_pubkey_der", b"\x04" + b"z" * 64),
        ("android_pubkey_der", b"\x04" + b"z" * 64),
        ("tls_spki_fingerprint", "b1b2c3d4e5f6a7b8"),
        ("nonce_desktop", b"m1" * 8),
        ("nonce_android", b"m2" * 8),
        ("expiry", 8888888888),
    ]

    for field_name, new_value in fields_to_mutate:
        params = dict(base)
        params[field_name] = new_value
        _secret = params.pop("pairing_secret")
        t = ref_build_pairing_transcript(**params)
        sas = ref_derive_sas(_secret, t)
        results.append({
            "field": field_name,
            "transcript_changed": t != base_transcript,
            "sas_changed": sas != base_sas,
        })

    return results

# Shared test secret (not a transcript parameter)
TRANSCRIPT_SECRET = b"x" * 32
