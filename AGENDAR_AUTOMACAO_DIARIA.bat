@echo off
cd /d "%~dp0"
set TASK_NAME=DOMA_Dashboard_Daily_Sync
set PYTHON_EXE=%~dp0.venv\Scripts\python.exe
set SCRIPT_PATH=%~dp0scripts\daily_sync.py

schtasks /Create /TN "%TASK_NAME%" /TR "\"%PYTHON_EXE%\" \"%SCRIPT_PATH%\"" /SC DAILY /ST 06:00 /F

if %ERRORLEVEL% EQU 0 (
  echo Tarefa agendada com sucesso: %TASK_NAME% - todo dia as 06:00.
) else (
  echo Falha ao agendar. Rode este arquivo como Administrador.
)
pause
