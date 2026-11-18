"""Static release packaging requirements that do not need a Rust toolchain."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_release_build_and_sidecar_are_no_console():
    workflow = (ROOT / ".github/workflows/desktop-windows.yml").read_text(
        encoding="utf-8"
    )
    rust_main = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
    sidecar = (ROOT / "desktop/src-tauri/src/sidecar.rs").read_text(encoding="utf-8")

    assert "cargo tauri build\n" in workflow
    assert "cargo tauri build --debug" not in workflow
    assert "--noconsole" in workflow
    assert 'windows_subsystem = "windows"' in rust_main
    assert "CREATE_NO_WINDOW" in sidecar
    assert ".stdout(Stdio::from(stdout))" in sidecar
    assert ".stderr(Stdio::from(stderr))" in sidecar


def test_windows_firewall_helper_runs_all_child_commands_without_console():
    helper = (ROOT / "desktop/src-tauri/src/firewall_helper.rs").read_text(
        encoding="utf-8"
    )

    commands = re.findall(r'Command::new\("([^"]+)"\)', helper)
    hidden_commands = re.findall(
        r'run_without_console\(\s*Command::new\("([^"]+)"\)', helper
    )
    assert sorted(commands) == ["netsh.exe", "netsh.exe", "powershell.exe"]
    assert hidden_commands == commands
    assert helper.count(".status()") == 1
    assert re.search(r"configure_no_window\(command\);\s*command\s*\.status\(\)", helper)
