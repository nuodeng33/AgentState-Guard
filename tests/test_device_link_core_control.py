"""Core owns Device Link lifecycle without proxying the 8788 data plane."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentguard.api.server import create_app
from agentguard.device_link.crypto import (
    generate_ecdsa_p256_keypair,
    public_key_to_der,
)
from agentguard.device_link.gateway import DeviceLinkGateway


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def pair_poll(self, session_id: str) -> dict:
        self.calls.append(("poll", session_id))
        return {"schema_version": "device-link-pairing-1", "session_id": session_id}

    def pair_desktop_confirm(self, session_id: str, confirm: bool) -> dict:
        self.calls.append(("confirm", session_id, confirm))
        return {"schema_version": "device-link-pairing-1", "state": "sas_pending"}

    def cancel_pairing(self, session_id: str) -> dict:
        self.calls.append(("cancel", session_id))
        return {"schema_version": "device-link-pairing-1", "state": "cancelled"}

    def revoke_device(self, device_uuid: str) -> dict:
        self.calls.append(("revoke", device_uuid))
        return {"status": "revoked", "device_uuid": device_uuid}


class _Controller:
    def __init__(self) -> None:
        self.gateway = _Gateway()
        self.calls: list[str] = []

    def product_status(self) -> dict:
        return {
            "schema_version": "device-link-lifecycle-1",
            "enabled": False,
            "status": "DISABLED",
            "reason_code": "DEVICE_LINK_DISABLED",
            "bound_devices": [],
        }

    def enable(self) -> dict:
        self.calls.append("enable")
        return {**self.product_status(), "enabled": True, "status": "ENABLED"}

    def disable(self) -> dict:
        self.calls.append("disable")
        return self.product_status()

    def refresh_network(self) -> dict:
        self.calls.append("refresh")
        return self.product_status()

    def create_pairing_invitation(self) -> dict:
        self.calls.append("pair")
        return {
            "protocol_version": 1,
            "session_id": "a" * 32,
            "ticket": "b" * 64,
            "expires_at_epoch": 2_000_000_000,
        }


def _client(tmp_path) -> tuple[TestClient, dict[str, str], _Controller]:
    controller = _Controller()
    client = TestClient(
        create_app(
            state_db_path=tmp_path / "state.db",
            config={"base_dir": str(tmp_path)},
            device_link_controller=controller,
        )
    )
    token = client.get("/api/session").json()["token"]
    return client, {"X-Session-Token": token}, controller


def test_core_device_link_lifecycle_and_pairing_are_strict(tmp_path):
    client, headers, controller = _client(tmp_path)

    assert client.get("/api/v1/devices", headers=headers).json()["status"] == "DISABLED"
    assert (
        client.post("/api/v1/device-link/enable", json={}, headers=headers).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/device-link/enable", json={"host": "0.0.0.0"}, headers=headers
        ).json()["reason_code"]
        == "DEVICE_LINK_REQUEST_INVALID"
    )
    invitation = client.post(
        "/api/v1/device-link/pairings", json={}, headers=headers
    ).json()
    assert invitation["session_id"] == "a" * 32
    assert (
        client.post(
            f"/api/v1/device-link/pairings/{'a' * 32}/confirm",
            json={"confirm": True},
            headers=headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/device-link/pairings/{'a' * 32}/cancel",
            json={},
            headers=headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/device-link/devices/android-a/revoke", json={}, headers=headers
        ).json()["status"]
        == "revoked"
    )
    assert (
        client.post(
            "/api/v1/device-link/network/refresh", json={}, headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post("/api/v1/device-link/disable", json={}, headers=headers).status_code
        == 200
    )
    assert controller.calls == ["enable", "pair", "refresh", "disable"]


def test_core_device_link_control_requires_loopback_session_token(tmp_path):
    client, _headers, _controller = _client(tmp_path)

    assert client.get("/api/v1/devices").status_code == 401
    assert client.post("/api/v1/device-link/enable", json={}).status_code == 401
    assert client.get("/device/v1/status").status_code == 404


def test_desktop_pairing_projection_uses_the_same_server_owned_sas_as_android(
    tmp_path,
):
    desktop_private, desktop_public = generate_ecdsa_p256_keypair()
    gateway = DeviceLinkGateway(
        "desktop-a",
        public_key_to_der(desktop_public),
        desktop_private,
        "a" * 64,
    )
    controller = _Controller()
    controller.gateway = gateway
    client = TestClient(
        create_app(
            state_db_path=tmp_path / "state.db",
            config={"base_dir": str(tmp_path)},
            device_link_controller=controller,
        )
    )
    token = client.get("/api/session").json()["token"]
    headers = {"X-Session-Token": token}
    invitation = gateway.create_pairing_invitation("192.168.1.10", 8788)
    session_id = invitation["session_id"]
    connected = gateway.accept_pairing_ticket(
        session_id,
        invitation["ticket"],
        "android-a",
        "ab" * 32,
    )
    _android_private, android_public = generate_ecdsa_p256_keypair()

    android = gateway.pair_start_sas_scoped(
        session_id,
        connected["pairing_token"],
        public_key_to_der(android_public).hex(),
    )
    desktop = client.get(
        f"/api/v1/device-link/pairings/{session_id}", headers=headers
    )

    assert desktop.status_code == 200
    assert desktop.json()["state"] == "sas_pending"
    assert desktop.json()["sas"] == android["sas"]
    assert len(desktop.json()["sas"]) == 7

    gateway.pair_android_confirm(session_id, connected["pairing_token"], True)
    still_pending = client.get(
        f"/api/v1/device-link/pairings/{session_id}", headers=headers
    ).json()
    assert still_pending["sas"] == android["sas"]
    gateway.pair_desktop_confirm(session_id, True)
    terminal_projection = client.get(
        f"/api/v1/device-link/pairings/{session_id}", headers=headers
    ).json()
    assert terminal_projection["state"] == "confirmed_both"
    assert "sas" not in terminal_projection
