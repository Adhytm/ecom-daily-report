@echo off
chcp 65001 >nul
title 电商销售日报生成器 - 网页看板服务

echo ======================================================
echo           多平台电商销售日报生成器 - 网页看板
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
    echo 请先安装 Python 3.11 或更高版本。
    echo.
    pause
    exit /b 1
)

echo 正在启动可视化看板并打开浏览器...
echo 提示：如需退出，请直接关闭本黑框窗口。
echo.

"%PYTHON_EXE%" "%~dp0core\runner.py" web --port 8000
pause
