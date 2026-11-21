@echo off
REM Double-click to add optional free API keys (FRED, BLS, EIA, Congress.gov, FMP, ...). The terminal works without any.
REM Opens local\api_keys.env in Notepad, then applies the keys. Setup-Keys.cmd --no-edit only applies the file.
cd /d "%~dp0"
if not exist ".venv-v5\Scripts\python.exe" (
  echo Not installed yet - double-click Install.cmd first.
  pause
  exit /b 1
)
".venv-v5\Scripts\python.exe" scripts\launcher.py keys %*
pause
