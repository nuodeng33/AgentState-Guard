"""Tests for WSL list parsing and live domain boundaries."""

from __future__ import annotations

import pytest

from agentguard.discovery.domains.wsl import (
    WslDistroState,
    parse_wsl_list,
    validate_distro_name,
)


def test_empty_wsl_list_is_a_valid_empty_result():
    result = parse_wsl_list("  NAME      STATE      VERSION\n")

    assert result.distros == ()
    assert result.warnings == ()


def test_running_wsl2_distro_is_parsed_from_verbose_columns():
    result = parse_wsl_list(
        "  NAME            STATE      VERSION\n* Ubuntu-22.04    Running    2\n"
    )

    assert len(result.distros) == 1
    assert result.distros[0].name == "Ubuntu-22.04"
    assert result.distros[0].state is WslDistroState.RUNNING
    assert result.distros[0].generation == "WSL2"
    assert result.distros[0].is_default is True


def test_multiple_running_and_stopped_distros_remain_distinct():
    result = parse_wsl_list(
        "  NAME            STATE      VERSION\n"
        "* Ubuntu         Running    2\n"
        "  Debian         Stopped    2\n"
        "  Legacy         Stopped    1\n"
    )

    assert [(item.name, item.state.value, item.generation) for item in result.distros] == [
        ("Ubuntu", "RUNNING", "WSL2"),
        ("Debian", "STOPPED", "WSL2"),
        ("Legacy", "STOPPED", "WSL1"),
    ]


def test_localized_header_and_chinese_state_preserve_distro_as_unknown():
    result = parse_wsl_list(
        "  名称          状态        版本\n"
        "  Ubuntu      正在运行    2\n"
    )

    assert len(result.distros) == 1
    assert result.distros[0].name == "Ubuntu"
    assert result.distros[0].state is WslDistroState.UNKNOWN
    assert result.distros[0].generation == "WSL2"


def test_arbitrary_unknown_state_is_not_treated_as_stopped():
    result = parse_wsl_list(
        "  NAME       STATE       VERSION\n"
        "  Ubuntu    Paused      2\n"
    )

    assert result.distros[0].state is WslDistroState.UNKNOWN


def test_distro_name_with_spaces_is_preserved():
    result = parse_wsl_list(
        "  NAME                  STATE      VERSION\n"
        "  Ubuntu Development    Stopped    2\n"
    )

    assert result.distros[0].name == "Ubuntu Development"


def test_malicious_distro_list_entry_is_rejected_with_machine_warning():
    result = parse_wsl_list(
        "  NAME                         STATE      VERSION\n"
        "  Ubuntu;curl example.invalid  Running    2\n"
    )

    assert result.distros == ()
    assert "INVALID_DISTRO_ENTRY_REJECTED" in result.warnings


@pytest.mark.parametrize(
    "name",
    ["", " Ubuntu", "Ubuntu ", "Ubuntu;curl example.invalid", "Ubuntu\nInjected", 42],
)
def test_distro_name_validation_rejects_non_data_boundaries(name):
    with pytest.raises(ValueError, match="distribution name is invalid"):
        validate_distro_name(name)
