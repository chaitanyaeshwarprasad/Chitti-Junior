@echo off
title Chitti Junior - Wi-Fi setup
echo.
echo   Gives Chitti Junior your Wi-Fi name and password through the USB cable.
echo   1. Plug the ESP32's port marked USB (not COM) into this PC.
echo   2. Type the Wi-Fi name and password below. It must be 2.4 GHz Wi-Fi,
echo      the SAME network this PC is on.
echo   The password only goes to the board through the cable.
echo.
if not exist "%~dp0..\server\.venv\Scripts\python.exe" (
  echo   Run "Start Chitti Junior.bat" once first.
  pause & exit /b 1
)
"%~dp0..\server\.venv\Scripts\python.exe" "%~dp0..\server\set_wifi.py"
echo.
pause
