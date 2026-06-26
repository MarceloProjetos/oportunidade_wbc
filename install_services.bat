@echo off
REM ===========================================================================
REM Registra os DOIS servicos no Windows via NSSM, com auto-start no boot,
REM restart automatico em caso de queda e log em arquivo (com rotacao):
REM   - OrcaView-Scheduler : agendador de oportunidades (run_scheduler.bat)
REM   - OrcaView-OS-API    : API / painel na porta 8077 (run_api.bat)
REM
REM Rode COMO ADMINISTRADOR, uma vez. Requer o NSSM (https://nssm.cc) no PATH.
REM Depois disso, NAO use mais o run_all.bat manual (os servicos sobem sozinhos).
REM
REM Antes: confirme que 'run_scheduler.bat' e 'run_api.bat' rodam sem erro
REM (registrar um servico que quebra vira loop de restart).
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

echo === Agendador (oportunidades) ===
nssm install OrcaView-Scheduler "%PROJ%\run_scheduler.bat"
nssm set     OrcaView-Scheduler AppDirectory "%PROJ%"
nssm set     OrcaView-Scheduler Start SERVICE_AUTO_START
nssm set     OrcaView-Scheduler AppStdout "%PROJ%\logs\scheduler_service.log"
nssm set     OrcaView-Scheduler AppStderr "%PROJ%\logs\scheduler_service.log"
nssm set     OrcaView-Scheduler AppRotateFiles 1
nssm set     OrcaView-Scheduler AppRotateBytes 5000000

echo === API (ordens de servico / painel) ===
nssm install OrcaView-OS-API "%PROJ%\run_api.bat"
nssm set     OrcaView-OS-API AppDirectory "%PROJ%"
nssm set     OrcaView-OS-API Start SERVICE_AUTO_START
nssm set     OrcaView-OS-API AppStdout "%PROJ%\logs\api_service.log"
nssm set     OrcaView-OS-API AppStderr "%PROJ%\logs\api_service.log"
nssm set     OrcaView-OS-API AppRotateFiles 1
nssm set     OrcaView-OS-API AppRotateBytes 5000000

echo === Iniciando os servicos ===
nssm start OrcaView-Scheduler
nssm start OrcaView-OS-API

echo.
echo OK. Servicos registrados (sobem no boot e reiniciam se cairem):
echo   - OrcaView-Scheduler  -^> logs\scheduler_service.log
echo   - OrcaView-OS-API     -^> logs\api_service.log  (porta 8077)
echo Gerencie em services.msc  ou:  nssm restart OrcaView-OS-API
echo IMPORTANTE: feche as janelas manuais do run_all.bat (brigam pela porta 8077).
echo Para remover depois:  nssm remove OrcaView-Scheduler confirm  ^&  nssm remove OrcaView-OS-API confirm
endlocal
