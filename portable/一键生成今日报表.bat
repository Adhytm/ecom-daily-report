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
    echo 请先安装 Python 3.11 或更高版本，或者联系管理员配置环境。
    echo.
    pause
    exit /b 1
)

echo 正在处理【输入表格放这里】中的销售文件...
echo.

"%PYTHON_EXE%" "%~dp0core\runner.py" run

echo.
echo 处理完成！按任意键关闭窗口...
pause >nul
