@echo off
cd /d "%~dp0"
set OLD_TASK_NAME=DOMA_Ebook_Pipeline_MWF
set TASK_NAME=DOMA_Ebook_Pipeline_Daily
set PYTHON_EXE=%~dp0.venv\Scripts\pythonw.exe
set SCRIPT_PATH=%~dp0scripts\sync_ebook_pipeline.py

schtasks /Delete /TN "%OLD_TASK_NAME%" /F >nul 2>&1

REM Daily now (2026-09-22): ebooks used to only arrive Mon/Wed/Fri via
REM Marianeel's Drive drop, but the Content Calendar's own upload path
REM (any day, any time) needs the GHL-form recheck to run daily too, or a
REM form duplicated same-day could sit unattached until the next MWF run.
schtasks /Create /TN "%TASK_NAME%" /TR "\"%PYTHON_EXE%\" \"%SCRIPT_PATH%\"" /SC DAILY /ST 10:00 /RI 30 /DU 0003:00 /F

if %ERRORLEVEL% EQU 0 (
  echo Tarefa agendada com sucesso: %TASK_NAME% - todo dia, 10h-13h, a cada 30min.
  echo Roda com pythonw.exe - sem janela preta piscando na tela.
) else (
  echo Falha ao agendar. Rode este arquivo como Administrador.
)
pause
