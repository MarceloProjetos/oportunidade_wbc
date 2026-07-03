@echo off
REM ---------------------------------------------------------------------------
REM Registra a fachada MCP (HTTP :8078) como servico do Windows via NSSM.
REM Espelha o padrao do install_services.bat (OrcaView-OS-API / OrcaView-Scheduler).
REM Requer: nssm no PATH; mcp\.env preenchido (SIS_MCP_TOKEN, SIS_API_BASE loopback).
REM ---------------------------------------------------------------------------
setlocal
set "PROJ=%~dp0"
if "%PROJ:~-1%"=="\" set "PROJ=%PROJ:~0,-1%"

where nssm >nul 2>&1
if errorlevel 1 (
    echo ERRO: nssm nao encontrado no PATH.
    exit /b 1
)

echo === MCP (fachada HTTP na porta 8078) - servico OrcaView-MCP ===
nssm install OrcaView-MCP "%PROJ%\run_mcp.bat"
nssm set     OrcaView-MCP AppDirectory "%PROJ%"
nssm set     OrcaView-MCP Start SERVICE_AUTO_START
nssm set     OrcaView-MCP DependOnService OrcaView-OS-API
nssm set     OrcaView-MCP AppStdout "%PROJ%\logs\mcp_service.log"
nssm set     OrcaView-MCP AppStderr "%PROJ%\logs\mcp_service.log"
nssm set     OrcaView-MCP AppRotateFiles 1
nssm set     OrcaView-MCP AppRotateBytes 5000000
nssm start   OrcaView-MCP

echo.
echo Feito. Conferir:  nssm status OrcaView-MCP
echo Remover:          nssm stop OrcaView-MCP ^&^& nssm remove OrcaView-MCP confirm
endlocal
