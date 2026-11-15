@echo off
chcp 65001 >nul
title 高中庶務股長智慧訂餐系統

echo ===================================================
echo   高中庶務股長智慧訂餐系統 一鍵啟動精靈
echo ===================================================
echo.

cd /d "%~dp0"

:: 1. 檢查 Python 虛擬環境
if exist "venv\Scripts\python.exe" (
    set "PYTHON_CMD=%~dp0venv\Scripts\python.exe"
) else (
    echo [警告] 未偵測到 venv 虛擬環境，使用系統全域 Python...
    set "PYTHON_CMD=python"
)

:: 2. 自動檢查並釋放 8000 連接埠 (防止殘留行程占用)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING 2^>nul') do (
    if not "%%a"=="" (
        echo [提示] 偵測到 8000 連接埠被行程 PID %%a 佔用，正在自動釋放...
        taskkill /F /PID %%a >nul 2>&1
    )
)

echo [1/3] 環境與連接埠檢查完成！
echo [2/3] 正在啟動 FastAPI / Uvicorn 伺服器...
echo       - 同學訂餐首頁: http://localhost:8000
echo       - 庶務股長後台: http://localhost:8000/admin (預設PIN碼: 66776677)
echo.
echo [3/3] 正在為您自動開啟瀏覽器首頁...

start "" /b cmd /c "ping 127.0.0.1 -n 3 >nul && start http://localhost:8000"

echo ===================================================
echo   伺服器已就緒！(關閉此黑視窗或按 Ctrl+C 可停止服務)
echo ===================================================
echo.

%PYTHON_CMD% -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [錯誤] 伺服器意外終止，錯誤碼: %ERRORLEVEL%
    pause
)
