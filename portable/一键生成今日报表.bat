@echo off
chcp 65001 >nul
title 电商销售日报生成器 - 一键生成报表

echo ======================================================
echo           多平台电商销售日报生成器 (小白便携版)
echo ======================================================
echo.

:: 检测 Python 环境（优先父级虚拟环境，其次系统 python）
set PYTHON_EXE=
if exist "%~dp0..\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0..\.venv\Scripts\python.exe"
) else if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if %errorlevel% equ 0 (
        set "PYTHON_EXE=python"
    )
)

if "%PYTHON_EXE%"=="" (
    echo [错误] 未检测到 Python 环境！
    echo 请先安装 Python 3.11 或更高版本（安装时请勾选 "Add python.exe to PATH"）。
    echo 官方下载地址：https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

:: 检测核心依赖库是否就绪（缺失时自动尝试安装）
"%PYTHON_EXE%" -c "import fastapi, pandas, openpyxl, sqlalchemy" >nul 2>nul
if %errorlevel% neq 0 (
    echo [提示] 检测到当前 Python 环境缺少运行所需核心组件（pandas/fastapi 等）。
    echo 正在自动为您安装所需运行依赖，请保持网络畅通并稍候...
    echo.
    if exist "%~dp0core\requirements.txt" (
        "%PYTHON_EXE%" -m pip install -r "%~dp0core\requirements.txt"
    ) else if exist "%~dp0..\source\backend\requirements.lock" (
        "%PYTHON_EXE%" -m pip install -r "%~dp0..\source\backend\requirements.lock"
    ) else if exist "%~dp0..\source\backend\requirements.txt" (
        "%PYTHON_EXE%" -m pip install -r "%~dp0..\source\backend\requirements.txt"
    ) else (
        echo [错误] 未找到 requirements.txt 依赖清单文件，无法自动安装。
        pause
        exit /b 1
    )
    if %errorlevel% neq 0 (
        echo.
        echo [错误] 自动安装依赖失败，请检查网络连接或手动运行 pip install。
        pause
        exit /b 1
    )
    echo.
    echo [成功] 依赖组件安装完成！
    echo.
)

echo 正在处理【输入表格放这里】中的销售文件...
echo.

"%PYTHON_EXE%" "%~dp0core\runner.py" run

echo.
echo 处理完成！按任意键关闭窗口...
pause >nul
