@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0upgrade_app.ps1" -SourceDirectory "%~dp0."
if errorlevel 1 echo Upgrade failed. Keep using the existing installation and check the error above.
pause
