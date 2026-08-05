<#
.SYNOPSIS
    TOMAS Agent Installer --- install from GitHub or local source.
.DESCRIPTION
    Installs the TOMAS coding agent globally. Works both:
      - Locally:   powershell -ExecutionPolicy Bypass -File install.ps1
      - Remote:    powershell -c "iex (iwr -UseBasicParsing -Uri https://raw.githubusercontent.com/oleksandkov/Agent-For-TOM/main/install.ps1)"
.PARAMETER InstallDir
    Where to install TOMAS (default: ~/.tomas).
.PARAMETER RepoUrl
    GitHub archive URL to download (default: https://github.com/oleksandkov/Agent-For-TOM/archive/main.zip).
    Leave as default to skip remote download (local install mode).
.PARAMETER NoPrompt
    Skip interactive prompts.
#>

param(
    [string]$InstallDir = "",
    [string]$RepoUrl = "https://github.com/oleksandkov/Agent-For-TOM/archive/main.zip",
    [switch]$NoPrompt
)

# -- Config ------------------------------------------------------------------
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

# -- Detect mode -------------------------------------------------------------
$isPiped = $MyInvocation.MyCommand.Name -eq "__remote_exec__" -or
           $MyInvocation.MyCommand.Path -eq "" -or
           [Console]::IsInputRedirected

Write-Host ""
Write-Host "  ==========================================" -ForegroundColor Cyan
Write-Host "       TOMAS Agent Installer v2.0" -ForegroundColor Cyan
Write-Host "  ==========================================" -ForegroundColor Cyan
Write-Host ""

# -- Prerequisites -----------------------------------------------------------
$pythonPath = ""

# Helper: test a Python executable and return its path if >= 3.10
function Test-PythonExe {
    param([string]$ExePath)
    if (-not $ExePath -or -not (Test-Path $ExePath)) { return $null }
    try {
        $ver = & $ExePath -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ([version]$ver -ge [version]"3.10") { return $ExePath }
    } catch {}
    return $null
}

# Strategy 1: Use the Python launcher (py) --- always points to python.org Python on Windows
try {
    $pyExe = (Get-Command "py" -ErrorAction SilentlyContinue).Source
    if ($pyExe) {
        # Try default py launcher first, then specific version flags in descending order
        $pyVersions = @("", "-3.14", "-3.13", "-3.12", "-3.11", "-3.10")
        foreach ($vFlag in $pyVersions) {
            $cmd = if ($vFlag) { "$pyExe $vFlag" } else { $pyExe }
            $result = & $pyExe $vFlag -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $result) {
                $candidate = $result.Trim()
                if ($candidate -notmatch 'msys64|ucrt64|mingw') {
                    $pythonPath = Test-PythonExe $candidate
                    if ($pythonPath) { break }
                }
            }
        }
    }
} catch {}

# Strategy 2: Try python from PATH, preferring standard Python.org installations over MSYS2/venv
if (-not $pythonPath) {
    $pythonCandidates = @()
    try {
        $pythonCandidates = @(Get-Command "python" -ErrorAction SilentlyContinue -TotalCount 10 | Select-Object -ExpandProperty Source)
    } catch {}

    # First pass: prefer standard python.org paths (AppData\Local\Programs\Python or AppData\Local\Python)
    foreach ($p in $pythonCandidates) {
        if ($p -match 'AppData\\Local\\(Programs\\Python|Python)' -and $p -notmatch 'venv|\.venv') {
            $pythonPath = Test-PythonExe $p
            if ($pythonPath) { break }
        }
    }
    # Second pass: accept any non-MSYS2, non-venv python
    if (-not $pythonPath) {
        foreach ($p in $pythonCandidates) {
            if ($p -notmatch 'msys64|ucrt64|mingw|venv|\.venv') {
                $pythonPath = Test-PythonExe $p
                if ($pythonPath) { break }
            }
        }
    }
    # Third pass: accept any non-MSYS2 python (even in a venv)
    if (-not $pythonPath) {
        foreach ($p in $pythonCandidates) {
            if ($p -notmatch 'msys64|ucrt64|mingw') {
                $pythonPath = Test-PythonExe $p
                if ($pythonPath) { break }
            }
        }
    }
    # Fourth pass: accept any python (including MSYS2 as last resort)
    if (-not $pythonPath) {
        foreach ($p in $pythonCandidates) {
            $pythonPath = Test-PythonExe $p
            if ($pythonPath) { break }
        }
    }
}

