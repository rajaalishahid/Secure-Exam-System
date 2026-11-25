@echo off
setlocal
cd /d "%~dp0"
echo This legacy HTTP launcher has been retired.
echo Start the supported HTTPS hotspot service with Start-Hotspot.cmd.
echo For local-only development, use Start-Local.cmd.
pause
endlocal
