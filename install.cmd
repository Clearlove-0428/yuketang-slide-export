@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.11 -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m playwright install chromium --only-shell
if errorlevel 1 goto fail
echo Ready. Double-click start.cmd.
pause
exit /b 0
:fail
echo Setup failed. Check the messages above.
pause
exit /b 1
