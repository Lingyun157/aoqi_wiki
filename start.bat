@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
  python server.py %*
) else (
  echo [错误] 未找到 Python 3，请安装 Python 3.10+ 并加入 PATH 后重试。
)
pause
