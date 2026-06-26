@echo off
REM ---------------------------------------------------------------------------
REM Launcher unico: sobe o AGENDADOR (run_scheduler.bat) e a API (run_api.bat),
REM cada um no seu proprio processo (start). Este .bat dispara os dois e encerra
REM — adequado para Task Scheduler (gatilho ONSTART) ou execucao manual no boot.
REM
REM IMPORTANTE (NSSM): para serviços dedicados, registre run_scheduler.bat e
REM run_api.bat como DOIS servicos separados — NAO use este launcher no NSSM, pois
REM ele termina logo apos iniciar os dois e o NSSM acharia que o servico "morreu".
REM ---------------------------------------------------------------------------
cd /d "%~dp0"

start "OrcaView-Scheduler" cmd /c run_scheduler.bat
start "OrcaView-OS-API"    cmd /c run_api.bat

echo Iniciados: Agendador (oportunidades) + API (ordens de servico).
echo Esta janela pode ser fechada — os dois seguem rodando em janelas proprias.
