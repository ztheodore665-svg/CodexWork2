@echo off
chcp 65001 >nul
rem ============================================================
rem  Traffic Copilot 依赖的本地 LLM 模型一键安装（免费离线）
rem  需要：已安装 Ollama（https://ollama.com/download）
rem ============================================================
echo [1/3] 检查 Ollama 是否已安装...
where ollama >nul 2>nul
if errorlevel 1 (
    echo [X] 未检测到 ollama 命令。请先安装 Ollama：
    echo     https://ollama.com/download
    pause
    exit /b 1
)
echo [OK] Ollama 已安装。

echo [2/3] 拉取推荐模型 qwen2.5:3b（约 1.9 GB，推荐用于答辩演示）...
ollama pull qwen2.5:3b
if errorlevel 1 (
    echo [X] 拉取失败，请检查网络后重试。
    pause
    exit /b 1
)
echo [OK] qwen2.5:3b 就绪。

echo [3/3] （可选）拉取轻量备选模型 qwen2.5:1.5b（约 986 MB）...
ollama pull qwen2.5:1.5b

echo.
echo 完成！启动后端前设置： set LLM_MODEL=qwen2.5:3b
echo 验证： ollama list
pause
