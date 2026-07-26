@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-interactive.ps1"
if errorlevel 1 (
  echo Installation failed. Review the administrator window for details.
  pause
  exit /b 1
)
echo Personal MCP Gateway installation completed.
pause
