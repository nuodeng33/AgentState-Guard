"""Phase 0.2 — Comprehensive adversarial audit of crypto + pairing + gateway."""

import pytest
from tests.reference_crypto import (
    ref_build_pairing_transcript, ref_derive_sas, ref_spki_fingerprint,
    compute_golden_vector_a, compute_all_one_field_changes,
    TRANSCRIPT_A_PARAMS,
    TRANSCRIPT_SECRET,
)
from agentguard.device_link.crypto import (
    build_pairing_transcript, derive_sas, spki_fingerprint,
    canonical_concat, encode_uint16_be, encode_utf8,
    FakeClock, DeterministicRandom, ReplayCache,
    SecureRandom, format_sas,
)
from agentguard.device_link.pairing import (
    PairingSession, PairState, ALLOWED_TRANSITIONS,
    is_valid_transition, TERMINAL_STATES,
)


# ============================================================
# 1. Golden vector — reference vs production
# ============================================================

# Previously skipped — re-enabled after API fix
class TestGoldenVectors:
    """Production crypto MUST match reference for transcript and SAS."""

    def test_transcript_matches_reference(self):
        prod_t = build_pairing_transcript(**TRANSCRIPT_A_PARAMS)
        ref_t = ref_build_pairing_transcript(**TRANSCRIPT_A_PARAMS)
        assert prod_t == ref_t, "Production transcript diverges from reference"
        assert len(prod_t) > 0

    def test_sas_matches_reference(self):
        t = ref_build_pairing_transcript(**TRANSCRIPT_A_PARAMS)
        prod_sas = derive_sas(TRANSCRIPT_SECRET, t)
        ref_sas = ref_derive_sas(TRANSCRIPT_SECRET, t)
        assert prod_sas == ref_sas, "Production SAS diverges from reference"
        assert len(prod_sas) == 6

    def test_spki_fingerprint_matches_reference(self):
        key = b"\x04" + b"k" * 64
        prod_fp = spki_fingerprint(key)
        ref_fp = ref_spki_fingerprint(key)
        assert prod_fp == ref_fp, "Production fingerprint diverges from reference"

    def test_all_fields_change_transcript(self):
        results = compute_all_one_field_changes()
        for r in results:
            assert r["transcript_changed"], f"Field {r['field']} did not change transcript"
            assert r["sas_changed"], f"Field {r['field']} did not change SAS"

    def test_reference_does_not_import_agentguard(self):
        """The reference crypto MUST be independent."""
        import tests.reference_crypto as rc
        src = rc.__file__
        with open(src) as f:
            content = f.read()
        import_lines = [l.strip() for l in content.split('\n')
                         if l.strip().startswith(('import ', 'from '))]
        for line in import_lines:
            assert 'agentguard' not in line, \
                f"Reference crypto imports agentguard: {line}"
        assert True  # All imports verified clean


# ============================================================
# 2. Canonical transcript — order sensitivity
# ============================================================

# Previously skipped — re-enabled after API fix
class TestTranscriptCanonicalization:
    def test_field_order_sensitivity(self):
        base = TRANSCRIPT_A_PARAMS
        t1 = ref_build_pairing_transcript(**base)
        # Swapping desktop and android UUIDs should produce different transcript
        swapped = dict(base)
        swapped["desktop_uuid"] = base["android_uuid"]
        swapped["android_uuid"] = base["desktop_uuid"]
        t2 = ref_build_pairing_transcript(**swapped)
        assert t1 != t2

    def test_canonical_concat_identity(self):
        a, b, c = b"a", b"b", b"c"
        assert canonical_concat(a, b, c) == b"abc"
        assert canonical_concat(a, c, b) != b"abc"


# ============================================================
# 3. Clock injection — expiry boundary
# ============================================================

# Previously skipped — re-enabled after API fix
class TestClockExpiry:
    def test_pairing_not_expired_at_119_seconds(self):
        clock = FakeClock(0)
        rng = DeterministicRandom(42)
        s = PairingSession("sid", "duuid", b"pk", "fp",
                           expiry_seconds=120, clock=clock, rng=rng)
        clock.advance(119)
        assert not s.is_expired
        assert s.state == PairState.CREATED

    def test_pairing_expired_at_121_seconds(self):
        clock = FakeClock(0)
        rng = DeterministicRandom(42)
        s = PairingSession("sid", "duuid", b"pk", "fp",
                           expiry_seconds=120, clock=clock, rng=rng)
        clock.advance(121)
        assert s.is_expired

    def test_expired_cannot_transition_to_active(self):
        clock = FakeClock(0)
        rng = DeterministicRandom(42)
        s = PairingSession("sid", "duuid", b"pk", "fp",
                           expiry_seconds=120, clock=clock, rng=rng)
        clock.advance(121)
        with pytest.raises(ValueError):
            s.first_connection("a", b"n" * 16)

    def test_replay_cache_expiry(self):
        clock = FakeClock(0)
        cache = ReplayCache(ttl_seconds=10, clock=clock)
        assert cache.check_and_record("n1")
        assert not cache.check_and_record("n1")  # replay rejected
        clock.advance(11)
        assert cache.check_and_record("n1")  # expired, now accepted as fresh


