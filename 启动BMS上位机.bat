@echo off
chcp 65001 >nul
title BMS 老化测试上位机

:: 锁定项目专用 Python 3.11（含 PySide6 等全部依赖）
set PYTHON_EXE=C:\Users\95403\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PYTHON_EXE%" (
    echo [错误] 未找到项目 Python 解释器:
    echo %PYTHON_EXE%
    pause
    exit /b 1
)

cd /d "%~dp0"
echo 正在启动 BMS 老化测试上位机...
"%PYTHON_EXE%" "%~dp0main.py"
if errorlevel 1 pause
