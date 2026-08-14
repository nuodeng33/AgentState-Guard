"""Product-owned checkpoint, test-restore, and restore HTTP contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from agentguard.api.server import create_app
from agentguard.discovery import (
    CapabilityStatus,
    DiscoverySnapshot,
    RuntimeDescriptor,
)


class _FixedDiscovery:
    def discover(self) -> DiscoverySnapshot:
        return DiscoverySnapshot(
            snapshot_id="product-recovery-domain",
            observed_at=datetime(2026, 8, 14, tzinfo=UTC),
            runtimes=(
                RuntimeDescriptor(
                    runtime_id="core-runtime",
                    runtime_type="SELF_RUNTIME",
                    domain_id="windows-current",
                    status=CapabilityStatus.AVAILABLE,
                ),
            ),
            status=CapabilityStatus.AVAILABLE,
        )


def _client(root: Path) -> tuple[TestClient, dict[str, str]]:
    client = TestClient(
        create_app(
            state_db_path=root / "state.db",
            config={"base_dir": str(root)},
            discovery_service=_FixedDiscovery(),
        )
    )
    token = client.get("/api/session").json()["token"]
    return client, {"X-Session-Token": token}


def test_product_recovery_round_trip_uses_server_owned_target(tmp_path):
    client, headers = _client(tmp_path)
    target = tmp_path / "config" / "agentguard.toml"
    original = target.read_bytes()

    created = client.post("/api/v1/recovery/checkpoints", json={}, headers=headers)
    assert created.status_code == 200
    checkpoint_id = created.json()["checkpoint_id"]
    assert created.json()["reason_code"] == "RECOVERY_SNAPSHOT_CREATED"

    target.write_text("[checks]\nport = 9999\n", encoding="utf-8")
    test_result = client.post(
        f"/api/v1/recovery/{checkpoint_id}/test", json={}, headers=headers
    )
    assert test_result.status_code == 200
    assert test_result.json()["reason_code"] == "TEST_RESTORE_VERIFIED"
    assert target.read_text(encoding="utf-8") == "[checks]\nport = 9999\n"

    denied = client.post(
        f"/api/v1/recovery/{checkpoint_id}/restore",
        json={"confirm": False},
        headers=headers,
    )
    assert denied.status_code == 409
    assert denied.json()["reason_code"] == "RECOVERY_CONFIRMATION_REQUIRED"

    restored = client.post(
        f"/api/v1/recovery/{checkpoint_id}/restore",
        json={"confirm": True},
        headers=headers,
    )
    assert restored.status_code == 200
    assert restored.json()["reason_code"] == "RECOVERY_RESTORED_AND_VERIFIED"
    assert target.read_bytes() == original

    projection = client.get("/api/v1/recovery", headers=headers).json()
    assert projection["checkpoint_count"] == 1
    assert projection["latest_checkpoint"]["checkpoint_id"] == checkpoint_id
    assert projection["test_restore_status"] == "VERIFIED_R2"
    assert projection["actual_restore_status"] == "VERIFIED"
    assert projection["recovery_verified"] is True


def test_product_recovery_rejects_caller_selected_authority(tmp_path):
    client, headers = _client(tmp_path)

    invalid_create = client.post(
        "/api/v1/recovery/checkpoints",
        json={"target_path": "C:/caller-selected"},
        headers=headers,
    )
    assert invalid_create.status_code == 422
    assert invalid_create.json()["reason_code"] == "RECOVERY_REQUEST_INVALID"

    invalid_restore = client.post(
        "/api/v1/recovery/1/restore",
        json={"confirm": True, "execution_domain_id": "caller-domain"},
        headers=headers,
    )
    assert invalid_restore.status_code == 422
    assert invalid_restore.json()["reason_code"] == "RECOVERY_REQUEST_INVALID"


def test_product_restore_fails_closed_when_target_becomes_symlink(tmp_path):
    client, headers = _client(tmp_path)
    target = tmp_path / "config" / "agentguard.toml"
    created = client.post("/api/v1/recovery/checkpoints", json={}, headers=headers)
    checkpoint_id = created.json()["checkpoint_id"]
    other = tmp_path / "other.toml"
    other.write_text("safe = true\n", encoding="utf-8")
    target.unlink()
    try:
        target.symlink_to(other)
    except OSError:
        return

    restored = client.post(
        f"/api/v1/recovery/{checkpoint_id}/restore",
        json={"confirm": True},
        headers=headers,
    )

    assert restored.status_code == 409
    assert restored.json()["status"] == "UNCHANGED"
    assert other.read_text(encoding="utf-8") == "safe = true\n"
