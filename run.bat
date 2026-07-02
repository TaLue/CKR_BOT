@echo off
REM Launch CKR Farm Bot using the project venv (has cv2/adbutils; system Python may not).
REM Double-click to open the Control Panel, or:  run.bat farm   /   run.bat record --name x
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] venv not found. Create it first:
  echo    py -3.12 -m venv .venv
  echo    .venv\Scripts\python.exe -m pip install -e ".[dev]"
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m ckrbot %*
