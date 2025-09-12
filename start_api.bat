@echo off
REM ======== start_api.bat ========
REM 切换到项目目录
cd /d C:\rfid_api

REM 激活虚拟环境（Windows 用 .bat 版本，避免 PowerShell ExecutionPolicy 问题）
call .venv\Scripts\activate.bat

REM 启动 FastAPI (main.py)
echo Starting RFID API service...
uvicorn main:app --host 0.0.0.0 --port 8080

REM 保持窗口不自动关闭（可选）
pause
