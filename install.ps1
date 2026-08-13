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
    [switch]$NoPrompt,
    # Fetch the ~170 MB Playwright browser up front. Off by default -- see
    # `Install-PlaywrightBrowser` for why it is not part of a plain install.
    [switch]$WithBrowser
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

# Downloads are ~10x slower in Windows PowerShell with the progress bar on:
# Invoke-WebRequest repaints it per chunk and blocks on the console while it
# does. Costs nothing on pwsh 7, where it is already off.
$ProgressPreference = 'SilentlyContinue'

# -- Step counter ------------------------------------------------------------
# One counter, so the numbers cannot disagree with each other. They did: the
# steps ran [1/9] through [9/9] and then finished with [10/10], which is two
# different claims about how long the install is, on screen, in the same run.
$script:StepNo = 0
$script:StepTotal = 11

function Step {
    param([string]$Text)
    $script:StepNo++
    Write-Host ""
    Write-Host ("  [{0}/{1}] {2}" -f $script:StepNo, $script:StepTotal, $Text) -ForegroundColor Cyan
}

function StepOk   { param([string]$Text) Write-Host "        $Text" -ForegroundColor Green }
function StepInfo { param([string]$Text) Write-Host "        $Text" -ForegroundColor DarkGray }

function Install-PlaywrightBrowser {
    <#
      Chromium is ~170 MB and it is a *fallback*.

      `web_search` prefers Playwright and drops to duckduckgo_search/ddgs when
      it is unavailable, so the browser is a better-results upgrade, not a
      dependency -- and downloading it unconditionally made it the single
      longest part of the install, longer than everything else together on a
      normal connection.

      So: fetched when it is already on the machine (near-instant, keeps it
      current), when -WithBrowser is passed, or when the user says yes. Skipped
      by default in a piped/unattended run, where nobody is there to be asked
      and a several-minute download is the least welcome surprise.
    #>
    $cached = Join-Path $env:LOCALAPPDATA "ms-playwright"
    $alreadyHave = (Test-Path $cached) -and
                   (Get-ChildItem $cached -Directory -Filter "chromium-*" `
                        -ErrorAction SilentlyContinue).Count -gt 0

    $want = $WithBrowser -or $alreadyHave
    if (-not $want -and -not $NoPrompt -and -not $isPiped) {
        Write-Host "        Download the Playwright browser for web search? " `
                   -ForegroundColor Yellow -NoNewline
        Write-Host "(~170 MB) [y/N] " -ForegroundColor DarkGray -NoNewline
        $want = (Read-Host) -match '^[Yy]'
    }

    if (-not $want) {
        StepInfo "Skipped the Playwright browser (~170 MB)."
        StepInfo "Web search still works via duckduckgo; add it later with:"
        StepInfo "  TOMAS browser"
        return
    }
    try {
        & $script:PythonExe -m playwright install chromium 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { StepOk "Playwright Chromium ready" }
        else { StepInfo "Playwright browser not installed - run 'TOMAS browser' later" }
    } catch {
        StepInfo "Playwright browser not installed - run 'TOMAS browser' later"
    }
}

Write-Host ""
Write-Host "  TOMAS" -ForegroundColor Cyan -NoNewline
Write-Host "  -  Terminal Operated Modular Agent System" -ForegroundColor DarkGray
Write-Host "  $([string][char]0x2500 * 46)" -ForegroundColor DarkGray

