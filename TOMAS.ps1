#!/usr/bin/env pwsh
# TOMAS.ps1 — Launch the TOMAS agent CLI
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Try ~/.tomas/.venv first (system-wide install), then local .venv (dev mode)
$tomasDir = Join-Path $HOME ".tomas"
$candidates = @(
    @{ Dir = Join-Path $tomasDir ".venv"; Label = "system" }
    @{ Dir = Join-Path $scriptDir ".venv"; Label = "local"  }
)
$python = $null
foreach ($c in $candidates) {
    $p = Join-Path (Join-Path $c.Dir "Scripts") "python.exe"
    if (Test-Path $p) { $python = $p; break }
    # Also check bin/ (MSYS2/MinGW venvs)
    $p = Join-Path (Join-Path $c.Dir "bin") "python.exe"
    if (Test-Path $p) { $python = $p; break }
}
if (-not $python) {
    Write-Host "ERROR: TOMAS virtual environment not found." -ForegroundColor Red
    Write-Host "" -ForegroundColor Red
    Write-Host "  Checked:" -ForegroundColor Yellow
    Write-Host "    - $tomasDir\.venv\Scripts\python.exe (system install)" -ForegroundColor Yellow
    Write-Host "    - $scriptDir\.venv\Scripts\python.exe  (local dev)" -ForegroundColor Yellow
    Write-Host "" -ForegroundColor Yellow
    Write-Host "  Install with:" -ForegroundColor Yellow
    Write-Host "    powershell -ExecutionPolicy Bypass -File install.ps1" -ForegroundColor Yellow
    exit 1
}
$cli = Join-Path $scriptDir "agent_cli.py"
& $python $cli @args
exit $LASTEXITCODE