# Strategy 3: Try python3 from PATH
if (-not $pythonPath) {
    $python3Candidates = @()
    try {
        $python3Candidates = @(Get-Command "python3" -ErrorAction SilentlyContinue -TotalCount 5 | Select-Object -ExpandProperty Source)
    } catch {}
    foreach ($p in $python3Candidates) {
        if ($p -notmatch 'msys64|ucrt64|mingw|WindowsApps|venv|\.venv') {
            $pythonPath = Test-PythonExe $p
            if ($pythonPath) { break }
        }
    }
    if (-not $pythonPath) {
        foreach ($p in $python3Candidates) {
            $pythonPath = Test-PythonExe $p
            if ($pythonPath) { break }
        }
    }
}

if (-not $pythonPath) {
    Write-Host "  [FAIL] Python 3.10+ is required but not found." -ForegroundColor Red
    Write-Host "         Install Python from: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "         Make sure to check 'Add Python to PATH' during installation."
    exit 1
}
$pyDisplayVer = & $pythonPath -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>$null
Write-Host "  [OK] Python $pyDisplayVer found at: $pythonPath" -ForegroundColor Green

# -- Create directory structure ---------------------------------------------
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

# -- Get source code --------------------------------------------------------
$localSource = if ($PSCommandPath) { Split-Path -Parent $PSCommandPath } else { $null }
$hasLocalSource = $localSource -and (Test-Path (Join-Path $localSource "agent.py"))

