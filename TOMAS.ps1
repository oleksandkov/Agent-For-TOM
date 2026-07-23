#!/usr/bin/env pwsh
# TOMAS.ps1 — Launch the TOMAS agent CLI
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvDir = Join-Path $scriptDir ".venv"
$python = Join-Path (Join-Path $venvDir "Scripts") "python.exe"
$cli = Join-Path $scriptDir "agent_cli.py"
& $python $cli @args
exit $LASTEXITCODE
