"""Static release packaging requirements that do not need a Rust toolchain."""

from __future__ import annotations

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
