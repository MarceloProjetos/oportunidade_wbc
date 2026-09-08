@echo off
REM ============================================================================
REM  deploy_update.bat - Atualiza o ServidorIntegracaoSAP em producao (.11) via git.
REM
REM  Roda NO servidor 192.168.7.11, na raiz C:\Python\ServidorIntegracaoSAP.
REM  Fluxo: para os 5 servicos -> git pull --ff-only -> pip (so se requirements
REM  mudou; no venv se houver, senao no Python do sistema) -> sobe os servicos na
REM  ordem certa -> confere /health da API e a porta do painel WBC.
REM
REM  Servicos (NSSM): OrcaView-MCP, OrcaView-OS-API, OrcaView-Scheduler,
REM  OrcaView-WBC-Painel e OrcaView-WBC-Worker. O WORKER so volta a subir se
REM  estava rodando antes do deploy: antes da virada ele fica parado, e o deploy
REM  nao pode ser a porta dos fundos que liga o integrador novo.
REM
REM  Precisa de Administrador (mexe nos servicos NSSM).
REM  NAO toca em venv\, .env, .env.*, logs\ nem state\ (todos no .gitignore).
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

REM --- o worker WBC estava rodando? (sc query e ANSI; a saida do nssm e UTF-16 e nao parseia) ---
set "WORKER_ATIVO="
sc query OrcaView-WBC-Worker 2>nul | find "RUNNING" >nul 2>&1 && set "WORKER_ATIVO=1"
if defined WORKER_ATIVO (
  echo [nssm] OrcaView-WBC-Worker esta RODANDO: sera parado ^(espera o ciclo terminar^) e religado no fim.
) else (
  echo [nssm] OrcaView-WBC-Worker parado ou nao instalado: continua parado apos o deploy.
)

REM --- parar servicos antes de mexer nos arquivos (MCP depende da API: para o MCP 1o;
REM     o worker por ultimo, porque a parada dele espera o ciclo em andamento) ---
echo [nssm] parando servicos...
nssm stop OrcaView-MCP        >nul 2>&1
nssm stop OrcaView-OS-API     >nul 2>&1
nssm stop OrcaView-Scheduler  >nul 2>&1
nssm stop OrcaView-WBC-Painel >nul 2>&1
if defined WORKER_ATIVO (
  echo [nssm] parando o worker WBC ^(ate 60 s, termina o ciclo atual^)...
  nssm stop OrcaView-WBC-Worker >nul 2>&1
)

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
REM     venv\ se existir; senao, o Python do sistema (e o caso da .11: Python312, sem venv).
REM     Antes o script PULAVA o pip sem venv - e o deploy do status de OP exigiu pip a mao.
if defined REQCHANGED (
  if exist "venv\Scripts\python.exe" (
    echo [pip] requirements mudaram; atualizando o venv...
    venv\Scripts\python.exe -m pip install -r requirements.txt -r mcp\requirements.txt || goto :fail
  ) else (
    where python >nul 2>&1 || (echo ERRO: python nao encontrado no PATH. & goto :fail)
    echo [pip] requirements mudaram; instalando no Python do sistema...
    python -m pip install -r requirements.txt -r mcp\requirements.txt || goto :fail
  )
) else (
  echo [pip] requirements sem mudanca - dependencias mantidas.
)

REM --- subir servicos (API antes do MCP, que depende dela; os outros independem) ---
echo [nssm] subindo servicos...
nssm start OrcaView-OS-API     >nul 2>&1
nssm start OrcaView-MCP        >nul 2>&1
nssm start OrcaView-Scheduler  >nul 2>&1
nssm start OrcaView-WBC-Painel >nul 2>&1
if defined WORKER_ATIVO (
  nssm start OrcaView-WBC-Worker >nul 2>&1
) else (
  echo [nssm] OrcaView-WBC-Worker segue parado ^(virada = nssm start OrcaView-WBC-Worker^).
)

REM --- validacao rapida ---
set "PAINEL_PORTA=8079"
for /f "usebackq tokens=2 delims== " %%p in (`findstr /b /i "PAINEL_PORTA=" .env 2^>nul`) do set "PAINEL_PORTA=%%p"
echo [health] aguardando a API e o painel subirem...
timeout /t 4 >nul
curl -s http://127.0.0.1:8077/health
echo.
curl -s -o nul -w "[painel WBC] http://127.0.0.1:%PAINEL_PORTA%/entrar -> HTTP %%{http_code}" http://127.0.0.1:%PAINEL_PORTA%/entrar
echo.
nssm status OrcaView-OS-API
nssm status OrcaView-MCP
nssm status OrcaView-Scheduler
nssm status OrcaView-WBC-Painel
nssm status OrcaView-WBC-Worker

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
echo       (Isso NAO apaga venv\, .env, .env.*, logs\ nem state\.)
echo [nssm] religando os servicos para nao deixar o servidor parado...
goto :religar

:fail
echo.
echo ERRO no deploy - veja a mensagem acima. Os servicos podem estar PARADOS.
echo Tentando religar os servicos...

:religar
nssm start OrcaView-OS-API     >nul 2>&1
nssm start OrcaView-MCP        >nul 2>&1
nssm start OrcaView-Scheduler  >nul 2>&1
nssm start OrcaView-WBC-Painel >nul 2>&1
if defined WORKER_ATIVO nssm start OrcaView-WBC-Worker >nul 2>&1
pause
exit /b 1
