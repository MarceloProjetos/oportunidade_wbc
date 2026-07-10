@echo off
REM ============================================================================
REM  deploy_update.bat - Atualiza o ServidorIntegracaoSAP em producao (.11) via git.
REM
REM  Roda NO servidor 192.168.7.11, na raiz C:\Python\ServidorIntegracaoSAP.
REM  Fluxo: para os 3 servicos -> git pull --ff-only -> pip (so se requirements
REM  mudou) -> sobe os 3 servicos na ordem certa -> confere /health.
REM
REM  Precisa de Administrador (mexe nos servicos NSSM).
REM  NAO toca em venv\, .env, .env.* nem logs\ (todos no .gitignore).
REM ============================================================================
setlocal enabledelayedexpansion

set "REPO=https://github.com/MarceloProjetos/oportunidade_wbc.git"
set "BRANCH=master"

REM --- ir para a raiz do repo (a pasta deste .bat) ---
cd /d "%~dp0"
echo [deploy] pasta: %CD%

REM --- exige Administrador ---
net session >nul 2>&1
if errorlevel 1 (
  echo ERRO: rode este script como Administrador ^(mexe nos servicos NSSM^).
  pause & exit /b 1
)

REM --- git disponivel? ---
where git >nul 2>&1
if errorlevel 1 (
  echo ERRO: git nao encontrado no PATH. Abra um terminal NOVO apos instalar.
  pause & exit /b 1
)

REM --- parar servicos antes de mexer nos arquivos (MCP depende da API: para o MCP 1o) ---
echo [nssm] parando servicos...
nssm stop OrcaView-MCP       >nul 2>&1
nssm stop OrcaView-OS-API    >nul 2>&1
nssm stop OrcaView-Scheduler >nul 2>&1

set "REQCHANGED="

if not exist ".git" (
  echo [bootstrap] 1a execucao: vinculando a pasta ao repositorio remoto...
  git init                                        || goto :fail
  git remote add origin "%REPO%"                  || goto :fail
  git fetch origin %BRANCH%                        || goto :fail
  echo [bootstrap] alinhando arquivos com origin/%BRANCH% ^(reset --hard^)...
  git reset --hard origin/%BRANCH%                 || goto :fail
  git branch --set-upstream-to=origin/%BRANCH% %BRANCH% >nul 2>&1
  set "REQCHANGED=1"
) else (
  echo [git] atualizando a partir de origin/%BRANCH%...
  for /f %%i in ('git rev-parse HEAD') do set "BEFORE=%%i"
  git fetch origin %BRANCH%                        || goto :fail
  git pull --ff-only origin %BRANCH%               || goto :pullfail
  for /f %%i in ('git rev-parse HEAD') do set "AFTER=%%i"
  if not "!BEFORE!"=="!AFTER!" (
    git diff --name-only !BEFORE! !AFTER! | findstr /i "requirements" >nul && set "REQCHANGED=1"
  ) else (
    echo [git] ja estava atualizado ^(nenhum commit novo^).
  )
)

REM --- reinstalar dependencias so se requirements mudaram ---
if defined REQCHANGED (
  if exist "venv\Scripts\pip.exe" (
    echo [pip] requirements mudaram; atualizando venv...
    venv\Scripts\pip install -r requirements.txt -r mcp\requirements.txt || goto :fail
  ) else (
    echo [pip] AVISO: venv\ nao encontrado - pulei o pip.
  )
) else (
  echo [pip] requirements sem mudanca - venv mantido.
)

REM --- subir servicos (API antes do MCP, que depende dela; Scheduler independe) ---
echo [nssm] subindo servicos...
nssm start OrcaView-OS-API    >nul 2>&1
nssm start OrcaView-MCP       >nul 2>&1
nssm start OrcaView-Scheduler >nul 2>&1

REM --- validacao rapida ---
echo [health] aguardando a API subir...
timeout /t 3 >nul
curl -s http://127.0.0.1:8077/health
echo.
nssm status OrcaView-OS-API
nssm status OrcaView-MCP
nssm status OrcaView-Scheduler

echo.
echo ===== DEPLOY OK =====
pause
exit /b 0

:pullfail
echo.
echo ERRO: git pull --ff-only falhou. Ha alteracoes locais em arquivos versionados
echo       nesta pasta que impedem o fast-forward. Nada foi alterado.
echo       Para descartar as mudancas locais e forcar o estado do remoto:
echo           git reset --hard origin/%BRANCH%
echo       (Isso NAO apaga venv\, .env, .env.* nem logs\.)
echo [nssm] religando os servicos para nao deixar o servidor parado...
nssm start OrcaView-OS-API    >nul 2>&1
nssm start OrcaView-MCP       >nul 2>&1
nssm start OrcaView-Scheduler >nul 2>&1
pause
exit /b 1

:fail
echo.
echo ERRO no deploy - veja a mensagem acima. Os servicos podem estar PARADOS.
echo Tentando religar os servicos...
nssm start OrcaView-OS-API    >nul 2>&1
nssm start OrcaView-MCP       >nul 2>&1
nssm start OrcaView-Scheduler >nul 2>&1
pause
exit /b 1
