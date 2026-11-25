@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
.venv\Scripts\python.exe configure.py
.venv\Scripts\python.exe manage.py migrate
if errorlevel 1 goto failed
.venv\Scripts\python.exe desktop_launcher.py
exit /b
:failed
 echo Setup failed. Read the error above.
 pause