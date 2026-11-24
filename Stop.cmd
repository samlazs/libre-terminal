@echo off
title Libre Terminal - Stop
REM Double-click to stop the data backends and the terminal UI.
if not exist "%~dp0scripts\launcher.py" (
  echo.
  echo You opened Stop.cmd from inside the ZIP file.
  echo Close this window, extract the ZIP first, and run files from the extracted folder.
  echo.
  pause
  exit /b 1
)
cd /d "%~dp0"
if not exist ".venv-v5\Scripts\python.exe" (
  echo Not installed - nothing to stop.
  pause
  exit /b 0
)
".venv-v5\Scripts\python.exe" scripts\launcher.py stop %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo Stop exited with code %RC%.
  pause
  exit /b %RC%
)
timeout /t 3 >nul 2>nul
exit /b 0
