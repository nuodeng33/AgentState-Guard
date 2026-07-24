# AgentState Guard — Android SDK Bootstrap
# Installs: command-line tools only (no Android Studio)
# Usage: .\scripts\setup-android-sdk.ps1 [--DryRun]

param([switch]$DryRun)
$SDK_ROOT = if ($env:AGENT_BUILD_ROOT) { "$env:AGENT_BUILD_ROOT\android-sdk" } else { "D:\AgentBuild\android-sdk" }
$CMDLINE_URL = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"

Write-Host "=== Android SDK Bootstrap ===" -ForegroundColor Cyan
Write-Host "SDK root: $SDK_ROOT"
Write-Host ""

$env:ANDROID_HOME = $SDK_ROOT

# Step 1: JDK check
Write-Host "[1/3] JDK 17" -ForegroundColor Yellow
try { java -version 2>&1 | Out-Null; Write-Host "  Java: $(java -version 2>&1 | Select-Object -First 1)" -ForegroundColor Green }
catch { Write-Host "  JDK 17 NOT FOUND. Install Eclipse Temurin 17 from: https://adoptium.net/" -ForegroundColor Red }

# Step 2: Command-line tools
Write-Host "[2/3] Android Command-Line Tools" -ForegroundColor Yellow
$sdkmanager = Join-Path $SDK_ROOT "cmdline-tools\latest\bin\sdkmanager.bat"
if (Test-Path $sdkmanager) {
    Write-Host "  Already installed" -ForegroundColor Green
} elseif ($DryRun) {
    Write-Host "  [DRY-RUN] Would download: $CMDLINE_URL"
    Write-Host "  [DRY-RUN] Would install to: $SDK_ROOT"
} else {
    Write-Host "  Downloading command-line tools..."
    New-Item -ItemType Directory -Force -Path "$SDK_ROOT\cmdline-tools" | Out-Null
    $zip = "$env:TEMP\android-cmdline.zip"
    Invoke-WebRequest -Uri $CMDLINE_URL -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath "$SDK_ROOT\cmdline-tools" -Force
    Move-Item "$SDK_ROOT\cmdline-tools\cmdline-tools" "$SDK_ROOT\cmdline-tools\latest" -Force
    Remove-Item $zip
}

# Step 3: SDK packages
Write-Host "[3/3] SDK packages" -ForegroundColor Yellow
if (Test-Path $sdkmanager) {
    if ($DryRun) {
        Write-Host "  [DRY-RUN] Would install: platforms;android-34 build-tools;34.0.0 platform-tools"
    } else {
        Write-Host "  Accepting licenses and installing packages..."
        & $sdkmanager --sdk_root=$SDK_ROOT "platform-tools" "platforms;android-34" "build-tools;34.0.0"
        Write-Host "  Accepting licenses..."
        & $sdkmanager --sdk_root=$SDK_ROOT --licenses
    }
}

Write-Host ""
Write-Host "Next: .\scripts\build-android.ps1"
