@echo off
title Libre Terminal - Setup Keys
REM Double-click to add optional free API keys (FRED, BLS, EIA, Congress.gov, FMP, ...). The terminal works without any.
REM Opens local\api_keys.env in Notepad, then applies the keys. Setup-Keys.cmd --no-edit only applies the file.
if not exist "%~dp0scripts\launcher.py" (
  echo.
  echo You opened Setup-Keys.cmd from inside the ZIP file.
  echo Close this window, right-click the ZIP file, click "Extract All...",
  echo open the extracted Libre-Terminal folder, and double-click Install.cmd first.
  echo.
  pause
  exit /b 1
)
cd /d "%~dp0"
if not exist ".venv-v5\Scripts\python.exe" (
  echo Not installed yet - double-click Install.cmd first.
  pause
  exit /b 1
)
".venv-v5\Scripts\python.exe" scripts\launcher.py keys %*
pause
