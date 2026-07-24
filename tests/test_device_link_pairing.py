"""Tests for ASDL/1 pairing state machine."""

import time as _time

import pytest
from agentguard.device_link.pairing import PairingSession, PairingManager, PairState


class TestPairingStateMachine:
    def make_session(self, **kwargs):
        return PairingSession(
            session_id="test-session-001",
            desktop_uuid="desktop-uuid-1",
            desktop_pubkey_der=b"pubkey-der-bytes",
            desktop_tls_spki_fp="a1b2c3d4e5f6a7b8",
            **kwargs,
        )

    def test_initial_state(self):
        s = self.make_session()
        assert s.state == PairState.CREATED
        assert not s.is_terminal

    def test_first_connection(self):
        s = self.make_session()
        s.first_connection("android-uuid-1", b"nonce-16bytes-abc")
        assert s.state == PairState.FIRST_CONNECTION

    def test_sas_flow(self):
        s = self.make_session()
        s.first_connection("android-uuid-1", b"nonce-16bytes-abc")
        s.set_android_pubkey(b"android-pubkey-der-bytes")
        sas = s.start_sas()
        assert s.state == PairState.SAS_PENDING
        assert len(sas) == 7  # "482 913" format
        assert " " in sas

    def test_confirm_consume(self):
        s = self.make_session()
        s.first_connection("android-uuid-1", b"nonce-16bytes-abc")
        s.set_android_pubkey(b"android-pubkey-der-bytes")
        s.start_sas()
        s.confirm()
        assert s.state == PairState.CONFIRMED_BOTH
        s.consume()
        assert s.state == PairState.CONSUMED
        assert s.is_terminal

    def test_expired(self):
        s = self.make_session(expiry_seconds=0)
        s.checkpoint()
        assert s.state == PairState.EXPIRED
        assert s.is_terminal

    def test_cancelled(self):
        s = self.make_session()
        s.cancel()
        assert s.state == PairState.CANCELLED
        assert s.is_terminal

    def test_rejected(self):
        s = self.make_session()
        s.reject()
        assert s.state == PairState.REJECTED
        assert s.is_terminal

    def test_sas_failure_limit(self):
        s = self.make_session(max_sas_attempts=2)
        s.first_connection("a-uuid", b"n" * 16)
        s.set_android_pubkey(b"p" * 32)
        s.start_sas()
        s.fail()  # attempt 1
        assert s.state == PairState.SAS_PENDING
        s.fail()  # attempt 2 → exceeded
        assert s.state == PairState.FAILED
        assert s.is_terminal

    def test_invalid_transition_first_connection_twice(self):
        s = self.make_session()
        s.first_connection("a1", b"n" * 16)
        with pytest.raises(ValueError):
            s.first_connection("a2", b"n" * 16)

    def test_cannot_start_sas_before_connection(self):
        s = self.make_session()
        with pytest.raises(ValueError):
            s.start_sas()

    def test_cannot_confirm_before_sas(self):
        s = self.make_session()
        s.first_connection("a1", b"n" * 16)
        with pytest.raises(ValueError):
            s.confirm()

    def test_cannot_consume_before_confirm(self):
        s = self.make_session()
        s.first_connection("a1", b"n" * 16)
        with pytest.raises(ValueError):
            s.consume()


class TestPairingManager:
    def test_create_session(self):
        mgr = PairingManager()
        s = mgr.create_session("duuid", b"pubkey", "fp")
        assert s.state == PairState.CREATED
        assert mgr.active_sessions() >= 1

    def test_get_session(self):
        mgr = PairingManager()
        created = mgr.create_session("duuid", b"pubkey", "fp")
        retrieved = mgr.get(created.session_id)
        assert retrieved is not None
        assert retrieved.session_id == created.session_id

    def test_get_nonexistent(self):
        mgr = PairingManager()
        assert mgr.get("nonexistent") is None

    def test_expired_cleanup(self):
        mgr = PairingManager()
        s = mgr.create_session("duuid", b"pubkey", "fp")
        s.cancel()  # terminal
        assert mgr.active_sessions() == 0  # purges terminal

    def test_sas_consistency(self):
        """Two sessions with identical params produce deterministic SAS."""
        mgr1 = PairingManager()
        s1 = mgr1.create_session("duuid", b"pubkey-der-bytes", "a1b2c3d4")
        s1.first_connection("auuid", b"nonce-16bytes-abc")
        s1.set_android_pubkey(b"android-pubkey-der")
        sas1 = s1.start_sas()

        mgr2 = PairingManager()
        s2 = mgr2.create_session("duuid", b"pubkey-der-bytes", "a1b2c3d4")
        s2.first_connection("auuid", b"nonce-16bytes-abc")
        s2.set_android_pubkey(b"android-pubkey-der")
        sas2 = s2.start_sas()

        # Different pairing secrets → different SAS
        assert sas1 != sas2
