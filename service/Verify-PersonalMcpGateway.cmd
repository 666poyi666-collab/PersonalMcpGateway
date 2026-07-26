@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0verify-windows-interactive.ps1"
if errorlevel 1 (
  echo Verification failed. Review evidence\windows-verification-result.json.
  pause
  exit /b 1
)
echo Personal MCP Gateway verification completed.
pause
