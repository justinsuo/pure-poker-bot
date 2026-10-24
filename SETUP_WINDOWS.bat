@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Pure Poker Bot Setup

echo === Pure Poker Bot - automatic setup ===
echo.
set "PY="
py -3 -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    python -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY goto python_error

%PY% -c "import sys; print('Using Python', sys.version.split()[0])"
if errorlevel 1 goto python_error

if not exist ".venv\Scripts\python.exe" (
    echo Creating isolated Python environment...
    %PY% -m venv .venv
    if errorlevel 1 goto error
)

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error

echo Running offline test suite...
".venv\Scripts\python.exe" -m unittest discover -s tests -v
if errorlevel 1 goto error

echo Running offline diagnostics...
".venv\Scripts\python.exe" tools\doctor.py --offline
if errorlevel 1 goto error

echo.
echo === Setup complete ===
echo Next: install/start ADB and Ollama, then follow README.md sections 3-6.
echo Run ".venv\Scripts\python.exe tools\doctor.py" for connection checks.
pause
exit /b 0

:python_error
echo.
echo Python 3.10+ was not found. Install it from https://www.python.org/downloads/
echo During installation, enable "Add Python to PATH".
goto error

:error
echo.
echo Setup did not complete. Read the error above and try again.
pause
exit /b 1
