@echo off
REM Double-click to start the V5 data backend and the terminal UI, then open the terminal in your browser.
REM Options from a command prompt: Start.cmd --no-browser, Start.cmd --no-wait, Start.cmd --with-v4, Start.cmd --ui-port 7950 --v5-port 7900
cd /d "%~dp0"
if not exist ".venv-v5\Scripts\python.exe" (
  echo Not installed yet - double-click Install.cmd first.
  pause
  exit /b 1
)
".venv-v5\Scripts\python.exe" scripts\launcher.py start %*
if errorlevel 1 (
  echo.
  echo Something did not start. Read the message above; logs are in local\logs.
  pause
  exit /b 1
)
timeout /t 5 >nul 2>nul
exit /b 0
