"""Product projection contracts over the existing verified Evidence Ledger."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from agentguard.api.r4_projection import R4ReadProjectionService
from agentguard.discovery import CapabilityStatus, DiscoverySnapshot, ProbeEvidence
from agentguard.evidence.discovery_adapter import record_discovery_snapshot
from agentguard.evidence.ledger import EvidenceLedger
from agentguard.evidence.models import EventFamily, EventType, EvidenceEvent
from agentguard.policy.models import Decision, PolicyDecision
from agentguard.storage.db import StateDB
from agentguard.storage.snapshots import SnapshotStore
from agentguard.supervision.service import SupervisionService

OBSERVED_AT = datetime(2026, 8, 14, 1, 2, 3, tzinfo=UTC)


def _decision() -> PolicyDecision:
    return PolicyDecision(
        decision=Decision.REVIEW,
        severity="MEDIUM",
        matched_rule_ids=("product-review",),
        summary_code="PRODUCT_REVIEW",
        evidence_refs=("source-evidence",),
        uncertainties=(),
        required_checks=("human_review",),
        requires_checkpoint=True,
        requires_manual_approval=True,
    )


def _projector(tmp_path):
    database = StateDB(tmp_path / "state.db")
    database.connect()
    projector = R4ReadProjectionService(database, SnapshotStore(tmp_path / "snapshots"))
    return database, projector


def test_runtime_projection_includes_authoritative_observation_time(tmp_path):
    database, projector = _projector(tmp_path)
    try:
        record_discovery_snapshot(
            database,
            DiscoverySnapshot(
                snapshot_id="product-projection-observation",
                observed_at=OBSERVED_AT,
                evidence=(
                    ProbeEvidence(
                        evidence_id="runtime-observation",
                        collector="test-product-projection",
                        source="local-runtime",
                        observed_at=OBSERVED_AT,
                        fact_type="runtime.metadata",
                        value={
                            "runtime_kind": "SELF_RUNTIME",
                            "execution_domain_id": "windows-current",
                            "capabilities": ["self_visible"],
                        },
                        status=CapabilityStatus.AVAILABLE,
                        sanitized=True,
                    ),
                ),
                status=CapabilityStatus.AVAILABLE,
            ),
            recorded_at=OBSERVED_AT,
        )

        runtime = projector.runtime()

        assert runtime["observed_at"] == OBSERVED_AT.isoformat()
        assert runtime["items"][0]["observed_at"] == OBSERVED_AT.isoformat()
    finally:
        database.close()


def test_changes_and_supervision_share_verified_activity_projection(tmp_path):
    database, projector = _projector(tmp_path)
    try:
        session = SupervisionService(database).create("product intent", _decision())
        change_id = "change-product-001"
        with database.transaction() as connection:
            EvidenceLedger().append(
                connection,
                EvidenceEvent(
                    schema_version=1,
                    event_id=change_id,
                    recorded_at=OBSERVED_AT,
                    observed_at=OBSERVED_AT,
                    event_family=EventFamily.CHANGE,
                    event_type=EventType.OBSERVED_CHANGE,
                    source="r4-config-change",
                    result="CHANGED",
                    execution_domain_id="windows-current",
                    supervision_session_id=session.supervision_session_id,
                    transaction_id="change-transaction-001",
                    checkpoint_id="7",
                    subject_ref="target:" + "a" * 64,
                    evidence_refs=(session.supervision_session_id,),
                    payload_safe={
                        "before_digest": "b" * 64,
                        "after_digest": "c" * 64,
                        "target_ref_digest": "a" * 64,
                        "verification": {"result": "PASS"},
                        "debug_detail": "raw-payload-must-not-project",
                    },
                ),
            )

        changes = projector.changes()
        supervision = projector.supervision()

        assert changes["items"][0]["event_id"] == change_id
        assert changes["items"][0]["verification_summary"] == "PASS"
        assert changes["items"][0]["affected_objects"] == ["a" * 64]
        projected_session = next(
            item
            for item in supervision["items"]
            if item["supervision_session_id"] == session.supervision_session_id
        )
        assert change_id in {
            item["event_id"] for item in projected_session["recent_verified_activities"]
        }
        assert projected_session["pending_approval"] is True
        assert projected_session["current_task"] is None
        assert projected_session["current_phase"] is None
        assert projected_session["current_action"] is None
    finally:
        database.close()


def test_evidence_detail_is_bounded_and_does_not_return_arbitrary_payload(tmp_path):
    database, projector = _projector(tmp_path)
    try:
        event_id = "bounded-evidence-detail"
        with database.transaction() as connection:
            receipt = EvidenceLedger().append(
                connection,
                EvidenceEvent(
                    schema_version=1,
                    event_id=event_id,
                    recorded_at=OBSERVED_AT,
                    observed_at=OBSERVED_AT,
                    event_family=EventFamily.CHANGE,
                    event_type=EventType.OBSERVED_CHANGE,
                    source="r4-config-change",
                    result="CHANGED",
                    execution_domain_id="windows-current",
                    supervision_session_id="session-product-detail",
                    transaction_id="change-product-detail",
                    checkpoint_id="8",
                    subject_ref="target:" + "d" * 64,
                    evidence_refs=("safe-related-ref",),
                    payload_safe={
                        "reason_code": "CONTROLLED_CHANGE_COMPLETED",
                        "target_ref_digest": "d" * 64,
                        "verification": {"result": "PASS"},
                        "debug_detail": "raw-payload-must-not-project",
                    },
                ),
            )

        detail = projector.evidence(event_id)
        encoded = json.dumps(detail)

        assert detail["event_id"] == event_id
        assert detail["event_type"] == "OBSERVED_CHANGE"
        assert detail["chain_ref"] == receipt.curr_hash
        assert detail["sanitized_detail"] == {
            "affected_objects": ["d" * 64],
            "verification": "PASS",
        }
        assert "raw-payload-must-not-project" not in encoded
        assert "payload_safe" not in detail
    finally:
        database.close()


def test_unknown_evidence_id_is_not_found_without_query_expansion(tmp_path):
    database, projector = _projector(tmp_path)
    try:
        assert projector.evidence("missing-event") == {
            "schema_version": "r4-product-evidence-1",
            "status": "NOT_FOUND",
            "reason_code": "EVIDENCE_EVENT_NOT_FOUND",
            "event_id": "missing-event",
        }
    finally:
        database.close()
