@echo off
REM ===========================================================================
REM  ajustar_logs_nssm.bat - os logs do NSSM param de acumular.
REM
REM  Roda NA .11 (192.168.7.11), como Administrador.
REM
REM  O PROBLEMA: o NSSM nao apaga log, ele RENOMEIA. Com AppRotateFiles 1 +
REM  AppRotateBytes 5000000, ao passar de 5 MB o arquivo vira
REM  api_service-2026-09-11T07-55-00.log e comeca outro - e o renomeado fica
REM  para sempre. O NSSM nao tem opcao de "guardar so N": nao existe retencao.
REM
REM  A CORRECAO: sem rotacao nenhuma (AppRotateFiles 0) e
REM  AppStdoutCreationDisposition = 2 (CREATE_ALWAYS), que faz o NSSM TRUNCAR o
REM  arquivo a cada start do servico. O default e 4 (OPEN_ALWAYS = append).
REM  Assim nunca existe arquivo renomeado, e cada log guarda no maximo o que
REM  saiu desde o ultimo start. Como a .11 reinicia todo dia (~06:12), na
REM  pratica e um dia.
REM
REM  NAO SE PERDE HISTORICO: o que importa esta no log do PYTHON, que cada
REM  servico escreve por conta propria e que se limpa sozinho -
REM  logs\api.log e logs\scheduled_execution.log (6 dias) e logs\wbcpython.log
REM  (5 MB x 3). O arquivo do NSSM e' so o stdout/stderr cru, que serve para
REM  crash antes do logging subir.
REM
REM  VALE NO PROXIMO START do servico. Este script NAO reinicia nada de
REM  proposito - o reboot diario ou o proximo deploy_update.bat ja aplicam.
REM
REM  ASCII de proposito (o .bat e lido como ANSI).
REM ===========================================================================
setlocal enabledelayedexpansion

where nssm >nul 2>nul
if errorlevel 1 (
  echo ERRO: NSSM nao encontrado no PATH.
  exit /b 1
)

set "SERVICOS=OrcaView-OS-API OrcaView-Scheduler OrcaView-WBC-Painel OrcaView-WBC-Worker"

echo === Ajustando a captura de log do NSSM ===
echo.

for %%S in (%SERVICOS%) do (
  sc query %%S >nul 2>&1
  if errorlevel 1 (
    echo   %%S: NAO INSTALADO - pulado
  ) else (
    echo   %%S ...
    nssm set %%S AppRotateFiles 0 >nul 2>&1 || echo      AVISO: falhou AppRotateFiles
    nssm reset %%S AppRotateBytes >nul 2>&1
    nssm set %%S AppStdoutCreationDisposition 2 >nul 2>&1 || echo      AVISO: falhou AppStdoutCreationDisposition ^(nssm antigo?^)
    nssm set %%S AppStderrCreationDisposition 2 >nul 2>&1 || echo      AVISO: falhou AppStderrCreationDisposition ^(nssm antigo?^)
  )
)

echo.
echo === Conferindo o que ficou gravado ===
for %%S in (%SERVICOS%) do (
  sc query %%S >nul 2>&1
  if not errorlevel 1 (
    for /f "usebackq delims=" %%v in (`nssm get %%S AppStdoutCreationDisposition 2^>nul`) do (
      echo   %%S AppStdoutCreationDisposition = %%v   ^(2 = trunca no start^)
    )
  )
)

echo.
echo === Renomeados que ja existem (do jeito antigo) ===
set "ALVO=%~dp0..\logs"
set /a ACHADOS=0
for %%F in ("%ALVO%\*_service-*.log") do (
  set /a ACHADOS+=1
  echo   %%~nxF  ^(%%~zF bytes^)
)
if !ACHADOS!==0 (
  echo   nenhum - nada a limpar.
) else (
  echo.
  echo   !ACHADOS! arquivo^(s^). Para apagar: maintenance\disco_limpeza.ps1 -Confirmar
)

echo.
echo Feito. Vale no PROXIMO start de cada servico ^(reboot diario ou deploy_update.bat^).
endlocal
