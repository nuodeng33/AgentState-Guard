"""CLI contracts for the local supervision workflow."""

from __future__ import annotations

import json
from pathlib import Path

from agentguard.cli import main


def _create_args(directory: Path, *extra: str) -> list[str]:
    return [
        "--json", "--directory", str(directory), "supervise", "create",
        "--intent-kind", "discovery", "--effect-kind", "read_metadata",
        "--domain", "local", "--target", "runtime-1", "--scope", "runtime-1",
        "--network-effect", "false", "--privilege-effect", "false",
        "--destructive-effect", "false", "--secret-access", "false",
        "--checkpoint-id", "checkpoint-1", *extra,
    ]


def test_supervise_create_show_activate_complete_json(tmp_path, capsys):
    assert main(_create_args(tmp_path)) == 0
    created = json.loads(capsys.readouterr().out)
    session_id = created["session_id"]
    assert created["status"] == "EVALUATED"
    assert "database" not in created

    assert main(["--json", "--directory", str(tmp_path), "supervise", "activate", session_id]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ACTIVE"
    assert main(["--json", "--directory", str(tmp_path), "supervise", "complete", session_id]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "COMPLETED"


def test_supervise_block_does_not_echo_sensitive_input(tmp_path, capsys):
    secret = "synthetic-secret-value"
    assert main(_create_args(tmp_path, "--secret-access", "true", "--target", secret)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["code"] == "POLICY_BLOCK"
    assert secret not in json.dumps(result)


def test_supervise_review_needs_approval(tmp_path, capsys):
    args = _create_args(tmp_path, "--intent-kind", "change", "--effect-kind", "provider_config_change")
    assert main(args) == 0
    created = json.loads(capsys.readouterr().out)
    session_id = created["session_id"]
    assert created["status"] == "AWAITING_APPROVAL"

    assert main(["--json", "--directory", str(tmp_path), "supervise", "activate", session_id]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "APPROVAL_REQUIRED"
    assert main(["--json", "--directory", str(tmp_path), "supervise", "approve", session_id]) == 0
    capsys.readouterr()
    assert main(["--json", "--directory", str(tmp_path), "supervise", "activate", session_id]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "CHECKPOINT_REQUIRED"


def test_supervise_rejects_caller_supplied_recovery_number(tmp_path):
    try:
        main(_create_args(tmp_path, "--recovery-coverage", "1.0"))
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("caller recovery coverage option must not be accepted")


def test_supervise_unknown_has_stable_code(tmp_path, capsys):
    args = _create_args(tmp_path, "--domain", "", "--effect-kind", "unclassified")
    assert main(args) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "POLICY_UNKNOWN"


def test_supervise_blocked_session_cannot_activate(tmp_path, capsys):
    assert main(_create_args(tmp_path, "--secret-access", "true")) == 1
    session_id = json.loads(capsys.readouterr().out)["session_id"]

    assert main(["--json", "--directory", str(tmp_path), "supervise", "activate", session_id]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "REJECTED"
    assert result["code"] == "POLICY_BLOCK"


def test_supervise_show_reject_and_fail_follow_lifecycle(tmp_path, capsys):
    args = _create_args(tmp_path, "--intent-kind", "change", "--effect-kind", "provider_config_change")
    assert main(args) == 0
    session_id = json.loads(capsys.readouterr().out)["session_id"]

    assert main(["--json", "--directory", str(tmp_path), "supervise", "show", session_id]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "AWAITING_APPROVAL"
    assert main(["--json", "--directory", str(tmp_path), "supervise", "reject", session_id]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "REJECTED"

    assert main(_create_args(tmp_path)) == 0
    active_id = json.loads(capsys.readouterr().out)["session_id"]
    assert main(["--json", "--directory", str(tmp_path), "supervise", "activate", active_id]) == 0
    capsys.readouterr()
    assert main(["--json", "--directory", str(tmp_path), "supervise", "fail", active_id]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "FAILED"
