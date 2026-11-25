@echo off
setlocal
cd /d "%~dp0"
echo This legacy network launcher has been retired because it exposed Django's development server over plain HTTP.
echo Use Start-Hotspot.cmd for the supported HTTPS hotspot setup, or Start-Local.cmd for local-only development.
echo.
call "%~dp0Start-Hotspot.cmd"
endlocal
