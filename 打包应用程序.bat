@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Project Python missing: .venv\Scripts\python.exe
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 "tools\package_app.py" --open %*
set "BUILD_RESULT=%ERRORLEVEL%"
if not "%BUILD_RESULT%"=="0" echo Build failed. See logs\packaging.
pause
exit /b %BUILD_RESULT%
