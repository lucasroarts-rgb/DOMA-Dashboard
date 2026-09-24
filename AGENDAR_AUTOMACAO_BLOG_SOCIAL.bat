@echo off
cd /d "%~dp0"
set TASK_NAME=DOMA_Calendar_Social_Post
set PYTHON_EXE=%~dp0.venv\Scripts\pythonw.exe
set SCRIPT_PATH=%~dp0scripts\sync_blog_social.py

REM Every 30min, all day - a Content Calendar item with an image already
REM uploaded gets posted to Facebook + Instagram on its scheduled date,
REM no manual step. Thalles/Michelle prepare the image; this only writes
REM the caption and publishes it.
schtasks /Create /TN "%TASK_NAME%" /TR "\"%PYTHON_EXE%\" \"%SCRIPT_PATH%\"" /SC DAILY /ST 07:00 /RI 30 /DU 16:00 /F

if %ERRORLEVEL% EQU 0 (
  echo Tarefa agendada com sucesso: %TASK_NAME% - todo dia, 7h-23h, a cada 30min.
  echo Roda com pythonw.exe - sem janela preta piscando na tela.
) else (
  echo Falha ao agendar. Rode este arquivo como Administrador.
)
pause
