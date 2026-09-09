@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 运行全部单元测试（mock 引擎，不依赖 SUMO）...
python -m pytest tests/ -v
if %errorlevel% neq 0 (
    echo.
    echo [失败] 存在未通过的测试，请检查后重试。
    exit /b 1
)
echo.
echo [成功] 全部测试通过。
exit /b 0
