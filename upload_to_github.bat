@echo off
chcp 65001 >nul
title 上傳專案至 GitHub (project-lunch)

echo ===================================================
echo   高中訂餐系統 - GitHub 自動上傳精靈
echo   目標儲存庫: https://github.com/CCChester-ex/project-lunch
echo ===================================================
echo.

cd /d "%~dp0"

:: 1. 檢查是否已安裝 Git
where git >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [提示] 系統尚未偵測到 Git，正在透過 Windows winget 為您自動安裝 Git...
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
    set "PATH=%PATH%;C:\Program Files\Git\cmd;C:\Users\wwwib\AppData\Local\Programs\Git\cmd"
)

where git >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [錯誤] 無法找到 git.exe，請先安裝 Git (https://git-scm.com/) 或重新開啟視窗再試。
    pause
    exit /b 1
)

:: 2. 初始化 Git 儲存庫並加入檔案
if not exist ".git" (
    echo [1/4] 正在初始化本地 Git 儲存庫...
    git init
)

:: 檢查使用者名稱與信箱是否已設定
git config user.name >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    git config user.name "CCChester-ex"
    git config user.email "CCChester-ex@users.noreply.github.com"
)

echo [2/4] 正在打包專案檔案 (排除 venv 與本地資料庫個資)...
git rm --cached data/lunch.db >nul 2>&1
git add .
git commit -m "feat: 升級後台 HMAC 權杖驗證、防暴力破解與支援 Neon PostgreSQL 永久資料庫"

echo [3/4] 正在綁定遠端儲存庫 https://github.com/CCChester-ex/project-lunch.git ...
git branch -M main
git remote remove origin >nul 2>&1
git remote add origin https://github.com/CCChester-ex/project-lunch.git

echo [4/4] 正在推送至 GitHub (若跳出 GitHub 登入視窗，請完成授權登入)...
git push -u origin main --force

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ===================================================
    echo   🎉 上傳成功！請前往以下網址查看您的專案：
    echo   https://github.com/CCChester-ex/project-lunch
    echo ===================================================
) else (
    echo.
    echo [錯誤] 推送未完成，請確認是否已在彈出視窗登入 GitHub 帳號並具備該儲存庫權限。
)
pause
