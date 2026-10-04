@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 电商销售日报与分析工具 - 一键启动

echo ========================================================
echo      多平台电商销售分析工具 - 本地安全版
echo ========================================================
echo.
echo [1/3] 正在检查运行环境...

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo.
    echo [错误] 未检测到 Python 虚拟环境: .venv\Scripts\python.exe
    echo 请确认是否已正确安装并初始化运行环境！
    echo.
    pause
    exit /b 1
)

echo [2/3] 正在启动后台分析引擎 (端口 8000)...

rem 检查 8000 端口是否已在运行
curl.exe -s -m 1 http://127.0.0.1:8000/api/health >nul 2>nul
if %errorlevel% equ 0 (
    echo [提示] 后台分析引擎已在运行中，无需重复启动。
) else (
    start "电商日报引擎后台" /min /D "%~dp0" "%~dp0.venv\Scripts\python.exe" -m uvicorn backend.app.main:app --port 8000
    
    echo 正在等待后台服务就绪...
    rem 循环检测服务健康状态，最多等待 15 秒（每次通过 ping 等待 1 秒）
    setlocal enabledelayedexpansion
    set "READY=0"
    for /l %%i in (1, 1, 15) do (
        if !READY! equ 0 (
            ping 127.0.0.1 -n 2 >nul
            curl.exe -s -m 1 http://127.0.0.1:8000/api/health >nul 2>nul
            if !errorlevel! equ 0 (
                set "READY=1"
            )
        )
    )
)

echo [3/3] 正在唤起浏览器界面...
start http://localhost:8000

echo.
echo ========================================================
echo   服务已成功启动！
echo   访问地址: http://localhost:8000
echo   所有数据均保存在本地 data 目录，不联网、绝不外泄。
echo.
echo   [提示] 关闭此窗口不会影响后台运行；
echo   如需彻底关闭服务，请在任务管理器中结束 python 进程。
echo ========================================================
echo.
pause