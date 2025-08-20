@echo off
cd /d "C:\Users\dyuan\Downloads\GWIM_RFID Warehouse Management System"
call venv\Scripts\activate
python -m uvicorn main:app --host 0.0.0.0 --port 8080