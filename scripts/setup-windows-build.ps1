# AgentState Guard — Windows Desktop Build Setup
# Reads: Cargo.toml, tauri.conf.json, pyproject.toml
# Installs: NOTHING automatically. Outputs a plan.
# Usage: .\scripts\setup-windows-build.ps1 [--DryRun]

param([switch]$DryRun)

$BuildRoot = if ($env:AGENT_BUILD_ROOT) { $env:AGENT_BUILD_ROOT } else { "D:\AgentBuild" }
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host "=== AgentState Guard — Windows Build Setup ===" -ForegroundColor Cyan
Write-Host "Build root: $BuildRoot"
Write-Host "Dry run: $(if($DryRun){'YES'}else{'NO — will install'})"
Write-Host ""

# Step 1: Rust (rustup)
Write-Host "[1/4] Rust toolchain" -ForegroundColor Yellow
try {
    rustc --version 2>&1 | Out-Null
    Write-Host "  Rust: already installed ($(rustc --version))" -ForegroundColor Green
} catch {
    if ($DryRun) {
        Write-Host "  [DRY-RUN] Would download: https://rustup.rs"
        Write-Host "  [DRY-RUN] Would run: rustup-init.exe -y --default-toolchain stable"
    } else {
        Write-Host "  Installing Rust via rustup..."
        Invoke-WebRequest -Uri "https://win.rustup.rs" -OutFile "$env:TEMP\rustup-init.exe"
        & "$env:TEMP\rustup-init.exe" -y --default-toolchain stable --default-host x86_64-pc-windows-msvc
        $env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
    }
}

# Step 2: Tauri CLI
Write-Host "[2/4] Tauri CLI" -ForegroundColor Yellow
try {
    cargo tauri --version 2>&1 | Out-Null
    Write-Host "  Tauri CLI: already installed" -ForegroundColor Green
} catch {
    if ($DryRun) {
        Write-Host "  [DRY-RUN] Would run: cargo install tauri-cli --version ^2"
    } else {
        Write-Host "  Installing Tauri CLI..."
        cargo install tauri-cli --version "^2"
    }
}

# Step 3: MSVC Build Tools
Write-Host "[3/4] Microsoft C++ Build Tools" -ForegroundColor Yellow
$vsWhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vsWhere) {
    $vs = & $vsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -format value
    if ($vs) {
        Write-Host "  MSVC Build Tools: already installed" -ForegroundColor Green
    }
} else {
    Write-Host "  MSVC Build Tools: NOT FOUND" -ForegroundColor Red
    Write-Host "  ACTION REQUIRED: Download Visual Studio 2022 Build Tools from:"
    Write-Host "    https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022"
    Write-Host "  Select workload: 'Desktop development with C++'"
    Write-Host "  Required components: MSVC v143, Windows 10/11 SDK"
    Write-Host "  Exit code from installer = 3010 → reboot required (normal)."
    if (-not $DryRun) {
        Write-Host "  Run this script again after installing Build Tools."
    }
}

# Step 4: WebView2
Write-Host "[4/4] WebView2 Runtime" -ForegroundColor Yellow
$wv2 = Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" -ErrorAction SilentlyContinue
if ($wv2) {
    Write-Host "  WebView2: already installed (evergreen)" -ForegroundColor Green
} else {
    Write-Host "  WebView2: NOT FOUND (pre-installed on Win11; download for Win10)" -ForegroundColor Yellow
    Write-Host "  Download: https://developer.microsoft.com/en-us/microsoft-edge/webview2/"
}

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host "  1. Run: .\scripts\env-doctor.ps1"
Write-Host "  2. Run: .\scripts\build-desktop.ps1"
Write-Host "  3. Run: .\scripts\test-desktop.ps1"
