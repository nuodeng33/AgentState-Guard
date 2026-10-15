# AgentState Guard — Windows Environment Doctor
# Reads: nothing. Writes: stdout status report.
# Usage: .\scripts\env-doctor.ps1

param([switch]$Json)

$results = @()

function Check($Name, $Script, $MinVersion, $VersionArg) {
    $status = "MISSING"
    $actual = ""
    try {
        $out = & $Script $VersionArg 2>&1 | Out-String
        $actual = ($out -split '\n')[0].Trim()
        if ($LASTEXITCODE -eq 0 -and $actual -match '\d+') {
            $status = "PRESENT"
        }
    } catch {
        $status = "MISSING"
    }
    $results += [PSCustomObject]@{
        Name = $Name
        Status = $status
        Actual = $actual
        MinVersion = $MinVersion
    }
}

# OS
$os = Get-CimInstance Win32_OperatingSystem
$results += [PSCustomObject]@{Name="Windows"; Status="PRESENT"; Actual=$os.Caption; MinVersion="10+"}
$results += [PSCustomObject]@{Name="PowerShell"; Status="PRESENT"; Actual=$PSVersionTable.PSVersion.ToString(); MinVersion="7+"}

# Core tools
Check "Git" {git} "--version" "--version"
Check "Node" {node} "--version" "--version"
Check "npm" {npm} "--version" "--version"
Check "Python" {python3} "--version" "--version"

# Rust toolchain
try {
    $rustcOut = rustc --version 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        $results += [PSCustomObject]@{Name="Rust"; Status="PRESENT"; Actual=($rustcOut.Trim()); MinVersion="1.77+"}
    } else { Check "Rust" {rustc} "1.77+" "--version" }
} catch { Check "Rust" {rustc} "1.77+" "--version" }

try {
    $cargoOut = cargo --version 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        $results += [PSCustomObject]@{Name="Cargo"; Status="PRESENT"; Actual=($cargoOut.Trim()); MinVersion="bundled"}
    } else { Check "Cargo" {cargo} "bundled" "--version" }
} catch { Check "Cargo" {cargo} "bundled" "--version" }

# MSVC
$msvc = Get-CimInstance Win32_Product -Filter "Name LIKE '%Visual Studio%'" -ErrorAction SilentlyContinue
$results += [PSCustomObject]@{Name="MSVC Build Tools"; Status=if($msvc){"PRESENT"}else{"MISSING"}; Actual=($msvc.Name -join ', '); MinVersion="VS 2022"}

# WebView2
$wv2 = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" -ErrorAction SilentlyContinue
$results += [PSCustomObject]@{Name="WebView2"; Status=if($wv2){"PRESENT"}else{"MISSING"}; Actual=""; MinVersion="evergreen"}

# Java
Check "Java" {java} "--version" "--version"
$results += [PSCustomObject]@{Name="JAVA_HOME"; Status=if($env:JAVA_HOME){"PRESENT"}else{"MISSING"}; Actual=$env:JAVA_HOME; MinVersion="JDK 17"}

# Android SDK
Check "adb" {adb} "--version" "version"
$results += [PSCustomObject]@{Name="ANDROID_HOME"; Status=if($env:ANDROID_HOME){"PRESENT"}else{"MISSING"}; Actual=$env:ANDROID_HOME; MinVersion="cmdline-tools"}

# Gradle
Check "gradle" {gradle} "--version" "--version"

# Tauri CLI
Check "tauri" {cargo} "2.x" "tauri --version"

# Summary
$desktopReady = ($results | Where-Object {$_.Name -in @("Rust","Cargo","MSVC Build Tools","WebView2","Node","npm") -and $_.Status -eq "MISSING"}).Count -eq 0
$androidReady = ($results | Where-Object {$_.Name -in @("Java","adb","gradle") -and $_.Status -eq "MISSING"}).Count -eq 0

if ($Json) {
    @{
        tools = $results
        desktop_build_ready = $desktopReady
        android_build_ready = $androidReady
    } | ConvertTo-Json -Depth 3
} else {
    Write-Host "=== AgentState Guard Environment Doctor ===" -ForegroundColor Cyan
    $results | Format-Table Name, Status, Actual -AutoSize
    Write-Host ""
    Write-Host "Desktop Build Ready: $(if($desktopReady){'✅ YES'}else{'❌ NO — missing toolchain'})" -ForegroundColor $(if($desktopReady){'Green'}else{'Yellow'})
    Write-Host "Android Build Ready: $(if($androidReady){'✅ YES'}else{'❌ NO — missing toolchain'})" -ForegroundColor $(if($androidReady){'Green'}else{'Yellow'})
    Write-Host ""
    $missing = $results | Where-Object {$_.Status -eq "MISSING"}
    if ($missing) {
        Write-Host "Missing tools:" -ForegroundColor Red
        $missing | ForEach-Object { Write-Host "  - $($_.Name) (min: $($_.MinVersion))" }
        Write-Host ""
        Write-Host "Run: .\scripts\setup-windows-build.ps1 --DryRun"
        Write-Host "Run: .\scripts\setup-android-sdk.ps1 --DryRun"
    }
}
