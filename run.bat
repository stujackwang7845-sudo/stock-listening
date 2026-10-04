@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================
echo  啟動 處置股監控系統
echo ============================

if exist ".venv\Scripts\python.exe" (
    echo [系統] 偵測到本地虛擬環境，直接啟動主程式...
    ".venv\Scripts\python.exe" scripts/main.py
    if not errorlevel 1 goto end
)

echo [系統] 嘗試使用 uv 同步並啟動環境...
uv sync --quiet 2>nul
uv run python scripts/main.py

:end
if errorlevel 1 (
    echo.
    echo 程式執行失敗，請檢查錯誤訊息
    pause
)

