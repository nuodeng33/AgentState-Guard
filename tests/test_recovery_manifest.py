"""Adversarial Snapshot V3 manifest validation contracts."""

from __future__ import annotations

from copy import deepcopy
from pathlib import PureWindowsPath

import pytest

import agentguard.recovery.manifest as manifest_module
from agentguard.evidence.canonical import canonical_json
from agentguard.recovery.manifest import manifest_digest, validate_snapshot_v3
from agentguard.recovery.policy import RestorePolicy
from agentguard.recovery.workspace_permissions import PosixPermissionBackend
from agentguard.recovery.workspace_policy import scan_workspace


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


def _workspace_snapshot(tmp_path):
    target = tmp_path / "source.txt"
    target.write_text("safe content", encoding="utf-8")
    scan = scan_workspace(tmp_path, permission_backend=PosixPermissionBackend())
    coverage = scan.entries[0]
    workspace_id = "workspace-" + "a" * 24
    logical_path = f"/workspace/{workspace_id}/{coverage.relative_path}"
    manifest_entry = {
        "domain": "windows-current",
        "logical_path": logical_path,
        "classification": "restorable",
        "blob_sha256": coverage.content_digest,
        "size": coverage.size,
        "mode": coverage.permission_proof.values["mode"],
        "uid": None,
        "gid": None,
        "validator": "workspace-hash-permission-v1",
        "sha256": coverage.content_digest,
        "status": "WORKSPACE_RESTORABLE",
    }
    extension = {
        "schema_version": 1,
        "workspace_id": workspace_id,
        "scope_observation_id": "workspace-observation-" + "b" * 24,
        "execution_domain_id": "windows-current",
        "root_digest": "sha256:" + "c" * 64,
        "coverage": [coverage.to_extension_dict()],
        "coverage_counts": scan.counts,
        "coverage_digest": scan.coverage_digest,
        "scan_complete": True,
        "scan_reason_code": "WORKSPACE_SCAN_COMPLETE",
    }
    return {
        "format_version": 3,
        "manifest": [manifest_entry],
        "blobs": {coverage.content_digest: coverage.content},
        "workspace": extension,
    }


def test_workspace_snapshot_extension_is_strict_and_verified(tmp_path):
    snapshot = _workspace_snapshot(tmp_path)

    valid, reason_code, digest = validate_snapshot_v3(
        snapshot,
        expected_domain="windows-current",
    )

    assert valid is True
    assert reason_code == "RECOVERY_MANIFEST_VERIFIED"
    assert digest == manifest_digest(snapshot)


def test_workspace_logical_paths_are_posix_even_under_windows_path_semantics(
    tmp_path, monkeypatch
):
    snapshot = _workspace_snapshot(tmp_path)
    monkeypatch.setattr(manifest_module, "PurePath", PureWindowsPath, raising=False)

    valid, reason_code, digest = validate_snapshot_v3(
        snapshot,
        expected_domain="windows-current",
    )

    assert valid is True
    assert reason_code == "RECOVERY_MANIFEST_VERIFIED"
    assert digest == manifest_digest(snapshot)


def test_legacy_manifest_digest_remains_byte_for_byte_compatible(tmp_path):
    snapshot = _snapshot(tmp_path)
    expected = __import__("hashlib").sha256(
        canonical_json(snapshot["manifest"]).encode("utf-8")
    ).hexdigest()

    assert manifest_digest(snapshot) == expected


def test_workspace_manifest_digest_binds_coverage(tmp_path):
    snapshot = _workspace_snapshot(tmp_path)
    original = manifest_digest(snapshot)
    snapshot["workspace"]["coverage"][0]["reason_code"] = "TAMPERED"

    assert manifest_digest(snapshot) != original
    valid, reason_code, _digest = validate_snapshot_v3(snapshot)
    assert valid is False
    assert reason_code == "RECOVERY_WORKSPACE_EXTENSION_INVALID"


def test_workspace_extension_rejects_permission_proof_tampering(tmp_path):
    snapshot = _workspace_snapshot(tmp_path)
    snapshot["workspace"]["coverage"][0]["permission_proof"]["values"][
        "mode"
    ] = "0o777"

    valid, reason_code, _digest = validate_snapshot_v3(snapshot)

    assert valid is False
    assert reason_code == "RECOVERY_WORKSPACE_EXTENSION_INVALID"


def test_workspace_extension_rejects_noncanonical_coverage_order(tmp_path):
    snapshot = _workspace_snapshot(tmp_path)
    second = deepcopy(snapshot["workspace"]["coverage"][0])
    second["relative_path"] = "aaa.txt"
    second["category"] = "excluded"
    second["reason_code"] = "WORKSPACE_GENERATED_OUTPUT_EXCLUDED"
    second["permission_proof"] = None
    snapshot["workspace"]["coverage"].append(second)
    snapshot["workspace"]["coverage_counts"]["excluded"] = 1
    snapshot["workspace"]["coverage_digest"] = __import__("hashlib").sha256(
        canonical_json(snapshot["workspace"]["coverage"]).encode("utf-8")
    ).hexdigest()

    valid, reason_code, _digest = validate_snapshot_v3(snapshot)

    assert valid is False
    assert reason_code == "RECOVERY_WORKSPACE_EXTENSION_INVALID"
