@echo off
title Chitti Junior - by Chaitanya Eshwar Prasad
setlocal
cd /d "%~dp0"
if exist "server\.venv\chitti_installed.txt" goto start

rem First run: check this PC and install everything Chitti needs (see server\install.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "server\install.ps1"
if errorlevel 1 (
  echo.
  echo   Chitti isn't installed yet. Fix the problem above, then double-click "Start Chitti Junior.bat" again.
  echo   Help: README.md - Troubleshooting
  pause
  exit /b 1
)

:start
cls
echo.
echo   --------------------------------------------------------------
echo     CHITTI JUNIOR is running.  KEEP THIS WINDOW OPEN while you use her.
echo     Close it to stop Junior.   Dashboard: http://localhost:8768
echo     By Chaitanya Eshwar Prasad  -  chaitanyaeshwarprasad.com
echo     Use at your own risk; the creator is not responsible.
echo   --------------------------------------------------------------
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "server\start_chitti.ps1"
echo.
echo   Chitti stopped.
pause
exit /b 0
