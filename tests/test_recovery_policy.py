"""P6 restore-policy contracts."""

from __future__ import annotations

from agentguard.recovery.policy import RestorePolicy, RestoreStatus


def test_default_policy_is_audit_only(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("safe=true")

    decision = RestorePolicy().classify(target, "domain-1")

    assert decision.mode == "audit_only"
    assert decision.status is RestoreStatus.USER_APPROVAL_REQUIRED


def test_restorable_requires_all_explicit_safety_gates(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    policy = RestorePolicy(
        approved_paths={"domain-1": (target,)},
        validators={"domain-1": "toml-parse"},
    )

    decision = policy.classify(target, "domain-1", user_approved=True)

    assert decision.mode == "restorable"
    assert decision.validator == "toml-parse"


def test_sensitive_content_can_only_downgrade_to_audit_only(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("API_KEY=synthetic-secret-value")
    policy = RestorePolicy(
        approved_paths={"domain-1": (target,)},
        validators={"domain-1": "toml-parse"},
    )

    decision = policy.classify(target, "domain-1", user_approved=True)

    assert decision.mode == "audit_only"
    assert decision.status is RestoreStatus.SENSITIVE_DOWNGRADED


def test_domain_and_path_mismatch_cannot_be_restorable(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    policy = RestorePolicy(
        approved_paths={"other-domain": (target,)},
        validators={"domain-1": "toml-parse"},
    )

    decision = policy.classify(target, "domain-1", user_approved=True)

    assert decision.mode == "audit_only"
    assert decision.status is RestoreStatus.PATH_NOT_APPROVED


def test_symlink_target_cannot_be_restorable_even_when_explicitly_listed(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    alias = tmp_path / "config-link.toml"
    alias.symlink_to(target)
    policy = RestorePolicy(
        approved_paths={"domain-1": (alias,)},
        validators={"domain-1": "toml-parse"},
    )

    decision = policy.classify(alias, "domain-1", user_approved=True)

    assert decision.mode == "audit_only"
    assert decision.status is RestoreStatus.PATH_NOT_APPROVED


def test_traversal_alias_cannot_match_an_approved_path(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    (tmp_path / "nested").mkdir()
    traversal_alias = tmp_path / "nested" / ".." / "config.toml"
    policy = RestorePolicy(
        approved_paths={"domain-1": (target,)},
        validators={"domain-1": "toml-parse"},
    )

    decision = policy.classify(traversal_alias, "domain-1", user_approved=True)

    assert decision.mode == "audit_only"
    assert decision.status is RestoreStatus.PATH_NOT_APPROVED


def test_snapshot_v3_manifest_uses_content_addressed_blob(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    policy = RestorePolicy(
        approved_paths={"domain-1": (target,)},
        validators={"domain-1": "toml-parse"},
    )

    snapshot = policy.snapshot_v3(target, "domain-1", user_approved=True)

    assert snapshot["format_version"] == 3
    entry = snapshot["manifest"][0]
    assert entry["logical_path"] == str(target)
    assert entry["classification"] == "restorable"
    assert entry["blob_sha256"] in snapshot["blobs"]
    assert entry["validator"] == "toml-parse"


def test_snapshot_binds_classification_and_blob_to_one_file_read(tmp_path, monkeypatch):
    target = tmp_path / "config.toml"
    target.write_text("safe=true")
    policy = RestorePolicy(
        approved_paths={"domain-1": (target,)},
        validators={"domain-1": "toml-parse"},
    )
    secret = "API_KEY=synthetic-secret-value"

    def mutate_after_scan(_text):
        target.write_text(secret)
        return False

    monkeypatch.setattr("agentguard.recovery.policy.contains_sensitive_data", mutate_after_scan)

    snapshot = policy.snapshot_v3(target, "domain-1", user_approved=True)

    assert secret.encode() not in snapshot["blobs"].values()
