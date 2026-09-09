@echo off
REM ============================================================================
REM  deploy_update.bat - Atualiza o ServidorIntegracaoSAP em producao (.11) via git.
REM
REM  Roda NO servidor 192.168.7.11, na raiz C:\Python\ServidorIntegracaoSAP.
REM  Fluxo: para os 5 servicos -> git pull --ff-only -> pip (so se o conteudo dos
REM  requirements difere do ultimo instalado, marca em state\deps.sha256; no venv se
REM  houver, senao no Python do sistema) -> sobe os servicos na ordem certa ->
REM  confere /health da API e a porta do painel WBC.
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

REM --- roda de uma COPIA em %TEMP%: o "git pull" abaixo reescreve ESTE .bat no meio da
REM     execucao, e o cmd.exe le o .bat por posicao de byte - a partir dali executa linhas
REM     da versao nova em posicoes da antiga (foi assim que "ja estava atualizado" apareceu
REM     junto de um fast-forward em 08/09/2026). A copia nao muda durante o pull.
if /i not "%~1"=="--copia" (
  copy /y "%~f0" "%TEMP%\deploy_update_run.bat" >nul || (
    echo ERRO: nao consegui copiar o script para %TEMP%.
    pause & exit /b 1
  )
  call "%TEMP%\deploy_update_run.bat" --copia "%~dp0"
  exit /b %errorlevel%
)
set "ROOT=%~2"

set "REPO=https://github.com/MarceloProjetos/oportunidade_wbc.git"
set "BRANCH=master"

REM --- ir para a raiz do repo (a pasta do .bat original, recebida como argumento) ---
cd /d "%ROOT%"
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
  REM Parada por arquivo (09/09/2026): o python.exe direto no servico nao tem console, o
  REM Ctrl+C do NSSM nao chega e o worker era morto no meio do ciclo (trava presa 30 min,
  REM execucao "em andamento" para sempre). O worker ve o arquivo entre orcamentos e entre
  REM ciclos, termina o que esta fazendo e sai; ele mesmo apaga o arquivo ao religar.
  if not exist "state" mkdir "state"
  echo parada pedida pelo deploy_update.bat em %DATE% %TIME%> "state\wbc_worker.stop"
  echo [nssm] parando o worker WBC ^(state\wbc_worker.stop gravado; termina o ciclo atual; espera ate 90 s^)...
  nssm stop OrcaView-WBC-Worker >nul 2>&1
  call :esperar_parar OrcaView-WBC-Worker 90
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

REM --- dependencias: instalar quando o CONTEUDO dos requirements difere do que foi
REM     instalado da ultima vez (hash em state\deps.sha256). Nao depende de como o pull
REM     aconteceu: um "git pull" feito a mao antes do deploy nao esconde mais uma
REM     dependencia nova (foi assim que o painel WBC subiu sem fastapi em 08/09/2026).
REM     venv\ se existir; senao, o Python do sistema (e o caso da .11: Python312, sem venv).
set "PYEXE=python"
if exist "venv\Scripts\python.exe" set "PYEXE=venv\Scripts\python.exe"
set "REQHASH="
for /f "usebackq delims=" %%h in (`%PYEXE% -c "import hashlib;print(hashlib.sha256(open('requirements.txt','rb').read()+open('mcp/requirements.txt','rb').read()).hexdigest())" 2^>nul`) do set "REQHASH=%%h"
set "INSTALADO="
if exist "state\deps.sha256" set /p INSTALADO=<"state\deps.sha256"
if not defined REQHASH (
  echo [pip] AVISO: nao calculei o hash dos requirements ^(python no PATH?^); decidindo so pelo git.
) else if not "!INSTALADO!"=="!REQHASH!" (
  set "REQCHANGED=1"
)
if defined REQCHANGED (
  where %PYEXE% >nul 2>&1 || (echo ERRO: python nao encontrado no PATH. & goto :fail)
  echo [pip] dependencias diferentes das instaladas; instalando com %PYEXE% ...
  %PYEXE% -m pip install -r requirements.txt -r mcp\requirements.txt || goto :fail
  if not exist "state" mkdir "state"
  if defined REQHASH >"state\deps.sha256" echo !REQHASH!
  echo [pip] instalado; marca gravada em state\deps.sha256
) else (
  echo [pip] dependencias iguais as instaladas - mantidas.
)

REM --- subir servicos (API antes do MCP, que depende dela; os outros independem) ---
echo [nssm] subindo servicos...
nssm start OrcaView-OS-API     >nul 2>&1
nssm start OrcaView-MCP        >nul 2>&1
nssm start OrcaView-Scheduler  >nul 2>&1
nssm start OrcaView-WBC-Painel >nul 2>&1
if defined WORKER_ATIVO (
  REM Um "nssm start" durante STOP_PENDING e recusado em silencio (aconteceu em 08/09/2026:
  REM o worker ficou parado depois do deploy). Por isso a espera acima e esta confirmacao.
  call :esperar_parar OrcaView-WBC-Worker 30
  nssm start OrcaView-WBC-Worker >nul 2>&1
  sc query OrcaView-WBC-Worker | find "RUNNING" >nul 2>&1 || (
    echo [nssm] AVISO: OrcaView-WBC-Worker NAO subiu - rode: nssm start OrcaView-WBC-Worker
  )
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
if defined WORKER_ATIVO (
  call :esperar_parar OrcaView-WBC-Worker 30
  nssm start OrcaView-WBC-Worker >nul 2>&1
)
pause
exit /b 1

REM --- espera o servico %1 chegar a STOPPED, no maximo %2 segundos (sc query e ANSI) ---
:esperar_parar
set /a _ESPERA=0
:esperar_parar_loop
sc query %1 2>nul | find "STOPPED" >nul 2>&1 && goto :eof
sc query %1 >nul 2>&1 || goto :eof
set /a _ESPERA+=1
if %_ESPERA% geq %2 (
  echo [nssm] AVISO: %1 nao chegou a STOPPED em %2 s ^(sc query^).
  goto :eof
)
timeout /t 1 /nobreak >nul
goto :esperar_parar_loop
