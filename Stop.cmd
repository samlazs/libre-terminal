@echo off
REM Double-click to stop the data backends and the terminal UI.
cd /d "%~dp0"
if not exist ".venv-v5\Scripts\python.exe" (
  echo Not installed - nothing to stop.
  pause
  exit /b 0
)
".venv-v5\Scripts\python.exe" scripts\launcher.py stop %*
timeout /t 5 >nul 2>nul
exit /b 0
