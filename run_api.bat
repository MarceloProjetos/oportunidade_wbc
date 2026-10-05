@echo off
REM ---------------------------------------------------------------------------
REM Wrapper para subir a API de disparo de sync de OS (api.py) no boot 24/7.
REM Pode ser usado tanto pelo Task Scheduler quanto pelo NSSM.
REM   - Garante o diretorio de trabalho correto (pasta deste .bat)
REM   - Usa o Python do venv se existir; senao, o Python do sistema
REM   - Forca saida UTF-8 no console
REM   - api.py sobe via waitress (producao); cai no servidor de DEV do Flask
REM     se waitress nao estiver instalado. Host/porta vem de OS_API_HOST/
REM     OS_API_PORT (.env). IMPORTANTE: defina OS_API_KEY no .env em producao.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else (
    set "PY=python"
)

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

"%PY%" api.py
