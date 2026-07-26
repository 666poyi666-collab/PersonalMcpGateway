@echo off
rem One-click repair: elevates and runs Install-PoyiFleet.ps1 (idempotent).
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','%~dp0Install-PoyiFleet.ps1','-Pause'"
