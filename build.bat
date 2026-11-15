@echo off
rem Builds dist\N9Ctrl.exe with PyInstaller. Run run.bat once before (creates .venv).
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run run.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt || goto fail
".venv\Scripts\python.exe" tools\make_icon.py || goto fail
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name N9Ctrl --icon build\n9ctrl.ico ^
  --add-data "ui;ui" ^
  --collect-submodules webview --hidden-import clr --hidden-import pystray._win32 ^
  app.py || goto fail
echo.
echo  Done: dist\N9Ctrl.exe
pause
exit /b 0
:fail
echo  Build failed. See the messages above.
pause
exit /b 1
