"""Focused product-contract tests for the shared Protection State projections."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agentguard.api.r4_projection import R4ReadProjectionService
from agentguard.evidence.ledger import EvidenceLedger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore
from tests.test_workspace_checkpoint import _client


OBSERVED_AT = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def _projector(tmp_path):
    database = StateDB(tmp_path / "state.db")
    database.connect()
    return database, R4ReadProjectionService(
        database,
        SnapshotStore(tmp_path / "snapshots"),
    )


def _append(
    database: StateDB,
    *,
    index: int,
    event_type: EventType,
    family: EventFamily,
    checkpoint_id: str | None = None,
    workspace_id: str | None = None,
    change_kind: str | None = None,
) -> None:
    payload = {"reason_code": event_type.value}
    if workspace_id is not None:
        payload["workspace_id"] = workspace_id
    if change_kind is not None:
        payload.update(
            {
                "attribution": "UNATTRIBUTED",
                "change_kind": change_kind,
                "recovery_disposition": "RECOVERABLE",
                "target_ref_digest": f"{index:064x}",
            }
        )
    with database.transaction() as connection:
        EvidenceLedger().append(
            connection,
            EvidenceEvent(
                schema_version=1,
                event_id=f"product-event-{index:04d}",
                recorded_at=OBSERVED_AT + timedelta(seconds=index),
                observed_at=OBSERVED_AT + timedelta(seconds=index),
                event_family=family,
                event_type=event_type,
                source="product-contract-test",
                result=change_kind or "AVAILABLE",
                execution_domain_id="docker-domain",
                supervision_session_id=None,
                transaction_id=(f"change-{index:04d}" if change_kind else None),
                checkpoint_id=checkpoint_id,
                subject_ref=workspace_id,
                evidence_refs=(),
                payload_safe=payload,
            ),
        )


def test_changes_default_hides_process_noise_and_pages_protection_history(tmp_path):
    database, projector = _projector(tmp_path)
    try:
        for index in range(505):
            _append(
                database,
                index=index,
                event_type=(
                    EventType.PROCESS_STARTED if index % 2 == 0 else EventType.PROCESS_EXITED
                ),
                family=EventFamily.SUPERVISION,
            )
        meaningful = (
            (EventType.CHECKPOINT_CREATED, EventFamily.RECOVERY, None),
            (EventType.OBSERVED_CHANGE, EventFamily.CHANGE, "ADDED"),
            (EventType.OBSERVED_CHANGE, EventFamily.CHANGE, "MODIFIED"),
            (EventType.OBSERVED_CHANGE, EventFamily.CHANGE, "DELETED"),
            (EventType.OBSERVED_CHANGE, EventFamily.CHANGE, "TYPE_CHANGED"),
            (EventType.OBSERVED_CHANGE, EventFamily.CHANGE, "METADATA_CHANGED"),
            (EventType.RECOVERY_DRILL_VERIFIED, EventFamily.RECOVERY, None),
        )
        for offset, (event_type, family, change_kind) in enumerate(meaningful, start=505):
            _append(
                database,
                index=offset,
                event_type=event_type,
                family=family,
                checkpoint_id="41",
                workspace_id="workspace-product",
                change_kind=change_kind,
            )

        first = projector.changes(limit=3)
        pages = [first]
        while pages[-1]["next_cursor"] is not None:
            pages.append(
                projector.changes(
                    limit=3,
                    before_sequence=pages[-1]["next_cursor"],
                )
            )
        combined = [item for page in pages for item in page["items"]]

        assert first["page_size"] == 3
        assert first["next_cursor"] is not None
        assert pages[-1]["next_cursor"] is None
        assert len(combined) == 7
        assert not {"PROCESS_STARTED", "PROCESS_EXITED"} & {
            item["type"] for item in combined
        }
        assert {item["category"] for item in combined} >= {
            "CHANGE",
            "CHECKPOINT",
            "VERIFICATION",
        }
        assert all(isinstance(item["sequence"], int) for item in combined)
        assert all(item["source"] == "product-contract-test" for item in combined)
        assert {
            item["change_kind"]
            for item in combined
            if item["category"] == "CHANGE"
        } == {"ADDED", "MODIFIED", "DELETED", "TYPE_CHANGED", "METADATA_CHANGED"}

        raw = projector.changes(limit=100, include_process_activity=True)
        assert {"PROCESS_STARTED", "PROCESS_EXITED"} & {
            item["type"] for item in raw["items"]
        }
    finally:
        database.close()


def test_workspace_activity_is_not_projected_as_checkpoint_relative_change(tmp_path):
    database, projector = _projector(tmp_path)
    try:
        _append(
            database,
            index=1,
            event_type=EventType.WORKSPACE_ACTIVITY_OBSERVED,
            family=EventFamily.CHANGE,
            workspace_id="workspace-product",
        )
        _append(
            database,
            index=2,
            event_type=EventType.OBSERVED_CHANGE,
            family=EventFamily.CHANGE,
            checkpoint_id="42",
            workspace_id="workspace-product",
            change_kind="TYPE_CHANGED",
        )

        by_type = {item["type"]: item for item in projector.changes()["items"]}

        assert by_type["WORKSPACE_ACTIVITY_OBSERVED"]["category"] == "WORKSPACE_ACTIVITY"
        assert by_type["WORKSPACE_ACTIVITY_OBSERVED"]["change_kind"] is None
        assert by_type["WORKSPACE_ACTIVITY_OBSERVED"]["protection_state"] != "CHANGE_PROVEN"
        assert by_type["OBSERVED_CHANGE"]["category"] == "CHANGE"
        assert by_type["OBSERVED_CHANGE"]["protection_state"] == "CHANGE_PROVEN"
        assert by_type["OBSERVED_CHANGE"]["actor_attribution"] == "UNATTRIBUTED"
    finally:
        database.close()


def test_change_and_recovery_share_checkpoint_protection_and_reverse_trace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "source.txt"
    target.write_text("before", encoding="utf-8")
    client, headers, _product_state = _client(tmp_path, workspace)

    checkpoint = client.post(
        "/api/v1/recovery/checkpoints", json={}, headers=headers
    ).json()
    target.write_text("after", encoding="utf-8")
    assert client.post(
        "/api/v1/discovery/refresh", json={}, headers=headers
    ).status_code == 200

    before_restore = client.get("/api/v1/changes", headers=headers).json()
    change = next(
        item
        for item in before_restore["items"]
        if item["type"] == "OBSERVED_CHANGE"
    )
    assert client.post(
        f"/api/v1/recovery/{checkpoint['checkpoint_id']}/test",
        json={},
        headers=headers,
    ).status_code == 200
    assert client.post(
        f"/api/v1/recovery/{checkpoint['checkpoint_id']}/restore",
        json={"confirm": True},
        headers=headers,
    ).status_code == 200

    changes = client.get("/api/v1/changes", headers=headers).json()
    recovery = client.get("/api/v1/recovery", headers=headers).json()
    current_change = next(
        item for item in changes["items"] if item["event_id"] == change["event_id"]
    )
    recovered_checkpoint = recovery["latest_checkpoint"]

    assert current_change["workspace_id"] == recovered_checkpoint["workspace_id"]
    assert current_change["checkpoint_id"] == recovered_checkpoint["checkpoint_id"]
    assert recovered_checkpoint["storage_kind"] == "HOST_PATH", recovered_checkpoint
    assert current_change["storage_kind"] == recovered_checkpoint["storage_kind"], current_change
    assert current_change["protection_state"] == recovered_checkpoint["protection_state"] == "RECOVERY_VERIFIED"
    assert current_change["verification_state"] == recovered_checkpoint["verification_state"] == "RECOVERY_VERIFIED"
    assert recovered_checkpoint["related_change_count"] == 1
    assert recovered_checkpoint["related_change_event_ids"] == [change["event_id"]]
    assert recovered_checkpoint["related_changes_truncated"] is False
