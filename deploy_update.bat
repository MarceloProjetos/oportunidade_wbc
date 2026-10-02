@echo off
REM ============================================================================
REM  deploy_update.bat - Atualiza o ServidorIntegracaoSAP em producao (.11) via git.
REM
REM  Roda NO servidor 192.168.7.11, na raiz C:\Python\ServidorIntegracaoSAP.
REM  Fluxo: confere que o Controle de Producao nao tem execucao em andamento -> para os
REM  git fetch (antes de parar) -> 6 servicos -> git merge --ff-only -> pip (so se o conteudo dos requirements difere do
REM  ultimo instalado, marca em state\deps.sha256; no venv se houver, senao no Python do
REM  sistema) -> sobe os servicos na ordem certa -> confere /health da API, a porta do
REM  painel WBC e o /health do Controle de Producao.
REM
REM  Servicos (NSSM): OrcaView-MCP, OrcaView-OS-API, OrcaView-Scheduler,
REM  OrcaView-WBC-Painel, OrcaView-ControleProducao e OrcaView-WBC-Worker. O WORKER so
REM  volta a subir se estava rodando antes do deploy: antes da virada ele fica parado, e o
REM  deploy nao pode ser a porta dos fundos que liga o integrador novo.
REM  O Controle de Producao NAO e parado com tarefa "executando"/"na fila": o deploy
REM  ABORTA (parar no meio de um processar deixa OPs criadas pela metade no SAP).
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

REM --- registro do deploy (02/10/2026): uma linha por etapa em logs\deploy.log, que a API le
REM     (GET /operacao/deploy; ferramenta ultimo_deploy do MCP). Antes so ficava esta janela.
if not exist "logs" mkdir "logs"
call :registrar inicio "%USERNAME% em %COMPUTERNAME%"

REM --- o worker WBC estava rodando? (sc query e ANSI; a saida do nssm e UTF-16 e nao parseia) ---
set "WORKER_ATIVO="
sc query OrcaView-WBC-Worker 2>nul | find "RUNNING" >nul 2>&1 && set "WORKER_ATIVO=1"
if defined WORKER_ATIVO (
  echo [nssm] OrcaView-WBC-Worker esta RODANDO: sera parado ^(espera o ciclo terminar^) e religado no fim.
) else (
  echo [nssm] OrcaView-WBC-Worker parado ou nao instalado: continua parado apos o deploy.
)

