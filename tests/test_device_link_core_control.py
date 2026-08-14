"""Core owns Device Link lifecycle without proxying the 8788 data plane."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentguard.api.server import create_app


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
