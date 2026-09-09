@echo off
chcp 65001 >nul
title 车路云协同管控平台 - 一键启动
cd /d "%~dp0"

REM 校验 SUMO_HOME（缺失时从 .env 读取）
if "%SUMO_HOME%"=="" (
    for /f "delims=" %%i in ('python -c "from app.config import Settings; print(Settings().sumo_home)"') do set "SUMO_HOME=%%i"
)
if "%SUMO_HOME%"=="" (
    echo [警告] 未检测到 SUMO_HOME，请在 .env 中配置 SUMO_HOME。
)

echo 启动后端服务: http://127.0.0.1:8000  （Ctrl+C 停止）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

pause
