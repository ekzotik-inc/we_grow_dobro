@echo off
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
title We Grow Dobro - marathon bot

rem This file stays ASCII-only on purpose: cmd.exe is unreliable with UTF-8 batch files.
rem All Russian messages are printed by scripts\start.py instead.

rem --- 1. Locate Python (py launcher first, then PATH) ---
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo.
    echo   [ERROR] Python not found / Python ne naiden.
    echo.
    echo   Install Python 3.11+ from https://www.python.org/downloads/
    echo   IMPORTANT: tick "Add python.exe to PATH" during setup.
    echo   Then close this window and run run.bat again.
    echo.
    pause
    exit /b 1
)

rem --- 2. Virtual environment ---
if not exist ".venv\Scripts\python.exe" (
    echo   [1/2] Creating virtual environment, this takes about a minute...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo   [ERROR] Could not create .venv
        pause
        exit /b 1
    )
)
set "VPY=.venv\Scripts\python.exe"

rem --- 3. Dependencies ---
echo   [2/2] Checking dependencies...
"%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
"%VPY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo   [ERROR] Could not install dependencies. Check your internet connection.
    pause
    exit /b 1
)

rem --- 4. Hand over: .env checks and the bot itself ---
"%VPY%" scripts\start.py
pause
