@echo off
REM ======== stop_api.bat ========
echo Stopping RFID API service...

REM 强制结束所有 python.exe 进程（uvicorn 运行在 python.exe 下）
taskkill /IM python.exe /F >nul 2>&1

echo Done.
pause
