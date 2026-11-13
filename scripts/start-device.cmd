@echo off
rem Copyright (c) 2026 Marco Parenzan. MIT License.
rem Delegate checks to the PowerShell launcher, including JSON parsing and dry-run.
setlocal
if "%~1"=="" exit /b 2
if "%~2"=="--stop" (
  powershell -NoProfile -File "%~dp0Start-Device.ps1" "%~1" -Stop
) else if "%~2"=="--dry-run" (
  powershell -NoProfile -File "%~dp0Start-Device.ps1" "%~1" -DryRun
) else if "%~2"=="" (
  powershell -NoProfile -File "%~dp0Start-Device.ps1" "%~1"
) else if "%~3"=="--dry-run" (
  powershell -NoProfile -File "%~dp0Start-Device.ps1" "%~1" -Seconds "%~2" -DryRun
) else (
  powershell -NoProfile -File "%~dp0Start-Device.ps1" "%~1" -Seconds "%~2"
)
exit /b %ERRORLEVEL%
