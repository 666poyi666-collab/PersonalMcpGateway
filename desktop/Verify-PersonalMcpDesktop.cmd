@echo off
REM Runs unelevated, like the install it checks. The board window opens briefly
REM unless an instance is already running, in which case that one is inspected.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0verify-desktop.ps1" %*
if errorlevel 1 (
  echo Verification failed. Review evidence\desktop-verification-result.json.
  pause
  exit /b 1
)
echo Poyi Control Center verification completed.
pause
