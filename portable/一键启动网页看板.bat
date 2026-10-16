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
    echo 请先安装 Python 3.11 或更高版本（安装时请勾选 "Add python.exe to PATH"）。
    echo 官方下载地址：https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

:: 检测核心依赖库是否就绪（含 uvicorn 网页服务，缺失时自动尝试安装）
"%PYTHON_EXE%" -c "import fastapi, pandas, openpyxl, sqlalchemy, uvicorn" >nul 2>nul
if %errorlevel% neq 0 (
    echo [提示] 检测到当前 Python 环境缺少运行所需核心组件（fastapi/uvicorn 等）。
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

echo 正在启动可视化看板并打开浏览器...
echo 提示：如需退出，请直接关闭本黑框窗口。
echo.

"%PYTHON_EXE%" "%~dp0core\runner.py" web --port 8000
pause
