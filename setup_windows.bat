@echo off
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -r requirements.txt
if not exist .env (
  copy .env.example .env
  echo Criado .env a partir de .env.example - preencha as credenciais antes de sincronizar.
)
echo.
echo Setup concluido. Use RUN_DASHBOARD.bat para abrir o dashboard local.
pause
