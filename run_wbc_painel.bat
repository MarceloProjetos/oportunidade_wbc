@echo off
REM ---------------------------------------------------------------------------
REM Wrapper para o PAINEL da Integracao WBC -> SAP (python -m wbcpython dashboard).
REM Registrado no NSSM como OrcaView-WBC-Painel (install_services.bat).
REM   - cwd = raiz do projeto (.env, state\wbc_tracking.db, logs\wbcpython.log)
REM   - Python do venv se existir; senao, o do sistema
REM   - host/porta vem do .env (PAINEL_HOST / PAINEL_PORTA; na .11: 0.0.0.0 / 8079)
REM   - a chave de acesso e a MESMA da API 8077 (OS_API_KEY no .env)
REM O painel le SOMENTE o banco de acompanhamento: nunca toca SAP nem WBC.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else (
    set "PY=python"
)

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

"%PY%" -m wbcpython dashboard
