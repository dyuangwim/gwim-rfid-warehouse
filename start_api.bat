@echo off
REM ======== start_api.bat ========
REM 切换到项目目录
cd /d C:\rfid_api

REM 激活虚拟环境
call .venv\Scripts\activate.bat

REM 启动 FastAPI (main.py)，后台运行并输出到 api.log
echo Starting RFID API service in background...
start "RFID API" cmd /c "uvicorn main:app --host 0.0.0.0 --port 8080 >> api.log 2>&1"
echo Done. Check api.log for details.
