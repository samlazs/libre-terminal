@echo off
title Libre Terminal
REM Double-click to start the V5 data backend and the terminal UI, then open the terminal in your browser.
REM Options from a command prompt: Start.cmd --no-browser, Start.cmd --no-wait, Start.cmd --with-v4, Start.cmd --ui-port 7950 --v5-port 7900
if not exist "%~dp0scripts\launcher.py" (
  echo.
  echo You opened Start.cmd from inside the ZIP file. It cannot run from there.
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
  echo Something did not start. The reason is in the lines above.
  echo Logs are in %~dp0local\logs
  echo Help: https://github.com/samlazs/libre-terminal#updating-and-troubleshooting
  echo.
  pause
  exit /b 1
)
echo.
echo You can close this window. The terminal keeps running in the background
echo until you double-click Stop.cmd or restart the computer.
timeout /t 15 >nul 2>nul
exit /b 0
