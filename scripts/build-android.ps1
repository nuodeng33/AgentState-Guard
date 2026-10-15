# AgentState Guard — Android Build (debug APK)
param([switch]$Release)
$root = Split-Path -Parent $PSScriptRoot
$ErrorActionPreference = "Stop"
Write-Host "=== Building AgentState Guard Android ===" -ForegroundColor Cyan

Write-Host "[1/3] Gradle clean" -ForegroundColor Yellow
Push-Location "$root\android"
if (Test-Path gradlew.bat) {
    .\gradlew.bat clean
} else {
    Write-Error "Android project not yet created. Run Gradle init first."
}

Write-Host "[2/3] Lint + unit tests" -ForegroundColor Yellow
.\gradlew.bat lintDebug testDebugUnitTest

Write-Host "[3/3] Build debug APK" -ForegroundColor Yellow
.\gradlew.bat assembleDebug

$apk = Get-ChildItem -Recurse -Filter "*-debug.apk" | Select-Object -First 1
if ($apk) {
    Write-Host "APK: $($apk.FullName) ($([math]::Round($apk.Length/1KB, 1)) KB)" -ForegroundColor Green
} else { Write-Warning "APK not found" }
Pop-Location
