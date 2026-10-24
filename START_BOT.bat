@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Pure Poker Bot

if not exist ".venv\Scripts\python.exe" (
    echo Setup has not run yet. Double-click SETUP_WINDOWS.bat first.
    pause
    exit /b 1
)

echo Starting in dry-run mode by default. Press Ctrl+C to stop.
echo For live mode after calibration, use: START_BOT.bat --live
echo.
".venv\Scripts\python.exe" bot.py %*
set "BOT_EXIT=%ERRORLEVEL%"
echo.
echo Bot stopped with exit code %BOT_EXIT%.
pause
exit /b %BOT_EXIT%
