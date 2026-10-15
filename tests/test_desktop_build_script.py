"""Exercise the real build script with isolated, deterministic tool processes."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows packaging script")
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("failure", ["", "npm", "PyInstaller", "tauri"])
def test_build_stages_stop_on_failure_and_restore_source(tmp_path, failure):
    root = tmp_path / "project with spaces"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/build-desktop.ps1", scripts / "build-desktop.ps1")
    provenance = root / "agentguard/core/_build_provenance.py"
    provenance.parent.mkdir(parents=True)
    original = b"# original\r\nPRODUCT_SHA = None\r\n"
    provenance.write_bytes(original)
    (root / "web").mkdir()
    (root / "desktop/src-tauri/bin").mkdir(parents=True)
    stale = root / "desktop/src-tauri/bin/agentguard-sidecar-x86_64-pc-windows-msvc.exe"
    stale.write_bytes(b"stale")
    (root / "agentguard/web_static").mkdir()
    (root / "agentguard/web_static/index.html").write_text("frontend")
    bins = tmp_path / "tools"
    bins.mkdir()
    driver = bins / "driver.py"
    driver.write_text(
        """import json, os, sys
from pathlib import Path
tool, *args = sys.argv[1:]
root = Path(os.environ['ASG_BUILD_TEST_ROOT'])
stage = 'PyInstaller' if tool == 'python' and '-m' in args else tool
if tool == 'cargo' and 'build' in args: stage = 'tauri'
with (root / 'calls.jsonl').open('a') as f: f.write(json.dumps(stage) + '\\n')
if stage == os.environ['ASG_BUILD_TEST_FAIL']: sys.exit(7)
if tool == 'git' and args[0] == 'rev-parse': print('a'*40)
elif tool == 'rustc': print('host: x86_64-pc-windows-msvc')
elif stage == 'PyInstaller':
    dest = Path(args[args.index('--distpath')+1]); dest.mkdir(parents=True)
    (dest/'agentguard-sidecar.exe').write_bytes(b'fresh')
elif stage == 'tauri': (root/'desktop/src-tauri/target/release').mkdir(parents=True)
""",
        encoding="utf-8",
    )
    for tool in ("git", "rustc", "cargo", "npm", "python"):
        (bins / f"{tool}.cmd").write_text(
            f'@"{sys.executable}" "{driver}" {tool} %*\n',
            encoding="utf-8",
        )
    env = dict(os.environ, ASG_BUILD_TEST_ROOT=str(root), ASG_BUILD_TEST_FAIL=failure)
    env["PATH"] = str(bins) + os.pathsep + env["PATH"]
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(scripts / "build-desktop.ps1"),
            "-Python",
            str(bins / "python.cmd"),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    calls = [
        json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()
    ]
    assert provenance.read_bytes() == original
    if failure:
        assert result.returncode != 0, result.stdout + result.stderr
        assert calls[-1] == failure
        assert not list(root.glob("artifacts/desktop-build/*/provenance.json"))
        if failure != "tauri":
            assert stale.read_bytes() == b"stale"
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert stale.read_bytes() == b"fresh"
        report = next(root.glob("artifacts/desktop-build/*/provenance.json"))
        assert (
            json.loads(report.read_text(encoding="utf-8-sig"))["runtime_product_sha"]
            == "a" * 40
        )
