<#
.SYNOPSIS
    TOMAS Agent Installer — install from GitHub or local source.
.DESCRIPTION
    Installs the TOMAS coding agent globally. Works both:
      - Locally:   powershell -ExecutionPolicy Bypass -File install.ps1
      - Remote:    powershell -c "iex (iwr -UseBasicParsing -Uri https://raw.githubusercontent.com/oleksandkov/Agent-For-TOM/prototype2-refactoring/install.ps1)"
.PARAMETER InstallDir
    Where to install TOMAS (default: ~/.tomas).
.PARAMETER RepoUrl
    GitHub archive URL to download (default: https://github.com/oleksandkov/Agent-For-TOM/archive/prototype2-refactoring.zip).
    Leave as default to skip remote download (local install mode).
.PARAMETER NoPrompt
    Skip interactive prompts.
#>

param(
    [string]$InstallDir = "",
    [string]$RepoUrl = "https://github.com/oleksandkov/Agent-For-TOM/archive/prototype2-refactoring.zip",
    [switch]$NoPrompt
)

# ── Config ──────────────────────────────────────────────────────────────────
if (-not $InstallDir) {
    $InstallDir = Join-Path $HOME ".tomas"
}
$BinDir      = Join-Path $InstallDir "bin"
$SrcDir      = Join-Path $InstallDir "src"
$VenvDir     = Join-Path $InstallDir ".venv"
$EnvFile     = Join-Path $InstallDir ".env"

$LauncherPs1 = Join-Path $BinDir "TOMAS.ps1"

# Helper: find the python.exe in the venv (bin/ on MSYS2, Scripts/ on Windows)
function Get-VenvPythonExe {
    param([string]$VenvDir)
    $candidates = @(
        Join-Path (Join-Path $VenvDir "bin") "python.exe"
        Join-Path (Join-Path $VenvDir "Scripts") "python.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    return $null
}
function Get-VenvPipExe {
    param([string]$VenvDir)
    $candidates = @(
        Join-Path (Join-Path $VenvDir "bin") "pip.exe"
        Join-Path (Join-Path $VenvDir "Scripts") "pip.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    return $null
}
# Default to Windows path; will be corrected after venv validation/creation.
$script:PythonExe = Join-Path (Join-Path $VenvDir "Scripts") "python.exe"
$script:PipExe    = Join-Path (Join-Path $VenvDir "Scripts") "pip.exe"
$LauncherCmd = Join-Path $BinDir "TOMAS.cmd"
$LauncherBat = Join-Path $BinDir "TOMAS.bat"

# ── Detect mode ─────────────────────────────────────────────────────────────
$isPiped = $MyInvocation.MyCommand.Name -eq "__remote_exec__" -or
           $MyInvocation.MyCommand.Path -eq "" -or
           [Console]::IsInputRedirected

Write-Host ""
Write-Host "  ==========================================" -ForegroundColor Cyan
Write-Host "       TOMAS Agent Installer v2.0" -ForegroundColor Cyan
Write-Host "  ==========================================" -ForegroundColor Cyan
Write-Host ""

# ── Prerequisites ───────────────────────────────────────────────────────────
$pythonPath = ""

# Try python first, then python3
foreach ($cmd in @("python", "python3")) {
    try {
        $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ([version]$ver -ge [version]"3.10") {
            $pythonPath = (Get-Command $cmd -ErrorAction SilentlyContinue).Source
            break
        }
    } catch {}
}

if (-not $pythonPath) {
    Write-Host "  [FAIL] Python 3.10+ is required but not found." -ForegroundColor Red
    Write-Host "         Install Python from: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "         Make sure to check 'Add Python to PATH' during installation."
    exit 1
}
Write-Host "  [OK] Python $ver found at: $pythonPath" -ForegroundColor Green

# ── Create directory structure ─────────────────────────────────────────────
Write-Host ""
Write-Host "  [2/9] Setting up directories..." -ForegroundColor Cyan
$null = New-Item -ItemType Directory -Path $BinDir -Force
$null = New-Item -ItemType Directory -Path $SrcDir -Force
$null = New-Item -ItemType Directory -Path (Join-Path $InstallDir "sessions") -Force
$null = New-Item -ItemType Directory -Path (Join-Path $InstallDir "self-improve") -Force
$null = New-Item -ItemType Directory -Path (Join-Path $InstallDir "memory") -Force
$null = New-Item -ItemType Directory -Path (Join-Path $InstallDir "self-notes") -Force
$null = New-Item -ItemType Directory -Path (Join-Path $InstallDir "instructions") -Force
$null = New-Item -ItemType Directory -Path (Join-Path (Join-Path $InstallDir "instructions") "project") -Force
Write-Host "  [OK] Install directory: $InstallDir" -ForegroundColor Green

# ── Get source code ────────────────────────────────────────────────────────
$localSource = if ($PSCommandPath) { Split-Path -Parent $PSCommandPath } else { $null }
$hasLocalSource = $localSource -and (Test-Path (Join-Path $localSource "agent.py"))

if ($hasLocalSource) {
    Write-Host ""
    Write-Host "  [3/9] Copying local source..." -ForegroundColor Cyan
    # Copy all project files except excluded patterns
    $exclude = @('.venv', '__pycache__', '.git', '.agent', '*.pyc', '.gitignore')
    Get-ChildItem -Path $localSource -File | Where-Object {
        $excludeFile = $false
        foreach ($pat in $exclude) {
            if ($_.Name -like $pat -or $_.FullName -like "*\$pat\*") { $excludeFile = $true; break }
        }
        -not $excludeFile
    } | Copy-Item -Destination $SrcDir -Force
    Write-Host "  [OK] Copied $( (Get-ChildItem $SrcDir -File).Count ) files to $SrcDir" -ForegroundColor Green
}
else {
    Write-Host ""
    Write-Host "  [3/9] Downloading from GitHub..." -ForegroundColor Cyan
    Write-Host "       URL: $RepoUrl" -ForegroundColor DarkGray

    $zipPath = Join-Path $env:TEMP "tomas-$(Get-Random).zip"
    try {
        # Try Invoke-WebRequest first (PowerShell 5+)
        if ($PSVersionTable.PSVersion.Major -ge 5) {
            Invoke-WebRequest -Uri $RepoUrl -OutFile $zipPath -UseBasicParsing -ErrorAction Stop
        } else {
            # Fallback to WebClient
            $wc = New-Object System.Net.WebClient
            $wc.DownloadFile($RepoUrl, $zipPath)
        }

        # Extract archive
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $env:TEMP)
        $extracted = Get-ChildItem (Join-Path $env:TEMP "Agent-For-TOM-*") -Directory | Select-Object -First 1
        if ($extracted) {
            # Remove old src, copy new
            if (Test-Path $SrcDir) { Remove-Item -Path $SrcDir -Recurse -Force }
            Move-Item -Path $extracted.FullName -Destination $SrcDir -Force
            Write-Host "  [OK] Downloaded and extracted to $SrcDir" -ForegroundColor Green
        } else {
            throw "Could not find extracted directory"
        }
    } catch {
        Write-Host "  [FAIL] Download failed: $_" -ForegroundColor Red
        Write-Host "         Make sure the RepoUrl is correct:" -ForegroundColor Yellow
        Write-Host "         $RepoUrl" -ForegroundColor Yellow
        Write-Host "         Or clone the repo manually and run install.ps1 from the project folder." -ForegroundColor Yellow
        exit 1
    } finally {
        if (Test-Path $zipPath) { Remove-Item $zipPath -Force -ErrorAction SilentlyContinue }
    }
}

# ── Create virtual environment ─────────────────────────────────────────────
Write-Host ""
Write-Host "  [4/9] Creating virtual environment..." -ForegroundColor Cyan

# Check if existing venv has a working Python executable (bin or Scripts)
$existingPythonExe = Get-VenvPythonExe $VenvDir
$venvPythonOk = $false
if ($existingPythonExe) {
    $script:PythonExe = $existingPythonExe
    $script:PipExe    = Get-VenvPipExe $VenvDir
    try {
        $ver = & $script:PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($ver) { $venvPythonOk = $true }
    } catch {}
}
if (-not $venvPythonOk) {
    if (Test-Path $VenvDir) {
        Write-Host "  [WARN] Existing venv is corrupted, removing..." -ForegroundColor Yellow
        Remove-Item -Path $VenvDir -Recurse -Force
    }
    & $pythonPath -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [FAIL] Failed to create virtual environment" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] Virtual environment created" -ForegroundColor Green
} else {
    Write-Host "  [OK] Virtual environment already exists" -ForegroundColor Green
}

# Detect venv python/pip executables (Scripts on Windows, bin on MSYS2/MinGW)
$script:PythonExe = Get-VenvPythonExe $VenvDir
$script:PipExe    = Get-VenvPipExe $VenvDir
if (-not $script:PythonExe) {
    Write-Host "  [FAIL] Could not detect python.exe in venv" -ForegroundColor Red
    Write-Host "         Checked: bin/python.exe and Scripts/python.exe" -ForegroundColor Yellow
    exit 1
}
if (-not $script:PipExe) {
    Write-Host "  [WARN] Could not detect pip.exe in venv" -ForegroundColor Yellow
}

# ── Install dependencies ───────────────────────────────────────────────────
Write-Host ""
Write-Host "  [5/9] Installing Python dependencies..." -ForegroundColor Cyan

# Check that pip exists before using it; if not, run ensurepip
if (-not $script:PipExe) {
    Write-Host "  [INFO] pip not found in venv, running ensurepip..." -ForegroundColor Yellow
    if (-not $script:PythonExe) {
        Write-Host "  [FAIL] Python executable missing in venv" -ForegroundColor Red
        Write-Host "         Deleting corrupted venv and recreating..." -ForegroundColor Yellow
        Remove-Item -Path $VenvDir -Recurse -Force
        & $pythonPath -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [FAIL] Failed to recreate virtual environment" -ForegroundColor Red
            exit 1
        }
        # Re-detect after recreation
        $script:PythonExe = Get-VenvPythonExe $VenvDir
        $script:PipExe    = Get-VenvPipExe $VenvDir
        if (-not $script:PythonExe) {
            Write-Host "  [FAIL] Could not detect venv python.exe after recreation" -ForegroundColor Red
            exit 1
        }
    }
    & $script:PythonExe -m ensurepip --upgrade 2>&1 | Out-Null
    # Re-detect pip after ensurepip
    $script:PipExe = Get-VenvPipExe $VenvDir
    if (-not $script:PipExe) {
        Write-Host "  [FAIL] pip is not available in the virtual environment" -ForegroundColor Red
        Write-Host "         Try deleting $VenvDir and re-running the installer" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "  [OK] pip installed in virtual environment" -ForegroundColor Green
}

& $script:PipExe install --quiet --upgrade pip setuptools wheel 2>&1 | Out-Null
$reqFile = Join-Path $SrcDir "requirements.txt"
if (Test-Path $reqFile) {
    & $script:PipExe install --quiet -r $reqFile
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Dependencies installed successfully" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] Failed to install some dependencies" -ForegroundColor Red
        Write-Host "         Run manually: $script:PipExe install -r $reqFile" -ForegroundColor Yellow
        if ($script:PipExe -like "*\bin\*") {
            Write-Host "         Note: MSYS2 Python detected (bin/ venv). Some Rust-based" -ForegroundColor Yellow
            Write-Host "               packages (e.g. jiter) may need python.org Python for" -ForegroundColor Yellow
            Write-Host "               pre-built wheels." -ForegroundColor Yellow
            Write-Host "         Try using python.org Python 3.12 if you see build errors." -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "  [WARN] No requirements.txt found" -ForegroundColor Yellow
}

# ── Create launcher scripts ────────────────────────────────────────────────
Write-Host ""
Write-Host "  [6/9] Creating launcher scripts..." -ForegroundColor Cyan

# TOMAS.ps1 — PowerShell launcher (used by the PATH entry)
$ps1Content = @'
#!/usr/bin/env pwsh
# TOMAS.ps1 — TOMAS Agent Launcher (installed)
$ErrorActionPreference = "Stop"
$tomasDir = "{InstallDir}"
$venvDir = Join-Path $tomasDir ".venv"
# Detect Scripts vs bin directory (Windows vs MSYS2/MinGW venvs)
$venvBin = "Scripts"
if (Test-Path (Join-Path $venvDir "bin")) { $venvBin = "bin" }
$python = Join-Path (Join-Path $venvDir $venvBin) "python.exe"
$cli = Join-Path (Join-Path $tomasDir "src") "agent_cli.py"
if (-not (Test-Path $python)) {
    Write-Host "ERROR: TOMAS venv not found at $python" -ForegroundColor Red
    Write-Host "Reinstall with: powershell -c `"iex (iwr -UseBasicParsing -Uri https://raw.githubusercontent.com/oleksandkov/Agent-For-TOM/prototype2-refactoring/install.ps1)`"" -ForegroundColor Yellow
    exit 1
}
& $python $cli @args
exit $LASTEXITCODE
'@ -replace '{InstallDir}', $InstallDir

[System.IO.File]::WriteAllText($LauncherPs1, $ps1Content, [System.Text.Encoding]::UTF8)
Write-Host "  [OK] $LauncherPs1" -ForegroundColor Green

# TOMAS.cmd — CMD launcher (so `TOMAS` works from cmd.exe)
# Detect Scripts vs bin directory (Windows vs MSYS2/MinGW venvs)
$venvBin = "Scripts"; if (Test-Path (Join-Path $VenvDir "bin")) { $venvBin = "bin" }
$cmdContent = @'
@echo off
set "TOMAS_DIR={InstallDir}"
"{InstallDir}\.venv\{VenvBin}\python.exe" "{InstallDir}\src\agent_cli.py" %*
'@ -replace '{InstallDir}', $InstallDir -replace '\{VenvBin\}', $venvBin

[System.IO.File]::WriteAllText($LauncherCmd, $cmdContent, [System.Text.Encoding]::UTF8)
Write-Host "  [OK] $LauncherCmd" -ForegroundColor Green

# TOMAS.bat — also create in bin (some environments prefer .bat)
Copy-Item $LauncherCmd $LauncherBat -Force
Write-Host "  [OK] $LauncherBat" -ForegroundColor Green

# ── Create upgrade & uninstall commands ──────────────────────────────────
# TOMAS-upgrade.cmd — re-run remote install
$upgradeBat = Join-Path $BinDir "TOMAS-upgrade.cmd"
$upgradeContent = @'
@echo off
echo   ==========================================
echo       TOMAS Upgrade
echo   ==========================================
echo.
echo   Upgrading TOMAS from GitHub...
echo.
powershell -ExecutionPolicy Bypass -c "iex (iwr -UseBasicParsing -Uri https://raw.githubusercontent.com/oleksandkov/Agent-For-TOM/prototype2-refactoring/install.ps1)"
if %ERRORLEVEL% neq 0 (
    echo   Upgrade failed. See messages above.
    pause
    exit /b %ERRORLEVEL%
)

rem ── Refresh PATH so `tomas` works immediately in this session ──
set "PATH=%USERPROFILE%\.tomas\bin;%PATH%"
echo.
echo   Upgrade complete! You can now run: TOMAS
'@
[System.IO.File]::WriteAllText($upgradeBat, $upgradeContent, [System.Text.Encoding]::UTF8)
Write-Host "  [OK] $upgradeBat" -ForegroundColor Green

# TOMAS-uninstall.cmd — call uninstall.ps1
$uninstallBat = Join-Path $BinDir "TOMAS-uninstall.cmd"
$uninstallContent = @'
@echo off
echo   ==========================================
echo       TOMAS Uninstall
echo   ==========================================
echo.
echo   This will remove TOMAS completely from your system.
echo.
powershell -ExecutionPolicy Bypass -File "{UninstallPs1}"
if %ERRORLEVEL% neq 0 (
    echo   Uninstall may have failed. See messages above.
    pause
)
'@ -replace '{UninstallPs1}', (Join-Path $BinDir "uninstall.ps1")
[System.IO.File]::WriteAllText($uninstallBat, $uninstallContent, [System.Text.Encoding]::UTF8)
Write-Host "  [OK] $uninstallBat" -ForegroundColor Green

# ── Create default instructions and sessions dir ──────────────────────────
Write-Host ""
Write-Host "  [7/9] Setting up agent instructions..." -ForegroundColor Cyan

$InstructionsDir = Join-Path $InstallDir "instructions"
$ProjectsDir = Join-Path $InstructionsDir "project"
$SessionsDir = Join-Path $InstallDir "sessions"
$SelfImproveDir = Join-Path $InstallDir "self-improve"
$MemoryDir = Join-Path $InstallDir "memory"
$SelfNotesDir = Join-Path $InstallDir "self-notes"
$null = New-Item -ItemType Directory -Path $InstructionsDir -Force
$null = New-Item -ItemType Directory -Path $ProjectsDir -Force
$null = New-Item -ItemType Directory -Path $SessionsDir -Force
$null = New-Item -ItemType Directory -Path $SelfImproveDir -Force
$null = New-Item -ItemType Directory -Path $MemoryDir -Force
$null = New-Item -ItemType Directory -Path $SelfNotesDir -Force

# Create default AGENT.md (local-level agent identity)
$agentInstrFile = Join-Path $InstructionsDir "AGENT.md"
if (-not (Test-Path $agentInstrFile)) {
    @"
# Agent Identity

- Your name is TOMAS agent.
- Each report must be ended with My Lord.
"@ | Out-File -FilePath $agentInstrFile -Encoding utf8
    Write-Host "  [OK] Created agent identity: $agentInstrFile" -ForegroundColor Green
} else {
    Write-Host "  [OK] Agent identity file already exists (keeping existing)" -ForegroundColor Green
}

# Create README for the instructions folder
$readmeFile = Join-Path $InstructionsDir "README.md"
if (-not (Test-Path $readmeFile)) {
    @"
# TOMAS Agent Instructions

This folder contains **global instructions** that apply to every TOMAS
session, regardless of the project you're working on.

## How it works

- Every `.md` file in this folder is loaded in alphabetical order and
  merged into the agent's system prompt.
- Use these files to set persistent preferences, coding standards, and
  default behaviour.

## Project-level instructions

You can also add instructions per project:

1. Place `AGENT.md` or `agent.md` in the project root directory.
2. OR place `<project-name>.md` in the `project/` subfolder here.

Project-level instructions are loaded on top of global instructions.

## Example files

- `AGENT.md` — local agent identity (safe to edit or delete)
- `project/` — per-project instruction files
"@ | Out-File -FilePath $readmeFile -Encoding utf8
    Write-Host "  [OK] Created instructions README: $readmeFile" -ForegroundColor Green
}

# Create .gitkeep in project instructions dir
$gitkeep = Join-Path $ProjectsDir ".gitkeep"
if (-not (Test-Path $gitkeep)) {
    "" | Out-File -FilePath $gitkeep -Encoding utf8
}

Write-Host "  [OK] Sessions directory: $SessionsDir" -ForegroundColor Green

# ── Set up .env ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  [8/9] Configuring environment..." -ForegroundColor Cyan

if (-not (Test-Path $EnvFile)) {
    @"
# TOMAS configuration (created by install.ps1)
# Required: set your API key below
ANTHROPIC_API_KEY=
# Optional: API base URL (default: https://api.anthropic.com)
# ANTHROPIC_BASE_URL=
# Optional: model name (default: claude-sonnet-4-5)
# AGENT_MODEL=claude-sonnet-4-5
# Optional: "1" to auto-approve low-risk tools
# AGENT_AUTO_APPROVE=1
"@ | Out-File -FilePath $EnvFile -Encoding utf8
    Write-Host "  [OK] Created .env configuration file" -ForegroundColor Green
} else {
    Write-Host "  [OK] .env already exists (keeping existing)" -ForegroundColor Green
}

# ── Configure API key (if running interactively) ────────────────────────────
if (-not $NoPrompt -and $host.Name -ne 'Default Host' -and -not $isPiped) {
    $currentKey = (Select-String -Path $EnvFile -Pattern "^ANTHROPIC_API_KEY=(.*)$" | ForEach-Object { $_.Matches.Groups[1].Value }) -replace '"',''
    if (-not $currentKey) {
        Write-Host ""
        Write-Host "  [INFO] API Key Setup" -ForegroundColor Yellow
        Write-Host "  " -NoNewline
        $key = Read-Host "         Enter your Anthropic API key (or press Enter to skip)"
        if ($key) {
            $content = Get-Content $EnvFile -Raw
            $content = $content -replace '^ANTHROPIC_API_KEY=$', "ANTHROPIC_API_KEY=$key"
            Set-Content -Path $EnvFile -Value $content -NoNewline
            Write-Host "  [OK] API key saved to $EnvFile" -ForegroundColor Green
        } else {
            Write-Host "  [INFO] Skipped. Edit $EnvFile later to add your API key." -ForegroundColor Yellow
        }
    } else {
        Write-Host "  [OK] API key already configured" -ForegroundColor Green
    }
}

# ── Add to PATH ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  [9/9] Finalizing setup..." -ForegroundColor Cyan
Write-Host "       Adding to system PATH..." -ForegroundColor DarkGray

$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
$paths = $currentPath -split ';'

if ($paths -notcontains $BinDir) {
    $newPath = $BinDir + ';' + $currentPath
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "  [OK] Added $BinDir to user PATH" -ForegroundColor Green
} else {
    Write-Host "  [OK] Already in PATH: $BinDir" -ForegroundColor Green
}

# Also update current session PATH
if ($env:Path -notlike "*$BinDir*") {
    $env:Path = $BinDir + ';' + $env:Path
}

# ── Create uninstaller ─────────────────────────────────────────────────────
$uninstallScript = Join-Path $BinDir "uninstall.ps1"
$uninstallContent = @'
<#
.SYNOPSIS
    Uninstall TOMAS Agent completely.
#>
Write-Host ""
Write-Host "  Removing TOMAS..." -ForegroundColor Cyan

$tomasDir = "{InstallDir}"
$binDir = Join-Path $tomasDir "bin"

# Remove from PATH
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
$paths = $currentPath -split ';' | Where-Object { $_ -ne $binDir }
[Environment]::SetEnvironmentVariable("Path", ($paths -join ';'), "User")

Write-Host "  [OK] Removed $binDir from PATH" -ForegroundColor Green

# Remove install directory
if (Test-Path $tomasDir) {
    Remove-Item -Path $tomasDir -Recurse -Force
    Write-Host "  [OK] Deleted $tomasDir" -ForegroundColor Green
}

Write-Host ""
Write-Host "  TOMAS has been uninstalled." -ForegroundColor Green
Write-Host "  Close and reopen your terminal for PATH changes to take effect."
'@ -replace '{InstallDir}', $InstallDir

[System.IO.File]::WriteAllText($uninstallScript, $uninstallContent, [System.Text.Encoding]::UTF8)
Write-Host "  [OK] Created uninstaller: $uninstallScript" -ForegroundColor Green

# ── Done ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ==========================================" -ForegroundColor Cyan
Write-Host "         Installation Complete!" -ForegroundColor Cyan
Write-Host "  ==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "    Installed to:  $InstallDir" -ForegroundColor White
Write-Host "    Source code:   $SrcDir" -ForegroundColor White
Write-Host "    Python venv:   $VenvDir" -ForegroundColor White
Write-Host "    Launchers:     $BinDir" -ForegroundColor White
Write-Host "    Instructions:  $(Join-Path $InstallDir 'instructions')" -ForegroundColor White
Write-Host "    Sessions:      $(Join-Path $InstallDir 'sessions')" -ForegroundColor White
Write-Host ""
Write-Host "  Commands:" -ForegroundColor Yellow
Write-Host "    TOMAS              Run the agent" -ForegroundColor Cyan
Write-Host "    TOMAS-upgrade      Update TOMAS from GitHub" -ForegroundColor Cyan
Write-Host "    TOMAS-uninstall    Remove TOMAS completely" -ForegroundColor Cyan
Write-Host ""
Write-Host "  New features:" -ForegroundColor Yellow
Write-Host "    💾 Sessions         Auto-saved on exit. Browse/continue from menu." -ForegroundColor Cyan
Write-Host "    📋 Instructions     Edit ~/.tomas/instructions/ for global agent rules." -ForegroundColor Cyan
Write-Host "    📄 Project config   Put AGENT.md in your project root for per-project rules." -ForegroundColor Cyan
Write-Host ""
Write-Host "  To use TOMAS in this terminal, run:" -ForegroundColor White
Write-Host "    `$env:Path = '$BinDir;' + `$env:Path; tomas" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Or if you ran from cmd.exe with install.cmd" -ForegroundColor White
Write-Host "  the PATH is already updated — just type: TOMAS" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  (New terminals will find TOMAS automatically.)" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  First time? Edit your API key in:" -ForegroundColor White
Write-Host "    $EnvFile" -ForegroundColor DarkGray
Write-Host ""
