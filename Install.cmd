@echo off
title Libre Terminal - Install
REM Double-click to install the terminal (first time, or to repair/update). Safe to run again.
REM Downloads Python 3.12 and the data packages into this folder; takes a few minutes the first time.
REM Options from a command prompt: Install.cmd --with-v4 (also install optional legacy V4 backend), Install.cmd --upgrade
if not exist "%~dp0scripts\install.ps1" (
  echo.
  echo You opened Install.cmd from inside the ZIP file. It cannot run from there.
  echo.
  echo   1. Close this window.
  echo   2. Right-click Libre-Terminal-Windows.zip and choose "Extract All...", then click Extract.
  echo   3. Open the extracted Libre-Terminal folder and double-click Install.cmd again.
  echo.
  pause
  exit /b 1
)
echo.
echo Installing Libre Terminal.
echo The first install downloads a few hundred MB and takes 2 to 5 minutes.
echo Keep this window open until it says "Install finished".
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
if errorlevel 1 (
  echo.
  echo The install did not finish. The reason is in the red or yellow lines above.
  echo Fix it, then double-click Install.cmd again: it carries on where it stopped.
  echo Help: https://github.com/samlazs/libre-terminal#updating-and-troubleshooting
  echo.
  pause
  exit /b 1
)
echo.
echo You can close this window now.
pause
