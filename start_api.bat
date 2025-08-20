@echo off
cd /d "C:\Users\dyuan\Downloads\GWIM_RFID Warehouse Management System"
call venv\Scripts\activate

python -m uvicorn main:app --host 0.0.0.0 --port 8080



@echo off
setlocal
title RFID API Starter
rem 1) 切到脚本所在目录
cd /d "%~dp0"

rem 2) 选对你的 venv 目录名：.venv 或 venv（二选一）
set VENV=.venv
if not exist "%VENV%\Scripts\python.exe" (
  set VENV=venv
)

rem 3) 如果还是没有虚拟环境，自动创建并装依赖
if not exist "%VENV%\Scripts\python.exe" (
  echo [+] Creating virtualenv...
  py -3 -m venv .venv || (echo [ERROR] 没有 Python launcher (py)。請安裝 Python 或把 py 加到 PATH。& pause & exit /b 1)
  set VENV=.venv
  echo [+] Upgrading pip...
  "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
  echo [+] Installing deps...
  "%VENV%\Scripts\pip.exe" install fastapi uvicorn[standard] python-dotenv mysql-connector-python
)

rem 4) 基础检查
if not exist "main.py" (
  echo [ERROR] 找不到 main.py（請把 main.py 放到：%cd%）
  pause & exit /b 1
)

echo [+] Import check...
"%VENV%\Scripts\python.exe" -c "import main;print('OK, app_exists=',hasattr(main,'app'))" || (
  echo [ERROR] 無法 import main 或 main.app 不存在
  pause & exit /b 1
)

rem 5) 启动 Uvicorn（如 8080 被占用就改成 8088）
echo [+] Starting Uvicorn on 0.0.0.0:8080 ...
"%VENV%\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8080
echo.
echo [INFO] Uvicorn 已退出。
pause
endlocal
