"""Adversarial Snapshot V3 manifest validation contracts."""

from __future__ import annotations

from copy import deepcopy

import pytest

from agentguard.recovery.manifest import validate_snapshot_v3
from agentguard.recovery.policy import RestorePolicy


def _snapshot(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    return RestorePolicy(
        approved_paths={"domain-1": (target,)},
        validators={"domain-1": "toml-parse"},
    ).snapshot_v3(target, "domain-1", user_approved=True)


@pytest.mark.parametrize(
    "field",
    ["domain", "logical_path", "classification", "blob_sha256", "size", "mode", "uid", "gid", "validator", "sha256", "status"],
)
def test_snapshot_v3_requires_the_complete_entry_contract(tmp_path, field):
    snapshot = _snapshot(tmp_path)
    del snapshot["manifest"][0][field]

    valid, reason_code, digest = validate_snapshot_v3(snapshot, expected_domain="domain-1")

    assert valid is False
    assert reason_code == "RECOVERY_MANIFEST_INVALID"
    assert digest is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("size", 999), ("sha256", "0" * 64), ("blob_sha256", "0" * 64)],
)
def test_snapshot_v3_binds_blob_size_and_digests(tmp_path, field, value):
    snapshot = _snapshot(tmp_path)
    snapshot["manifest"][0][field] = value

    valid, reason_code, _digest = validate_snapshot_v3(snapshot, expected_domain="domain-1")

    assert valid is False
    assert reason_code == "RECOVERY_MANIFEST_INVALID"


def test_snapshot_v3_rejects_cross_domain_interpretation(tmp_path):
    snapshot = _snapshot(tmp_path)

    valid, reason_code, _digest = validate_snapshot_v3(snapshot, expected_domain="other-domain")

    assert valid is False
    assert reason_code == "RECOVERY_DOMAIN_MISMATCH"


def test_snapshot_v3_rejects_unreferenced_blobs(tmp_path):
    snapshot = deepcopy(_snapshot(tmp_path))
    snapshot["blobs"]["0" * 64] = b"unreferenced"

    valid, reason_code, _digest = validate_snapshot_v3(snapshot, expected_domain="domain-1")

    assert valid is False
    assert reason_code == "RECOVERY_MANIFEST_INVALID"
