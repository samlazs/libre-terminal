@echo off
title Libre Terminal - Setup Keys
REM Double-click to add optional free API keys (FRED, BLS, EIA, Congress.gov, FMP, ...). The terminal works without any.
REM Opens local\api_keys.env in Notepad, then applies the keys. Setup-Keys.cmd --no-edit only applies the file.
if not exist "%~dp0scripts\launcher.py" (
  echo.
  echo You opened Setup-Keys.cmd from inside the ZIP file. It cannot run from there.
  echo.
  echo   1. Close this window.
  echo   2. Right-click Libre-Terminal-Windows.zip and choose "Extract All...", then click Extract.
  echo   3. Open the extracted Libre-Terminal folder and double-click Install.cmd first.
  echo.
  pause
  exit /b 1
)
cd /d "%~dp0"
if not exist ".venv-v5\Scripts\python.exe" (
  echo Libre Terminal is not installed yet. Double-click Install.cmd first.
  pause
  exit /b 1
)
".venv-v5\Scripts\python.exe" scripts\launcher.py keys %*
pause
