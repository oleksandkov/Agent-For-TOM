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

# Helper: detect whether the venv uses bin/ (MSYS2) or Scripts/ (Windows)
function Get-VenvScriptsDir {
    param([string]$VenvDir)
    if (Test-Path (Join-Path $VenvDir "bin"))      { return "bin" }
    if (Test-Path (Join-Path $VenvDir "Scripts"))  { return "Scripts" }
    return $null
}
# Default to Windows path; will be corrected after venv validation/creation.
$PythonExe = Join-Path (Join-Path $VenvDir "Scripts") "python.exe"
$PipExe    = Join-Path (Join-Path $VenvDir "Scripts") "pip.exe"
$LauncherCmd = Join-Path $BinDir "TOMAS.cmd"
$LauncherBat = Join-Path $BinDir "TOMAS.bat"

# ── Detect mode ─────────────────────────────────────────────────────────────
$isPiped = $MyInvocation.MyCommand.Name -eq "__remote_exec__" -or
           $MyInvocation.MyCommand.Path -eq "" -or
           [Console]::IsInputRedirected
$isRemote = $isPiped -or (-not $PSCommandPath) -or (-not (Test-Path (Split-Path -Parent $PSCommandPath)))

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
Write-Host "  [2/7] Setting up directories..." -ForegroundColor Cyan
$null = New-Item -ItemType Directory -Path $BinDir -Force
$null = New-Item -ItemType Directory -Path $SrcDir -Force
Write-Host "  [OK] Install directory: $InstallDir" -ForegroundColor Green

# ── Get source code ────────────────────────────────────────────────────────
$localSource = if ($PSCommandPath) { Split-Path -Parent $PSCommandPath } else { $null }
$hasLocalSource = $localSource -and (Test-Path (Join-Path $localSource "agent.py"))

