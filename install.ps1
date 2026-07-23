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
$PythonExe   = Join-Path (Join-Path $VenvDir "Scripts") "python.exe"
$PipExe      = Join-Path (Join-Path $VenvDir "Scripts") "pip.exe"
$LauncherPs1 = Join-Path $BinDir "TOMAS.ps1"
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

if (-not (Test-Path $VenvDir)) {
    & $pythonPath -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [FAIL] Failed to create virtual environment" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] Virtual environment created" -ForegroundColor Green
} else {
    Write-Host "  [OK] Virtual environment already exists" -ForegroundColor Green
}

# ── Install dependencies ───────────────────────────────────────────────────
Write-Host ""
Write-Host "  [5/7] Installing Python dependencies..." -ForegroundColor Cyan

# Check that pip exists before using it
if (-not (Test-Path $PipExe)) {
    Write-Host "  [INFO] pip not found in venv, running ensurepip..." -ForegroundColor Yellow
    & $PythonExe -m ensurepip --upgrade 2>&1 | Out-Null
    if (-not (Test-Path $PipExe)) {
        Write-Host "  [FAIL] pip is not available in the virtual environment" -ForegroundColor Red
        Write-Host "         Try deleting $VenvDir and re-running the installer" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "  [OK] pip installed in virtual environment" -ForegroundColor Green
}

& $PipExe install --quiet --upgrade pip 2>&1 | Out-Null
$reqFile = Join-Path $SrcDir "requirements.txt"
if (Test-Path $reqFile) {
    & $PipExe install --quiet -r $reqFile
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Dependencies installed successfully" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] Failed to install some dependencies" -ForegroundColor Red
        Write-Host "         Run manually: $PipExe install -r $reqFile"
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
$python = Join-Path (Join-Path $venvDir "Scripts") "python.exe"
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
$cmdContent = @'
@echo off
set "TOMAS_DIR={InstallDir}"
"{InstallDir}\.venv\Scripts\python.exe" "{InstallDir}\src\agent_cli.py" %*
'@ -replace '{InstallDir}', $InstallDir

[System.IO.File]::WriteAllText($LauncherCmd, $cmdContent, [System.Text.Encoding]::UTF8)
Write-Host "  [OK] $LauncherCmd" -ForegroundColor Green

# TOMAS.bat — also create in bin (some environments prefer .bat)
Copy-Item $LauncherCmd $LauncherBat -Force
Write-Host "  [OK] $LauncherBat" -ForegroundColor Green

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

if ($changed -or -not $isPiped) {
    Write-Host "  ----------------------------------------------" -ForegroundColor DarkGray
    Write-Host "  To use TOMAS now:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "    1. Close this terminal and open a NEW one" -ForegroundColor White
    Write-Host "    2. Type:  TOMAS" -ForegroundColor Cyan
    Write-Host "    3. First time? Edit your API key in:" -ForegroundColor White
    Write-Host "       $EnvFile" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Or run this in your current terminal:" -ForegroundColor Yellow
    Write-Host "    TOMAS --help" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  To update TOMAS, re-run the install command." -ForegroundColor DarkGray
    Write-Host "  To uninstall:  uninstall-tomas" -ForegroundColor DarkGray
    Write-Host "  ----------------------------------------------" -ForegroundColor DarkGray
} else {
    Write-Host "  Close and reopen your terminal, then run: TOMAS" -ForegroundColor Yellow
}
Write-Host ""
