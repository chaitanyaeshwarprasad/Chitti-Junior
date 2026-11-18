@echo off
title Chitti - allow in Windows Firewall
echo.
echo   Lets Chitti's board reach this PC over your home Wi-Fi (private networks only).
echo   Windows will ask for permission. Use at your own risk.
echo.
if not exist "%~dp0..\server\.venv\Scripts\python.exe" (
  echo   Run "Start Chitti Junior.bat" once first.
  pause & exit /b 1
)
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList '-NoProfile','-Command','Remove-NetFirewallRule -DisplayName ''Chitti server'' -ErrorAction SilentlyContinue; New-NetFirewallRule -DisplayName ''Chitti server'' -Direction Inbound -Action Allow -Profile Private,Domain -Program ''%~dp0..\server\.venv\Scripts\python.exe'' | Out-Null'"
if errorlevel 1 (echo   Not changed.) else (echo   Done. Chitti is allowed on private networks.)
echo.
pause
