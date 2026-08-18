"""Packaged MSI firewall helper resolution regression (PACKAGED_MSI_WINDOWS_BLOCKER).

Physical MSI dogfood (SHA 62aff59) reported
``DEVICE_FIREWALL_ELEVATION_UNAVAILABLE`` on a correctly installed bundle.

Root cause candidates this file pins fail-closed fixes for:

1. ``helper.parent != sidecar.parent`` compares unresolved paths: the Rust
   launcher canonicalizes ``ASG_DESKTOP_EXECUTABLE`` (on Windows yielding a
   ``\\?\\`` verbatim path), so a lexically different-but-identical path was
   rejected even though both files are the same install directory. Validation
   must compare *resolved parents* and tolerate the verbatim prefix.
2. The helper must never be the sidecar itself (env tampering defence) and
   never an arbitrary foreign executable.
3. Declined UAC (error 1223) maps to DECLINED, not UNAVAILABLE.

The helper trust validation itself is not relaxed: only fixed-operation,
same-install-directory, non-symlink, non-empty ``.exe`` candidates are
accepted.
"""

from __future__ import annotations

from pathlib import Path

from agentguard.device_link.windows_elevation import (
    ElevationLaunchError,
    WindowsFirewallElevationRunner,
)


def _packaged_paths(tmp_path: Path) -> tuple[Path, Path]:
    desktop = tmp_path / "agentstate-guard.exe"
    sidecar = tmp_path / "agentguard-sidecar-x86_64-pc-windows-msvc.exe"
    desktop.write_bytes(b"desktop")
    sidecar.write_bytes(b"sidecar")
    return desktop, sidecar


class TestVerbatimPrefixNormalization:
    def test_canonicalize_verbatim_prefix_helper_accepted(self, tmp_path):
        """Rust std::fs::canonicalize() output (\\\\?\\C:\\...) must pass the
        same-directory trust check instead of producing UNAVAILABLE."""
        desktop, sidecar = _packaged_paths(tmp_path)
        verbatim = "\\\\?\\" + str(desktop)
        calls = []
        runner = WindowsFirewallElevationRunner(
            helper_path=verbatim,
            sidecar_path=sidecar,
            launcher=lambda path, args: calls.append((path, args)) or 0,
        )

        result = runner.apply("192.168.50.8", 24)

        assert result.ok is True
        assert result.reason_code == "DEVICE_FIREWALL_APPLIED"
        assert len(calls) == 1
        launched_path, launched_args = calls[0]
        assert "\\\\?\\" not in str(launched_path)
        assert Path(str(launched_path)).name == desktop.name
        assert launched_args[0] == "--asg-firewall-helper"

    def test_normalize_helper_candidate_text(self):
        from agentguard.device_link.windows_elevation import _normalize_candidate

        assert _normalize_candidate("\\\\?\\C:\\Apps\\a.exe") == "C:\\Apps\\a.exe"
        assert _normalize_candidate("\\\\?\\UNC\\s\\sh\\a.exe") == "\\\\s\\sh\\a.exe"
        assert _normalize_candidate("\\\\?\\Volume{guid}\\a.exe").startswith("\\\\?\\")
        assert _normalize_candidate("C:\\Apps\\a.exe") == "C:\\Apps\\a.exe"


class TestHelperTrustBoundary:
    def test_sidecar_itself_is_never_a_valid_helper(self, tmp_path):
        """ASG_DESKTOP_EXECUTABLE pointing at the sidecar binary must fail."""
        _desktop, sidecar = _packaged_paths(tmp_path)
        calls = []
        runner = WindowsFirewallElevationRunner(
            helper_path=sidecar,
            sidecar_path=sidecar,
            launcher=lambda *_a: calls.append(1) or 0,
        )

        result = runner.apply("192.168.50.8", 24)

        assert result.ok is False
        assert result.reason_code == "DEVICE_FIREWALL_ELEVATION_UNAVAILABLE"
        assert calls == []

    def test_same_name_foreign_directory_still_rejected(self, tmp_path):
        """An identical filename outside the install dir must not be trusted."""
        _desktop, sidecar = _packaged_paths(tmp_path)
        foreign_dir = tmp_path / "elsewhere"
        foreign_dir.mkdir()
        foreign = foreign_dir / "agentstate-guard.exe"
        foreign.write_bytes(b"foreign")
        calls = []
        runner = WindowsFirewallElevationRunner(
            helper_path=foreign,
            sidecar_path=sidecar,
            launcher=lambda *_a: calls.append(1) or 0,
        )

        result = runner.apply("192.168.50.8", 24)

        assert result.ok is False
        assert result.reason_code == "DEVICE_FIREWALL_ELEVATION_UNAVAILABLE"
        assert calls == []


class TestElevationSemanticsStayHonest:
    def test_declined_uac_is_declined_not_unavailable(self, tmp_path):
        desktop, sidecar = _packaged_paths(tmp_path)

        def _declined(*_a):
            raise ElevationLaunchError(1223)

        runner = WindowsFirewallElevationRunner(
            helper_path=desktop,
            sidecar_path=sidecar,
            launcher=_declined,
        )
        result = runner.apply("192.168.50.8", 24)
        assert result.ok is False
        assert result.reason_code == "DEVICE_FIREWALL_ELEVATION_DECLINED"
        assert result.reason_code != "DEVICE_FIREWALL_ELEVATION_UNAVAILABLE"
