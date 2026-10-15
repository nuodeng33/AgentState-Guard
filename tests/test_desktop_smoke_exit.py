"""Run real workflow cleanup blocks under the Actions PowerShell exit wrapper."""

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows smoke cleanup")


def _script(name):
    lines = (ROOT / '.github/workflows/desktop-windows.yml').read_text(encoding='utf-8').splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == '- name: ' + name)
    start = next(i for i in range(start, len(lines)) if lines[i].strip() == 'run: |') + 1
    end = start
    while end < len(lines) and (not lines[end].strip() or lines[end].startswith('          ')):
        end += 1
    return textwrap.dedent('\n'.join(lines[start:end]))


@pytest.mark.parametrize('name', [
    'Sidecar standalone runtime smoke', 'Bundled sidecar runtime smoke', 'Packaged main app to Core smoke',
])
@pytest.mark.parametrize('mode', ['clean', 'leak', 'primary-failure'])
def test_cleanup_exit_reflects_assertions_not_native_exit_code(tmp_path, name, mode):
    source = _script(name)
    if name == 'Sidecar standalone runtime smoke':
        cleanup = source[source.index('Write-Host "=== Shutting down sidecar ==="'):]
        if mode == 'primary-failure':
            cleanup = "throw 'PRIMARY_FAILURE'\n" + cleanup
    else:
        cleanup = source[source.rindex('} finally {') + len('} finally {'):]
        cleanup = "try { if ($env:ASG_SMOKE_MODE -eq 'primary-failure') { throw 'PRIMARY_FAILURE' } } finally {" + cleanup
    # Only process/network/sleep boundaries are simulated. No real user process
    # is killed and no port is touched; assertions are the actual workflow code.
    preamble = """
$ErrorActionPreference = 'Stop'
$global:LASTEXITCODE = 1
$sidecarPid = 123; $mainPid = 123; $listenerPid = 456; $launcherPid = 123
$sidecarLog = Join-Path $PSScriptRoot 'absent.log'
$port = 19876
$env:SIDECAR_PORT = '19876'
function taskkill { $global:LASTEXITCODE = 1 }
function Start-Sleep { param($Seconds) }
function Get-NetTCPConnection { param($LocalPort, $State, $ErrorAction) }
function Get-Process {
  param($Id, $Name, $ErrorAction)
  if ($env:ASG_SMOKE_MODE -eq 'leak') { [pscustomobject]@{ Id=123; ProcessName='fixture' } }
}
"""
    # GitHub's pwsh runner propagates the last native exit even after successful
    # PowerShell assertions unless the script terminates explicitly.
    wrapper = "\nif (Test-Path -LiteralPath variable:\\LASTEXITCODE) { exit $LASTEXITCODE }\n"
    path = tmp_path / 'smoke.ps1'
    path.write_text(preamble + cleanup + wrapper, encoding='utf-8')
    command = shutil.which('pwsh') or shutil.which('powershell')
    assert command, 'PowerShell is required for desktop smoke checks'
    result = subprocess.run([command, '-NoProfile', '-File', str(path)], check=False, capture_output=True,
                            env={**os.environ, 'ASG_SMOKE_MODE': mode}, timeout=20)
    assert (result.returncode == 0) == (mode == 'clean'), result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace')
