"""Product pairing invitation, scoped ticket, and mutual auth tests."""

from __future__ import annotations

import time

import pytest

from agentguard.device_link.crypto import (
    generate_ecdsa_p256_keypair,
    public_key_to_der,
    sign_challenge,
    verify_signature,
)
from agentguard.device_link.errors import DeviceLinkError
from agentguard.device_link.gateway import (
    DeviceLinkGateway,
    GatewayConfig,
    build_auth_message,
)
from agentguard.device_link.identity import DesktopIdentityStore
from agentguard.device_link.persistence import DeviceBindingStore
from agentguard.storage.db import StateDB


def _gateway(tmp_path):
    identity = DesktopIdentityStore(tmp_path / "identity.json").load_or_create()
    database = StateDB(tmp_path / "state.db")
    database.connect()
    gateway = DeviceLinkGateway(
        identity.desktop_uuid,
        identity.signing_public_key_der,
        identity.signing_private_key_pem,
        desktop_tls_spki_fp=identity.tls_spki_fingerprint,
        device_registry=DeviceBindingStore(database),
        single_device=True,
    )
    return gateway, identity, database


def test_qr_ticket_is_one_time_and_pairing_calls_require_scoped_token(tmp_path):
    gateway, _identity, database = _gateway(tmp_path)
    try:
        invitation = gateway.create_pairing_invitation("192.168.1.5", 8788)
        assert invitation["expires_at_epoch"] > int(time.time())
        assert invitation["endpoint"] == "https://192.168.1.5:8788"
        assert invitation["ticket"] not in str(gateway.pairing_mgr.__dict__)

        connected = gateway.accept_pairing_ticket(
            invitation["session_id"], invitation["ticket"], "android-a", "aa" * 32
        )
        assert len(connected["pairing_token"]) == 64
        with pytest.raises(DeviceLinkError) as replay:
            gateway.accept_pairing_ticket(
                invitation["session_id"], invitation["ticket"], "android-a", "aa" * 32
            )
        assert replay.value.code == "PAIR_TICKET_INVALID"

        _, public = generate_ecdsa_p256_keypair()
        with pytest.raises(DeviceLinkError) as missing_token:
            gateway.pair_start_sas_scoped(
                invitation["session_id"], "", public_key_to_der(public).hex()
            )
        assert missing_token.value.code == "PAIR_TOKEN_INVALID"
    finally:
        database.close()


def test_mutual_auth_returns_desktop_signature_and_replay_fails(tmp_path):
    gateway, identity, database = _gateway(tmp_path)
    device_private, device_public = generate_ecdsa_p256_keypair()
    device_der = public_key_to_der(device_public)
    try:
        gateway.devices.add(
            "android-a", device_der, "Phone", ["read", "approve_once", "reject"], 1
        )
        issued = gateway.auth_challenge_scoped("android-a", 1)
        message = build_auth_message(
            protocol_version=1,
            desktop_uuid=identity.desktop_uuid,
            device_uuid="android-a",
            challenge_id=issued["challenge_id"],
            challenge=bytes.fromhex(issued["desktop_challenge"]),
        )
        result = gateway.auth_response_scoped(
            "android-a",
            issued["challenge_id"],
            sign_challenge(device_private, message).hex(),
            1,
        )
        assert len(result["session_token"]) == 64
        assert verify_signature(
            identity.signing_public_key_der,
            message,
            bytes.fromhex(result["desktop_signature"]),
        )
        with pytest.raises(DeviceLinkError) as replay:
            gateway.auth_response_scoped(
                "android-a",
                issued["challenge_id"],
                sign_challenge(device_private, message).hex(),
                1,
            )
        assert replay.value.code == "AUTH_CHALLENGE_USED"
    finally:
        database.close()


def test_revoke_invalidates_every_session_for_the_authenticated_device(tmp_path):
    gateway, identity, database = _gateway(tmp_path)
    device_private, device_public = generate_ecdsa_p256_keypair()
    device_der = public_key_to_der(device_public)
    try:
        gateway.devices.add(
            "android-a",
            device_der,
            "Phone",
            ["read", "approve_once", "reject", "self_unpair"],
            1,
        )
        legacy_challenge = gateway.auth_challenge("android-a")
        challenge_bytes = bytes.fromhex(legacy_challenge["desktop_challenge"])
        legacy_token = gateway.auth_response(
            "android-a",
            sign_challenge(device_private, challenge_bytes).hex(),
            challenge_bytes.hex(),
        )["session_token"]

        product_challenge = gateway.auth_challenge_scoped("android-a", 1)
        product_message = build_auth_message(
            protocol_version=1,
            desktop_uuid=identity.desktop_uuid,
            device_uuid="android-a",
            challenge_id=product_challenge["challenge_id"],
            challenge=bytes.fromhex(product_challenge["desktop_challenge"]),
        )
        product_token = gateway.auth_response_scoped(
            "android-a",
            product_challenge["challenge_id"],
            sign_challenge(device_private, product_message).hex(),
            1,
        )["session_token"]
        assert gateway.validate_token(legacy_token) == "android-a"
        assert gateway.validate_token(product_token) == "android-a"

        assert gateway.revoke_device("android-a") == {
            "status": "revoked",
            "device_uuid": "android-a",
        }
        assert gateway.validate_token(legacy_token) is None
        assert gateway.validate_token(product_token) is None
        assert gateway.devices.get("android-a") is None
    finally:
        database.close()


def test_pairing_capacity_and_protocol_errors_are_product_errors(tmp_path):
    gateway, _identity, database = _gateway(tmp_path)
    try:
        gateway.config.max_pair_sessions = 0
        gateway.pairing_mgr._max = 0
        with pytest.raises(DeviceLinkError) as capacity:
            gateway.create_pairing_invitation("192.168.1.5", 8788)
        assert capacity.value.code == "PAIRING_CAPACITY_EXHAUSTED"

        gateway.config = GatewayConfig()
        _private, public = generate_ecdsa_p256_keypair()
        gateway.devices.add(
            "android-protocol", public_key_to_der(public), "Phone", ["read"], 1
        )
        with pytest.raises(DeviceLinkError) as protocol:
            gateway.auth_challenge_scoped("android-protocol", 2)
        assert protocol.value.code == "DEVICE_PROTOCOL_UNSUPPORTED"
    finally:
        database.close()
