@echo off
title Libre Terminal - Stop
REM Double-click to stop the data backends and the terminal UI.
if not exist "%~dp0scripts\launcher.py" (
  echo.
  echo You opened Stop.cmd from inside the ZIP file. It cannot run from there.
  echo Close this window and double-click Stop.cmd in the extracted Libre-Terminal folder.
  echo.
  pause
  exit /b 1
)
cd /d "%~dp0"
if not exist ".venv-v5\Scripts\python.exe" (
  echo Libre Terminal is not installed, so there is nothing to stop.
  pause
  exit /b 0
)
".venv-v5\Scripts\python.exe" scripts\launcher.py stop %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo Stop did not finish ^(exit code %RC%^). The reason is in the lines above.
  echo Restarting the computer also stops the terminal.
  pause
  exit /b %RC%
)
timeout /t 3 >nul 2>nul
exit /b 0
