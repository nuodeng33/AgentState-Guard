# AgentState Guard — fail-closed local desktop smoke (PowerShell 7).
[CmdletBinding()]
param(
    [string]$BinaryPath = '',
    [ValidateRange(1, 120)][int]$ReadinessAttempts = 90
)

$ErrorActionPreference = 'Stop'
$port = 8787 # The packaged application currently uses this fixed port.
$main = $null
$owned = @{}
$depth = @{}
$failed = $false

function Get-SameProcess($processId, $startedAt) {
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($process -and $process.StartTime -eq $startedAt) { return $process }
    return $null
}

function Remember-OwnedTree {
    $live = @{}
    foreach ($processId in @($owned.Keys)) {
        if (Get-SameProcess $processId $owned[$processId]) { $live[$processId] = $true }
    }
    $snapshot = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop)
    do {
        $changed = $false
        foreach ($row in $snapshot) {
            $childId = [int]$row.ProcessId
            $parentId = [int]$row.ParentProcessId
            if ($live.ContainsKey($parentId) -and -not $owned.ContainsKey($childId)) {
                $parentRow = $snapshot | Where-Object { [int]$_.ProcessId -eq $parentId } | Select-Object -First 1
                if (-not $parentRow -or -not $parentRow.CreationDate) { continue }
                $ownedParentTicks = $owned[$parentId].ToUniversalTime().Ticks
                $snapshotParentTicks = ([datetime]$parentRow.CreationDate).ToUniversalTime().Ticks
                if (($ownedParentTicks - $ownedParentTicks % 10) -ne ($snapshotParentTicks - $snapshotParentTicks % 10)) { continue }
                if (-not (Get-SameProcess $parentId $owned[$parentId])) { continue }
                $child = Get-Process -Id $childId -ErrorAction SilentlyContinue
                if ($child -and $row.CreationDate -and $child.StartTime -ge $owned[$parentId]) {
                    # CIM timestamps have microsecond resolution; compare that exact precision.
                    $actualTicks = $child.StartTime.ToUniversalTime().Ticks
                    $snapshotTicks = ([datetime]$row.CreationDate).ToUniversalTime().Ticks
                    if (($actualTicks - $actualTicks % 10) -eq ($snapshotTicks - $snapshotTicks % 10)) {
                        $owned[$childId] = $child.StartTime; $depth[$childId] = $depth[$parentId] + 1
                        $live[$childId] = $true; $changed = $true
                    }
                }
            }
        }
    } while ($changed)
}

try {
    if (-not $BinaryPath) {
        $BinaryPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../desktop/src-tauri/target/release/agentstate-guard.exe'))
    }
    if (-not (Test-Path -LiteralPath $BinaryPath -PathType Leaf)) { throw 'DESKTOP_BINARY_NOT_FOUND' }
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw 'DESKTOP_PORT_ALREADY_OCCUPIED' }
    $main = Start-Process -FilePath $BinaryPath -PassThru -WindowStyle Hidden
    $owned[[int]$main.Id] = $main.StartTime
    $depth[[int]$main.Id] = 0
    Remember-OwnedTree
    $listener = $null
    for ($attempt = 0; $attempt -lt $ReadinessAttempts; $attempt++) {
        if ($main.HasExited) { throw 'DESKTOP_EXITED_BEFORE_READY' }
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
        if ($listeners.Count) { $listener = $listeners; break }
        Start-Sleep -Seconds 1
    }
    if (-not $listener) { throw 'DESKTOP_READINESS_TIMEOUT' }
    Remember-OwnedTree
    foreach ($entry in $listener) {
        $ownerId = [int]$entry.OwningProcess
        if ($entry.LocalAddress -ne '127.0.0.1') { throw 'DESKTOP_LISTENER_NOT_LOOPBACK' }
        if (-not $owned.ContainsKey($ownerId) -or -not (Get-SameProcess $ownerId $owned[$ownerId])) { throw 'DESKTOP_LISTENER_NOT_OWNED' }
    }
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/health" -NoProxy -TimeoutSec 10 -ErrorAction Stop
    if ($response.StatusCode -ne 200) { throw 'DESKTOP_HEALTH_HTTP_FAILED' }
    $health = $response.Content | ConvertFrom-Json -ErrorAction Stop
    if ($health.status -ne 'ok') { throw 'DESKTOP_HEALTH_STATUS_FAILED' }
    if ($main.HasExited -or -not (Get-SameProcess $main.Id $owned[[int]$main.Id])) { throw 'DESKTOP_EXITED_DURING_HEALTH' }
    Write-Host 'DESKTOP_HEALTH_VERIFIED'
} catch {
    $failed = $true
    Write-Host "ERROR: $($_.Exception.Message)"
} finally {
    if ($main) {
        try {
            Remember-OwnedTree
        } catch {
            $failed = $true
            Write-Host "CLEANUP ERROR: $($_.Exception.Message)"
        }
        foreach ($processId in @($owned.Keys | Sort-Object { $depth[$_] } -Descending)) {
            try {
                if (Get-SameProcess $processId $owned[$processId]) { Stop-Process -Id $processId -Force -ErrorAction Stop }
            } catch { $failed = $true; Write-Host "CLEANUP ERROR: $($_.Exception.Message)" }
        }
        for ($attempt = 0; $attempt -lt 10; $attempt++) {
            $remaining = @($owned.Keys | Where-Object { Get-SameProcess $_ $owned[$_] })
            $bound = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
            if (-not $remaining.Count -and -not $bound.Count) { break }
            Start-Sleep -Seconds 1
        }
        if ($remaining.Count -or $bound.Count) { $failed = $true; Write-Host 'CLEANUP ERROR: DESKTOP_PROCESS_OR_PORT_LEAK' }
    }
}
if ($failed) { exit 1 }
Write-Host 'Done: DESKTOP_SMOKE_VERIFIED' -ForegroundColor Green
exit 0
