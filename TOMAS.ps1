#!/usr/bin/env pwsh
# TOMAS.ps1 — Launch the TOMAS agent CLI
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $scriptDir ".venv" "Scripts" "python.exe"
$cli = Join-Path $scriptDir "agent_cli.py"
& $python $cli @args
exit $LASTEXITCODE