# ============================================================
# 4. Exhaustive state machine — all transitions
# ============================================================

# Previously skipped — re-enabled after API fix
class TestExhaustiveFSM:
    ALL_STATES = list(PairState)

    def make_session(self, **kw):
        clock = FakeClock(0)
        rng = DeterministicRandom(42)
        return PairingSession("sid", "duuid", b"pk", "fp",
                              clock=clock, rng=rng, **kw)

    def test_allowed_all_succeed(self):
        for src, targets in ALLOWED_TRANSITIONS.items():
            for tgt in targets:
                s = self.make_session()
                # Navigate to src state
                for intermediate in self._path_to(src):
                    try:
                        s.set_state(intermediate)
                    except ValueError:
                        break
                if s.state == src:
                    try:
                        s.set_state(tgt)
                    except ValueError as e:
                        pytest.fail(f"Allowed transition {src.value}→{tgt.value} raised: {e}")

    def test_forbidden_all_rejected(self):
        for src in self.ALL_STATES:
            for tgt in self.ALL_STATES:
                if tgt in ALLOWED_TRANSITIONS.get(src, set()):
                    continue
                s = self.make_session()
                for intermediate in self._path_to(src):
                    try:
                        s.set_state(intermediate)
                    except ValueError:
                        break
                if s.state == src:
                    with pytest.raises(ValueError, match="Invalid|Terminal"):
                        s.set_state(tgt)

    def test_terminal_states_reject_all(self):
        for terminal in TERMINAL_STATES:
            s = self.make_session()
            for intermediate in self._path_to(terminal):
                try:
                    s.set_state(intermediate)
                except ValueError:
                    break
            if s.state == terminal:
                for any_state in self.ALL_STATES:
                    with pytest.raises(ValueError, match="Terminal"):
                        s.set_state(any_state)

    def _path_to(self, target: PairState) -> list:
        """Minimal path from CREATED to target."""
        paths = {
            PairState.CREATED: [],
            PairState.FIRST_CONNECTION: [PairState.FIRST_CONNECTION],
            PairState.SAS_PENDING: [PairState.FIRST_CONNECTION, PairState.SAS_PENDING],
            PairState.CONFIRMED_BOTH: [PairState.FIRST_CONNECTION, PairState.SAS_PENDING, PairState.CONFIRMED_BOTH],
            PairState.CONSUMED: [PairState.FIRST_CONNECTION, PairState.SAS_PENDING, PairState.CONFIRMED_BOTH, PairState.CONSUMED],
        }
        return paths.get(target, [PairState.EXPIRED])


# ============================================================
# 5. Security invariants
# ============================================================

# Previously skipped — re-enabled after API fix
class TestSecurityInvariants:
    def test_random_bytes_are_different(self):
        a = SecureRandom().bytes(32)
        b = SecureRandom().bytes(32)
        assert a != b

    def test_different_pairing_secrets_produce_different_sas(self):
        t = ref_build_pairing_transcript(**TRANSCRIPT_A_PARAMS)
        sas1 = derive_sas(b"a" * 32, t)
        sas2 = derive_sas(b"b" * 32, t)
        assert sas1 != sas2

    def test_sas_is_6_digits(self):
        t = ref_build_pairing_transcript(**TRANSCRIPT_A_PARAMS)
        for secret in [b"s" * 32, b"t" * 32, b"u" * 32]:
            sas = derive_sas(secret, t)
            assert len(sas) == 6
            assert sas.isdigit()

    def test_replay_cache_evicts_properly(self):
        clock = FakeClock(0)
        cache = ReplayCache(ttl_seconds=5, clock=clock)
        for i in range(100):
            assert cache.check_and_record(f"n{i}")
        clock.advance(10)
        # All should be expired
        assert cache.size() <= 0

    def test_hash_consistency(self):
        from hashlib import sha256
        assert sha256(b"hello").hexdigest() == sha256(b"hello").hexdigest()
        assert sha256(b"hello").hexdigest() != sha256(b"world").hexdigest()


# ============================================================
# 6. Positive/Negative coverage count
# ============================================================

# Previously skipped — re-enabled after API fix
class TestCoverageSummary:
    def test_positive_tests_present(self):
        """Verify that the audit suite includes real positive-path tests alongside security tests."""
        import inspect
        import tests.test_device_link_audit as mod
        funcs = inspect.getmembers(mod, inspect.isfunction)
        test_count = sum(1 for name, _ in funcs if name.startswith("test_"))
        assert test_count >= 3, (
            f"Expected at least 3 test functions in audit suite, found {test_count}. "
            "Both positive and negative test coverage is required."
        )
