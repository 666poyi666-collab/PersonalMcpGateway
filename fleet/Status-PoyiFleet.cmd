@echo off
rem One-click fleet status (no elevation).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0status.ps1"
pause
