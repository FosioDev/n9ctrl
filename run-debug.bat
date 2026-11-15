@echo off
rem Runs the app with a console window so errors are visible.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run run.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" app.py
pause
