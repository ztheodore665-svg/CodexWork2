@echo off
rem ============================================================
rem  车路云协同管控平台 - 一键启动（前端 + 后端）
rem  编码：本文件为 ANSI/GBK（中文系统原生），无需 chcp
rem  后端：xiongan_v4\平台后端（.env 决定 LLM：智谱免费API / 本地Ollama）
rem  前端：本目录（Vue 3 + Vite，端口 5173，/api 与 /ws 自动代理到 8000）
rem ============================================================
set "FE=%~dp0"
set "BE=%~dp0..\平台后端"

title 车路云协同管控平台 - 一键启动

echo [1/4] 检查后端依赖...
python -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
    echo     缺少后端依赖，正在安装（首次约 1-2 分钟）...
    pip install -r "%BE%\requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
)
python -c "import openai" >nul 2>nul
if errorlevel 1 (
    echo     缺少 ML/AI 依赖（openai/onnx），正在安装...
    pip install openai onnx onnxruntime -i https://pypi.tuna.tsinghua.edu.cn/simple
)

echo [2/4] 检查前端依赖...
if not exist "%FE%node_modules" (
    echo     首次安装前端依赖（npm install）...
    pushd "%FE%"
    call npm install
    popd
)

echo [3/4] 启动后端（新窗口，端口 8000）...
pushd "%BE%"
start "车路云后端" cmd /k "python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
popd
timeout /t 6 /nobreak >nul

echo [4/4] 启动前端（新窗口，端口 5173）...
pushd "%FE%"
start "车路云前端" cmd /k "npm run dev"
popd
timeout /t 5 /nobreak >nul

echo.
echo 正在打开浏览器: http://localhost:5173
start http://localhost:5173
echo.
echo 完成！两个新窗口请保留（后端窗口显示 API 日志，前端窗口显示 Vite 地址）。
echo 关闭时：直接关掉这两个窗口即可。
pause


