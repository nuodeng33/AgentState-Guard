"""Device Link cryptographic and state-machine security tests.

Production HTTP-path coverage is in ``test_device_link_fastapi.py``.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from agentguard.device_link.crypto import (
    DeterministicRandom,
    FakeClock,
    ReplayCache,
    build_pairing_transcript,
    derive_sas,
    generate_ecdsa_p256_keypair,
    public_key_to_der,
    sign_challenge,
    spki_fingerprint,
    verify_signature,
)
from agentguard.device_link.gateway import DeviceLinkGateway
from agentguard.device_link.pairing import (
    ALLOWED_TRANSITIONS,
    PairingSession,
    PairState,
)
from tests.reference_crypto import (
    TRANSCRIPT_A_PARAMS,
    TRANSCRIPT_SECRET,
    ref_build_pairing_transcript,
    ref_derive_sas,
    ref_spki_fingerprint,
)


class TestECDSADERContract:
    def test_der_p256_roundtrip(self):
        private_key, public_key = generate_ecdsa_p256_keypair()
        public_der = public_key_to_der(public_key)
        message = b"challenge-data-for-signing"
        signature = sign_challenge(private_key, message)

        assert public_der[0] == 0x30
        assert signature[0] == 0x30
        assert verify_signature(public_der, message, signature) is True

    def test_pem_is_not_auto_detected(self):
        private_key, public_pem = generate_ecdsa_p256_keypair()
        signature = sign_challenge(private_key, b"message")
        assert verify_signature(public_pem, b"message", signature) is False

    def test_wrong_key_and_modified_message_are_rejected(self):
        private_one, public_one = generate_ecdsa_p256_keypair()
        _, public_two = generate_ecdsa_p256_keypair()
        signature = sign_challenge(private_one, b"original")

        assert not verify_signature(
            public_key_to_der(public_two),
            b"original",
            signature,
        )
        assert not verify_signature(
            public_key_to_der(public_one),
            b"modified",
            signature,
        )

    @pytest.mark.parametrize("key_kind", ["rsa", "p384"])
    def test_non_p256_der_is_rejected(self, key_kind):
        if key_kind == "rsa":
            public_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
            ).public_key()
        else:
            public_key = ec.generate_private_key(ec.SECP384R1()).public_key()
        public_der = public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        assert verify_signature(public_der, b"message", b"signature") is False

    def test_fingerprint_matches_independent_reference(self):
        _, public_key = generate_ecdsa_p256_keypair()
        public_der = public_key_to_der(public_key)
        assert spki_fingerprint(public_der) == ref_spki_fingerprint(public_der)


class TestTimeAndReplayBoundaries:
    @pytest.mark.parametrize(
        ("age", "expired"),
        [(119.999, False), (120.0, True), (120.001, True)],
    )
    def test_pairing_expiry_boundary(self, age, expired):
        clock = FakeClock(0)
        session = PairingSession(
            "s",
            "d",
            b"p",
            "f",
            expiry_seconds=120,
            clock=clock,
            rng=DeterministicRandom(42),
        )
        clock.advance(age)
        assert session.is_expired is expired

    def test_replay_cache_boundary_and_replay(self):
        clock = FakeClock(0)
        cache = ReplayCache(ttl_seconds=10, clock=clock)
        assert cache.check_and_record("nonce")
        assert not cache.check_and_record("nonce")
        clock.advance(10)
        assert cache.check_and_record("nonce")


def _terminal_session(terminal: PairState) -> PairingSession:
    clock = FakeClock(0)
    session = PairingSession(
        "s",
        "d",
        b"p",
        "f",
        max_sas_attempts=1,
        clock=clock,
        rng=DeterministicRandom(42),
    )
    if terminal == PairState.EXPIRED:
        clock.advance(120)
        assert session.expire_if_needed()
    elif terminal == PairState.REJECTED:
        session.reject()
    elif terminal == PairState.CANCELLED:
        session.cancel()
    else:
        session.set_state(PairState.FIRST_CONNECTION)
        session.set_state(PairState.SAS_PENDING)
        if terminal == PairState.FAILED:
            session.fail()
        else:
            session.set_state(PairState.CONFIRMED_BOTH)
            session.consume()
    return session


class TestTransitionMatrix:
    def test_matrix_is_total(self):
        states = list(PairState)
        checked = sum(
            1
            for source in states
            for target in states
            if isinstance(target in ALLOWED_TRANSITIONS[source], bool)
        )
        assert checked == len(states) ** 2

    @pytest.mark.parametrize("terminal", list({
        PairState.CONSUMED,
        PairState.EXPIRED,
        PairState.REJECTED,
        PairState.FAILED,
        PairState.CANCELLED,
    }))
    def test_terminal_state_rejects_all_later_events(self, terminal):
        session = _terminal_session(terminal)
        assert session.state == terminal
        assert session.is_terminal

        for target in PairState:
            with pytest.raises(ValueError):
                session.set_state(target)
        with pytest.raises(ValueError):
            session.reject()
        with pytest.raises(ValueError):
            session.cancel()
        with pytest.raises(ValueError):
            session.fail()


class TestIndependentVectorsAndLeaks:
    def test_pairing_transcript_and_sas_match_reference(self):
        production = build_pairing_transcript(**TRANSCRIPT_A_PARAMS)
        reference = ref_build_pairing_transcript(**TRANSCRIPT_A_PARAMS)
        assert production == reference
        assert derive_sas(
            TRANSCRIPT_SECRET,
            production,
        ) == ref_derive_sas(TRANSCRIPT_SECRET, reference)

    def test_gateway_does_not_print_secret_material(self):
        private_key, public_key = generate_ecdsa_p256_keypair()
        gateway = DeviceLinkGateway(
            "desktop",
            public_key_to_der(public_key),
            private_key,
            "fingerprint",
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            response = gateway.pair_start()
        output = stdout.getvalue() + stderr.getvalue()
        assert "pairing_secret" not in output
        assert "private_key" not in output
        assert "session_id" in response
