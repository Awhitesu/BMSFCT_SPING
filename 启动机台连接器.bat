@echo off
chcp 65001 >nul
title 机台互联连接器 - Machine Connector

:: 检查 Python 解释器
set PYTHON_EXE=C:\Users\95403\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PYTHON_EXE%" (
    set PYTHON_EXE=python
)

:: 启动连接器
echo 正在启动机台互联连接器...
start "" "%PYTHON_EXE%" "%~dp0tools\machine_connector.py"
exit
