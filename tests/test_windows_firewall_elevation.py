"""Product-owned, fixed-scope Windows firewall elevation contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentguard.device_link.errors import DeviceLinkError
from agentguard.device_link.network import LanCandidate, ScopedFirewall
from agentguard.device_link.windows_elevation import (
    ElevationLaunchError,
    WindowsFirewallElevationRunner,
)


def _candidate() -> LanCandidate:
    return LanCandidate(
        "Ethernet", "Intel", "192.168.50.8", 24, True, 10, 5, True
    )


def _installed_paths(tmp_path: Path) -> tuple[Path, Path]:
    desktop = tmp_path / "AgentState Guard.exe"
    sidecar = tmp_path / "agentguard-sidecar.exe"
    desktop.write_bytes(b"desktop")
    sidecar.write_bytes(b"sidecar")
    return desktop, sidecar


def test_elevation_refuses_missing_or_non_product_helper_path(tmp_path):
    _desktop, sidecar = _installed_paths(tmp_path)
    calls = []
    missing = WindowsFirewallElevationRunner(
        helper_path=tmp_path / "missing.exe",
        sidecar_path=sidecar,
        launcher=lambda *_args: calls.append(_args) or 0,
    )

    result = missing.apply("192.168.50.8", 24)

    assert result.ok is False
    assert result.reason_code == "DEVICE_FIREWALL_ELEVATION_UNAVAILABLE"
    assert calls == []

    other = tmp_path / "other"
    other.mkdir()
    foreign = other / "AgentState Guard.exe"
    foreign.write_bytes(b"foreign")
    untrusted = WindowsFirewallElevationRunner(
        helper_path=foreign,
        sidecar_path=sidecar,
        launcher=lambda *_args: calls.append(_args) or 0,
    )
    assert untrusted.apply("192.168.50.8", 24).reason_code == (
        "DEVICE_FIREWALL_ELEVATION_UNAVAILABLE"
    )
    assert calls == []


def test_elevation_accepts_only_fixed_operation_and_private_scope(tmp_path):
    desktop, sidecar = _installed_paths(tmp_path)
    calls = []
    runner = WindowsFirewallElevationRunner(
        helper_path=desktop,
        sidecar_path=sidecar,
        launcher=lambda path, args: calls.append((path, args)) or 0,
    )

    invalid = runner.apply("198.18.0.1", 24)
    applied = runner.apply("192.168.50.8", 24)
    removed = runner.remove("192.168.50.8", 24)

    assert invalid.reason_code == "DEVICE_FIREWALL_SCOPE_INVALID"
    assert applied.ok is True
    assert removed.ok is True
    assert calls == [
        (
            desktop.resolve(),
            (
                "--asg-firewall-helper",
                "apply",
                "--address",
                "192.168.50.8",
                "--prefix",
                "24",
            ),
        ),
        (
            desktop.resolve(),
            (
                "--asg-firewall-helper",
                "remove",
                "--address",
                "192.168.50.8",
                "--prefix",
                "24",
            ),
        ),
    ]


@pytest.mark.parametrize(
    ("error_code", "reason_code"),
    [
        (1223, "DEVICE_FIREWALL_ELEVATION_DECLINED"),
        (2, "DEVICE_FIREWALL_ELEVATION_FAILED"),
    ],
)
def test_elevation_launch_failures_have_stable_reason_codes(
    tmp_path, error_code, reason_code
):
    desktop, sidecar = _installed_paths(tmp_path)

    def _failed(*_args):
        raise ElevationLaunchError(error_code)

    runner = WindowsFirewallElevationRunner(
        helper_path=desktop,
        sidecar_path=sidecar,
        launcher=_failed,
    )

    assert runner.apply("10.0.0.5", 24).reason_code == reason_code


def test_scoped_firewall_uses_elevation_and_never_starts_unscoped_netsh(tmp_path):
    desktop, sidecar = _installed_paths(tmp_path)
    calls = []
    elevation = WindowsFirewallElevationRunner(
        helper_path=desktop,
        sidecar_path=sidecar,
        launcher=lambda path, args: calls.append((path, args)) or 0,
    )
    firewall = ScopedFirewall(platform_name="nt", elevation_runner=elevation)

    firewall.apply(_candidate())
    firewall.remove()

    assert [call[1][1] for call in calls] == ["apply", "remove"]
    assert all("netsh.exe" not in call[1] for call in calls)
    assert firewall.status()["operation"] == "REMOVE"
    assert firewall.status()["reason_code"] == "DEVICE_FIREWALL_REMOVED"
    assert len(firewall.status()["scope_digest"]) == 64


def test_elevation_decline_is_fail_closed_before_listener_start(tmp_path):
    desktop, sidecar = _installed_paths(tmp_path)
    elevation = WindowsFirewallElevationRunner(
        helper_path=desktop,
        sidecar_path=sidecar,
        launcher=lambda *_args: (_ for _ in ()).throw(ElevationLaunchError(1223)),
    )
    firewall = ScopedFirewall(platform_name="nt", elevation_runner=elevation)

    with pytest.raises(DeviceLinkError) as denied:
        firewall.apply(_candidate())

    assert denied.value.code == "DEVICE_FIREWALL_ELEVATION_DECLINED"


def test_rust_helper_source_has_no_profile_mutation_or_generic_shell():
    rust_root = Path(__file__).parents[1] / "desktop" / "src-tauri" / "src"
    source = (rust_root / "firewall_helper.rs").read_text(encoding="utf-8")
    sidecar = (rust_root / "sidecar.rs").read_text(encoding="utf-8")
    main = (rust_root / "main.rs").read_text(encoding="utf-8")

    assert "Set-NetConnectionProfile" not in source
    assert "cmd.exe" not in source
    assert 'Command::new("netsh.exe")' in source
    assert '"profile=private"' in source
    assert '.env("ASG_DESKTOP_EXECUTABLE", desktop_executable)' in sidecar
    assert main.index("firewall_helper::run_if_requested") < main.index(
        "tauri::Builder::default"
    )
