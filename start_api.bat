@echo off
cd /d "C:\Users\dyuan\Downloads\GWIM_RFID Warehouse Management System"
call venv\Scripts\activate

python -m uvicorn main:app --host 0.0.0.0 --port 8080




@echo off
setlocal
title RFID API Starter
rem 切到脚本所在目录
cd /d "%~dp0"

rem 1) 确保有虚拟环境
if not exist ".venv\Scripts\python.exe" (
  echo [+] Creating venv...
  py -3 -m venv .venv  || (echo [ERROR] venv create failed & pause & exit /b 1)
  echo [+] Upgrading pip...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  echo [+] Installing deps...
  ".venv\Scripts\pip.exe" install fastapi uvicorn[standard] python-dotenv mysql-connector-python
)

rem 2) 基础检查
if not exist "main.py" (
  echo [ERROR] main.py not found in %cd%
  pause & exit /b 1
)
".venv\Scripts\python.exe" -c "import main; import sys; print('OK - app exists:', hasattr(main,'app'))" || (
  echo [ERROR] Cannot import main.py / app
  pause & exit /b 1
)

rem 3) 启动 Uvicorn（端口按你需要改）
echo [+] Starting Uvicorn on 0.0.0.0:8080 ...
".venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8080
echo [INFO] Uvicorn exited.
pause
endlocal
