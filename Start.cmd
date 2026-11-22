@echo off
title Libre Terminal
REM Double-click to start the V5 data backend and the terminal UI, then open the terminal in your browser.
REM Options from a command prompt: Start.cmd --no-browser, Start.cmd --no-wait, Start.cmd --with-v4, Start.cmd --ui-port 7950 --v5-port 7900
if not exist "%~dp0scripts\launcher.py" (
  echo.
  echo You opened Start.cmd from inside the ZIP file.
  echo Close this window, right-click the ZIP file, click "Extract All...",
  echo open the extracted Libre-Terminal folder, and double-click Install.cmd first.
  echo.
  pause
  exit /b 1
)
cd /d "%~dp0"
if not exist ".venv-v5\Scripts\python.exe" (
  echo.
  echo Libre Terminal is not installed yet.
  echo Double-click Install.cmd first, then double-click Start.cmd.
  echo.
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
