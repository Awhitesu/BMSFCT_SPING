@echo off
setlocal
pushd "%~dp0"
set "app_python="
if exist ".venv\pyvenv.cfg" (
    for /f "tokens=1,* delims==" %%A in (.venv\pyvenv.cfg) do (
        if "%%A"=="home " for /f "tokens=*" %%C in ("%%B") do set "app_python=%%C\python.exe"
    )
)
if not defined app_python set "app_python=python"
"%app_python%" "launch_app.py"
set "app_exit=%errorlevel%"
popd
exit /b %app_exit%
