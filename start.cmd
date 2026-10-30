@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -X utf8 yuketang_export.py wizard
) else (
  py -3.11 -X utf8 yuketang_export.py wizard
)
pause
