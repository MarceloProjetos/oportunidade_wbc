@echo off
REM ---------------------------------------------------------------------------
REM Wrapper para o WORKER da Integracao WBC -> SAP (python -m wbcpython worker).
REM Registrado no NSSM como OrcaView-WBC-Worker (install_services.bat).
REM   - cwd = raiz do projeto (onde estao o .env e a pasta state\ do acompanhamento)
REM   - Python do venv se existir; senao, o do sistema (a .11 usa o do sistema, 3.12)
REM   - saida UTF-8 (o log tem acento)
REM
REM Parada limpa: o NSSM manda Ctrl+C; o worker termina o ciclo em andamento
REM (9-14 s medidos em 2026-09-08) e so entao sai. Por isso o servico e registrado
REM com AppStopMethodConsole 60000: nunca matar o processo no meio de um POST no SAP.
REM
REM O worker so roda ciclo dentro do expediente dele (WORKER_HORARIO_* / DIAS no .env);
REM fora dele o processo fica vivo e ocioso - nao e falha.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else (
    set "PY=python"
)

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

"%PY%" -m wbcpython worker