if ($hasLocalSource) {
    Write-Host ""
    Write-Host "  [3/7] Copying local source..." -ForegroundColor Cyan
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
    Write-Host "  [3/7] Downloading from GitHub..." -ForegroundColor Cyan
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
Write-Host "  [4/7] Creating virtual environment..." -ForegroundColor Cyan

# Check if existing venv has a working Python executable (bin or Scripts)
$venvScriptsDir = Get-VenvScriptsDir $VenvDir
$venvPythonOk = $false
if ($venvScriptsDir) {
    $PythonExe = Join-Path (Join-Path $VenvDir $venvScriptsDir) "python.exe"
    $PipExe    = Join-Path (Join-Path $VenvDir $venvScriptsDir) "pip.exe"
    try {
        $ver = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
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

# Detect venv script directory (Scripts on Windows, bin on MSYS2/MinGW)
$scriptsDir = Get-VenvScriptsDir $VenvDir
if (-not $scriptsDir) {
    Write-Host "  [FAIL] Could not detect venv script directory" -ForegroundColor Red
    exit 1
}
$PythonExe = Join-Path (Join-Path $VenvDir $scriptsDir) "python.exe"
$PipExe    = Join-Path (Join-Path $VenvDir $scriptsDir) "pip.exe"

# ── Install dependencies ───────────────────────────────────────────────────
Write-Host ""
Write-Host "  [5/7] Installing Python dependencies..." -ForegroundColor Cyan

# Check that pip exists before using it
if (-not (Test-Path $PipExe)) {
    Write-Host "  [INFO] pip not found in venv, running ensurepip..." -ForegroundColor Yellow
    if (-not (Test-Path $PythonExe)) {
        Write-Host "  [FAIL] Python executable missing in venv" -ForegroundColor Red
        Write-Host "         Deleting corrupted venv and recreating..." -ForegroundColor Yellow
        Remove-Item -Path $VenvDir -Recurse -Force
        & $pythonPath -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [FAIL] Failed to recreate virtual environment" -ForegroundColor Red
            exit 1
        }
        # Re-detect Scripts vs bin after recreation (MSYS2 uses bin)
        $scriptsDir = Get-VenvScriptsDir $VenvDir
        if (-not $scriptsDir) {
            Write-Host "  [FAIL] Could not detect venv script directory after recreation" -ForegroundColor Red
            exit 1
        }
        $PythonExe = Join-Path (Join-Path $VenvDir $scriptsDir) "python.exe"
        $PipExe    = Join-Path (Join-Path $VenvDir $scriptsDir) "pip.exe"
    }
    & $PythonExe -m ensurepip --upgrade 2>&1 | Out-Null
    if (-not (Test-Path $PipExe)) {
        Write-Host "  [FAIL] pip is not available in the virtual environment" -ForegroundColor Red
        Write-Host "         Try deleting $VenvDir and re-running the installer" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "  [OK] pip installed in virtual environment" -ForegroundColor Green
}

& $PipExe install --quiet --upgrade pip setuptools wheel 2>&1 | Out-Null
$reqFile = Join-Path $SrcDir "requirements.txt"
if (Test-Path $reqFile) {
    & $PipExe install --quiet -r $reqFile
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Dependencies installed successfully" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] Failed to install some dependencies" -ForegroundColor Red
        Write-Host "         Run manually: $PipExe install -r $reqFile" -ForegroundColor Yellow
        if ($PipExe -like "*\bin\*") {
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
Write-Host "  [6/7] Creating launcher scripts..." -ForegroundColor Cyan

# TOMAS.ps1 — PowerShell launcher (used by the PATH entry)
$ps1Content = @'
#!/usr/bin/env pwsh
# TOMAS.ps1 — TOMAS Agent Launcher (installed)
$ErrorActionPreference = "Stop"
$tomasDir = "{InstallDir}"
$venvDir = Join-Path $tomasDir ".venv"
# Detect Scripts vs bin directory (Windows vs MSYS2/MinGW venvs)
$venvBin = "Scripts"
if (Test-Path (Join-Path $venvDir "bin")) {{ $venvBin = "bin" }}
$python = Join-Path (Join-Path $venvDir $venvBin) "python.exe"
$cli = Join-Path (Join-Path $tomasDir "src") "agent_cli.py"
if (-not (Test-Path $python)) {{
    Write-Host "ERROR: TOMAS venv not found at $python" -ForegroundColor Red
    Write-Host "Reinstall with: powershell -c `"iex (iwr -UseBasicParsing -Uri https://raw.githubusercontent.com/oleksandkov/Agent-For-TOM/prototype2-refactoring/install.ps1)`"" -ForegroundColor Yellow
    exit 1
}}
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
)
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

# ── Set up .env ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "       Configuring environment..." -ForegroundColor Cyan

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
Write-Host "  [7/7] Finalizing setup..." -ForegroundColor Cyan
Write-Host "       Adding to system PATH..." -ForegroundColor DarkGray

$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
$paths = $currentPath -split ';'
$changed = $false

if ($paths -notcontains $BinDir) {
    $newPath = $BinDir + ';' + $currentPath
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "  [OK] Added $BinDir to user PATH" -ForegroundColor Green
    $changed = $true
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
if (Test-Path $tomasDir) {{
    Remove-Item -Path $tomasDir -Recurse -Force
    Write-Host "  [OK] Deleted $tomasDir" -ForegroundColor Green
}}

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
Write-Host ""
Write-Host "  Commands:" -ForegroundColor Yellow
Write-Host "    TOMAS              Run the agent" -ForegroundColor Cyan
Write-Host "    TOMAS-upgrade      Update TOMAS from GitHub" -ForegroundColor Cyan
Write-Host "    TOMAS-uninstall    Remove TOMAS completely" -ForegroundColor Cyan
Write-Host ""
Write-Host "  First time? Edit your API key in:" -ForegroundColor White
Write-Host "    $EnvFile" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Close this terminal, open a NEW one, then run: TOMAS" -ForegroundColor Yellow
Write-Host ""
Write-Host ""