REM --- Controle de Producao com execucao em andamento? Entao NAO e hora de deploy.
REM     /health/ocupado responde "1" (tarefa executando ou na fila) ou "0". A guarda FALHA
REM     FECHADA: so segue com um "0" literal. Sem resposta (curl expirou - o loop do uvicorn
REM     fica preso numa consulta HANA/SQL Server sincrona justamente durante um processar)
REM     com o servico RUNNING = aborta; servico parado/inexistente = nada a proteger, segue.
REM     Rota aberta (sem chave). 20 s de espera: um bloqueio normal do loop dura segundos, e
REM     com o servico parado o curl falha na hora.
set "CP_PORTA=8080"
for /f "usebackq tokens=2 delims== " %%p in (`findstr /b /i "CP_PORTA=" .env 2^>nul`) do set "CP_PORTA=%%p"
set "CP_OCUPADO="
for /f "usebackq delims=" %%o in (`curl -s -m 20 http://127.0.0.1:%CP_PORTA%/health/ocupado 2^>nul`) do set "CP_OCUPADO=%%o"
if "%CP_OCUPADO%"=="1" (
  echo.
  echo ERRO: o Controle de Producao tem execucao em andamento ^(http://127.0.0.1:%CP_PORTA%/tarefas^).
  echo       Parar agora deixaria Ordens de Producao pela metade no SAP. Espere terminar e rode de novo.
  echo       Nada foi alterado.
  call :registrar abortado "Controle de Producao com execucao em andamento - nada alterado"
  pause & exit /b 1
)
if not "%CP_OCUPADO%"=="0" (
  sc query OrcaView-ControleProducao 2>nul | find "RUNNING" >nul 2>&1 && (
    echo.
    echo ERRO: OrcaView-ControleProducao esta RODANDO mas nao respondeu em
    echo       http://127.0.0.1:%CP_PORTA%/health/ocupado em 20 s. Pode ser uma execucao presa numa
    echo       consulta longa. Confira /tarefas e CP_PORTA no .env; se for travamento, pare a mao
    echo       ^(nssm stop OrcaView-ControleProducao^) e rode de novo. Nada foi alterado.
    call :registrar abortado "Controle de Producao nao respondeu /health/ocupado - nada alterado"
    pause & exit /b 1
  )
  echo [cp] OrcaView-ControleProducao parado ou nao instalado: nada a proteger.
)

REM --- baixar o codigo novo ANTES de parar qualquer servico (01/10/2026): o fetch e o unico passo
REM     que precisa de rede. A .11 ficou sem resolver github.com e o deploy, que parava os 6
REM     servicos primeiro, derrubou tudo a toa. Agora sem GitHub o deploy para aqui, com tudo no
REM     ar; depois da parada, a atualizacao e so local (merge do que ja foi baixado). ---
if exist ".git" (
  echo [git] baixando origin/%BRANCH% ^(antes de parar os servicos^)...
  git fetch origin %BRANCH%
  if errorlevel 1 (
    echo.
    echo ERRO: nao foi possivel baixar o codigo do GitHub ^(rede, DNS ou proxy - veja a mensagem acima^).
    echo       NADA foi parado nem alterado: os servicos continuam no ar com a versao atual.
    echo       Confira o acesso a github.com nesta maquina e rode o deploy de novo.
    call :registrar abortado "git fetch falhou - rede, DNS ou proxy; nada parado"
    pause & exit /b 1
  )
)

REM --- parar servicos antes de mexer nos arquivos (Controle de Producao 1o; MCP antes da API, que ele usa;
REM     o worker por ultimo, porque a parada dele espera o ciclo em andamento) ---
echo [nssm] parando servicos...
REM Controle de Producao FIRST (30/09/2026): the ocupado check above is only true for the
REM moment it ran. Stopped last, it stayed up through four other stops (each .bat-wrapped
REM service waits its own stop timeout) - seconds in which a Liberar or processar clicked
REM on the screen would be killed mid-way.
nssm stop OrcaView-ControleProducao >nul 2>&1
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
  call :registrar git "1a execucao - alinhado com origin/%BRANCH%"
) else (
  echo [git] aplicando origin/%BRANCH% ^(ja baixado^)...
  for /f %%i in ('git rev-parse HEAD') do set "BEFORE=%%i"
  REM No network here: origin/%BRANCH% was fetched before the services stopped.
  git merge --ff-only origin/%BRANCH%              || goto :pullfail
  for /f %%i in ('git rev-parse HEAD') do set "AFTER=%%i"
  call :registrar git "de !BEFORE! para !AFTER!"
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
REM     venv\ se existir; senao, o Python do sistema (e o caso da .11: Python314, sem venv).
set "PYEXE=python"
if exist "venv\Scripts\python.exe" set "PYEXE=venv\Scripts\python.exe"
REM The worker runs the python.exe written into its NSSM service at install time; the other
REM services take "python" from the PATH. If they differ, pip below installs into the PATH
REM one only and the worker may start without a new dependency (14/09/2026). Warn - the
REM reg query output is ANSI, unlike nssm's UTF-16.
set "PY_WORKER="
for /f "tokens=2,*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Services\OrcaView-WBC-Worker\Parameters" /v Application 2^>nul ^| findstr /i "Application"') do set "PY_WORKER=%%b"
set "PY_PATH="
for /f "delims=" %%w in ('where python 2^>nul') do if not defined PY_PATH set "PY_PATH=%%w"
if defined PY_WORKER if defined PY_PATH if /i not "!PY_WORKER!"=="!PY_PATH!" (
  echo [pip] AVISO: o worker roda "!PY_WORKER!", mas o pip usa "!PY_PATH!".
  echo       Dependencia nova instalada aqui NAO chega ao worker. Ver CLAUDE.md, "O worker chama o python.exe por caminho absoluto".
)
set "REQHASH="
for /f "usebackq delims=" %%h in (`%PYEXE% -c "import hashlib;print(hashlib.sha256(open('requirements.txt','rb').read()+open('mcp/requirements.txt','rb').read()).hexdigest())" 2^>nul`) do set "REQHASH=%%h"
set "INSTALADO="
if exist "state\deps.sha256" set /p INSTALADO=<"state\deps.sha256"
if not defined REQHASH (
  echo [pip] AVISO: nao calculei o hash dos requirements ^(python no PATH?^); decidindo so pelo git.
) else (
  REM With the hash available it alone decides: the git heuristic above also matches
  REM requirements-dev.txt, which is never installed here (pip for nothing).
  set "REQCHANGED="
  if not "!INSTALADO!"=="!REQHASH!" set "REQCHANGED=1"
)
if defined REQCHANGED (
  where %PYEXE% >nul 2>&1 || (echo ERRO: python nao encontrado no PATH. & goto :fail)
  echo [pip] dependencias diferentes das instaladas; instalando com %PYEXE% ...
  %PYEXE% -m pip install -r requirements.txt -r mcp\requirements.txt || goto :pipfail
  if not exist "state" mkdir "state"
  if defined REQHASH >"state\deps.sha256" echo !REQHASH!
  echo [pip] instalado; marca gravada em state\deps.sha256
  call :registrar pip "dependencias instaladas"
) else (
  echo [pip] dependencias iguais as instaladas - mantidas.
  call :registrar pip "dependencias mantidas"
)

REM --- subir servicos (API antes do MCP, que depende dela; os outros independem) ---
echo [nssm] subindo servicos...
nssm start OrcaView-OS-API     >nul 2>&1
nssm start OrcaView-MCP        >nul 2>&1
nssm start OrcaView-Scheduler  >nul 2>&1
nssm start OrcaView-WBC-Painel >nul 2>&1
nssm start OrcaView-ControleProducao >nul 2>&1
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

REM --- validacao: o veredito final depende destas tres respostas (01/10/2026: antes o
REM     "DEPLOY OK" saia sempre, mesmo com a API fora do ar) ---
set "PAINEL_PORTA=8079"
for /f "usebackq tokens=2 delims== " %%p in (`findstr /b /i "PAINEL_PORTA=" .env 2^>nul`) do set "PAINEL_PORTA=%%p"
set "API_PORTA=8077"
for /f "usebackq tokens=2 delims== " %%p in (`findstr /b /i "OS_API_PORT=" .env 2^>nul`) do set "API_PORTA=%%p"
echo [health] aguardando a API e o painel subirem...
timeout /t 4 >nul
set "FALHOU="
call :conferir "API 8077" http://127.0.0.1:%API_PORTA%/health 200
call :conferir "painel WBC" http://127.0.0.1:%PAINEL_PORTA%/entrar 200
call :conferir "controle producao" http://127.0.0.1:%CP_PORTA%/health 200
nssm status OrcaView-OS-API
nssm status OrcaView-MCP
nssm status OrcaView-Scheduler
nssm status OrcaView-WBC-Painel
nssm status OrcaView-ControleProducao
nssm status OrcaView-WBC-Worker

echo.
if defined FALHOU (
  call :registrar fim "AVISOS - nao respondeu: !FALHOU!"
  echo ===== DEPLOY TERMINOU COM AVISOS: !FALHOU!nao respondeu como esperado =====
  echo       O codigo novo esta no lugar. Veja logs\ e "nssm status" acima antes de seguir.
  pause
  exit /b 2
)
call :registrar fim "OK"
echo ===== DEPLOY OK =====
pause
exit /b 0

:pipfail
call :registrar erro "pip falhou - codigo volta para !BEFORE!"
echo.
echo ERRO: o pip falhou. Voltando o codigo para o commit anterior ^(!BEFORE!^) para os
echo       servicos nao subirem com codigo novo e dependencias velhas...
if defined BEFORE (
  git reset --hard !BEFORE! && echo [git] codigo de volta em !BEFORE!. Corrija o pip e rode o deploy de novo.
) else (
  echo [git] AVISO: sem commit anterior conhecido ^(1a execucao^); o codigo novo fica.
)
goto :religar

:pullfail
call :registrar erro "git merge --ff-only falhou - alteracoes locais"
echo.
echo ERRO: git merge --ff-only falhou. Ha alteracoes locais em arquivos versionados
echo       nesta pasta que impedem o fast-forward. Nada foi alterado.
echo       Para descartar as mudancas locais e forcar o estado do remoto:
echo           git reset --hard origin/%BRANCH%
echo       (Isso NAO apaga venv\, .env, .env.*, logs\ nem state\.)
echo [nssm] religando os servicos para nao deixar o servidor parado...
goto :religar

:fail
call :registrar erro "falha no deploy - ver a janela"
echo.
echo ERRO no deploy - veja a mensagem acima. Os servicos podem estar PARADOS.
echo Tentando religar os servicos...

:religar
nssm start OrcaView-OS-API     >nul 2>&1
nssm start OrcaView-MCP        >nul 2>&1
nssm start OrcaView-Scheduler  >nul 2>&1
nssm start OrcaView-WBC-Painel >nul 2>&1
nssm start OrcaView-ControleProducao >nul 2>&1
if defined WORKER_ATIVO (
  call :esperar_parar OrcaView-WBC-Worker 30
  nssm start OrcaView-WBC-Worker >nul 2>&1
)
call :registrar fim "ERRO - servicos religados"
pause
exit /b 1

REM --- confere que a URL %2 responde HTTP %3; senao, acrescenta %1 em FALHOU (ate 3 tentativas,
REM     o servico pode estar terminando de subir) ---
:conferir
set "_COD="
for /l %%t in (1,1,3) do (
  if not "!_COD!"=="%3" (
    for /f "usebackq delims=" %%c in (`curl -s -L -m 10 -o nul -w "%%{http_code}" %2 2^>nul`) do set "_COD=%%c"
    if not "!_COD!"=="%3" timeout /t 3 /nobreak >nul
  )
)
echo [health] %~1: %2 -^> HTTP !_COD!
call :registrar health "%~1 HTTP !_COD!"
if not "!_COD!"=="%3" set "FALHOU=!FALHOU!%~1; "
goto :eof

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

REM --- acrescenta "<data> <hora> | %1 | %~2" em logs\deploy.log (lido por operacao/versao.py).
REM     Texto so ASCII e sem ! ^< ^> ^| ^& : passa por expansao atrasada e por echo. ---
:registrar
>>"logs\deploy.log" echo %DATE% %TIME% ^| %~1 ^| %~2
goto :eof
