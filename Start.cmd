@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "melody_applicator.py" %*
) else (
  python "melody_applicator.py" %*
)
if errorlevel 1 (
  echo.
  echo See README.md for Python and dependency setup.
  pause
)
