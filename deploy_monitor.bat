@echo off
REM ============================================================================
REM  Deploy do monitor da tarefa "Integracao WBC".
REM  Rode como Administrador (botao direito > Executar como administrador).
REM  Repassa qualquer argumento ao deploy_monitor.ps1
REM  (ex.: deploy_monitor.bat -SkipRestart  ou  -Dest "C:\Python\oportunidade_wbc").
REM ============================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy_monitor.ps1" %*
echo.
pause
