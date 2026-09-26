@echo off
REM Simple launcher for developers who prefer a repo-local .venv over pipx.
REM Put this file (or the folder containing it) on PATH.
REM
REM Usage: lily [subcommand] [args...]
setlocal
set "REPO=%~dp0.."
if exist "%REPO%\.venv\Scripts\lily.exe" (
    "%REPO%\.venv\Scripts\lily.exe" %*
    exit /b %ERRORLEVEL%
)
if exist "%REPO%\.venv\Scripts\python.exe" (
    "%REPO%\.venv\Scripts\python.exe" -m lily %*
    exit /b %ERRORLEVEL%
)
echo Lily is not installed. Run: pip install -e ".[dev]"  (from %REPO%)
exit /b 1
