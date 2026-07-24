"""Tests for ASDL/1 crypto primitives."""

import pytest
from agentguard.device_link.crypto import (
    canonical_concat, encode_uint16_be, encode_utf8,
    encode_hex, encode_uint64_be, spki_fingerprint,
    derive_sas, build_pairing_transcript, format_sas,
    random_bytes, random_session_id,
    ReplayCache,
)


class TestCryptoSAS:
    def test_sas_derivation_deterministic(self):
        """Same input → same SAS."""
        secret = b"a" * 32
        transcript = b"test-transcript"

        sas1 = derive_sas(secret, transcript)
        sas2 = derive_sas(secret, transcript)
        assert sas1 == sas2
        assert len(sas1) == 6
        assert sas1.isdigit()

    def test_sas_different_secret(self):
        """Different secret → different SAS."""
        transcript = b"test"
        sas1 = derive_sas(b"a" * 32, transcript)
        sas2 = derive_sas(b"b" * 32, transcript)
        assert sas1 != sas2

    def test_sas_different_transcript(self):
        """Different transcript → different SAS."""
        secret = b"a" * 32
        sas1 = derive_sas(secret, b"transcript-1")
        sas2 = derive_sas(secret, b"transcript-2")
        assert sas1 != sas2

    def test_sas_formatting(self):
        formatted = format_sas("123456")
        assert formatted == "123 456"

    def test_build_pairing_transcript(self):
        t = build_pairing_transcript(
            1,
            "sess-001",
            "desktop-uuid-1",
            "android-uuid-1",
            b"desktop-pubkey",
            b"android-pubkey",
            "a1b2c3d4",
            b"n-desk" * 4,
            b"n-andr" * 4,
            9999999999,
        )
        assert len(t) > 0
        # Verify it contains expected substrings
        assert b"desktop-uuid-1" in t
        assert b"android-uuid-1" in t


class TestCryptoSerialization:
    def test_canonical_concat(self):
        result = canonical_concat(b"a", b"b", b"c")
        assert result == b"abc"

    def test_encode_uint16(self):
        assert encode_uint16_be(1) == b"\x00\x01"
        assert encode_uint16_be(256) == b"\x01\x00"

    def test_encode_utf8(self):
        assert encode_utf8("hello") == b"hello"

    def test_encode_hex(self):
        assert encode_hex("deadbeef") == b"\xde\xad\xbe\xef"

    def test_spki_fingerprint_length(self):
        fp = spki_fingerprint(b"fake-public-key-der-bytes-here")
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)


class TestCryptoRandom:
    def test_random_bytes_length(self):
        for n in [16, 32, 64]:
            b = random_bytes(n)
            assert len(b) == n

    def test_random_bytes_unique(self):
        a = random_bytes(32)
        b = random_bytes(32)
        assert a != b

    def test_random_session_id_format(self):
        sid = random_session_id()
        assert len(sid) == 32
        assert all(c in "0123456789abcdef" for c in sid)


class TestReplayCache:
    def test_first_accept(self):
        cache = ReplayCache(ttl_seconds=60)
        assert cache.check_and_record("nonce-1") is True

    def test_replay_reject(self):
        cache = ReplayCache(ttl_seconds=60)
        cache.check_and_record("nonce-1")
        assert cache.check_and_record("nonce-1") is False

    def test_different_nonce_accept(self):
        cache = ReplayCache(ttl_seconds=60)
        cache.check_and_record("nonce-1")
        assert cache.check_and_record("nonce-2") is True

    def test_expiry_cleanup(self):
        cache = ReplayCache(ttl_seconds=0)  # instant expiry
        assert cache.check_and_record("nonce-1") is True
        assert cache.check_and_record("nonce-1") is True  # expired, so different


class TestCryptoTestVectors:
    """Verify the test vectors from CRYPTO_TEST_VECTORS.md produce correct behavior."""

    def test_vector_1_sas_consistency(self):
        """Same transcript → same SAS (deterministic)."""
        secret = bytes.fromhex(
            "deadbeef00000000deadbeef00000000"
            "deadbeef00000000deadbeef00000000"
        )
        # Build a known transcript
        t = build_pairing_transcript(
            1, "sid-001", "d-uuid", "a-uuid",
            b"dpub", b"apub", "a1b2c3d4",
            b"n1" * 8, b"n2" * 8, 9999999999,
        )
        sas1 = derive_sas(secret, t)
        sas2 = derive_sas(secret, t)
        assert sas1 == sas2, "Same transcript must produce same SAS"

    def test_vector_5_single_bit_mismatch(self):
        """1-bit change in transcript → different SAS."""
        secret = bytes.fromhex(
            "deadbeef00000000deadbeef00000000"
            "deadbeef00000000deadbeef00000000"
        )
        t1 = build_pairing_transcript(
            1, "sid-001", "d-uuid", "a-uuid",
            b"dpub", b"apub", "a1b2c3d4",
            b"n1" * 8, b"n2" * 8, 9999999999,
        )
        t2 = build_pairing_transcript(
            1, "sid-002", "d-uuid", "a-uuid",  # different session_id
            b"dpub", b"apub", "a1b2c3d4",
            b"n1" * 8, b"n2" * 8, 9999999999,
        )
        assert derive_sas(secret, t1) != derive_sas(secret, t2)
