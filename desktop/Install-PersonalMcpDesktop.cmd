@echo off
REM User-scoped install. Do NOT run this as administrator: the board installs
REM into the profile of whoever runs it, and it needs no elevated rights.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-desktop.ps1" %*
if errorlevel 1 (
  echo Installation failed. Review the messages above.
  pause
  exit /b 1
)
echo Poyi Control Center installation completed.
pause