# -- Prerequisites -----------------------------------------------------------
Step "Checking Python..."
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
Step "Setting up directories..."
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
    Step "Copying local source..."
    # Copy project files AND package directories. This used to be -File only,
    # which silently left out core/, adapters/ and learning/ - the install
    # completed "successfully" and then died on first run with
    # ModuleNotFoundError: No module named 'learning'.
    # -- What the agent needs to run, named explicitly ---------------------
    #
    # An allowlist, not a denylist, and the difference is the whole point. A
    # denylist means every new file in the repository leaks into every install
    # until someone notices and adds another exclusion. It had been losing that
    # race for a while: a real install contained eight simulation reports
    # (`TOMAS_SIMULATION_REPORT_V4.md` and friends), `Kimi_K3_Report.docx`,
    # `hello_world.html`, `image.png`, the whole test suite, `_scratch`, the
    # `labwork` corpus, and `nextjs-site` — none of which the agent imports or
    # reads at runtime.
    #
    # The module list is not guesswork: it is the transitive closure of
    # `import` from `agent.py` and `agent_cli.py`. Anything not reachable from
    # an entry point cannot be needed to start one.
    #
    # `install.ps1` is deliberately absent — `TOMAS-upgrade.cmd` fetches it
    # from GitHub, so the copy in `src` was only ever a copy.
    $runtimeModules = @(
        'agent.py', 'agent_cli.py',
        'instructions_manager.py', 'mcp_manager.py', 'net_probe.py',
        'openai_adapter.py', 'pdf_report_skill.py', 'provider_manager.py',
        'self_improve.py', 'self_notes.py', 'session_manager.py',
        'skills_manager.py', 'text_display.py', 'zen_catalog.py',
        'zen_proxy.py'
    )
    # Python packages the entry points import.
    $runtimePackages = @('adapters', 'core', 'learning')
    # Not imported, but read at runtime or by the installer itself.
    $runtimeExtras   = @('skills')
    $runtimeFiles    = $runtimeModules + @('requirements.txt')

    $exclude = @('.venv', '__pycache__', '.git', '.agent', '.claude', '.kilo',
                 '.github', '.pytest_cache', '.mypy_cache', 'node_modules')
    # `.env` must never be copied into $SrcDir. It holds API keys, and $SrcDir
    # is deleted wholesale by `TOMAS update` -- so copying it there both spreads
    # the secret and stages it for destruction. agent_cli then treats the copy
    # as a "legacy" env and migrates it back, which makes the round trip look
    # deliberate. The durable copy lives at ~/.tomas/.env and stays there.
    # `.env` and `providers.json` must never be copied into $SrcDir. They hold
    # API keys and provider setup, and $SrcDir is deleted wholesale by
    # `TOMAS update` -- so copying them there both spreads the secret and stages
    # it for destruction. agent_cli then treats the copied .env as a "legacy"
    # file and migrates it back, which makes the round trip look deliberate.
    # The durable copies live in ~/.tomas/ and stay there.
    $excludeFilePatterns = @('*.pyc', '.gitignore', '.env', '.env.*',
                             'providers.json',
                             'simulation_results.json', 'cyrillic_results.json',
                             'session_audit_*.json')

    # `$exclude` as one regex over a relative path, so a nested `node_modules`
    # is skipped as surely as a top-level one. Built from the same list rather
    # than a second hand-written pattern — two lists that must agree are two
    # lists that will not.
    # Built in two statements on purpose: `-join '|' + ')(\\|$)'` binds as
    # `-join ('|' + ')(\\|$)')`, so the separator becomes the tail of the
    # pattern and the result is an unbalanced regex that throws at match time.
    $excludeNames = ($exclude + @('.next', '.turbo', 'dist', 'build', '.cache') |
                     ForEach-Object { [regex]::Escape($_) }) -join '|'
    $excludeDirPattern = '(^|\\)(' + $excludeNames + ')(\\|$)'

    # Root files: exactly the runtime list, and a named failure for anything
    # missing. A module that silently does not arrive becomes a
    # ModuleNotFoundError on first run, three steps after the install said OK.
    $missingModules = @()
    foreach ($name in $runtimeFiles) {
        $source = Join-Path $localSource $name
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination $SrcDir -Force
        } elseif ($name -ne 'requirements.txt') {
            $missingModules += $name
        }
    }
    if ($missingModules.Count -gt 0) {
        Write-Host "  [FAIL] Source is missing runtime modules: $($missingModules -join ', ')" -ForegroundColor Red
        Write-Host "         This is not a TOMAS checkout, or it is incomplete." -ForegroundColor Yellow
        exit 1
    }

    $dirCount = 0
    $copyErrors = @()
    $wanted = $runtimePackages + $runtimeExtras
    foreach ($dir in (Get-ChildItem -Path $localSource -Directory)) {
        if ($wanted -notcontains $dir.Name) { continue }
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
            # $exclude is checked at *every* level, not only the top one. It was
            # applied to `$dir.Name` alone, so a directory that is not itself
            # excluded was descended into and everything under it copied —
            # including the `node_modules` that the exclusion list names.
            #
            # Measured on this checkout: `nextjs-site` is not an excluded name,
            # so the walk copied `nextjs-site\node_modules` — 479 MB in 15,607
            # files — plus an 89 MB `.next` build, one Copy-Item per file, for
            # a directory whose actual source is five files. That is the whole
            # of the long wait at "[3/11] Copying local source".
            if ($relative -match $excludeDirPattern) { continue }
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
    Step "Downloading from GitHub..."
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
Step "Creating virtual environment..."

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
Step "Installing Python dependencies..."

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

# uv, when it is here, does the same work about five times faster.
#
# Measured on this machine against the ten packages in requirements.txt, with
# a warm wheel cache and a fresh venv both times:
#
#     pip   56.8 s
#     uv    11.0 s
#
# The gap is wider on a cold cache, where pip's serial resolve-then-download
# dominates. This is the single largest saving in the install that does not
# involve skipping something.
#
# Never installed on the user's behalf: an installer that silently fetches a
# second package manager to speed itself up has made a decision that is not
# its to make. If uv is absent, pip does the job it always did.
$script:UvExe = (Get-Command "uv" -ErrorAction SilentlyContinue).Source
if ($script:UvExe) {
    StepInfo "using uv ($script:UvExe)"
} else {
    & $script:PipExe install --quiet --upgrade pip setuptools wheel 2>&1 | Out-Null
}

$reqFile = Join-Path $SrcDir "requirements.txt"
if ((Test-Path $reqFile) -and $script:UvExe) {
    $env:VIRTUAL_ENV = $VenvDir
    & $script:UvExe pip install --quiet --python $script:PythonExe -r $reqFile
    if ($LASTEXITCODE -eq 0) {
        StepOk "Dependencies installed"
    } else {
        Write-Host "  [WARN] uv failed; falling back to pip" -ForegroundColor Yellow
        & $script:PipExe install --quiet -r $reqFile 2>&1 |
            Where-Object { $_ -notmatch 'Cache entry deserialization failed' }
        if ($LASTEXITCODE -eq 0) {
            StepOk "Dependencies installed"
        } else {
            Write-Host "  [FAIL] Failed to install dependencies" -ForegroundColor Red
            Write-Host "         Run manually: $script:PipExe install -r $reqFile" -ForegroundColor Yellow
        }
    }
    Install-PlaywrightBrowser
} elseif (Test-Path $reqFile) {
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
        StepOk "Dependencies installed"
        Install-PlaywrightBrowser
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
    Write-Host "         The source copy is incomplete -- dependencies cannot be" -ForegroundColor Yellow
    Write-Host "         installed and TOMAS will not start. Re-run the installer" -ForegroundColor Yellow
    Write-Host "         from a complete checkout with TOMAS closed." -ForegroundColor Yellow
    exit 1
}

# -- Verify the install can actually start -----------------------------
# An installer that reports success and then dies on first run with
# ModuleNotFoundError has not installed anything. Import the entry point here,
# where the failure can still be explained and acted on.
Step "Verifying installation..."
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
Step "Creating launcher scripts..."

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
echo.
echo   TOMAS  -  updating from GitHub
echo.
powershell -ExecutionPolicy Bypass -c "iex (iwr -UseBasicParsing -Uri https://raw.githubusercontent.com/oleksandkov/Agent-For-TOM/main/install.ps1)"
if %ERRORLEVEL% neq 0 (
    echo.
    echo   Update failed - see the messages above. Your existing install is
    echo   untouched, so TOMAS still runs.
    pause
    exit /b %ERRORLEVEL%
)

rem -- Refresh PATH so `tomas` works immediately in this session --
set "PATH=%USERPROFILE%\.tomas\bin;%PATH%"
echo.
echo   Updated. Your settings, sessions and instructions were kept.
'@
[System.IO.File]::WriteAllText($upgradeBat, $upgradeContent, $Utf8NoBom)
Write-Host "  [OK] $upgradeBat" -ForegroundColor Green

# TOMAS-uninstall.cmd --- call uninstall.ps1
$uninstallBat = Join-Path $BinDir "TOMAS-uninstall.cmd"
$uninstallContent = @'
@echo off
echo.
echo   TOMAS  -  uninstall
echo.
echo   This removes the program, and with it your sessions, saved
echo   providers and agent instructions. There is no undo.
echo.
set /p "ok=  Type YES to remove TOMAS: "
if /I not "%ok%"=="YES" (
    echo   Cancelled - nothing was removed.
    exit /b 0
)
powershell -ExecutionPolicy Bypass -File "{UninstallPs1}"
if %ERRORLEVEL% neq 0 (
    echo   Uninstall may have failed. See the messages above.
    pause
)
'@ -replace '{UninstallPs1}', (Join-Path $BinDir "uninstall.ps1")
[System.IO.File]::WriteAllText($uninstallBat, $uninstallContent, $Utf8NoBom)
Write-Host "  [OK] $uninstallBat" -ForegroundColor Green

# -- Create default instructions and sessions dir --------------------------
Step "Setting up agent instructions..."

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
#
# Written by Python, not by this script.
#
# The template is Ukrainian, and a non-ASCII byte in a BOM-less .ps1 is fatal
# on Windows PowerShell: it reads the file in the machine's ANSI codepage, so
# on a cp1251 system the UTF-8 bytes of a Cyrillic letter (D0 92) decode to
# `R` + U+2019 -- and PowerShell treats U+2019 as a string delimiter. That
# opened a string which never closed, and the whole installer failed to parse
# with 22 errors before running a single line. install.cmd invokes
# `powershell` (5.1), so on any machine with a Cyrillic codepage -- which is
# every machine this template is written for -- the installer installed
# nothing at all.
#
# `instructions_manager.DEFAULT_AGENT_INSTRUCTIONS` already held this text, so
# the here-string was a second copy of it as well. One source, written through
# Python, which has no such encoding problem.
$agentInstrFile = Join-Path $InstructionsDir "AGENT.md"
if (-not (Test-Path $agentInstrFile)) {
    # Single quotes inside the Python, deliberately. PowerShell strips double
    # quotes when it hands an argument to a native command, so `encoding="utf-8"`
    # arrived at Python as `encoding=utf-8` and died with
    # `NameError: name 'utf' is not defined` -- after reporting the step as done,
    # because the failure was in the child process.
    $writeDefaults = @'
import sys, pathlib
sys.path.insert(0, sys.argv[1])
import instructions_manager as im
pathlib.Path(sys.argv[2]).write_text(
    im.DEFAULT_AGENT_INSTRUCTIONS, encoding='utf-8')
'@
    & $script:PythonExe -c $writeDefaults $SrcDir $agentInstrFile
    if (Test-Path $agentInstrFile) {
        StepOk "Default instructions: $agentInstrFile"
    } else {
        StepInfo "Could not write default instructions - edit $agentInstrFile by hand"
    }
} else {
    StepOk "Kept your existing $agentInstrFile"
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
Step "Configuring environment..."

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
    # handing back an empty template -- a missing .env on a machine that has run
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
Step "Finalizing setup..."
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
    Uninstall TOMAS Agent.

.NOTES
    Two defects this replaces, both observed on a real uninstall:

    1. It announced the removal before attempting it. "$tomasDir deleted" and
       "TOMAS is gone." were printed immediately after *launching* a background
       cleanup script, so the words were a prediction. The run that prompted
       this rewrite printed both, and left the directory in place.

    2. The background script could not terminate. It was

           :retry
           rmdir /s /q "<dir>"
           if not exist "<dir>" goto done
           ping ... & goto retry

       with no attempt limit. Windows will not delete a running executable, and
       a leftover TOMAS process was holding
       `.tomas\.venv\Scripts\python.exe` — so `rmdir` removed every unlocked
       file (all the user's sessions, providers and instructions) and the
       directory never disappeared. The loop then spun once a second forever in
       a console window that could not be closed, which is exactly what the
       user reported.

    So: stop the processes that cause it, act before reporting, bound the
    retry, and when something survives say which file and which process rather
    than claiming success.
#>
Write-Host ""
Write-Host "  TOMAS  -  removing" -ForegroundColor Cyan

$tomasDir = "{InstallDir}"
$binDir = Join-Path $tomasDir "bin"

# -- PATH first: it is the one step that always succeeds, and leaving a stale
#    entry behind is the failure the user notices months later.
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
$paths = $currentPath -split ';' | Where-Object { $_ -and $_ -ne $binDir }
[Environment]::SetEnvironmentVariable("Path", ($paths -join ';'), "User")
Write-Host "    PATH entry removed" -ForegroundColor DarkGray

# -- Stop what is holding the files open ---------------------------------
# The interpreter inside the install directory is the usual culprit, and it is
# also the only thing that makes the delete impossible rather than merely slow.
$running = @()
try {
    $running = @(Get-Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Path -and $_.Path.StartsWith($tomasDir, [StringComparison]::OrdinalIgnoreCase)
        })
} catch { }

if ($running.Count -gt 0) {
    Write-Host "    $($running.Count) TOMAS process(es) still running - stopping them" -ForegroundColor Yellow
    foreach ($proc in $running) {
        try {
            Stop-Process -Id $proc.Id -Force -ErrorAction Stop
            Write-Host "      stopped PID $($proc.Id)" -ForegroundColor DarkGray
        } catch {
            Write-Host "      could not stop PID $($proc.Id): $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
    Start-Sleep -Milliseconds 700
}

# -- Delete everything we can from here ----------------------------------
# `bin` is skipped: this script is running from it, so PowerShell holds it
# open. Everything else goes now, synchronously, so the result is known before
# anything is printed about it.
foreach ($child in (Get-ChildItem -LiteralPath $tomasDir -Force -ErrorAction SilentlyContinue)) {
    if ($child.FullName -ieq $binDir) { continue }
    try {
        Remove-Item -LiteralPath $child.FullName -Recurse -Force -ErrorAction Stop
    } catch {
        Write-Host "    still in use: $($child.Name)" -ForegroundColor Yellow
    }
}

# -- Hand the last of it to a bounded background pass ---------------------
# Bounded, hidden, and it leaves a note rather than a spinning window. The
# window is what made the old one impossible to ignore; the note is what makes
# this one possible to act on.
$cleanPs1 = Join-Path $env:TEMP "tomas-uninstall-clean.ps1"
$noteFile = Join-Path $env:TEMP "tomas-uninstall-incomplete.txt"
$cleanBody = @"
`$target = '{InstallDir}'
`$note   = '$noteFile'
Remove-Item -LiteralPath `$note -Force -ErrorAction SilentlyContinue
for (`$i = 0; `$i -lt 30; `$i++) {
    Start-Sleep -Milliseconds 500
    Remove-Item -LiteralPath `$target -Recurse -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath `$target)) { break }
}
if (Test-Path -LiteralPath `$target) {
    `$left = (Get-ChildItem -LiteralPath `$target -Recurse -Force -ErrorAction SilentlyContinue |
              Select-Object -First 20 -ExpandProperty FullName) -join [Environment]::NewLine
    @(
      'TOMAS could not be fully removed.'
      ''
      "Left behind in `$target :"
      `$left
      ''
      'Something still had these files open. Close any terminal running TOMAS,'
      'then delete the folder by hand:'
      "    Remove-Item -LiteralPath '`$target' -Recurse -Force"
    ) | Set-Content -LiteralPath `$note -Encoding UTF8
}
Remove-Item -LiteralPath `$MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
"@
[System.IO.File]::WriteAllText($cleanPs1, $cleanBody)

try {
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", $cleanPs1) `
        -WindowStyle Hidden | Out-Null
} catch {
    Write-Host "    background cleanup could not start: $($_.Exception.Message)" -ForegroundColor Yellow
}

# -- Say what actually happened ------------------------------------------
$leftover = @(Get-ChildItem -LiteralPath $tomasDir -Force -ErrorAction SilentlyContinue |
              Where-Object { $_.FullName -ine $binDir })
Write-Host ""
if ($leftover.Count -eq 0) {
    Write-Host "  TOMAS is gone." -ForegroundColor Green
    Write-Host "  A background pass removes the last folder in a few seconds." -ForegroundColor DarkGray
} else {
    Write-Host "  TOMAS is mostly removed." -ForegroundColor Yellow
    Write-Host "  Still present: $($leftover.Name -join ', ')" -ForegroundColor DarkGray
    Write-Host "  A background pass retries for 15s; if it cannot finish it writes" -ForegroundColor DarkGray
    Write-Host "    $noteFile" -ForegroundColor DarkGray
}
Write-Host "  Open a new terminal for the PATH change to take effect." -ForegroundColor DarkGray
Write-Host "  Reinstall any time with install.ps1 - nothing here blocks it." -ForegroundColor DarkGray
'@ -replace '{InstallDir}', $InstallDir

[System.IO.File]::WriteAllText($uninstallScript, $uninstallContent, $Utf8NoBom)
Write-Host "  [OK] Created uninstaller: $uninstallScript" -ForegroundColor Green

# -- Run setup to install default MCPs --
Step "Configuring default MCP servers..."
try {
    & $script:PythonExe "$SrcDir\agent_cli.py" setup
    Write-Host "  [OK] Default MCPs configured" -ForegroundColor Green
} catch {
    Write-Host "  [WARN] Setup MCPs skipped: $_" -ForegroundColor Yellow
    Write-Host "         Run 'TOMAS setup' later to configure default MCPs." -ForegroundColor Yellow
}

# -- Done --------------------------------------------------------------------
# Six lines, not thirty.
#
# The old summary listed six install paths, three commands, three "new
# features", two ways to fix PATH and the .env location -- everything the
# installer knew, at equal weight, so the one line that matters ("type TOMAS")
# sat in the middle of a wall the reader scrolls past. What a person needs on
# finishing an install is: did it work, what do I type, where does it live.
$rule = [string][char]0x2500 * 46
Write-Host ""
Write-Host "  $rule" -ForegroundColor DarkGray
Write-Host "  TOMAS is installed." -ForegroundColor Green
Write-Host ""
Write-Host "    Type " -ForegroundColor White -NoNewline
Write-Host "TOMAS" -ForegroundColor Cyan -NoNewline
Write-Host " in a new terminal to start." -ForegroundColor White
Write-Host "    $InstallDir" -ForegroundColor DarkGray
Write-Host ""
Write-Host "    TOMAS-upgrade" -ForegroundColor DarkGray -NoNewline
Write-Host "  update   " -ForegroundColor DarkGray -NoNewline
Write-Host "TOMAS-uninstall" -ForegroundColor DarkGray -NoNewline
Write-Host "  remove   " -ForegroundColor DarkGray -NoNewline
Write-Host "TOMAS browser" -ForegroundColor DarkGray -NoNewline
Write-Host "  web search" -ForegroundColor DarkGray
Write-Host "  $rule" -ForegroundColor DarkGray
Write-Host ""
if (-not $env:ANTHROPIC_API_KEY -and -not (Select-String -Path $EnvFile -Pattern '^\s*ANTHROPIC_API_KEY\s*=\s*\S' -Quiet -ErrorAction SilentlyContinue)) {
    # Only when there is genuinely nothing configured. TOMAS falls back to the
    # OpenCode Zen free tier on first run, so this is a note, not a blocker --
    # printing it unconditionally taught people to ignore it.
    Write-Host "  No API key yet -- TOMAS will start on the free tier." -ForegroundColor DarkGray
    Write-Host "  Connect your own provider from the menu, or edit $EnvFile" -ForegroundColor DarkGray
    Write-Host ""
}
