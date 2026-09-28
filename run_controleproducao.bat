@echo off
REM ---------------------------------------------------------------------------
REM Wrapper para o CONTROLE DE PRODUCAO (python -m controleproducao web).
REM Registrado no NSSM como OrcaView-ControleProducao (install_wbc_services.bat).
REM   - cwd = raiz do projeto (.env, logs\controleproducao.log)
REM   - Python do venv se existir; senao, o do sistema (o mesmo dos outros servicos)
REM   - host/porta vem do .env (CP_HOST / CP_PORTA; na .11: 127.0.0.1 / 8080 ate o piloto,
REM     0.0.0.0 so na F6 do plano, junto com a regra de firewall da 8080)
REM   - a chave de acesso e a MESMA da API 8077 e do painel WBC (OS_API_KEY no .env)
REM ESCREVE em SAP de PRODUCAO (Service Layer) - so na maquina com o IP da .11
REM (wbcpython\safety.py). Parar com execucao em andamento deixa OP pela metade:
REM o deploy_update.bat confere /health/ocupado antes de parar.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else (
    set "PY=python"
)

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

"%PY%" -m controleproducao web
