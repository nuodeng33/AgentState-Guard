"""P0-A security tests against the production FastAPI Device Link router."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from agentguard.api.server import create_app, run_server
from agentguard.device_link.crypto import (
    DeterministicRandom,
    FakeClock,
    generate_ecdsa_p256_keypair,
    public_key_to_der,
    sign_challenge,
)
from agentguard.device_link.errors import DeviceLinkError
from agentguard.device_link.gateway import (
    DeviceLinkGateway,
    GatewayConfig,
    build_auth_message,
)


def _gateway(
    *,
    clock: FakeClock | None = None,
    max_pair_sessions: int = 5,
    pair_ttl: int = 120,
    challenge_ttl: int = 60,
) -> DeviceLinkGateway:
    desktop_priv, desktop_pub = generate_ecdsa_p256_keypair()
    return DeviceLinkGateway(
        desktop_uuid="desktop-test-001",
        desktop_device_pubkey_der=public_key_to_der(desktop_pub),
        desktop_device_privkey_pem=desktop_priv,
        desktop_tls_spki_fp="test-fingerprint",
        config=GatewayConfig(
            max_pair_sessions=max_pair_sessions,
            pair_ttl=pair_ttl,
            session_ttl=60,
            challenge_ttl=challenge_ttl,
            max_auth_attempts=3,
        ),
        clock=clock or FakeClock(100),
        rng=DeterministicRandom(7),
    )


def _client(gateway: DeviceLinkGateway, peer: str = "127.0.0.1") -> TestClient:
    app = create_app(
        config={"base_dir": "."},
        device_link_gateway=gateway,
    )
    return TestClient(
        app,
        client=(peer, 50000),
        raise_server_exceptions=False,
    )


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def _pair_through_confirmation(client: TestClient, device_uuid: str = "android-001"):
    start = client.post("/device/v1/pair/start")
    assert start.status_code == 200
    sid = start.json()["session_id"]

    connect = client.post(
        f"/device/v1/pair/{sid}/connect",
        json={"android_uuid": device_uuid, "nonce": "ab" * 32},
    )
    assert connect.status_code == 200

    private_key, public_key = generate_ecdsa_p256_keypair()
    public_der = public_key_to_der(public_key)
    sas = client.post(
        f"/device/v1/pair/{sid}/sas",
        json={"android_pubkey_der_hex": public_der.hex()},
    )
    assert sas.status_code == 200
    confirm = client.post(
        f"/device/v1/pair/{sid}/confirm",
        json={"confirm": True},
    )
    assert confirm.status_code == 200
    return sid, private_key, public_der


def _pair_device(client: TestClient, device_uuid: str = "android-001"):
    sid, private_key, public_der = _pair_through_confirmation(client, device_uuid)
    complete = client.post(
        f"/device/v1/pair/{sid}/complete",
        json={
            "android_uuid": device_uuid,
            "android_pubkey_der_hex": public_der.hex(),
            "display_name": "Test Android",
            "protocol_version": 1,
        },
    )
    assert complete.status_code == 200
    return sid, private_key, public_der, complete.json()["session_token"]


def test_real_http_status_and_strict_request_validation():
    gateway = _gateway()
    with _client(gateway) as client:
        sid = client.post("/device/v1/pair/start").json()["session_id"]

        wrong_type = client.post(
            f"/device/v1/pair/{sid}/connect",
            json={"android_uuid": "android-001", "nonce": 42},
        )
        assert wrong_type.status_code == 400
        assert _error_code(wrong_type) == "DEVICE_INVALID_REQUEST"

        wrong_nonce_length = client.post(
            f"/device/v1/pair/{sid}/connect",
            json={"android_uuid": "android-001", "nonce": "aa"},
        )
        assert wrong_nonce_length.status_code == 400
        assert _error_code(wrong_nonce_length) == "DEVICE_INVALID_REQUEST"

        unknown_field = client.post(
            f"/device/v1/pair/{sid}/connect",
            json={
                "android_uuid": "android-001",
                "nonce": "ab" * 32,
                "ignored": True,
            },
        )
        assert unknown_field.status_code == 400
        assert _error_code(unknown_field) == "DEVICE_INVALID_REQUEST"

        whitespace_hex = client.post(
            f"/device/v1/pair/{sid}/connect",
            json={
                "android_uuid": "android-001",
                "nonce": "aa" * 16 + "  " + "aa" * 16,
            },
        )
        assert whitespace_hex.status_code == 400
        assert _error_code(whitespace_hex) == "DEVICE_INVALID_REQUEST"


def test_pair_start_rejects_unknown_body_fields():
    with _client(_gateway()) as client:
        response = client.post(
            "/device/v1/pair/start",
            json={"ignored": True},
        )
        assert response.status_code == 400
        assert _error_code(response) == "DEVICE_INVALID_REQUEST"


def test_pairing_expiry_boundary_is_410():
    clock = FakeClock(100)
    gateway = _gateway(clock=clock, pair_ttl=5)
    with _client(gateway) as client:
        session_id = client.post(
            "/device/v1/pair/start",
        ).json()["session_id"]
        clock.advance(5)
        response = client.get(f"/device/v1/pair/{session_id}")
        assert response.status_code == 410
        assert _error_code(response) == "PAIR_SESSION_EXPIRED"


def test_capacity_is_429_and_has_no_hidden_session():
    gateway = _gateway(max_pair_sessions=5)
    with _client(gateway) as client:
        for _ in range(5):
            assert client.post("/device/v1/pair/start").status_code == 200

        sixth = client.post("/device/v1/pair/start")
        assert sixth.status_code == 429
        assert _error_code(sixth) == "PAIR_CAPACITY_EXCEEDED"
        assert gateway.pairing_mgr.active_sessions() == 5


def test_pair_complete_rejects_identity_substitution_without_side_effects():
    gateway = _gateway()
    with _client(gateway) as client:
        sid, _, public_der = _pair_through_confirmation(client)
        substituted = client.post(
            f"/device/v1/pair/{sid}/complete",
            json={
                "android_uuid": "attacker-001",
                "android_pubkey_der_hex": public_der.hex(),
                "display_name": "Attacker",
                "protocol_version": 1,
            },
        )

        assert substituted.status_code == 409
        assert _error_code(substituted) == "PAIR_IDENTITY_MISMATCH"
        assert gateway.devices.list() == []
        assert gateway.pairing_mgr.get(sid).state.value == "confirmed_both"


def test_pair_complete_maps_invalid_der_to_400_before_identity_comparison():
    gateway = _gateway()
    with _client(gateway) as client:
        sid, _, _ = _pair_through_confirmation(client)
        response = client.post(
            f"/device/v1/pair/{sid}/complete",
            json={
                "android_uuid": "android-001",
                "android_pubkey_der_hex": "00",
                "display_name": "Invalid Key",
                "protocol_version": 1,
            },
        )
        assert response.status_code == 400
        assert _error_code(response) == "DEVICE_INVALID_REQUEST"
        assert gateway.devices.list() == []
        assert gateway.pairing_mgr.get(sid).state.value == "confirmed_both"


def test_pair_complete_maps_valid_substituted_p256_key_to_409():
    gateway = _gateway()
    with _client(gateway) as client:
        sid, _, _ = _pair_through_confirmation(client)
        _, substituted_public = generate_ecdsa_p256_keypair()
        response = client.post(
            f"/device/v1/pair/{sid}/complete",
            json={
                "android_uuid": "android-001",
                "android_pubkey_der_hex": public_key_to_der(
                    substituted_public
                ).hex(),
                "display_name": "Substituted Key",
                "protocol_version": 1,
            },
        )
        assert response.status_code == 409
        assert _error_code(response) == "PAIR_IDENTITY_MISMATCH"
        assert gateway.devices.list() == []
        assert gateway.pairing_mgr.get(sid).state.value == "confirmed_both"


@pytest.mark.parametrize("wrong_version", [True, 1.0])
def test_protocol_version_rejects_bool_and_float(wrong_version):
    gateway = _gateway()
    with _client(gateway) as client:
        sid, _, public_der = _pair_through_confirmation(client)
        response = client.post(
            f"/device/v1/pair/{sid}/complete",
            json={
                "android_uuid": "android-001",
                "android_pubkey_der_hex": public_der.hex(),
                "display_name": "Test Android",
                "protocol_version": wrong_version,
            },
        )
        assert response.status_code == 400
        assert _error_code(response) == "DEVICE_INVALID_REQUEST"
        assert gateway.devices.list() == []


def test_terminal_pairing_state_cannot_be_overwritten():
    gateway = _gateway()
    with _client(gateway) as client:
        sid, _, _, _ = _pair_device(client)
        rejected = client.post(
            f"/device/v1/pair/{sid}/confirm",
            json={"confirm": False},
        )
        assert rejected.status_code == 409
        assert _error_code(rejected) == "PAIR_STATE_CONFLICT"

        poll = client.get(f"/device/v1/pair/{sid}")
        assert poll.status_code == 200
        assert poll.json()["state"] == "consumed"


def test_protected_reads_require_the_device_session_token():
    gateway = _gateway()
    with _client(gateway) as client:
        assert client.get("/device/v1/status").status_code == 401
        assert client.get("/device/v1/checkpoints").status_code == 401

        _, _, _, token = _pair_device(client)
        headers = {"X-Session-Token": token}
        assert client.get("/device/v1/status", headers=headers).status_code == 200
        assert client.get("/device/v1/checkpoints", headers=headers).status_code == 200


def test_server_issued_challenge_is_bound_one_time_and_replaces_old_token():
    gateway = _gateway()
    with _client(gateway) as client:
        _, private_key, _, old_token = _pair_device(client)
        issued = client.post(
            "/device/v1/auth/challenge",
            json={"device_uuid": "android-001", "protocol_version": 1},
        )
        assert issued.status_code == 200
        challenge = issued.json()
        message = build_auth_message(
            protocol_version=1,
            desktop_uuid=gateway.desktop_uuid,
            device_uuid="android-001",
            challenge_id=challenge["challenge_id"],
            challenge=bytes.fromhex(challenge["desktop_challenge"]),
        )
        signature = sign_challenge(private_key, message).hex()

        response = client.post(
            "/device/v1/auth/response",
            json={
                "device_uuid": "android-001",
                "challenge_id": challenge["challenge_id"],
                "signature": signature,
                "protocol_version": 1,
            },
        )
        assert response.status_code == 200
        new_token = response.json()["session_token"]
        assert new_token != old_token

        assert client.get(
            "/device/v1/status",
            headers={"X-Session-Token": old_token},
        ).status_code == 401
        assert client.get(
            "/device/v1/status",
            headers={"X-Session-Token": new_token},
        ).status_code == 200

        replay = client.post(
            "/device/v1/auth/response",
            json={
                "device_uuid": "android-001",
                "challenge_id": challenge["challenge_id"],
                "signature": signature,
                "protocol_version": 1,
            },
        )
        assert replay.status_code == 401
        assert _error_code(replay) == "AUTH_CHALLENGE_USED"


def test_expired_challenge_is_410_and_issues_no_token():
    clock = FakeClock(100)
    gateway = _gateway(clock=clock, challenge_ttl=5)
    with _client(gateway) as client:
        _, private_key, _, token = _pair_device(client)
        issued = client.post(
            "/device/v1/auth/challenge",
            json={"device_uuid": "android-001", "protocol_version": 1},
        ).json()
        message = build_auth_message(
            protocol_version=1,
            desktop_uuid=gateway.desktop_uuid,
            device_uuid="android-001",
            challenge_id=issued["challenge_id"],
            challenge=bytes.fromhex(issued["desktop_challenge"]),
        )
        clock.advance(5)
        response = client.post(
            "/device/v1/auth/response",
            json={
                "device_uuid": "android-001",
                "challenge_id": issued["challenge_id"],
                "signature": sign_challenge(private_key, message).hex(),
                "protocol_version": 1,
            },
        )
        assert response.status_code == 410
        assert _error_code(response) == "AUTH_CHALLENGE_EXPIRED"
        assert gateway.validate_token(token) == "android-001"


def test_wrong_signature_attempts_are_bounded_and_challenge_stays_consumed():
    gateway = _gateway()
    with _client(gateway) as client:
        _pair_device(client)
        issued = client.post(
            "/device/v1/auth/challenge",
            json={"device_uuid": "android-001", "protocol_version": 1},
        ).json()
        wrong_private_key, _ = generate_ecdsa_p256_keypair()
        message = build_auth_message(
            protocol_version=1,
            desktop_uuid=gateway.desktop_uuid,
            device_uuid="android-001",
            challenge_id=issued["challenge_id"],
            challenge=bytes.fromhex(issued["desktop_challenge"]),
        )
        body = {
            "device_uuid": "android-001",
            "challenge_id": issued["challenge_id"],
            "signature": sign_challenge(wrong_private_key, message).hex(),
            "protocol_version": 1,
        }

        first = client.post("/device/v1/auth/response", json=body)
        second = client.post("/device/v1/auth/response", json=body)
        third = client.post("/device/v1/auth/response", json=body)
        fourth = client.post("/device/v1/auth/response", json=body)

        assert first.status_code == second.status_code == 401
        assert _error_code(first) == "AUTH_SIGNATURE_INVALID"
        assert _error_code(second) == "AUTH_SIGNATURE_INVALID"
        assert third.status_code == 401
        assert _error_code(third) == "AUTH_ATTEMPTS_EXHAUSTED"
        assert fourth.status_code == 401
        assert _error_code(fourth) == "AUTH_CHALLENGE_USED"


def test_unknown_and_cross_device_challenges_fail_closed():
    gateway = _gateway()
    with _client(gateway) as client:
        _pair_device(client, "android-one")
        _, private_two, _, _ = _pair_device(client, "android-two")
        issued = client.post(
            "/device/v1/auth/challenge",
            json={"device_uuid": "android-one", "protocol_version": 1},
        ).json()
        message = build_auth_message(
            protocol_version=1,
            desktop_uuid=gateway.desktop_uuid,
            device_uuid="android-two",
            challenge_id=issued["challenge_id"],
            challenge=bytes.fromhex(issued["desktop_challenge"]),
        )

        mismatch = client.post(
            "/device/v1/auth/response",
            json={
                "device_uuid": "android-two",
                "challenge_id": issued["challenge_id"],
                "signature": sign_challenge(private_two, message).hex(),
                "protocol_version": 1,
            },
        )
        unknown = client.post(
            "/device/v1/auth/response",
            json={
                "device_uuid": "android-two",
                "challenge_id": "0" * 32,
                "signature": sign_challenge(private_two, message).hex(),
                "protocol_version": 1,
            },
        )
        unbound = client.post(
            "/device/v1/auth/challenge",
            json={"device_uuid": "not-bound", "protocol_version": 1},
        )

        assert mismatch.status_code == 401
        assert _error_code(mismatch) == "AUTH_CHALLENGE_MISMATCH"
        assert unknown.status_code == 401
        assert _error_code(unknown) == "AUTH_CHALLENGE_UNKNOWN"
        assert unbound.status_code == 403
        assert _error_code(unbound) == "DEVICE_NOT_BOUND"


def test_token_expiry_revocation_and_hashed_storage():
    clock = FakeClock(100)
    gateway = _gateway(clock=clock)
    with _client(gateway) as client:
        _, _, _, token = _pair_device(client)
        assert token not in repr(gateway._tokens._records)
        clock.advance(60)
        expired = client.get(
            "/device/v1/status",
            headers={"X-Session-Token": token},
        )
        assert expired.status_code == 401
        assert _error_code(expired) == "DEVICE_TOKEN_INVALID"

        _, _, _, replacement = _pair_device(client, "revoked-device")
        assert gateway.revoke_device("revoked-device")["status"] == "revoked"
        revoked = client.get(
            "/device/v1/status",
            headers={"X-Session-Token": replacement},
        )
        assert revoked.status_code == 401
        assert _error_code(revoked) == "DEVICE_TOKEN_INVALID"


def test_sas_rejects_pem_and_non_p256_der():
    gateway = _gateway()
    with _client(gateway) as client:
        for key_bytes in (
            generate_ecdsa_p256_keypair()[1],
            ec.generate_private_key(ec.SECP384R1()).public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ),
        ):
            sid = client.post("/device/v1/pair/start").json()["session_id"]
            assert client.post(
                f"/device/v1/pair/{sid}/connect",
                json={"android_uuid": "android-001", "nonce": "ab" * 32},
            ).status_code == 200
            response = client.post(
                f"/device/v1/pair/{sid}/sas",
                json={"android_pubkey_der_hex": key_bytes.hex()},
            )
            assert response.status_code == 400
            assert _error_code(response) == "DEVICE_INVALID_REQUEST"


def test_concurrent_capacity_and_completion_have_single_side_effects():
    capacity_gateway = _gateway(max_pair_sessions=5)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: _try_pair_start(capacity_gateway), range(12)))
    assert results.count("ok") == 5
    assert results.count("PAIR_CAPACITY_EXCEEDED") == 7
    assert capacity_gateway.pairing_mgr.active_sessions() == 5

    gateway = _gateway()
    with _client(gateway) as client:
        sid, _, public_der = _pair_through_confirmation(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: _try_pair_complete(gateway, sid, public_der),
                range(2),
            )
        )
    assert results.count("bound") == 1
    assert results.count("PAIR_STATE_CONFLICT") == 1
    assert len(gateway.devices.list()) == 1


def test_concurrent_auth_response_consumes_challenge_once():
    gateway = _gateway()
    with _client(gateway) as client:
        _, private_key, _, _ = _pair_device(client)
        issued = gateway.auth_challenge("android-001", 1)
    message = build_auth_message(
        protocol_version=1,
        desktop_uuid=gateway.desktop_uuid,
        device_uuid="android-001",
        challenge_id=issued["challenge_id"],
        challenge=bytes.fromhex(issued["desktop_challenge"]),
    )
    signature = sign_challenge(private_key, message).hex()

    def respond(_):
        try:
            gateway.auth_response(
                "android-001",
                issued["challenge_id"],
                signature,
                1,
            )
            return "ok"
        except DeviceLinkError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(respond, range(2)))
    assert results.count("ok") == 1
    assert results.count("AUTH_CHALLENGE_USED") == 1


def _try_pair_start(gateway: DeviceLinkGateway) -> str:
    try:
        gateway.pair_start()
        return "ok"
    except DeviceLinkError as exc:
        return exc.code


def _try_pair_complete(
    gateway: DeviceLinkGateway,
    session_id: str,
    public_der: bytes,
) -> str:
    try:
        return gateway.pair_complete(
            session_id,
            "android-001",
            public_der.hex(),
            "Concurrent",
            1,
        )["status"]
    except DeviceLinkError as exc:
        return exc.code


def test_device_link_is_loopback_only():
    gateway = _gateway()
    with _client(gateway, peer="192.0.2.10") as client:
        response = client.post("/device/v1/pair/start")
        assert response.status_code == 403
        assert _error_code(response) == "DEVICE_LOOPBACK_REQUIRED"


def test_entire_device_namespace_is_gated_and_unknown_routes_are_json_404():
    gateway = _gateway()
    with _client(gateway) as client:
        for path in (
            "/device/v1",
            "/device/v1/",
            "/device/v1/no-such-route",
        ):
            response = client.get(path)
            assert response.status_code == 404
            assert _error_code(response) == "DEVICE_ROUTE_NOT_FOUND"

    with _client(gateway, peer="192.0.2.10") as remote:
        response = remote.get("/device/v1")
        assert response.status_code == 403
        assert _error_code(response) == "DEVICE_LOOPBACK_REQUIRED"


def test_remote_browser_origin_is_rejected_on_loopback_socket():
    gateway = _gateway()
    with _client(gateway) as client:
        response = client.post(
            "/device/v1/pair/start",
            headers={"Origin": "https://attacker.example"},
        )
        assert response.status_code == 403
        assert _error_code(response) == "DEVICE_ORIGIN_FORBIDDEN"


def test_malformed_browser_origin_is_stable_403():
    gateway = _gateway()
    with _client(gateway) as client:
        response = client.post(
            "/device/v1/pair/start",
            headers={"Origin": "http://["},
        )
        assert response.status_code == 403
        assert _error_code(response) == "DEVICE_ORIGIN_FORBIDDEN"


def test_payload_limit_measures_body_even_with_false_content_length():
    gateway = _gateway()
    with _client(gateway) as client:
        oversized = json.dumps({"padding": "x" * (1024 * 1024)}).encode()
        response = client.post(
            "/device/v1/pair/start",
            content=oversized,
            headers={
                "Content-Type": "application/json",
                "Content-Length": "1",
            },
        )
        assert len(oversized) > 1024 * 1024
        assert response.status_code == 413
        assert _error_code(response) == "DEVICE_PAYLOAD_TOO_LARGE"


def test_payload_exactly_one_mib_is_accepted():
    gateway = _gateway()
    with _client(gateway) as client:
        response = client.post(
            "/device/v1/pair/start",
            content=b"x" * (1024 * 1024),
            headers={"Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 400
        assert _error_code(response) == "DEVICE_INVALID_REQUEST"


def test_remote_server_startup_is_fail_closed(monkeypatch):
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("uvicorn.run", fake_run)
    with pytest.raises(ValueError, match="loopback"):
        run_server(host="0.0.0.0", allow_remote=True)
    assert called is False
