"""CLI contracts for controlled recovery trust state."""

from __future__ import annotations

import json

import pytest

from agentguard.cli import main


def _command(directory, *args: str) -> list[str]:
    return ["--json", "--directory", str(directory), "recovery", *args]


def test_recovery_help_lists_only_inspection_and_baseline_lifecycle():
    with pytest.raises(SystemExit) as raised:
        main(["recovery", "--help"])
    assert raised.value.code == 0


def test_recovery_drill_show_not_found_has_safe_structured_result(tmp_path, capsys):
    assert main(_command(tmp_path, "drill", "show", "unknown-drill")) == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "FAILED", "reason_code": "DRILL_NOT_FOUND"}
    assert str(tmp_path) not in json.dumps(result)


def test_recovery_baseline_create_requires_authoritative_r3(tmp_path, capsys):
    assert main(_command(
        tmp_path,
        "baseline",
        "create",
        "--checkpoint-id",
        "1",
        "--domain",
        "self-runtime",
    )) == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "FAILED", "reason_code": "TRUSTED_BASELINE_R3_REQUIRED"}
    assert str(tmp_path) not in json.dumps(result)


def test_recovery_baseline_approve_confirm_retire_show_fail_closed(tmp_path, capsys):
    for command, expected in (
        (("approve", "unknown-candidate"), "TRUSTED_BASELINE_CONFIRMATION_INVALID"),
        (("confirm", "unknown-candidate", "--authorization-id", "unknown", "--nonce", "unknown"), "TRUSTED_BASELINE_CONFIRMATION_INVALID"),
        (("retire", "unknown-baseline", "--reason-code", "OPERATOR_RETIRED"), "TRUSTED_BASELINE_NOT_FOUND"),
        (("show", "unknown-baseline"), "TRUSTED_BASELINE_NOT_FOUND"),
    ):
        assert main(_command(tmp_path, "baseline", *command)) == 1
        assert json.loads(capsys.readouterr().out) == {
            "status": "FAILED",
            "reason_code": expected,
        }
