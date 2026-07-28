"""Production FastAPI regression tests for Device Link identity and auth."""

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import uvicorn

from agentguard.api.server import create_app
from agentguard.device_link.crypto import (
    generate_ecdsa_p256_keypair,
    public_key_to_der,
    sign_challenge,
)
from agentguard.device_link.gateway import DeviceLinkGateway, GatewayConfig


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def device_api(tmp_path: Path):
    port = _free_port()
    app = create_app(
        state_db_path=tmp_path / "state.db",
        config={"base_dir": str(tmp_path)},
    )
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error",
    ))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "Production FastAPI server did not start"
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _request(
    base_url: str, path: str, body: dict | None = None, authorization: str | None = None,
) -> tuple[dict, int]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{base_url}{path}", data=data, method="POST" if body is not None else "GET",
    )
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if authorization is not None:
        req.add_header("Authorization", authorization)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read()), response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return json.loads(raw), exc.code
        except json.JSONDecodeError:
            return {"error": raw.decode(errors="replace")}, exc.code


def _pair_device(base_url: str, device_uuid: str = "android-A"):
    private_key, public_key = generate_ecdsa_p256_keypair()
    public_key_der = public_key_to_der(public_key)

    start, status = _request(base_url, "/device/v1/pair/start", {})
    assert status == 200
    session_id = start["session_id"]

    _, status = _request(
        base_url,
        f"/device/v1/pair/{session_id}/connect",
        {"android_uuid": device_uuid, "nonce": "ab" * 32},
    )
    assert status == 200

    _, status = _request(
        base_url,
        f"/device/v1/pair/{session_id}/sas",
        {"android_pubkey_der_hex": public_key_der.hex()},
    )
    assert status == 200

    _, status = _request(
        base_url,
        f"/device/v1/pair/{session_id}/confirm",
        {"confirm": True},
    )
    assert status == 200

    complete, status = _request(
        base_url,
        f"/device/v1/pair/{session_id}/complete",
        {
            "android_uuid": device_uuid,
            "android_pubkey_der_hex": public_key_der.hex(),
            "display_name": "Real P-256 Device",
        },
    )
    assert status == 200
    assert complete["status"] == "bound"
    return session_id, private_key, public_key_der


def _challenge(base_url: str, device_uuid: str):
    return _request(
        base_url, "/device/v1/auth/challenge", {"device_uuid": device_uuid},
    )


def _auth_response(
    base_url: str, device_uuid: str, challenge: bytes, signature: bytes,
):
    return _request(
        base_url,
        "/device/v1/auth/response",
        {
            "device_uuid": device_uuid,
            "nonce": challenge.hex(),
            "challenge_response": signature.hex(),
        },
    )



def test_device_read_endpoints_require_valid_bearer(device_api: str):
    for path in ("/device/v1/status", "/device/v1/checkpoints"):
        _, status = _request(device_api, path)
        assert status == 401
        _, status = _request(device_api, path, authorization="Bearer incorrect-token")
        assert status == 401

    _, _, public_key_der = _pair_device(device_api)
    start, status = _request(device_api, "/device/v1/pair/start", {})
    assert status == 200
    session_id = start["session_id"]
    assert _request(
        device_api,
        f"/device/v1/pair/{session_id}/connect",
        {"android_uuid": "android-B", "nonce": "cd" * 32},
    )[1] == 200
    assert _request(
        device_api,
        f"/device/v1/pair/{session_id}/sas",
        {"android_pubkey_der_hex": public_key_der.hex()},
    )[1] == 200
    assert _request(
        device_api,
        f"/device/v1/pair/{session_id}/confirm",
        {"confirm": True},
    )[1] == 200
    complete, status = _request(
        device_api,
        f"/device/v1/pair/{session_id}/complete",
        {
            "android_uuid": "android-B",
            "android_pubkey_der_hex": public_key_der.hex(),
            "display_name": "Bearer Test Device",
        },
    )
    assert status == 200
    token = complete["session_token"]

    for path in ("/device/v1/status", "/device/v1/checkpoints"):
        response, status = _request(
            device_api, path, authorization=f"Bearer {token}",
        )
        assert status == 200
        assert "detail" not in response


