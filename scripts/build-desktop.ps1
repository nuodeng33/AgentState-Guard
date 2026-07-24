# AgentState Guard — Desktop Build (Tauri + Python sidecar)
# Steps: frontend build → Python sidecar → Tauri Windows exe
param([switch]$Release)
$root = Split-Path -Parent $PSScriptRoot
$ErrorActionPreference = "Stop"
Write-Host "=== Building AgentState Guard Desktop ===" -ForegroundColor Cyan

Write-Host "[1/3] Frontend build" -ForegroundColor Yellow
Push-Location "$root\web"; npm ci --silent 2>$null; npm run build; Pop-Location

Write-Host "[2/3] Python sidecar" -ForegroundColor Yellow
Push-Location $root; pip install -e . -q 2>$null; Pop-Location

Write-Host "[3/3] Tauri build" -ForegroundColor Yellow
Push-Location "$root\desktop"; cargo tauri build; Pop-Location

Write-Host "Done: $root\desktop\src-tauri\target\release\" -ForegroundColor Green
