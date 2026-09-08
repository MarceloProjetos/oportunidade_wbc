@echo off
REM ===========================================================================
REM Registra os QUATRO servicos do projeto no Windows via NSSM, com auto-start no
REM boot, restart automatico em caso de queda e log em arquivo (com rotacao):
REM   - OrcaView-Scheduler  : agendador de oportunidades (run_scheduler.bat)
REM   - OrcaView-OS-API     : API / Painel de Sincronizacao na porta 8077 (run_api.bat)
REM   - OrcaView-WBC-Painel : painel da Integracao WBC -> SAP, PAINEL_PORTA (run_wbc_painel.bat)
REM   - OrcaView-WBC-Worker : worker da Integracao WBC -> SAP (run_wbc_worker.bat)
REM A fachada MCP (OrcaView-MCP, 8078) tem instalador proprio: install_mcp_service.bat.
REM
REM Rode COMO ADMINISTRADOR, uma vez. Requer o NSSM (https://nssm.cc) no PATH.
REM Depois disso, os servicos sobem sozinhos no boot (nao precisa iniciar na mao).
REM
REM O WORKER e registrado como MANUAL e NAO e iniciado aqui, de proposito: ele
REM escreve em producao no SAP e so pode ligar depois que o integrador legado
REM (tarefa "Integracao WBC") estiver desligado - ver docs/PLANO_INTEGRACAO_WBCPYTHON.md.
REM Na virada:  nssm set OrcaView-WBC-Worker Start SERVICE_AUTO_START  &  nssm start OrcaView-WBC-Worker
REM
REM Antes: confirme que os run_*.bat rodam sem erro numa janela (registrar um
REM servico que quebra vira loop de restart).
REM ===========================================================================
setlocal
cd /d "%~dp0"
set "PROJ=%~dp0"
if "%PROJ:~-1%"=="\" set "PROJ=%PROJ:~0,-1%"

where nssm >nul 2>nul
if errorlevel 1 (
  echo ERRO: NSSM nao encontrado no PATH.
  echo   Baixe em https://nssm.cc/download e ponha o nssm.exe numa pasta do PATH.
  exit /b 1
)

if not exist "%PROJ%\logs" mkdir "%PROJ%\logs"
if not exist "%PROJ%\state" mkdir "%PROJ%\state"

echo === Agendador (oportunidades) ===
nssm install OrcaView-Scheduler "%PROJ%\run_scheduler.bat"
nssm set     OrcaView-Scheduler AppDirectory "%PROJ%"
nssm set     OrcaView-Scheduler Start SERVICE_AUTO_START
nssm set     OrcaView-Scheduler AppStdout "%PROJ%\logs\scheduler_service.log"
nssm set     OrcaView-Scheduler AppStderr "%PROJ%\logs\scheduler_service.log"
nssm set     OrcaView-Scheduler AppRotateFiles 1
nssm set     OrcaView-Scheduler AppRotateBytes 5000000

echo === API (ordens de servico / Painel de Sincronizacao) ===
nssm install OrcaView-OS-API "%PROJ%\run_api.bat"
nssm set     OrcaView-OS-API AppDirectory "%PROJ%"
nssm set     OrcaView-OS-API Start SERVICE_AUTO_START
nssm set     OrcaView-OS-API AppStdout "%PROJ%\logs\api_service.log"
nssm set     OrcaView-OS-API AppStderr "%PROJ%\logs\api_service.log"
nssm set     OrcaView-OS-API AppRotateFiles 1
nssm set     OrcaView-OS-API AppRotateBytes 5000000

echo === Painel da Integracao WBC (FastAPI, PAINEL_PORTA do .env) ===
nssm install OrcaView-WBC-Painel "%PROJ%\run_wbc_painel.bat"
nssm set     OrcaView-WBC-Painel AppDirectory "%PROJ%"
nssm set     OrcaView-WBC-Painel Start SERVICE_AUTO_START
nssm set     OrcaView-WBC-Painel AppStdout "%PROJ%\logs\wbc_painel_service.log"
nssm set     OrcaView-WBC-Painel AppStderr "%PROJ%\logs\wbc_painel_service.log"
nssm set     OrcaView-WBC-Painel AppRotateFiles 1
nssm set     OrcaView-WBC-Painel AppRotateBytes 5000000

echo === Worker da Integracao WBC (MANUAL ate a virada; parada limpa de ate 60 s) ===
nssm install OrcaView-WBC-Worker "%PROJ%\run_wbc_worker.bat"
nssm set     OrcaView-WBC-Worker AppDirectory "%PROJ%"
nssm set     OrcaView-WBC-Worker Start SERVICE_DEMAND_START
nssm set     OrcaView-WBC-Worker AppStopMethodConsole 60000
nssm set     OrcaView-WBC-Worker AppStdout "%PROJ%\logs\wbc_worker_service.log"
nssm set     OrcaView-WBC-Worker AppStderr "%PROJ%\logs\wbc_worker_service.log"
nssm set     OrcaView-WBC-Worker AppRotateFiles 1
nssm set     OrcaView-WBC-Worker AppRotateBytes 5000000

echo === Iniciando os servicos (o worker NAO) ===
nssm start OrcaView-Scheduler
nssm start OrcaView-OS-API
nssm start OrcaView-WBC-Painel

echo.
echo OK. Servicos registrados (sobem no boot e reiniciam se cairem):
echo   - OrcaView-Scheduler   -^> logs\scheduler_service.log
echo   - OrcaView-OS-API      -^> logs\api_service.log         (porta 8077)
echo   - OrcaView-WBC-Painel  -^> logs\wbc_painel_service.log  (PAINEL_PORTA, 8079)
echo   - OrcaView-WBC-Worker  -^> logs\wbc_worker_service.log  (MANUAL: parado ate a virada)
echo Gerencie em services.msc  ou:  nssm restart OrcaView-OS-API
echo IMPORTANTE: feche janelas manuais de run_*.bat (brigam pela porta / pela trava do worker).
echo Para remover depois:  nssm remove ^<servico^> confirm
endlocal