def test_pair_complete_rejects_identity_substitution(device_api: str):
    _, public_a = generate_ecdsa_p256_keypair()
    public_a_der = public_key_to_der(public_a)
    _, public_b = generate_ecdsa_p256_keypair()
    public_b_der = public_key_to_der(public_b)

    start, _ = _request(device_api, "/device/v1/pair/start", {})
    session_id = start["session_id"]
    assert _request(
        device_api,
        f"/device/v1/pair/{session_id}/connect",
        {"android_uuid": "android-A", "nonce": "ab" * 32},
    )[1] == 200
    assert _request(
        device_api,
        f"/device/v1/pair/{session_id}/sas",
        {"android_pubkey_der_hex": public_a_der.hex()},
    )[1] == 200
    assert _request(
        device_api,
        f"/device/v1/pair/{session_id}/confirm",
        {"confirm": True},
    )[1] == 200

    substituted, status = _request(
        device_api,
        f"/device/v1/pair/{session_id}/complete",
        {
            "android_uuid": "android-B",
            "android_pubkey_der_hex": public_b_der.hex(),
            "display_name": "Substituted Device",
        },
    )

    assert 400 <= status < 500
    assert "session_token" not in substituted


def test_real_der_key_challenge_auth_succeeds(device_api: str):
    _, private_key, _ = _pair_device(device_api)
    issued, status = _challenge(device_api, "android-A")
    assert status == 200
    challenge = bytes.fromhex(issued["desktop_challenge"])

    authenticated, status = _auth_response(
        device_api,
        "android-A",
        challenge,
        sign_challenge(private_key, challenge),
    )

    assert status == 200
    assert authenticated["session_token"]


def test_wrong_signature_is_rejected(device_api: str):
    _pair_device(device_api)
    wrong_private, _ = generate_ecdsa_p256_keypair()
    issued, _ = _challenge(device_api, "android-A")
    challenge = bytes.fromhex(issued["desktop_challenge"])

    response, status = _auth_response(
        device_api,
        "android-A",
        challenge,
        sign_challenge(wrong_private, challenge),
    )

    assert status in (401, 403)
    assert "session_token" not in response


def test_unknown_challenge_is_rejected(device_api: str):
    _, private_key, _ = _pair_device(device_api)
    unknown = bytes.fromhex("42" * 32)

    response, status = _auth_response(
        device_api,
        "android-A",
        unknown,
        sign_challenge(private_key, unknown),
    )

    assert status in (401, 403)
    assert "session_token" not in response


def test_consumed_challenge_cannot_be_replayed(device_api: str):
    _, private_key, _ = _pair_device(device_api)
    issued, _ = _challenge(device_api, "android-A")
    challenge = bytes.fromhex(issued["desktop_challenge"])
    signature = sign_challenge(private_key, challenge)
    _, first_status = _auth_response(device_api, "android-A", challenge, signature)
    assert first_status == 200

    replay, replay_status = _auth_response(
        device_api, "android-A", challenge, signature,
    )

    assert replay_status in (401, 403)
    assert "session_token" not in replay


def test_challenge_is_bound_to_device(device_api: str):
    _, private_a, _ = _pair_device(device_api, "android-A")
    _pair_device(device_api, "android-B")
    issued, _ = _challenge(device_api, "android-A")
    challenge = bytes.fromhex(issued["desktop_challenge"])

    response, status = _auth_response(
        device_api,
        "android-B",
        challenge,
        sign_challenge(private_a, challenge),
    )

    assert status in (401, 403)
    assert "session_token" not in response


def test_expired_challenge_is_rejected():
    desktop_private, desktop_public = generate_ecdsa_p256_keypair()
    gateway = DeviceLinkGateway(
        "desktop",
        public_key_to_der(desktop_public),
        desktop_private,
        config=GatewayConfig(challenge_ttl=0),
    )
    device_private, device_public = generate_ecdsa_p256_keypair()
    gateway.devices.add(
        "android-A", public_key_to_der(device_public), "Device", ["read"], 1,
    )
    issued = gateway.auth_challenge("android-A")
    challenge = bytes.fromhex(issued["desktop_challenge"])

    response = gateway.auth_response(
        "android-A",
        sign_challenge(device_private, challenge).hex(),
        challenge.hex(),
    )

    assert response["code"] in (401, 403)
    assert "session_token" not in response


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/device/v1/auth/response", {
            "device_uuid": "android-A",
            "nonce": "not-hex",
            "challenge_response": "also-not-hex",
        }),
        ("/device/v1/pair/{session_id}/sas", {
            "android_pubkey_der_hex": "not-hex",
        }),
    ],
)
def test_malformed_auth_input_never_returns_500(
    device_api: str, path: str, body: dict,
):
    if "{session_id}" in path:
        start, _ = _request(device_api, "/device/v1/pair/start", {})
        path = path.format(session_id=start["session_id"])
    _, status = _request(device_api, path, body)
    assert 400 <= status < 500
