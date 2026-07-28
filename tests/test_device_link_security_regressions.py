"""Regression tests for adversarial Device Link invariants."""

import pytest

from agentguard.device_link.crypto import FakeClock, ReplayCache, random_token
from agentguard.device_link.gateway import DeviceLinkGateway, GatewayConfig
from agentguard.device_link.crypto import generate_ecdsa_p256_keypair, public_key_to_der
from agentguard.device_link.pairing import PairingManager, PairingSession, PairState


def _gateway(config=None):
    private, public = generate_ecdsa_p256_keypair()
    return DeviceLinkGateway("desktop", public_key_to_der(public), private, config=config)


def _complete(gateway, device_uuid):
    session_id = gateway.pair_start()["session_id"]
    gateway.pair_first_connection(session_id, device_uuid, "aa" * 32)
    private, public = generate_ecdsa_p256_keypair()
    public_der = public_key_to_der(public)
    assert "sas" in gateway.pair_start_sas(session_id, public_der.hex())
    assert gateway.pair_confirm(session_id, True)["state"] == "confirmed_both"
    return gateway.pair_complete(session_id, device_uuid, public_der.hex(), device_uuid)


def test_token_is_32_bytes():
    assert len(bytes.fromhex(random_token())) == 32


def test_session_token_uses_monotonic_exact_ttl(monkeypatch):
    gateway = _gateway(GatewayConfig(session_ttl=10))
    clock = FakeClock(100)
    monkeypatch.setattr("agentguard.device_link.gateway._time.monotonic", clock.now)
    result = _complete(gateway, "android-a")
    token = result["session_token"]

    clock.advance(9.999)
    assert gateway.validate_token(token) == "android-a"
    clock.advance(0.001)
    assert gateway.validate_token(token) is None


def test_reject_clears_pairing_secret_and_is_terminal():
    session = PairingSession("sid", "desktop", b"pk", "fp")
    session.reject()
    assert session.pairing_secret == b""
    with pytest.raises(ValueError):
        session.cancel()


def test_single_device_policy_rejects_second_binding():
    gateway = _gateway(GatewayConfig(single_device=True))
    assert _complete(gateway, "android-a")["status"] == "bound"
    result = _complete(gateway, "android-b")
    assert result["code"] == 409


def test_duplicate_uuid_cannot_replace_existing_key():
    gateway = _gateway()
    assert _complete(gateway, "android-a")["status"] == "bound"
    original_fingerprint = gateway.devices.get("android-a")["fingerprint"]
    result = _complete(gateway, "android-a")
    assert result["code"] == 409
    assert gateway.devices.get("android-a")["fingerprint"] == original_fingerprint


def test_pairing_rejects_non_p256_der_key():
    gateway = _gateway()
    session_id = gateway.pair_start()["session_id"]
    assert "state" in gateway.pair_first_connection(session_id, "android-a", "aa" * 32)
    result = gateway.pair_start_sas(session_id, b"not-a-der-key".hex())
    assert result["code"] == 400
    assert "P-256" in result["error"]


def test_replay_expires_at_ttl_boundary():
    clock = FakeClock(0)
    cache = ReplayCache(ttl_seconds=300, clock=clock)
    assert cache.check_and_record("nonce")
    clock.advance(300)
    assert cache.check_and_record("nonce")
    assert cache.size() == 1


def test_expired_session_cannot_be_reactivated_and_keeps_secret_cleared():
    clock = FakeClock(0)
    session = PairingSession("sid", "desktop", b"pk", "fp", expiry_seconds=10, clock=clock)
    clock.advance(10)

    with pytest.raises(ValueError, match="expired"):
        session.set_state(PairState.FIRST_CONNECTION)

    assert session.state == PairState.EXPIRED
    assert session.pairing_secret == b""
    clock.advance(0.001)
    with pytest.raises(ValueError, match="Terminal state expired"):
        session.set_state(PairState.FIRST_CONNECTION)
    assert session.state == PairState.EXPIRED
    assert session.pairing_secret == b""


@pytest.mark.parametrize("action", ["reject", "cancel", "fail"])
def test_expiry_wins_over_terminal_actions(action):
    clock = FakeClock(0)
    session = PairingSession(
        "sid", "desktop", b"pk", "fp", expiry_seconds=10,
        max_sas_attempts=1, clock=clock,
    )
    if action == "fail":
        session.first_connection("android", b"n" * 32)
        session.set_android_pubkey(b"p" * 32)
        session.start_sas()
    clock.advance(10)

    with pytest.raises(ValueError, match="expired"):
        getattr(session, action)()

    assert session.state == PairState.EXPIRED
    assert session.pairing_secret == b""


