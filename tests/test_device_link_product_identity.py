"""Durable Desktop identity and persistent minimal device binding tests."""

from __future__ import annotations

from agentguard.device_link.crypto import generate_ecdsa_p256_keypair, public_key_to_der
from agentguard.device_link.identity import DesktopIdentityStore
from agentguard.device_link.persistence import DeviceBindingStore
from agentguard.storage.db import StateDB


def test_desktop_identity_is_durable_and_separates_signing_from_tls(tmp_path):
    store = DesktopIdentityStore(tmp_path / "device-link-identity.json")
    first = store.load_or_create()
    second = DesktopIdentityStore(store.path).load_or_create()

    assert first.desktop_uuid == second.desktop_uuid
    assert first.signing_public_key_der == second.signing_public_key_der
    assert first.tls_public_key_der == second.tls_public_key_der
    assert first.signing_public_key_der != first.tls_public_key_der
    stored = store.path.read_bytes()
    assert first.signing_private_key_pem not in stored
    assert first.tls_private_key_pem not in stored


def test_device_binding_survives_database_reopen_and_revokes(tmp_path):
    database = StateDB(tmp_path / "state.db")
    database.connect()
    _, public = generate_ecdsa_p256_keypair()
    public_der = public_key_to_der(public)
    DeviceBindingStore(database).add(
        "android-a", public_der, "Phone", ["read", "approve_once", "reject"], 1
    )
    database.close()

    reopened = StateDB(tmp_path / "state.db")
    reopened.connect()
    store = DeviceBindingStore(reopened)
    assert store.get("android-a")["pubkey_der_hex"] == public_der.hex()
    assert store.count() == 1
    assert store.revoke("android-a") is True
    assert store.get("android-a") is None
    assert store.count() == 0
    store.add("android-a", public_der, "Phone again", ["read"], 1)
    assert store.get("android-a")["display_name"] == "Phone again"
    reopened.close()
