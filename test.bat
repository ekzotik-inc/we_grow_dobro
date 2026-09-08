@echo off
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
title We Grow Dobro - self test

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   [ERROR] Environment is not set up yet. Run run.bat first.
    echo.
    pause
    exit /b 1
)
set "VPY=.venv\Scripts\python.exe"
"%VPY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
"%VPY%" -m scripts.smoke_test
pause
