# AgentState Guard — Desktop Runtime Smoke Test
$exe = Get-ChildItem -Recurse -Filter "agentstate-guard.exe" | Select-Object -First 1
if (-not $exe) { Write-Error "No .exe found — run build-desktop.ps1 first"; exit 1 }

Write-Host "=== Desktop Smoke Test ===" -ForegroundColor Cyan
$proc = Start-Process -FilePath $exe.FullName -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 5

try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8787/api/health" -TimeoutSec 10
    Write-Host "Health: $($resp.Content)" -ForegroundColor Green
} catch {
    Write-Warning "Health check failed: $_"
}
Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
Write-Host "Done" -ForegroundColor Green