if ($hasLocalSource) {
    Write-Host ""
    Write-Host "  [3/9] Copying local source..." -ForegroundColor Cyan
    # Copy project files AND package directories. This used to be -File only,
    # which silently left out core/, adapters/ and learning/ - the install
    # completed "successfully" and then died on first run with
    # ModuleNotFoundError: No module named 'learning'.
    $exclude = @('.venv', '__pycache__', '.git', '.agent', '.claude', '.kilo',
                 '.github', '.pytest_cache', '.mypy_cache', 'node_modules')
    # `.env` must never be copied into $SrcDir. It holds API keys, and $SrcDir
    # is deleted wholesale by `TOMAS update` — so copying it there both spreads
    # the secret and stages it for destruction. agent_cli then treats the copy
    # as a "legacy" env and migrates it back, which makes the round trip look
    # deliberate. The durable copy lives at ~/.tomas/.env and stays there.
    # `.env` and `providers.json` must never be copied into $SrcDir. They hold
    # API keys and provider setup, and $SrcDir is deleted wholesale by
    # `TOMAS update` — so copying them there both spreads the secret and stages
    # it for destruction. agent_cli then treats the copied .env as a "legacy"
    # file and migrates it back, which makes the round trip look deliberate.
    # The durable copies live in ~/.tomas/ and stay there.
    $excludeFilePatterns = @('*.pyc', '.gitignore', '.env', '.env.*',
                             'providers.json',
                             'simulation_results.json', 'cyrillic_results.json',
                             'session_audit_*.json')

    Get-ChildItem -Path $localSource -File | Where-Object {
        $skip = $false
        foreach ($pat in ($exclude + $excludeFilePatterns)) {
            if ($_.Name -like $pat) { $skip = $true; break }
        }
        -not $skip
    } | Copy-Item -Destination $SrcDir -Force

    $dirCount = 0
    $copyErrors = @()
    foreach ($dir in (Get-ChildItem -Path $localSource -Directory)) {
        if ($exclude -contains $dir.Name) { continue }
        $dest = Join-Path $SrcDir $dir.Name
        if (Test-Path $dest) {
            Remove-Item -Path $dest -Recurse -Force -ErrorAction SilentlyContinue
        }
        # Walk and copy file by file, skipping __pycache__ on the way in.
        # `Copy-Item -Recurse` copied those directories and then deleted them
        # afterwards, which meant a stale or locked .pyc aborted the copy with
        # "Could not find a part of the path ...\learning\__pycache__" and left
        # the package directory empty. Excluding at copy time removes the whole
        # failure mode instead of cleaning up after it.
        $sourceRoot = $dir.FullName.TrimEnd('\')
        foreach ($item in (Get-ChildItem -Path $sourceRoot -Recurse -File -ErrorAction SilentlyContinue)) {
            $relative = $item.FullName.Substring($sourceRoot.Length).TrimStart('\')
            if ($relative -match '(^|\\)(__pycache__|\.pytest_cache|\.mypy_cache)(\\|$)') { continue }
            if ($item.Extension -eq '.pyc') { continue }
            $target = Join-Path $dest $relative
            $targetDir = Split-Path -Parent $target
            if (-not (Test-Path $targetDir)) {
                $null = New-Item -ItemType Directory -Path $targetDir -Force
            }
            try {
                Copy-Item -LiteralPath $item.FullName -Destination $target -Force -ErrorAction Stop
            } catch {
                $copyErrors += "$relative : $($_.Exception.Message)"
            }
        }
        $dirCount++
    }

    $fileCount = (Get-ChildItem $SrcDir -File -ErrorAction SilentlyContinue).Count
    if ($copyErrors.Count -gt 0) {
        Write-Host "  [WARN] $($copyErrors.Count) file(s) could not be copied:" -ForegroundColor Yellow
        foreach ($e in ($copyErrors | Select-Object -First 5)) {
            Write-Host "         $e" -ForegroundColor DarkYellow
        }
    }
    # A copy that moved nothing is a failed install, not a successful one. This
    # used to print [OK] with "Copied 0 files", carry on, warn that there was no
    # requirements.txt, and only fall over three steps later.
    if ($fileCount -eq 0) {
        Write-Host "  [FAIL] Copied 0 files from $localSource" -ForegroundColor Red
        Write-Host "         The install would be empty. Common causes:" -ForegroundColor Yellow
        Write-Host "         - a running TOMAS or MCP server is locking $SrcDir" -ForegroundColor Yellow
        Write-Host "           (close TOMAS, then: Get-Process python | Where-Object" -ForegroundColor Yellow
        Write-Host "            { `$_.Path -like '*\.tomas\*' } | Stop-Process -Force)" -ForegroundColor Yellow
        Write-Host "         - the source folder is not a TOMAS checkout" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "  [OK] Copied $fileCount files and $dirCount directories to $SrcDir" -ForegroundColor Green
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

# -- Create virtual environment ---------------------------------------------
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
        Remove-Item -Path $VenvDir -Recurse -Force -ErrorAction SilentlyContinue
        # A venv cannot be replaced while something is executing out of it.
        # Windows reports this as "Access to the path ...python.exe is denied",
        # the removal half-succeeds, `python -m venv` then fails to write its
        # launcher, and the installer used to print "[OK] Virtual environment
        # created" over the top of all of it. Name the process instead.
        if (Test-Path $VenvDir) {
            $holders = @(Get-Process -ErrorAction SilentlyContinue |
                Where-Object { $_.Path -and $_.Path.StartsWith($VenvDir, 'OrdinalIgnoreCase') })
            Write-Host "  [FAIL] Could not remove the existing venv at $VenvDir" -ForegroundColor Red
            if ($holders.Count -gt 0) {
                Write-Host "         These processes are running from inside it:" -ForegroundColor Yellow
                foreach ($h in $holders) {
                    Write-Host "           PID $($h.Id)  $($h.ProcessName)  $($h.Path)" -ForegroundColor Yellow
                }
                Write-Host "         Close TOMAS (its MCP servers keep running after it exits), or:" -ForegroundColor Yellow
                Write-Host "           $($holders.Id -join ', ') | ForEach-Object { Stop-Process -Id `$_ -Force }" -ForegroundColor Cyan
            } else {
                Write-Host "         Something is holding files open there. Close TOMAS and any" -ForegroundColor Yellow
                Write-Host "         editor or terminal with that venv activated, then re-run." -ForegroundColor Yellow
            }
            exit 1
        }
    }
    & $pythonPath -m venv $VenvDir
    # $LASTEXITCODE alone missed this: venv can report success having failed to
    # write the launcher. Check for the executable it was supposed to produce.
    if ($LASTEXITCODE -ne 0 -or -not (Get-VenvPythonExe $VenvDir)) {
        Write-Host "  [FAIL] Failed to create virtual environment at $VenvDir" -ForegroundColor Red
        Write-Host "         python -m venv exited $LASTEXITCODE and produced no python executable." -ForegroundColor Yellow
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

# -- Install dependencies ---------------------------------------------------
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
    # Drop one known-benign pip line, and only that line. "Cache entry
    # deserialization failed, entry ignored" is not a deserialization failure
    # and nothing is corrupt: pip's cachecontrol logs it whenever a cached
    # response's `Vary` headers do not match the current request's, which
    # happens constantly because most PyPI entries are stored under
    # `Vary: Accept-Encoding` and a fresh venv re-requests them with different
    # headers. pip re-downloads and carries on. Forty of these scrolling past
    # a reinstall reads like something broke; it has not. Everything else pip
    # says still comes through.
    & $script:PipExe install --quiet -r $reqFile 2>&1 |
        Where-Object { $_ -notmatch 'Cache entry deserialization failed' }
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Dependencies installed successfully" -ForegroundColor Green
        try {
            & $script:PythonExe -m playwright install chromium 2>&1 | Out-Null
            Write-Host "  [OK] Playwright Chromium browser installed" -ForegroundColor Green
        } catch {}
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
    # Not a warning: TOMAS cannot run without anthropic, dotenv and the rest.
    # Reaching here means the source copy dropped it, so the install is broken
    # in exactly the way the next step is about to discover anyway.
    Write-Host "  [FAIL] No requirements.txt at $reqFile" -ForegroundColor Red
    Write-Host "         The source copy is incomplete — dependencies cannot be" -ForegroundColor Yellow
    Write-Host "         installed and TOMAS will not start. Re-run the installer" -ForegroundColor Yellow
    Write-Host "         from a complete checkout with TOMAS closed." -ForegroundColor Yellow
    exit 1
}

# -- Verify the install can actually start -----------------------------
# An installer that reports success and then dies on first run with
# ModuleNotFoundError has not installed anything. Import the entry point here,
# where the failure can still be explained and acted on.
Write-Host ""
Write-Host "  [5b/9] Verifying installation..." -ForegroundColor Cyan
$requiredPackages = @('core', 'adapters', 'learning')
$missingPackages = @()
foreach ($pkg in $requiredPackages) {
    if (-not (Test-Path (Join-Path $SrcDir $pkg))) { $missingPackages += $pkg }
}
if ($missingPackages.Count -gt 0) {
    Write-Host "  [FAIL] Missing package directories: $($missingPackages -join ', ')" -ForegroundColor Red
    Write-Host "         The source copy is incomplete - TOMAS cannot start." -ForegroundColor Yellow
    Write-Host "         If you installed from GitHub, these directories may be" -ForegroundColor Yellow
    Write-Host "         missing from the repository (untracked). Commit them, or" -ForegroundColor Yellow
    Write-Host "         re-run this installer from a complete local checkout." -ForegroundColor Yellow
    exit 1
}

$importCheck = & $script:PythonExe -c "import sys; sys.path.insert(0, r'$SrcDir'); import agent; print('ok')" 2>&1
if ($LASTEXITCODE -ne 0 -or "$importCheck" -notmatch 'ok') {
    Write-Host "  [FAIL] TOMAS cannot be imported after install:" -ForegroundColor Red
    foreach ($line in ("$importCheck" -split "`n" | Select-Object -Last 6)) {
        if ($line.Trim()) { Write-Host "         $($line.Trim())" -ForegroundColor DarkGray }
    }
    Write-Host "         Fix the error above and re-run the installer." -ForegroundColor Yellow
    exit 1
}
Write-Host "  [OK] TOMAS imports cleanly ($($requiredPackages.Count) packages present)" -ForegroundColor Green

# -- Create launcher scripts ------------------------------------------------
Write-Host ""
Write-Host "  [6/9] Creating launcher scripts..." -ForegroundColor Cyan

# [System.Text.Encoding]::UTF8 writes a BOM. cmd.exe treats a leading BOM as
# literal characters on the first line, so "@echo off" becomes unrecognized
# and every generated .cmd/.bat launcher fails immediately. Use a BOM-less
# UTF8 encoding for every generated launcher file instead.
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# TOMAS.ps1 --- PowerShell launcher (used by the PATH entry)
$ps1Content = @'
#!/usr/bin/env pwsh
# TOMAS.ps1 --- TOMAS Agent Launcher (installed)
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
    Write-Host "Reinstall with: powershell -c `"iex (iwr -UseBasicParsing -Uri https://raw.githubusercontent.com/oleksandkov/Agent-For-TOM/main/install.ps1)`"" -ForegroundColor Yellow
    exit 1
}
& $python $cli @args
exit $LASTEXITCODE
'@ -replace '{InstallDir}', $InstallDir

[System.IO.File]::WriteAllText($LauncherPs1, $ps1Content, $Utf8NoBom)
Write-Host "  [OK] $LauncherPs1" -ForegroundColor Green

# TOMAS.cmd --- CMD launcher (so `TOMAS` works from cmd.exe)
# Detect Scripts vs bin directory (Windows vs MSYS2/MinGW venvs)
$venvBin = "Scripts"; if (Test-Path (Join-Path $VenvDir "bin")) { $venvBin = "bin" }
$cmdContent = @'
@echo off
set "TOMAS_DIR={InstallDir}"
"{InstallDir}\.venv\{VenvBin}\python.exe" "{InstallDir}\src\agent_cli.py" %*
'@ -replace '{InstallDir}', $InstallDir -replace '\{VenvBin\}', $venvBin

[System.IO.File]::WriteAllText($LauncherCmd, $cmdContent, $Utf8NoBom)
Write-Host "  [OK] $LauncherCmd" -ForegroundColor Green

# TOMAS.bat --- also create in bin (some environments prefer .bat)
Copy-Item $LauncherCmd $LauncherBat -Force
Write-Host "  [OK] $LauncherBat" -ForegroundColor Green

# -- Create upgrade & uninstall commands ----------------------------------
# TOMAS-upgrade.cmd --- re-run remote install
$upgradeBat = Join-Path $BinDir "TOMAS-upgrade.cmd"
$upgradeContent = @'
@echo off
echo   ==========================================
echo       TOMAS Upgrade
echo   ==========================================
echo.
echo   Upgrading TOMAS from GitHub...
echo.
powershell -ExecutionPolicy Bypass -c "iex (iwr -UseBasicParsing -Uri https://raw.githubusercontent.com/oleksandkov/Agent-For-TOM/main/install.ps1)"
if %ERRORLEVEL% neq 0 (
    echo   Upgrade failed. See messages above.
    pause
    exit /b %ERRORLEVEL%
)

rem -- Refresh PATH so `tomas` works immediately in this session --
set "PATH=%USERPROFILE%\.tomas\bin;%PATH%"
echo.
echo   Upgrade complete! You can now run: TOMAS
'@
[System.IO.File]::WriteAllText($upgradeBat, $upgradeContent, $Utf8NoBom)
Write-Host "  [OK] $upgradeBat" -ForegroundColor Green

# TOMAS-uninstall.cmd --- call uninstall.ps1
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
[System.IO.File]::WriteAllText($uninstallBat, $uninstallContent, $Utf8NoBom)
Write-Host "  [OK] $uninstallBat" -ForegroundColor Green

# -- Create default instructions and sessions dir --------------------------
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

# Education Focus

You work with students and teachers most of the time. Keep every default
below in mind, but a specific project's AGENTS.md or a direct request from
the user always overrides it.

## Audience and language

- Default response language is Ukrainian. Mirror the user's own language
  instead when they write in Russian, English, or anything else -- match
  them, don't force Ukrainian on them.
- With a student, teach: explain the reasoning, not just the final answer.
  With a teacher, act as a co-author: be efficient, precise, and ready to
  hand over finished material.

## Primary goal: lab-work guides (методичні вказівки / методичка)

- One of your main jobs is producing методичні вказівки (methodichka) --
  structured lab-work guides -- for programming/CS, engineering/physics, and
  general courses.
- Follow the conventional Ukrainian technical-education structure for each
  lab: a title ("ЛАБОРАТОРНА РОБОТА №N" plus topic), Мета роботи (goal),
  theoretical background (Загальні відомості / Методичні вказівки),
  Контрольні запитання (control questions), Завдання (tasks -- include a
  Варіанти table when the group needs individual variants), an optional
  Зауваження (remark), recommended tools (мова програмування / середовище
  програмування / тип проекту for programming labs, or equipment/instruments
  for engineering and physics labs), Зміст звіту (report contents) where
  relevant, and a numbered Література (references) list reused consistently
  across the labs of one course.
- When producing more than one lab work for the same course, keep numbering,
  terminology, and cross-references between labs consistent -- a later lab
  may reuse a module built in an earlier one, exactly as a real methodichka
  does.
- If something essential is genuinely missing (subject, number of labs,
  language, tooling), ask once -- one message listing everything you need --
  and then build. Do not ask again once the user has answered or told you to
  go ahead: choose sensible defaults, say in one line which you chose, and
  produce the document. "Just do it", "yes, correct" and a bare number are
  instructions to act, not invitations to confirm again. Asking twice about
  the same thing wastes the user's turn.

## Self-improving toward this specific user

- The built-in learning system (/self-improve) is not just a log -- it is
  how you get useful for this particular user faster. Use it to build a
  working profile of them: the terminology, syntax and phrasing habits they
  use in their own language, formatting conventions, and any corrections or
  preferences they've given you.
- Before producing material for a returning user -- a methodichka, a report,
  a message -- recall what you've learned about their style and apply it.
  Write the way they write and use the terms they use, instead of a generic
  default.
- Stay tied to the user's actual stated goal on every turn. Don't wander
  into unrelated territory, and check with them before assuming a large
  amount of structure or content they haven't described.
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

- `AGENT.md` --- local agent identity (safe to edit or delete)
- `project/` --- per-project instruction files
"@ | Out-File -FilePath $readmeFile -Encoding utf8
    Write-Host "  [OK] Created instructions README: $readmeFile" -ForegroundColor Green
}

# Create .gitkeep in project instructions dir
$gitkeep = Join-Path $ProjectsDir ".gitkeep"
if (-not (Test-Path $gitkeep)) {
    "" | Out-File -FilePath $gitkeep -Encoding utf8
}

Write-Host "  [OK] Sessions directory: $SessionsDir" -ForegroundColor Green

# -- Set up .env -------------------------------------------------------------
Write-Host ""
Write-Host "  [8/9] Configuring environment..." -ForegroundColor Cyan

# Back up before touching anything, always. These files are the only copy of
# the user's API keys and provider setup, and an installer that loses them has
# done real damage that no error message undoes. The copies have to be made
# *before* any branch below decides what to write.
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
foreach ($precious in @($EnvFile, (Join-Path $InstallDir "providers.json"))) {
    if (Test-Path $precious) {
        $backupPath = "$precious.backup-$stamp"
        try {
            Copy-Item -LiteralPath $precious -Destination $backupPath -Force -ErrorAction Stop
            Write-Host "  [OK] Backed up $(Split-Path -Leaf $precious) to $(Split-Path -Leaf $backupPath)" -ForegroundColor DarkGray
        } catch {
            Write-Host "  [WARN] Could not back up $(Split-Path -Leaf $precious): $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}

if (-not (Test-Path $EnvFile)) {
    # If a backup from an earlier run exists, say so rather than silently
    # handing back an empty template — a missing .env on a machine that has run
    # TOMAS before means something removed it, and the keys are recoverable.
    $priorBackups = @(Get-ChildItem -Path $InstallDir -Filter ".env.backup-*" -Force -ErrorAction SilentlyContinue |
                      Sort-Object LastWriteTime -Descending)
    if ($priorBackups.Count -gt 0) {
        Write-Host "  [WARN] .env was missing, but $($priorBackups.Count) backup(s) exist." -ForegroundColor Yellow
        Write-Host "         Your API keys are in: $($priorBackups[0].FullName)" -ForegroundColor Yellow
        Write-Host "         Copy them into the new .env after this finishes." -ForegroundColor Yellow
    }
    @"
# TOMAS configuration (created by install.ps1)
# Required: set your API key below
ANTHROPIC_API_KEY=
# Optional: API base URL (default: https://api.anthropic.com)
# ANTHROPIC_BASE_URL=
# Optional: model name (e.g. claude-sonnet-5)
# AGENT_MODEL=claude-sonnet-5
# Optional: "1" to auto-approve low-risk tools
# AGENT_AUTO_APPROVE=1
"@ | Out-File -FilePath $EnvFile -Encoding utf8
    Write-Host "  [OK] Created .env configuration file" -ForegroundColor Green
} else {
    Write-Host "  [OK] .env already exists (keeping existing)" -ForegroundColor Green
}

# --- Configure API key (if running interactively) -----------------------------
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

# --- Add to PATH --------------------------------------------------------------
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

# Always update current session PATH to put $BinDir at the very front and filter legacy paths
$cleanSessionPaths = $env:Path -split ';' | Where-Object { $_ -and $_ -ne $BinDir -and $_ -notlike "*Agent_for_TOM*" }
$env:Path = "$BinDir;" + ($cleanSessionPaths -join ';')

# --- Create uninstaller -------------------------------------------------------
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
$venvDir = Join-Path $tomasDir ".venv"

# Remove from PATH
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
$paths = $currentPath -split ';' | Where-Object { $_ -and $_ -ne $binDir }
[Environment]::SetEnvironmentVariable("Path", ($paths -join ';'), "User")
Write-Host "  [OK] Removed $binDir from PATH" -ForegroundColor Green

# Create a self-deleting background cleanup script in TEMP
$cleanBat = Join-Path $env:TEMP "tomas-uninstall-clean.cmd"
$cleanContent = "@echo off`r`nping 127.0.0.1 -n 3 >nul`r`n:retry`r`nrmdir /s /q `"{InstallDir}`" 2>nul`r`nif not exist `"{InstallDir}`" goto done`r`nping 127.0.0.1 -n 2 >nul`r`ngoto retry`r`n:done`r`ndel `"%~f0`" 2>nul".Replace('{InstallDir}', $tomasDir)
[System.IO.File]::WriteAllText($cleanBat, $cleanContent)

# Launch background process decoupled from job object
try {
    [void](Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine = "cmd.exe /c `"$cleanBat`""})
} catch {
    Start-Process -FilePath "$cleanBat" -WindowStyle Hidden
}
Write-Host "  [OK] Deleted $tomasDir" -ForegroundColor Green

Write-Host ""
Write-Host "  TOMAS has been uninstalled." -ForegroundColor Green
Write-Host "  Close and reopen your terminal for PATH changes to take effect."
'@ -replace '{InstallDir}', $InstallDir

[System.IO.File]::WriteAllText($uninstallScript, $uninstallContent, $Utf8NoBom)
Write-Host "  [OK] Created uninstaller: $uninstallScript" -ForegroundColor Green

# -- Run setup to install default MCPs --
Write-Host ""
Write-Host "  [10/10] Running TOMAS setup (default MCPs)..." -ForegroundColor Cyan
try {
    & $script:PythonExe "$SrcDir\agent_cli.py" setup
    Write-Host "  [OK] Default MCPs configured" -ForegroundColor Green
} catch {
    Write-Host "  [WARN] Setup MCPs skipped: $_" -ForegroundColor Yellow
    Write-Host "         Run 'TOMAS setup' later to configure default MCPs." -ForegroundColor Yellow
}

# -- Done --------------------------------------------------------------------
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
Write-Host "    [Sessions]      Auto-saved on exit. Browse/continue from menu." -ForegroundColor Cyan
Write-Host "    [Instructions]  Edit ~/.tomas/instructions/ for global agent rules." -ForegroundColor Cyan
Write-Host "    [Project config] Put AGENT.md in your project root for per-project rules." -ForegroundColor Cyan
Write-Host ""
Write-Host "  To use TOMAS in this terminal, run:" -ForegroundColor White
Write-Host "    `$env:Path = '$BinDir;' + `$env:Path; tomas" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Or if you ran from cmd.exe with install.cmd" -ForegroundColor White
Write-Host "  the PATH is already updated -- just type: TOMAS" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  (New terminals will find TOMAS automatically.)" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  First time? Edit your API key in:" -ForegroundColor White
Write-Host "    $EnvFile" -ForegroundColor DarkGray
Write-Host ""
