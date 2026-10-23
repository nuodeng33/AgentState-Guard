# Build with an existing project environment containing dependencies + PyInstaller.
# -Release requires a clean source tree; the default also builds release binaries,
# but a dirty developer build has no exact runtime PRODUCT_SHA.
param([switch]$Release, [string]$Python)
$root = Split-Path -Parent $PSScriptRoot
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable failed with exit code $LASTEXITCODE"
    }
}

function Get-Sha256File {
    param([string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $hash.Dispose() }
}

if (-not $Python) { $Python = Join-Path $root '.venv\Scripts\python.exe' }
$Python = (Get-Command $Python -ErrorAction Stop).Source
Push-Location $root
try {
    $sourceSha = (Invoke-Checked git @('rev-parse', 'HEAD') | Out-String).Trim()
    if ($sourceSha -notmatch '^[0-9a-f]{40}$') { throw 'Exact source SHA unavailable' }
    $sourceStatus = @(Invoke-Checked git @('status', '--porcelain', '--untracked-files=normal'))
    $dirty = $sourceStatus.Count -gt 0
    if ($Release -and $dirty) { throw 'Release packaging requires a clean source tree' }
    Invoke-Checked $Python @('-c', 'import agentguard, PyInstaller')
    $rustInfo = Invoke-Checked rustc @('-vV')
    $triple = (($rustInfo | Select-String '^host: ').Line -replace '^host: ', '').Trim()
    if ($triple -ne 'x86_64-pc-windows-msvc') {
        throw 'Desktop sidecar resolution currently supports Windows x64 MSVC only'
    }
    Invoke-Checked cargo @('tauri', '--version')

    Write-Host '[1/3] Frontend build'
    Push-Location (Join-Path $root 'web')
    try {
        Invoke-Checked npm @('ci')
        Invoke-Checked npm @('run', 'build')
    } finally { Pop-Location }

    # Each build uses a new directory: a failed build cannot reuse an old EXE.
    $buildRoot = Join-Path $root ('artifacts\desktop-build\' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
    $provenancePath = Join-Path $root 'agentguard\core\_build_provenance.py'
    $originalProvenance = [IO.File]::ReadAllBytes($provenancePath)
    $shaLiteral = if ($dirty) { 'None' } else { '"' + $sourceSha + '"' }
    Write-Host '[2/3] Python sidecar'
    try {
        [IO.File]::WriteAllText($provenancePath, "PRODUCT_SHA = $shaLiteral`n", [Text.UTF8Encoding]::new($false))
        $pyInstallerArgs = @(
            '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--noconsole',
            '--name', 'agentguard-sidecar', '--distpath', "$buildRoot\dist",
            '--workpath', "$buildRoot\work", '--specpath', $buildRoot,
            '--paths', $root, '--collect-data', 'agentguard', '--add-data', 'agentguard/discovery/application_probe.py;agentguard/discovery', '--add-data', 'agentguard/discovery/session_metadata.py;agentguard/discovery', '--add-data', 'agentguard/discovery/probes/container_processes.py;agentguard/discovery/probes', '--add-data', 'agentguard/recovery/protection_helper.py;agentguard/recovery'
        )
        foreach ($module in @(
            'uvicorn', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
            'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
            'uvicorn.protocols.websocket', 'uvicorn.protocols.websocket.auto',
            'fastapi', 'pydantic', 'cryptography', 'cryptography.hazmat',
            'cryptography.hazmat.backends.openssl', 'cryptography.hazmat.primitives'
        )) { $pyInstallerArgs += @('--hidden-import', $module) }
        $pyInstallerArgs += (Join-Path $root 'desktop\src-tauri\desktop_sidecar.py')
        Invoke-Checked $Python $pyInstallerArgs
    } finally {
        [IO.File]::WriteAllBytes($provenancePath, $originalProvenance)
    }
    $sidecar = Join-Path $buildRoot 'dist\agentguard-sidecar.exe'
    if (-not (Test-Path -LiteralPath $sidecar -PathType Leaf)) { throw 'Sidecar build produced no EXE' }
    Invoke-Checked $Python @((Join-Path $root 'scripts\verify_packaged_readers.py'), '--exe', $sidecar, '--source-root', $root, '--report', (Join-Path $buildRoot 'packaged-readers.json'))
    $binDir = Join-Path $root 'desktop\src-tauri\bin'
    New-Item -ItemType Directory -Path $binDir -Force | Out-Null
    Copy-Item -LiteralPath $sidecar -Destination (Join-Path $binDir "agentguard-sidecar-$triple.exe") -Force

    Write-Host '[3/3] Tauri build'
    Push-Location (Join-Path $root 'desktop')
    try { Invoke-Checked cargo @('tauri', 'build') } finally { Pop-Location }
    $releaseDir = Join-Path $root 'desktop\src-tauri\target\release'
    Copy-Item -LiteralPath $sidecar -Destination (Join-Path $releaseDir "agentguard-sidecar-$triple.exe") -Force
    @{
        source_sha = $sourceSha
        source_dirty = $dirty
        runtime_product_sha = $(if ($dirty) { $null } else { $sourceSha })
        sidecar_sha256 = Get-Sha256File $sidecar
        frontend_index_sha256 = Get-Sha256File "$root\agentguard\web_static\index.html"
        built_at = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath "$buildRoot\provenance.json" -Encoding utf8
    Write-Host "Done: $releaseDir (build provenance: $buildRoot\provenance.json)"
} finally { Pop-Location }
