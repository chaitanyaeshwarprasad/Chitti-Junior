@echo off
rem Build (and optionally flash) Chitti Junior's firmware.  Usage: build.bat [flash] [monitor]
rem Needs ESP-IDF v5.5. Found via IDF_PATH, firmware\idf_path.txt, %USERPROFILE%\esp\esp-idf or C:\Espressif.
setlocal
cd /d "%~dp0"
if not defined IDF_PATH if exist idf_path.txt set /p IDF_PATH=<idf_path.txt
if not defined IDF_PATH if exist "%USERPROFILE%\esp\esp-idf\export.bat" set "IDF_PATH=%USERPROFILE%\esp\esp-idf"
if not defined IDF_PATH if exist "C:\Espressif\frameworks\esp-idf-v5.5.6\export.bat" set "IDF_PATH=C:\Espressif\frameworks\esp-idf-v5.5.6"
if not exist "%IDF_PATH%\export.bat" (
  echo   ESP-IDF not found. Install ESP-IDF v5.5 and put its folder path in firmware\idf_path.txt
  exit /b 1
)
if not defined IDF_TOOLS_PATH if exist "%IDF_PATH%\..\tools" set "IDF_TOOLS_PATH=%IDF_PATH%\..\tools"
if not defined IDF_PYTHON_ENV_PATH for /d %%d in ("%IDF_TOOLS_PATH%\python_env\idf5.5_py*_env") do set "IDF_PYTHON_ENV_PATH=%%~fd"
if defined IDF_PYTHON_ENV_PATH set "PATH=%IDF_PYTHON_ENV_PATH%\Scripts;%PATH%"
call "%IDF_PATH%\export.bat" >nul || exit /b 1
where idf.py >nul 2>&1 || (echo   ESP-IDF did not load. & exit /b 1)
if not exist sdkconfig idf.py set-target esp32s3 || exit /b 1

set PORTARG=
if defined CHITTI_PORT set "PORTARG=-p %CHITTI_PORT%"
idf.py %PORTARG% build %* || exit /b 1
