@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python scripts\daily_sync.py
pause
