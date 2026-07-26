@echo off
REM TOMAS Agent Installer — CMD wrapper
REM
REM This batch file launches the PowerShell installer (install.ps1).
REM It works both when running from the repo and from a piped remote install.
REM
REM Usage:
REM   install.cmd              Install from local source (repo directory)
REM   install.cmd --remote     Install from GitHub (remote mode)

echo.
echo   ==========================================
echo       TOMAS Agent Installer
echo   ==========================================
echo.

if /i "%1"=="--remote" (
    echo   Installing from GitHub...
    powershell -ExecutionPolicy Bypass -c "iex (iwr -UseBasicParsing -Uri https://raw.githubusercontent.com/oleksandkov/Agent-For-TOM/prototype2-refactoring/install.ps1)"
) else (
    if not exist "%~dp0install.ps1" (
        echo ERROR: install.ps1 not found alongside install.cmd.
        echo.
        echo   Expected: %~dp0install.ps1
        echo.
        echo   To install from GitHub, run:
        echo     install.cmd --remote
        echo.
        pause
        exit /b 1
    )
    echo   Installing from local source: %~dp0
    powershell -ExecutionPolicy Bypass -File "%~dp0install.ps1"
)

if %ERRORLEVEL% neq 0 (
    echo.
    echo   Installation failed. See messages above.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo   Installation complete.
echo   Close and reopen your terminal, then run: TOMAS
echo.
pause
