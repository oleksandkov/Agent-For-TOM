@echo off
REM Try system-wide install first, then local .venv
if exist "%USERPROFILE%\.tomas\.venv\Scripts\python.exe" (
    "%USERPROFILE%\.tomas\.venv\Scripts\python.exe" "%~dp0agent_cli.py" %*
    exit /b %ERRORLEVEL%
)
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0agent_cli.py" %*
    exit /b %ERRORLEVEL%
)
echo ERROR: TOMAS virtual environment not found.
echo.
echo   Checked:
echo     - %USERPROFILE%\.tomas\.venv\Scripts\python.exe  (system install)
echo     - %~dp0.venv\Scripts\python.exe                  (local dev)
echo.
echo   Install with:
echo     powershell -ExecutionPolicy Bypass -File install.ps1
pause
exit /b 1
