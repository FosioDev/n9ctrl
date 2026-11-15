@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" goto run

echo.
echo  N9Ctrl - first run setup (takes 1-2 minutes)...
echo.
set "PY="
rem pythonnet (used by pywebview) supports Python 3.8 to 3.13
for %%V in (3.13 3.12 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; assert (3,8) <= sys.version_info[:2] <= (3,13)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo  Python 3.10 - 3.13 not found.
  echo  Install Python 3.13 from https://www.python.org/downloads/ and run this file again.
  pause
  exit /b 1
)
echo  Using: %PY%
%PY% -m venv .venv
if errorlevel 1 (
  echo  Could not create the virtual environment.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo  Could not install the dependencies. See the message above.
  rmdir /s /q .venv
  pause
  exit /b 1
)

:run
start "" ".venv\Scripts\pythonw.exe" "%~dp0app.py" %*
