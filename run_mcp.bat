@echo off
REM ---------------------------------------------------------------------------
REM Sobe a fachada MCP em HTTP (Streamable, porta 8078) como servico.
REM Espelha run_api.bat: cwd fixo, venv-ou-sistema, UTF-8.
REM
REM Config (host/porta/token/API base) vem de mcp\.env. Roda NA .11, entao o
REM mcp\.env deve ter SIS_API_BASE=http://127.0.0.1:8077 (loopback: a OS_API_KEY
REM nunca sai do servidor) e um SIS_MCP_TOKEN forte.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else (
    set "PY=python"
)

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

REM Python coloca mcp\ no sys.path do script -> "import mcp_server" funciona.
"%PY%" mcp\serve_http.py
