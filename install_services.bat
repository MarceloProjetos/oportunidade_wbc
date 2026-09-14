@echo off
REM ===========================================================================
REM Registra os DOIS servicos do nucleo no Windows via NSSM, com auto-start no
REM boot, restart automatico em caso de queda e log em arquivo:
REM   - OrcaView-Scheduler  : agendador de oportunidades (run_scheduler.bat)
REM   - OrcaView-OS-API     : API / Painel de Sincronizacao na porta 8077 (run_api.bat)
REM
REM Os outros tres tem instalador proprio, e NAO se misturam com este:
REM   - OrcaView-MCP                        -> install_mcp_service.bat
REM   - OrcaView-WBC-Painel e -WBC-Worker   -> install_wbc_services.bat
REM
REM Rode COMO ADMINISTRADOR, uma vez. Requer o NSSM (https://nssm.cc) no PATH.
REM Depois disso, os servicos sobem sozinhos no boot (nao precisa iniciar na mao).
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
REM Sem rotacao: o NSSM "rotaciona" RENOMEANDO (api_service-2026-....log) e NUNCA apaga
REM o renomeado - eles se acumulariam para sempre. CreationDisposition 2 (CREATE_ALWAYS)
REM ZERA o arquivo a cada start do servico; com o reboot diario da .11 (~06:12) cada um
REM guarda no maximo um dia. O historico de verdade esta no log do PYTHON (logs\scheduled_execution.log), que
REM rotaciona e apaga sozinho.
nssm set     OrcaView-Scheduler AppRotateFiles 0
nssm set     OrcaView-Scheduler AppStdoutCreationDisposition 2
nssm set     OrcaView-Scheduler AppStderrCreationDisposition 2

echo === API (ordens de servico / Painel de Sincronizacao) ===
nssm install OrcaView-OS-API "%PROJ%\run_api.bat"
nssm set     OrcaView-OS-API AppDirectory "%PROJ%"
nssm set     OrcaView-OS-API Start SERVICE_AUTO_START
nssm set     OrcaView-OS-API AppStdout "%PROJ%\logs\api_service.log"
nssm set     OrcaView-OS-API AppStderr "%PROJ%\logs\api_service.log"
REM Sem rotacao: o NSSM "rotaciona" RENOMEANDO (api_service-2026-....log) e NUNCA apaga
REM o renomeado - eles se acumulariam para sempre. CreationDisposition 2 (CREATE_ALWAYS)
REM ZERA o arquivo a cada start do servico; com o reboot diario da .11 (~06:12) cada um
REM guarda no maximo um dia. O historico de verdade esta no log do PYTHON (logs\api.log), que
REM rotaciona e apaga sozinho.
nssm set     OrcaView-OS-API AppRotateFiles 0
nssm set     OrcaView-OS-API AppStdoutCreationDisposition 2
nssm set     OrcaView-OS-API AppStderrCreationDisposition 2

echo === Integracao WBC (painel e worker) ===
echo   NAO sao instalados aqui - use o install_wbc_services.bat.
echo   O worker precisa do python.exe DIRETO no Application (sem .bat no meio),
echo   senao o Ctrl+C do NSSM morre no "Terminate batch job (Y/N)?" e a parada
echo   trava ate o timeout (08/09/2026). Este arquivo instalava pelo .bat e
echo   desfazia isso calado - por isso os dois blocos sairam daqui (14/09/2026).

echo === Iniciando os servicos ===
nssm start OrcaView-Scheduler
nssm start OrcaView-OS-API

echo.
echo OK. Servicos registrados (sobem no boot e reiniciam se cairem):
echo   - OrcaView-Scheduler   -^> logs\scheduler_service.log
echo   - OrcaView-OS-API      -^> logs\api_service.log         (porta 8077)
echo Faltam o MCP (install_mcp_service.bat) e os dois do WBC (install_wbc_services.bat).
echo Gerencie em services.msc  ou:  nssm restart OrcaView-OS-API
echo IMPORTANTE: feche janelas manuais de run_*.bat (brigam pela porta / pela trava do worker).
echo Para remover depois:  nssm remove ^<servico^> confirm
endlocal
