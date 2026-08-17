"""Independent Device Link app pairing, auth, and route-boundary tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentguard.device_link.api import create_device_link_app
from agentguard.device_link.crypto import (
    generate_ecdsa_p256_keypair,
    public_key_to_der,
    sign_challenge,
    verify_signature,
)
from agentguard.device_link.gateway import DeviceLinkGateway, build_auth_message
from agentguard.device_link.identity import DesktopIdentityStore
from agentguard.device_link.persistence import DeviceBindingStore
from agentguard.storage.db import StateDB


def _product(tmp_path):
    database = StateDB(tmp_path / "state.db")
    database.connect()
    identity = DesktopIdentityStore(tmp_path / "identity.json").load_or_create()
    gateway = DeviceLinkGateway(
        identity.desktop_uuid,
        identity.signing_public_key_der,
        identity.signing_private_key_pem,
        desktop_tls_spki_fp=identity.tls_spki_fingerprint,
        device_registry=DeviceBindingStore(database),
        single_device=True,
    )
    app = create_device_link_app(
        gateway=gateway,
        state_db_path=tmp_path / "state.db",
        snapshots_dir=tmp_path / "snapshots",
    )
    return TestClient(app), gateway, identity, database


def _pair(client, gateway):
    device_private, device_public = generate_ecdsa_p256_keypair()
    device_der = public_key_to_der(device_public)
    invitation = gateway.create_pairing_invitation("192.168.1.5", 8788)
    session_id = invitation["session_id"]
    connected = client.post(
        f"/device/v1/pair/{session_id}/connect",
        json={
            "ticket": invitation["ticket"],
            "android_uuid": "android-a",
            "nonce": "a" * 64,
        },
    )
    assert connected.status_code == 200
    pair_token = connected.json()["pairing_token"]
    pair_headers = {"X-Pairing-Token": pair_token}
    sas = client.post(
        f"/device/v1/pair/{session_id}/sas",
        json={"android_pubkey_der_hex": device_der.hex()},
        headers=pair_headers,
    )
    assert sas.status_code == 200
    android_confirm = client.post(
        f"/device/v1/pair/{session_id}/confirm",
        json={"confirm": True},
        headers=pair_headers,
    )
    assert android_confirm.json()["state"] == "sas_pending"
    assert gateway.pair_desktop_confirm(session_id, True)["state"] == "confirmed_both"
    completed = client.post(
        f"/device/v1/pair/{session_id}/complete",
        json={
            "android_uuid": "android-a",
            "android_pubkey_der_hex": device_der.hex(),
            "display_name": "Phone",
            "protocol_version": 1,
        },
        headers=pair_headers,
    )
    assert completed.status_code == 200
    return device_private, completed.json()["session_token"]


def test_full_pairing_and_bounded_projection_auth(tmp_path):
    client, gateway, _identity, database = _product(tmp_path)
    try:
        _private, token = _pair(client, gateway)
        assert client.get("/device/v1/status").status_code == 401
        status = client.get(
            "/device/v1/status", headers={"Authorization": f"Bearer {token}"}
        )
        assert status.status_code == 200
        assert status.json()["permissions"] == [
            "read",
            "approve_once",
            "reject",
            "self_unpair",
        ]
        assert (
            client.post(
                "/device/v1/recovery/restore",
                json={},
                headers={"Authorization": f"Bearer {token}"},
            ).status_code
            == 404
        )
    finally:
        database.close()


def test_authenticated_self_unpair_revokes_binding_and_invalidates_token(tmp_path):
    client, gateway, _identity, database = _product(tmp_path)
    try:
        revoke_calls = []
        revoke_device = gateway.revoke_device

        def traced_revoke(device_uuid):
            revoke_calls.append(device_uuid)
            return revoke_device(device_uuid)

        gateway.revoke_device = traced_revoke
        unauthenticated = client.post("/device/v1/self-unpair", json={})
        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["error"]["code"] == "DEVICE_TOKEN_INVALID"
        assert revoke_calls == []
        _private, token = _pair(client, gateway)
        headers = {"Authorization": f"Bearer {token}"}
        registry = DeviceBindingStore(database)
        assert registry.get("android-a") is not None

        injected = client.post(
            "/device/v1/self-unpair",
            json={"device_uuid": "someone-else"},
            headers=headers,
        )
        assert injected.status_code == 400
        assert injected.json()["error"]["code"] == "DEVICE_INVALID_REQUEST"
        assert registry.get("android-a") is not None
        assert revoke_calls == []

        unpaired = client.post("/device/v1/self-unpair", json={}, headers=headers)
        assert unpaired.status_code == 200
        assert unpaired.json() == {
            "schema_version": "device-link-self-unpair-1",
            "action": "SELF_UNPAIR",
            "status": "UNPAIRED",
            "reason_code": "DEVICE_SELF_UNPAIRED",
        }
        assert registry.get("android-a") is None
        assert revoke_calls == ["android-a"]

        replay = client.post("/device/v1/self-unpair", json={}, headers=headers)
        assert replay.status_code == 401
        assert replay.json()["error"]["code"] == "DEVICE_TOKEN_INVALID"
        status = client.get("/device/v1/status", headers=headers)
        assert status.status_code == 401
        assert status.json()["error"]["code"] == "DEVICE_TOKEN_INVALID"
        _new_private, new_token = _pair(client, gateway)
        assert new_token != token
        assert registry.get("android-a") is not None
    finally:
        database.close()


def test_self_unpair_accepts_server_proven_already_absent_binding(tmp_path):
    client, gateway, _identity, database = _product(tmp_path)
    try:
        _private, token = _pair(client, gateway)
        headers = {"Authorization": f"Bearer {token}"}
        registry = DeviceBindingStore(database)
        assert registry.revoke("android-a") is True
        assert registry.get("android-a") is None
        assert gateway.validate_token(token) == "android-a"

        terminal = client.post("/device/v1/self-unpair", json={}, headers=headers)

        assert terminal.status_code == 200
        assert terminal.json()["reason_code"] == "DEVICE_SELF_UNPAIRED"
        assert gateway.validate_token(token) is None
    finally:
        database.close()


def test_domain_separated_mutual_reauthentication_and_replay(tmp_path):
    client, gateway, identity, database = _product(tmp_path)
    try:
        device_private, _token = _pair(client, gateway)
        challenge = client.post(
            "/device/v1/auth/challenge",
            json={"device_uuid": "android-a", "protocol_version": 1},
        ).json()
        message = build_auth_message(
            protocol_version=1,
            desktop_uuid=identity.desktop_uuid,
            device_uuid="android-a",
            challenge_id=challenge["challenge_id"],
            challenge=bytes.fromhex(challenge["desktop_challenge"]),
        )
        body = {
            "device_uuid": "android-a",
            "challenge_id": challenge["challenge_id"],
            "signature": sign_challenge(device_private, message).hex(),
            "protocol_version": 1,
        }
        authenticated = client.post("/device/v1/auth/response", json=body)
        assert authenticated.status_code == 200
        assert verify_signature(
            identity.signing_public_key_der,
            message,
            bytes.fromhex(authenticated.json()["desktop_signature"]),
        )
        replay = client.post("/device/v1/auth/response", json=body)
        assert replay.status_code == 401
        assert replay.json()["error"]["code"] == "AUTH_CHALLENGE_USED"
    finally:
        database.close()


def test_malformed_or_unscoped_pairing_never_reaches_gateway(tmp_path):
    client, gateway, _identity, database = _product(tmp_path)
    try:
        invitation = gateway.create_pairing_invitation("192.168.1.5", 8788)
        session_id = invitation["session_id"]
        malformed = client.post(
            f"/device/v1/pair/{session_id}/connect",
            json={"ticket": "not-a-ticket", "android_uuid": "a", "nonce": "a" * 64},
        )
        assert malformed.status_code == 400
        assert malformed.json()["error"]["code"] == "DEVICE_INVALID_REQUEST"
        unscoped = client.post(
            f"/device/v1/pair/{session_id}/sas",
            json={"android_pubkey_der_hex": "04"},
        )
        assert unscoped.status_code == 401
        assert unscoped.json()["error"]["code"] == "PAIR_TOKEN_INVALID"
        oversized = client.post(
            f"/device/v1/pair/{session_id}/connect",
            content=b"x" * 1_048_577,
            headers={"Content-Type": "application/json"},
        )
        assert oversized.status_code == 413
        assert oversized.json()["error"]["code"] == "DEVICE_PAYLOAD_TOO_LARGE"
    finally:
        database.close()
