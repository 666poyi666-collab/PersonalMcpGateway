@echo off
REM Removes the user-scoped install. Add -Purge to drop the saved window layout.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall-desktop.ps1" %*
if errorlevel 1 (
  echo Removal failed. Review the messages above.
  pause
  exit /b 1
)
echo Poyi Control Center removal completed.
pause
