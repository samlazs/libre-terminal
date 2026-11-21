@echo off
REM Double-click to install the terminal (first time, or to repair/update). Safe to run again.
REM Downloads Python 3.12 and the data packages into this folder; takes a few minutes the first time.
REM Options from a command prompt: Install.cmd --with-v4 (also install optional legacy V4 backend), Install.cmd --upgrade
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
if errorlevel 1 (
  echo.
  echo Install did not finish. Read the message above, fix it, and double-click Install.cmd again.
  pause
  exit /b 1
)
echo.
echo Done. Double-click Start.cmd to open the terminal.
pause
