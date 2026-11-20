@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "ROOT=%~dp0."
set "PY=%~dp0.venv\Scripts\python.exe"
set "MODE=%~1"
if "%MODE%"=="" set "MODE=all"

echo.
echo  旅忆地图 TravelMemory - 开发启动器
echo  ------------------------------------------------
echo  用法：start-dev.bat [all^|main^|apps^|check]
echo    all    后端 8000 + 主应用 5173 + 轨迹演示 5174 + 分享旅程 5175（默认）
echo    main   只起后端 + 主应用（地图 / 问答）
echo    apps   只起两个独立小应用（演示双入口的"单独打开"）
echo    check  只体检，不启动任何服务
echo.

if /i "%MODE%"=="check" goto check
if /i "%MODE%"=="main" goto main
if /i "%MODE%"=="apps" goto apps
if /i "%MODE%"=="all" goto all

echo [错误] 不认识的参数：%MODE%
echo        可用值：all / main / apps / check
pause
exit /b 2

:check
call :preflight
echo.
echo [端口]
call :report_port 8000 "后端"
call :report_port 5173 "主应用"
call :report_port 5174 "轨迹演示"
call :report_port 5175 "分享旅程"
echo.
echo [提示] 8000/5173/5174/5175 各自独立是**故意的**：
echo        不同端口 = 不同 origin，iframe 里的子应用读不到主应用的 localStorage。
echo        把三个前端合并到一个端口，双入口的隔离承诺就没了。
exit /b 0

:all
call :preflight || exit /b 1
call :start_backend
call :start_port 5173 "TM main :5173" "npm run dev -w travel-memory-frontend"
call :start_port 5174 "TM track :5174" "npm run dev -w @travelmemory/track-studio"
call :start_port 5175 "TM share :5175" "npm run dev -w @travelmemory/trailshare-standalone"
echo.
echo 已尝试启动。页面会在端口就绪后**各自**打开：
echo   主应用    http://localhost:5173
echo   轨迹演示  http://localhost:5174
echo   分享旅程  http://localhost:5175
echo 主应用里的「轨迹演示 / 分享旅程」会去连 5174 / 5175，所以两个小应用也要在跑。
call :open_when_ready 5173 "http://localhost:5173"
call :open_when_ready 5174 "http://localhost:5174/"
call :open_when_ready 5175 "http://localhost:5175/"
goto done

:main
call :preflight || exit /b 1
call :start_backend
call :start_port 5173 "TM main :5173" "npm run dev -w travel-memory-frontend"
echo.
echo 只启动了主应用：地图相册 / 问答可用；
echo 点「轨迹演示 / 分享旅程」会提示小应用未启动——那是预期行为，不是 bug。
call :open_when_ready 5173 "http://localhost:5173"
goto done

:apps
call :preflight || exit /b 1
call :start_port 5174 "TM track :5174" "npm run dev -w @travelmemory/track-studio"
call :start_port 5175 "TM share :5175" "npm run dev -w @travelmemory/trailshare-standalone"
echo.
echo 只启动了两个独立小应用（零后端）：
echo   轨迹演示  http://localhost:5174/
echo   分享旅程  http://localhost:5175/
echo 每个端口还有第二个入口 embed.html（给主应用 iframe 用的宽松版 CSP）。
call :open_when_ready 5174 "http://localhost:5174/"
call :open_when_ready 5175 "http://localhost:5175/"
goto done

:done
echo.
echo 关闭服务：直接关掉各自弹出的命令行窗口即可。
echo.
pause
exit /b 0

REM ---------------------------------------------------------------- 子过程

:preflight
set "FAILED=0"
if not exist "%PY%" (
  echo [缺少] 虚拟环境：%PY%
  echo        先执行：python -m venv .venv
  echo                .venv\Scripts\pip install -r backend\requirements.txt -r backend\requirements-dev.txt
  set "FAILED=1"
)
if not exist "%~dp0node_modules" (
  echo [缺少] node_modules：先在仓库根执行 npm install
  set "FAILED=1"
)
where npm >nul 2>&1
if errorlevel 1 (
  echo [缺少] npm 不在 PATH 里，请先装 Node.js 20+
  set "FAILED=1"
)
netstat -ano | findstr /c:":5432 " | findstr /c:"LISTENING" >nul 2>&1
if errorlevel 1 (
  echo [警告] 5432 未监听：后端能启动，但照片 / 行程 / 记忆相关接口会失败。
  echo        先执行：docker compose up -d db   然后：make migrate
)
if "%FAILED%"=="1" exit /b 1
exit /b 0

:port_busy
set "BUSY=0"
netstat -ano | findstr /c:":%~1 " | findstr /c:"LISTENING" >nul 2>&1
if not errorlevel 1 set "BUSY=1"
exit /b 0

:report_port
call :port_busy %~1
if "%BUSY%"=="1" (echo   %~1  %~2  已被占用) else (echo   %~1  %~2  空闲)
exit /b 0

:open_when_ready
set "WAIT_PORT=%~1"
set "WAIT_URL=%~2"
set /a WAIT_TRIES=0
:wait_loop
call :port_busy %WAIT_PORT%
if "%BUSY%"=="1" (
  start "" "%WAIT_URL%"
  exit /b 0
)
set /a WAIT_TRIES+=1
if %WAIT_TRIES% GEQ 30 (
  echo [提示] 端口 %WAIT_PORT% 30 秒内没就绪，请手动打开 %WAIT_URL%
  exit /b 0
)
timeout /t 1 /nobreak >nul
goto wait_loop

:start_port
call :port_busy %~1
if "%BUSY%"=="1" (
  echo [跳过] 端口 %~1 已有服务在跑（%~2）
  exit /b 0
)
start "%~2" /d "%ROOT%" cmd /k "%~3"
echo [启动] %~2
exit /b 0

:start_backend
call :port_busy 8000
if "%BUSY%"=="1" (
  echo [跳过] 端口 8000 已有后端在跑
  exit /b 0
)
start "TM backend :8000" /d "%~dp0backend" cmd /k "..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000"
echo [启动] 后端 8000
exit /b 0
