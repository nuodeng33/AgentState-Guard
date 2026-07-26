"""Integration assertions against the production FastAPI application."""

from __future__ import annotations

from tests.test_device_link_fastapi import (
    _client,
    _error_code,
    _gateway,
    _pair_device,
)


class TestGatewayProductionIntegration:
    def test_full_pairing_and_authenticated_reads(self):
        gateway = _gateway()
        with _client(gateway) as client:
            session_id, _, _, token = _pair_device(
                client,
                device_uuid="integration-device",
            )
            assert client.get(
                f"/device/v1/pair/{session_id}",
            ).json()["state"] == "consumed"
            headers = {"X-Session-Token": token}
            status = client.get("/device/v1/status", headers=headers)
            checkpoints = client.get(
                "/device/v1/checkpoints",
                headers=headers,
            )
            assert status.status_code == 200
            assert status.json()["devices_bound"] == 1
            assert checkpoints.status_code == 200

    def test_valid_but_unknown_session_is_404(self):
        with _client(_gateway()) as client:
            response = client.get("/device/v1/pair/" + "0" * 32)
            assert response.status_code == 404
            assert _error_code(response) == "PAIR_SESSION_NOT_FOUND"

    def test_invalid_session_identifier_is_400(self):
        with _client(_gateway()) as client:
            response = client.get("/device/v1/pair/not-a-session")
            assert response.status_code == 400
            assert _error_code(response) == "DEVICE_INVALID_REQUEST"

    def test_malformed_json_is_400(self):
        with _client(_gateway()) as client:
            session_id = client.post(
                "/device/v1/pair/start",
            ).json()["session_id"]
            response = client.post(
                f"/device/v1/pair/{session_id}/connect",
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 400
            assert _error_code(response) == "DEVICE_INVALID_REQUEST"
