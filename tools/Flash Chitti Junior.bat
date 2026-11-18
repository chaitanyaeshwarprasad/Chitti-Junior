@echo off
title Chitti Junior - install software (manual)
setlocal
cd /d "%~dp0.."
set PY=server\.venv\Scripts\python.exe
set FW=firmware\prebuilt
if not exist "%PY%" (
  echo   Run "Start Chitti Junior.bat" once first.
  pause & exit /b 1
)
for %%f in (bootloader.bin partition-table.bin chitti_junior.bin) do if not exist "%FW%\%%f" (
  echo   Missing %FW%\%%f - download the full project again.
  pause & exit /b 1
)
echo.
echo   Writes Chitti Junior's ready-made software to the ESP32-S3 N16R8.
echo   Easiest: plug in the ESP32's port marked COM. Close Arduino IDE / serial monitors.
echo   (The dashboard's setup wizard does the same with a progress bar.)
echo.
echo   USB ports found:
"%PY%" -c "from serial.tools import list_ports; ps=list(list_ports.comports()); [print('    ', p.device, '-', p.description) for p in ps] or print('     none - check the cable')"
echo.
set PORT=
set /p PORT=  Type the port (e.g. COM8), or press Enter to find it automatically:
set PORTARG=
if not "%PORT%"=="" set "PORTARG=--port %PORT%"
echo.
echo   Writing Chitti Junior - about 1 minute...
"%PY%" -m esptool --chip esp32s3 %PORTARG% -b 921600 --before default_reset --after hard_reset write_flash --flash_mode dio --flash_freq 80m --flash_size 16MB 0x0 %FW%\bootloader.bin 0x8000 %FW%\partition-table.bin 0x10000 %FW%\chitti_junior.bin || goto failed
echo.
echo   Done! Junior restarts. First start: screen test - hold BOOT 2 s if the text looks right.
pause
exit /b 0

:failed
echo.
echo   Didn't work. Try the other USB-C port (COM), another data cable, or:
echo   hold BOOT, tap RST, let go of BOOT, then run this file again.
pause
exit /b 1
