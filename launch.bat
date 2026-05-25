@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe -m uvicorn src.main:app --host 0.0.0.0 --port 8005 --reload