@pytest.mark.parametrize(
    "terminal",
    [PairState.REJECTED, PairState.CANCELLED, PairState.FAILED, PairState.CONSUMED],
)
def test_terminal_sessions_cannot_transition_or_restore_secret(terminal):
    session = PairingSession("sid", "desktop", b"pk", "fp", max_sas_attempts=1)
    if terminal == PairState.REJECTED:
        session.reject()
    elif terminal == PairState.CANCELLED:
        session.cancel()
    else:
        session.first_connection("android", b"n" * 32)
        session.set_android_pubkey(b"p" * 32)
        session.start_sas()
        if terminal == PairState.FAILED:
            session.fail()
        else:
            session.confirm()
            session.consume()

    assert session.state == terminal
    assert session.pairing_secret == b""
    with pytest.raises(ValueError, match="Terminal state"):
        session.set_state(PairState.FIRST_CONNECTION)
    assert session.state == terminal
    assert session.pairing_secret == b""


def _confirmed_session_with_clock(gateway, clock, expiry_seconds=10):
    session_id = gateway.pair_start()["session_id"]
    session = gateway.pairing_mgr.get(session_id)
    session._clock = clock
    session._created_at = clock.now()
    session.expiry_seconds = expiry_seconds
    session._expiry_abs = clock.now() + expiry_seconds
    device_private, device_public = generate_ecdsa_p256_keypair()
    public_der = public_key_to_der(device_public)
    assert gateway.pair_first_connection(session_id, "android-a", "aa" * 32)["state"] == "first_connection"
    assert "sas" in gateway.pair_start_sas(session_id, public_der.hex())
    assert gateway.pair_confirm(session_id, True)["state"] == "confirmed_both"
    return session_id, session, public_der


def test_pair_complete_expiry_has_no_binding_side_effect(monkeypatch):
    gateway = _gateway(GatewayConfig(pair_ttl=10))
    clock = FakeClock(0)
    session_id, session, public_der = _confirmed_session_with_clock(gateway, clock)
    add_calls = []
    original_add = gateway.devices.add

    def record_add(*args, **kwargs):
        add_calls.append(args)
        return original_add(*args, **kwargs)

    monkeypatch.setattr(gateway.devices, "add", record_add)
    clock.advance(10)
    result = gateway.pair_complete(session_id, "android-a", public_der.hex(), "Android")

    assert result["code"] == 409
    assert "expired" in result["error"].lower()
    assert add_calls == []
    assert gateway.devices.get("android-a") is None
    assert session.state == PairState.EXPIRED
    assert session.pairing_secret == b""
    assert "session_token" not in result


def test_pair_complete_expiry_between_validation_and_binding_has_no_side_effect(monkeypatch):
    gateway = _gateway(GatewayConfig(pair_ttl=10))
    clock = FakeClock(0)
    session_id, session, public_der = _confirmed_session_with_clock(gateway, clock)
    clock.advance(9.999)
    add_calls = []
    original_add = gateway.devices.add

    def record_add(*args, **kwargs):
        add_calls.append(args)
        return original_add(*args, **kwargs)

    monkeypatch.setattr(gateway.devices, "add", record_add)
    import agentguard.device_link.gateway as gateway_module
    original_validator = gateway_module.validate_ecdsa_p256_public_key_der

    def advance_after_validation(value):
        result = original_validator(value)
        if value == public_der:
            clock.advance(0.001)
        return result

    monkeypatch.setattr(gateway_module, "validate_ecdsa_p256_public_key_der", advance_after_validation)
    result = gateway.pair_complete(session_id, "android-a", public_der.hex(), "Android")

    assert result["code"] == 409
    assert "expired" in result["error"].lower()
    assert add_calls == []
    assert gateway.devices.get("android-a") is None
    assert session.state == PairState.EXPIRED


def test_pair_complete_success_binds_once_and_consumes_session(monkeypatch):
    gateway = _gateway(GatewayConfig(pair_ttl=10))
    clock = FakeClock(0)
    session_id, session, public_der = _confirmed_session_with_clock(gateway, clock)
    add_calls = []
    original_add = gateway.devices.add

    def record_add(*args, **kwargs):
        add_calls.append(args)
        return original_add(*args, **kwargs)

    monkeypatch.setattr(gateway.devices, "add", record_add)
    result = gateway.pair_complete(session_id, "android-a", public_der.hex(), "Android")
    replay = gateway.pair_complete(session_id, "android-a", public_der.hex(), "Android")

    assert result["status"] == "bound"
    assert session.state == PairState.CONSUMED
    assert len(add_calls) == 1
    assert gateway.devices.get("android-a") is not None
    assert replay["code"] == 409
    assert len(add_calls) == 1


def test_pair_ttl_reaches_session_and_expired_sessions_free_capacity():
    gateway = _gateway(GatewayConfig(pair_ttl=7))
    started = gateway.pair_start()
    session = gateway.pairing_mgr.get(started["session_id"])
    assert started["expires_in_s"] == 7
    assert session.expiry_seconds == 7

    clock = FakeClock(0)
    manager = PairingManager(max_sessions=1)
    manager.create_session("desktop", b"pk", "fp", expiry_seconds=7, clock=clock)
    clock.advance(7)
    replacement = manager.create_session("desktop", b"pk", "fp", expiry_seconds=7, clock=clock)
    assert replacement.state == PairState.CREATED
    assert manager.active_sessions() == 1
