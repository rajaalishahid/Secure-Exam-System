@echo off
setlocal
cd /d "%~dp0"
title Secure Exam System - Live HTTPS Hotspot
echo Starting the live HTTPS hotspot deployment.
echo Enable Windows Mobile Hotspot first. Administrator approval is needed only if creating the scoped firewall rule.
echo Caddy must be installed on PATH or placed beside this project as caddy.exe.
echo.
call "%~dp0Start-Hotspot.cmd"
endlocal
