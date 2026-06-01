@echo off
REM ---------------------------------------------------------------------------
REM Wrapper para rodar o agendador (scheduled_execution.py) 24/6.
REM Pode ser usado tanto pelo Task Scheduler quanto pelo NSSM.
REM   - Garante o diretorio de trabalho correto (pasta deste .bat)
REM   - Usa o Python do venv se existir; senao, o Python do sistema
REM   - Forca saida UTF-8 no console
REM ---------------------------------------------------------------------------
cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else (
    set "PY=python"
)

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

"%PY%" scheduled_execution.py
