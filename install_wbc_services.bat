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

echo === Worker da Integracao WBC (OrcaView-WBC-Worker) - MANUAL ate a virada ===
nssm install OrcaView-WBC-Worker "%PROJ%\run_wbc_worker.bat" >nul 2>&1 || echo   (ja existia - parametros serao regravados)
nssm set OrcaView-WBC-Worker Application "%PROJ%\run_wbc_worker.bat"
nssm set OrcaView-WBC-Worker AppParameters ""
nssm set OrcaView-WBC-Worker AppDirectory "%PROJ%"
nssm set OrcaView-WBC-Worker Start SERVICE_DEMAND_START
nssm set OrcaView-WBC-Worker AppStopMethodConsole 60000
nssm set OrcaView-WBC-Worker AppStdout "%PROJ%\logs\wbc_worker_service.log"
nssm set OrcaView-WBC-Worker AppStderr "%PROJ%\logs\wbc_worker_service.log"
nssm set OrcaView-WBC-Worker AppRotateFiles 1
nssm set OrcaView-WBC-Worker AppRotateBytes 5000000

echo.
echo Registrados com a pasta: %PROJ%
echo   - OrcaView-WBC-Painel  -^> logs\wbc_painel_service.log   (nao iniciado aqui)
echo   - OrcaView-WBC-Worker  -^> logs\wbc_worker_service.log   (MANUAL, parado)
echo Proximos passos:
echo   1. bloco WBC no .env  (TRACKING_DB_URL=sqlite:///./state/wbc_tracking.db, LOG_FILE=logs/wbcpython.log,
echo      PAINEL_HOST=0.0.0.0, PAINEL_PORTA=8079 + SL_*, WBC_SQL_*, HANA_*, WORKER_*)
echo   2. python -m wbcpython doctor
echo   3. nssm start OrcaView-WBC-Painel   ^(se parar: Get-Content .\logs\wbc_painel_service.log -Tail 30 -Encoding utf8^)
endlocal
