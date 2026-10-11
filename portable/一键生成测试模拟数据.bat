@echo off
chcp 65001 >nul
title 电商销售日报生成器 - 生成演示数据

echo ======================================================
echo           多平台电商销售日报生成器 - 体验演示数据
echo ======================================================
echo.

:: 检测 Python 环境
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
    pause
    exit /b 1
)

echo 正在生成 7 天真实格式的模拟导出文件（包含淘宝、抖店、拼多多、京东）...
echo.

"%PYTHON_EXE%" "%~dp0core\runner.py" mock --days 7 --skus 20

echo.
echo 生成完毕！现在您可以双击【一键生成今日报表.bat】查看生成效果。
pause
