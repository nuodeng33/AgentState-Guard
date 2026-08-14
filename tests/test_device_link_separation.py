"""Core 8787 and bounded Device Link 8788 route separation."""

from fastapi.testclient import TestClient

from agentguard.api.server import create_app
from agentguard.device_link.api import create_device_link_app
from agentguard.device_link.crypto import generate_ecdsa_p256_keypair, public_key_to_der
from agentguard.device_link.gateway import DeviceLinkGateway
from agentguard.device_link.persistence import DeviceBindingStore
from agentguard.storage.db import StateDB


def test_core_does_not_mount_device_api(tmp_path):
    client = TestClient(
        create_app(
            state_db_path=tmp_path / "state.db", config={"base_dir": str(tmp_path)}
        )
    )
    assert client.get("/device/v1/status").status_code == 404


def test_device_app_exposes_only_allowlisted_routes(tmp_path):
    database = StateDB(tmp_path / "state.db")
    database.connect()
    desktop_private, desktop_public = generate_ecdsa_p256_keypair()
    gateway = DeviceLinkGateway(
        "desktop",
        public_key_to_der(desktop_public),
        desktop_private,
        device_registry=DeviceBindingStore(database),
        single_device=True,
    )
    _device_private, device_public = generate_ecdsa_p256_keypair()
    gateway.devices.add(
        "android-a",
        public_key_to_der(device_public),
        "Phone",
        ["read", "approve_once", "reject"],
        1,
    )
    token = gateway._issue_product_token("android-a")
    client = TestClient(
        create_device_link_app(
            gateway=gateway,
            state_db_path=tmp_path / "state.db",
            snapshots_dir=tmp_path / "snapshots",
        )
    )
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/device/v1/status", headers=headers).status_code == 200
    assert client.get("/device/v1/environment", headers=headers).status_code == 200
    assert (
        client.post("/device/v1/checkpoints", json={}, headers=headers).status_code
        == 405
    )
    assert (
        client.post("/device/v1/recovery/restore", json={}, headers=headers).status_code
        == 404
    )
    assert client.get("/api/v1/runtime", headers=headers).status_code == 404
    database.close()
