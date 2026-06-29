# TOM dev launcher — Windows PowerShell.
# Run from anywhere; it cd's into packages/backend, ensures deps are
# installed, and starts the HTTP API on the loopback interface.

$ErrorActionPreference = 'Stop'

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = (Resolve-Path (Join-Path $Here '..\packages\backend')).Path

Set-Location $Backend

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Error 'uv is required (https://docs.astral.sh/uv/)'
  exit 127
}

uv sync --quiet | Out-Null

$HostArg = if ($env:TOM_HOST) { $env:TOM_HOST } else { '127.0.0.1' }
$PortArg = if ($env:TOM_PORT) { [int]$env:TOM_PORT } else { 7878 }

Write-Host "Starting TOM backend on ${HostArg}:${PortArg} (Ctrl-C to stop)..."
uv run python -m backend.tom serve --host $HostArg --port $PortArg