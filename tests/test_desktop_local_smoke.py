"""Execute the complete desktop smoke script in PowerShell; simulate OS boundaries only."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = os.environ.get('ASG_PWSH') or shutil.which('pwsh') or shutil.which('powershell')
pytestmark = pytest.mark.skipif(not PWSH, reason='PowerShell required for script-control-flow tests')

PREAMBLE = r'''
$ErrorActionPreference = 'Stop'
$global:started = $false
$global:alive = $true
$global:processLive = @{123=$true;234=$true}
$global:LASTEXITCODE = 1
$global:childReads = 0
$global:parentReused = $false
$global:mode = $env:ASG_SMOKE_CASE
function Trace($line) { [IO.File]::AppendAllText($env:ASG_SMOKE_TRACE, "$line`n") }
function Get-ChildItem { param([switch]$Recurse, $Filter) [pscustomobject]@{ FullName=$env:ASG_SMOKE_BINARY } }
function Start-Sleep { param($Seconds, $Milliseconds) }
function Start-Process {
 param($FilePath, [switch]$PassThru, $WindowStyle)
 Trace "START $FilePath"
 if ($global:mode -eq 'start-fail') { throw 'fixture launch failure' }
 $global:started = $true
 $global:mainProcess = [pscustomobject]@{ Id=123; HasExited=($global:mode -eq 'early-exit'); StartTime=[datetime]'2026-10-23T00:00:00Z' }; $global:mainProcess
}
function Get-NetTCPConnection {
 param($LocalPort, $State, $ErrorAction)
 if ((-not $global:started) -and $global:mode -ne 'occupied') { return }
 if ((-not $global:alive) -and $global:mode -ne 'port-leak') { return }
 if ($global:mode -eq 'no-listener') { return }
 if ($global:started -and -not $global:processLive[234] -and $global:mode -notin @('foreign-listener','port-leak')) { return }
 $address = if ($global:mode -eq 'public-listener') { '0.0.0.0' } else { '127.0.0.1' }
 $owner = if ($global:mode -in @('occupied','foreign-listener','port-leak')) { 999 } else { 234 }
 [pscustomobject]@{ LocalAddress=$address; OwningProcess=$owner }
}
function Get-CimInstance {
 param($ClassName, $ErrorAction)
 if (-not $global:alive) { return }
 if ($global:mode -eq 'parent-reuse') { $global:parentReused=$true }
 @([pscustomobject]@{ ProcessId=123; ParentProcessId=10; CreationDate=$(if ($global:parentReused) { [datetime]'2026-10-24T00:00:00Z' } else { [datetime]'2026-10-23T00:00:00Z' }) }, [pscustomobject]@{ ProcessId=234; ParentProcessId=123; CreationDate=$(if ($global:parentReused) { [datetime]'2026-10-24T00:00:00Z' } elseif ($global:mode -eq 'stale-parent') { [datetime]'2026-10-22T00:00:00Z' } else { [datetime]'2026-10-23T00:00:00Z' }) })
}
function Get-Process {
 param($Id, $ErrorAction)
 if (-not $global:alive) { return }
 foreach ($item in @($Id)) {
  if ($item -in @(123,234) -and $global:processLive[[int]$item]) {
   $birth=[datetime]'2026-10-23T00:00:00Z'
   if ($global:parentReused) { $birth=$birth.AddDays(1) }
   if ($item -eq 234) {
    $global:childReads++
    if ($global:mode -eq 'stale-parent') { $birth=$birth.AddDays(-1) }
    if ($global:mode -eq 'pid-reuse' -and $global:childReads -ge 2) { $birth=$birth.AddDays(1) }
   }
   [pscustomobject]@{ Id=$item; StartTime=$birth }
  }
 }
}
function taskkill {
 param([Parameter(ValueFromRemainingArguments=$true)]$Arguments)
 Trace "KILL $Arguments"
 $global:LASTEXITCODE = 1
 if ($global:mode -in @('cleanup-fail','health-cleanup-fail')) { throw 'fixture cleanup failure' }
 if ($global:mode -ne 'process-leak') { $global:alive = $false }
}
function Stop-Process {
 param($Id, [switch]$Force, $ErrorAction)
 Trace "STOP $Id"
 if ($global:mode -in @('cleanup-fail','health-cleanup-fail')) { throw 'fixture cleanup failure' }
 if ($global:mode -ne 'process-leak') {
  $global:processLive[[int]$Id]=$false
  $global:alive=$global:processLive[123] -or $global:processLive[234]
 }
}
function Invoke-WebRequest {
 param($Uri, $TimeoutSec, [switch]$UseBasicParsing, [switch]$NoProxy, $ErrorAction)
 Trace 'HEALTH'
 if ($global:mode -eq 'health-late-exit') { $global:mainProcess.HasExited=$true }
 if ($global:mode -in @('health-throw','health-cleanup-fail')) { throw 'fixture timeout' }
 $status = if ($global:mode -eq 'http-error') { 503 } else { 200 }
 $body = switch ($global:mode) { 'bad-json' {'<html>error</html>'}; 'bad-status' {'{"status":"error"}'}; default {'{"status":"ok"}'} }
 [pscustomobject]@{ StatusCode=$status; Content=$body }
}
'''


@pytest.mark.parametrize('mode', ['clean', 'health-throw', 'http-error', 'bad-json', 'bad-status',
                                  'start-fail', 'early-exit', 'occupied', 'foreign-listener',
                                  'public-listener', 'no-listener', 'cleanup-fail', 'process-leak', 'port-leak',
                                  'pid-reuse', 'stale-parent', 'health-late-exit', 'health-cleanup-fail', 'missing-binary', 'parent-reuse'])
def test_complete_local_smoke_fails_closed_and_cleans_owned_processes(tmp_path, mode):
    binary = tmp_path / 'release product' / 'agentstate-guard.exe'
    binary.parent.mkdir()
    if mode != 'missing-binary':
        binary.write_bytes(b'fixture; Start-Process is simulated')
    trace = tmp_path / 'trace.log'
    wrapper = tmp_path / 'wrapper.ps1'
    wrapper.write_text(PREAMBLE + '\n& $env:ASG_SMOKE_SOURCE -BinaryPath $env:ASG_SMOKE_BINARY\n'
                       + 'if (Test-Path variable:\\LASTEXITCODE) { exit $LASTEXITCODE }\n', encoding='utf-8')
    result = subprocess.run([PWSH, '-NoProfile', '-File', str(wrapper)], check=False, capture_output=True, timeout=25,
                            env={**os.environ, 'XDG_CACHE_HOME': str(tmp_path / 'cache'), 'XDG_CONFIG_HOME': str(tmp_path / 'config'),
                                 'XDG_DATA_HOME': str(tmp_path / 'data'), 'XDG_STATE_HOME': str(tmp_path / 'state'),
                                 'POWERSHELL_TELEMETRY_OPTOUT': '1', 'ASG_SMOKE_SOURCE': os.environ.get('ASG_SMOKE_SCRIPT', str(ROOT / 'scripts/test-desktop.ps1')),
                                 'ASG_SMOKE_BINARY': str(binary), 'ASG_SMOKE_TRACE': str(trace), 'ASG_SMOKE_CASE': mode})
    output = result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace')
    calls = trace.read_text() if trace.exists() else ''
    assert (result.returncode == 0) == (mode == 'clean'), output
    assert ('Done' in output) == (mode == 'clean'), output
    if mode in {'occupied', 'missing-binary'}:
        assert 'START ' not in calls and 'KILL ' not in calls and 'STOP ' not in calls
    elif mode not in {'start-fail', 'parent-reuse'}:
        assert 'KILL ' in calls or 'STOP ' in calls
    if mode == 'clean':
        assert 'STOP 234' in calls and 'STOP 123' in calls
    if mode in {'pid-reuse', 'stale-parent', 'parent-reuse'}:
        assert 'STOP 234' not in calls
    if mode == 'parent-reuse':
        assert 'STOP 123' not in calls
    if mode == 'health-cleanup-fail':
        assert 'fixture timeout' in output and 'CLEANUP ERROR' in output
    assert 'PID 999' not in calls and 'STOP 999' not in calls
