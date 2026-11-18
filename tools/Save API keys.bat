@echo off
title Chitti - save API keys
echo.
echo   Saves YOUR Anthropic key (required) and Sarvam key (optional) on this PC only.
echo   Never share these keys in chats, screenshots, videos or GitHub.
echo   You can also paste them later in the dashboard: Settings - API keys.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\server\set_api_key.ps1"
echo.
pause
