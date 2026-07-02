<#
.SYNOPSIS
    Registra (ou re-registra) a tarefa agendada que roda monitor_wbc_task.ps1 a cada 10 min,
    e dispara uma execucao inicial. Rode UMA vez, no servidor, como Administrador.

.DESCRIPTION
    A tarefa do monitor roda como SYSTEM (LogonType ServiceAccount) com privilegio elevado:
    - dispensa armazenar senha de usuario;
    - SYSTEM enxerga e consulta a tarefa "Integracao WBC" (que roda como administrador);
    - "Run whether user is logged on or not" por padrao para ServiceAccount.

    Idempotente: se a tarefa do monitor ja existir, ela e removida e recriada.

.PARAMETER MonitorTaskName
    Nome da tarefa do MONITOR (nao confundir com a tarefa monitorada "Integracao WBC").

.PARAMETER IntervalMinutes
    Intervalo de repeticao (default 10 min).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install_monitor_task.ps1
#>
[CmdletBinding()]
param(
    [string]$MonitorTaskName = 'OrcaView-Monitor-WBC-Task',
    [int]$IntervalMinutes = 10
)

$ErrorActionPreference = 'Stop'

# Caminho absoluto do script de monitor (na mesma pasta deste instalador).
$scriptPath = Join-Path $PSScriptRoot 'monitor_wbc_task.ps1'
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "monitor_wbc_task.ps1 nao encontrado em '$scriptPath'."
}

Write-Host "Registrando tarefa '$MonitorTaskName' -> $scriptPath (a cada $IntervalMinutes min)..."

# Acao: powershell escondido rodando o monitor.
# IMPORTANTE: usa -Command "& '<script>'" e NAO -File. Sob SYSTEM + nao-interativo, o
# PowerShell 5.1 com -File falhava com exit 1 SEM executar nada (nem log gravava); via
# -Command o mesmo script roda corretamente (comprovado). As aspas simples sao dobradas
# para o caso raro de o caminho conter aspa simples.
$scriptPathEsc = $scriptPath -replace "'", "''"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ("-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -Command ""& '{0}'""" -f $scriptPathEsc)

# Gatilho: comeca agora e repete a cada N min, indefinidamente (janela de 10 anos ~ "para sempre").
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

# Roda como SYSTEM, com privilegio elevado.
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

# Uma execucao do monitor e rapida; corta em 5 min para nunca acumular instancias presas.
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

# Idempotencia: remove a versao anterior, se houver.
$existing = Get-ScheduledTask -TaskName $MonitorTaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Tarefa ja existe; removendo para recriar..."
    Unregister-ScheduledTask -TaskName $MonitorTaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $MonitorTaskName `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description 'Monitora a tarefa "Integracao WBC" e grava o estado para a API /status (porta 8077).' | Out-Null

Write-Host "Tarefa registrada. Disparando execucao inicial..."
Start-ScheduledTask -TaskName $MonitorTaskName

# Da um instante para a primeira execucao gravar o JSON e mostra o resultado.
Start-Sleep -Seconds 5
$stateFile = Join-Path $PSScriptRoot 'state\wbc_task_state.json'
if (Test-Path -LiteralPath $stateFile) {
    Write-Host "`nEstado inicial gravado em: $stateFile"
    Get-Content -LiteralPath $stateFile -Raw
}
else {
    Write-Warning "Estado ainda nao gravado. Verifique o Historico da tarefa '$MonitorTaskName' no Task Scheduler."
}

Write-Host "`nPronto. Confira em: http://localhost:8077/status?checks=tarefa"
