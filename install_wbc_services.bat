@echo off
REM ===========================================================================
REM Registra (ou CORRIGE) so os dois servicos da Integracao WBC -> SAP no NSSM:
REM   - OrcaView-WBC-Painel : painel FastAPI na PAINEL_PORTA do .env (auto-start)
REM   - OrcaView-WBC-Worker : worker (MANUAL: so liga na virada, com o legado desligado)
REM
REM Idempotente: se o servico ja existe, o "nssm install" avisa e segue; todos os
REM parametros sao (re)gravados com o caminho REAL desta pasta. Serve tambem para
REM consertar um registro feito com caminho errado (ex.: "%%PROJ%%" literal, que e o
REM que acontece quando se cola comando de cmd no PowerShell).
REM
REM Rode COMO ADMINISTRADOR, a partir desta pasta:  .\install_wbc_services.bat
REM Nao toca em OrcaView-OS-API, OrcaView-Scheduler nem OrcaView-MCP.
REM Depois: confira o .env (bloco WBC) e  python -m wbcpython doctor  antes do start.
REM ===========================================================================
setlocal
cd /d "%~dp0"
set "PROJ=%~dp0"
if "%PROJ:~-1%"=="\" set "PROJ=%PROJ:~0,-1%"

where nssm >nul 2>nul
if errorlevel 1 (
  echo ERRO: NSSM nao encontrado no PATH.
  exit /b 1
)

if not exist "%PROJ%\logs" mkdir "%PROJ%\logs"
if not exist "%PROJ%\state" mkdir "%PROJ%\state"

REM O WORKER roda o python.exe DIRETO (sem run_wbc_worker.bat no meio): com o .bat, o cmd.exe
REM segura o Ctrl+C do NSSM no "Terminate batch job (Y/N)?" e a parada so termina quando o
REM NSSM mata a arvore ao fim do AppStopMethodConsole - o "nssm start" do deploy chegava
REM durante o STOP_PENDING e era recusado (08/09/2026). Direto, o python recebe o Ctrl+C,
REM termina o ciclo e sai em segundos. venv se existir; senao o python do PATH.
set "PYEXE=%PROJ%\venv\Scripts\python.exe"
if not exist "%PYEXE%" (
  set "PYEXE="
  for /f "delims=" %%p in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%p"
)
if not defined PYEXE (
  echo ERRO: python nao encontrado ^(nem venv\ nem no PATH^).
  exit /b 1
)
echo Python do worker: %PYEXE%

echo === Painel da Integracao WBC (OrcaView-WBC-Painel) ===
nssm install OrcaView-WBC-Painel "%PROJ%\run_wbc_painel.bat" >nul 2>&1 || echo   (ja existia - parametros serao regravados)
nssm set OrcaView-WBC-Painel Application "%PROJ%\run_wbc_painel.bat"
nssm set OrcaView-WBC-Painel AppParameters ""
nssm set OrcaView-WBC-Painel AppDirectory "%PROJ%"
nssm set OrcaView-WBC-Painel Start SERVICE_AUTO_START
nssm set OrcaView-WBC-Painel AppStdout "%PROJ%\logs\wbc_painel_service.log"
nssm set OrcaView-WBC-Painel AppStderr "%PROJ%\logs\wbc_painel_service.log"
nssm set OrcaView-WBC-Painel AppRotateFiles 1
nssm set OrcaView-WBC-Painel AppRotateBytes 5000000

echo === Worker da Integracao WBC (OrcaView-WBC-Worker) ===
REM Inicio: MANUAL so na PRIMEIRA instalacao (antes da virada, com o legado ainda ligado).
REM Se o servico ja existe como AUTO_START (virada feita), preserva: rodar este script de
REM novo nao pode tirar o worker do boot - em 08/09/2026 fez exatamente isso, e o reboot
REM diario da .11 (~06:12) teria deixado a integracao parada ate alguem notar.
REM (o teste vem ANTES do "nssm install", que cria servico novo como AUTO por padrao)
set "WORKER_START=SERVICE_DEMAND_START"
sc qc OrcaView-WBC-Worker 2>nul | find "AUTO_START" >nul 2>&1 && set "WORKER_START=SERVICE_AUTO_START"
nssm install OrcaView-WBC-Worker "%PYEXE%" -m wbcpython worker >nul 2>&1 || echo   (ja existia - parametros serao regravados)
nssm set OrcaView-WBC-Worker Application "%PYEXE%"
nssm set OrcaView-WBC-Worker AppParameters "-m wbcpython worker"
nssm set OrcaView-WBC-Worker AppDirectory "%PROJ%"
nssm set OrcaView-WBC-Worker AppEnvironmentExtra PYTHONUTF8=1 PYTHONIOENCODING=utf-8
nssm set OrcaView-WBC-Worker Start %WORKER_START%
nssm set OrcaView-WBC-Worker AppStopMethodConsole 60000
nssm set OrcaView-WBC-Worker AppStdout "%PROJ%\logs\wbc_worker_service.log"
nssm set OrcaView-WBC-Worker AppStderr "%PROJ%\logs\wbc_worker_service.log"
nssm set OrcaView-WBC-Worker AppRotateFiles 1
nssm set OrcaView-WBC-Worker AppRotateBytes 5000000

echo.
echo Registrados com a pasta: %PROJ%
echo   - OrcaView-WBC-Painel  -^> logs\wbc_painel_service.log   (nao iniciado aqui)
echo   - OrcaView-WBC-Worker  -^> logs\wbc_worker_service.log   (python.exe direto; inicio: %WORKER_START%)
echo     ^(worker ja rodando? as mudancas valem no proximo start: nssm restart OrcaView-WBC-Worker^)
echo     ^(depois da virada o worker DEVE ser SERVICE_AUTO_START: nssm set OrcaView-WBC-Worker Start SERVICE_AUTO_START^)
echo Proximos passos:
echo   1. bloco WBC no .env  (TRACKING_DB_URL=sqlite:///./state/wbc_tracking.db, LOG_FILE=logs/wbcpython.log,
echo      PAINEL_HOST=0.0.0.0, PAINEL_PORTA=8079 + SL_*, WBC_SQL_*, HANA_*, WORKER_*)
echo   2. python -m wbcpython doctor
echo   3. nssm start OrcaView-WBC-Painel   ^(se parar: Get-Content .\logs\wbc_painel_service.log -Tail 30 -Encoding utf8^)
endlocal
