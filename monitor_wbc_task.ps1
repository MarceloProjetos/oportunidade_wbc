<#
.SYNOPSIS
    Monitora a tarefa agendada "Integracao WBC" (Task Scheduler do Windows) e grava o
    estado em um JSON que a API (/status, porta 8077) apenas le.

.DESCRIPTION
    Feito para rodar a cada 10 min pelo Task Scheduler (ver install_monitor_task.ps1).
    Consulta State / LastRunTime / LastTaskResult / NumberOfMissedRuns da tarefa e deriva
    uma lista de problemas ("problems") + um flag "healthy". A API le esse arquivo e
    transforma os problemas em alertas do /status (503 no ?strict=1).

    O script NUNCA lanca excecao para fora: qualquer falha ao consultar a tarefa vira um
    "problem" no proprio JSON. So retorna exit 1 se nao conseguir gravar o arquivo de estado.

    Observacao de encoding: este .ps1 e mantido 100% ASCII de proposito. O PowerShell 5.1
    le arquivos .ps1 sem BOM como ANSI, o que corromperia caracteres acentuados. O nome da
    tarefa (com acentos) e montado em tempo de execucao via codigos de caractere e gravado
    corretamente como UTF-8 no JSON de saida.

.NOTES
    Idempotente e sem estado: cada execucao sobrescreve o JSON (escrita atomica, UTF-8 sem BOM).
#>
[CmdletBinding()]
param(
    # Default "Integracao WBC" com os acentos montados via codigos de caractere (c-cedilha,
    # a-til) para manter o arquivo ASCII. Sobrescreva com -TaskName se o nome mudar.
    [string]$TaskName = ("Integra{0}{1}o WBC" -f [char]0x00E7, [char]0x00E3),

    # Arquivo de estado lido pela API. Default alinhado ao WBC_TASK_STATE_FILE (config.py).
    [string]$StateFile = (Join-Path $PSScriptRoot 'state\wbc_task_state.json'),

    # Instancia em "Running" por mais que isto (min) = provavel travamento.
    [int]$RunningMaxMin = 10,

    # Sem execucao por mais que isto (min) e sem estar "Running" = nao esta disparando.
    [int]$NotRunMaxMin = 15
)

$ErrorActionPreference = 'Stop'

# Log proprio do monitor (mesma pasta do estado). Registra o resultado de cada execucao
# e — crucial — a EXCECAO REAL quando a gravacao falha, para um exit 1 nunca ser silencioso.
$LogFile = Join-Path (Split-Path -Parent $StateFile) 'monitor_wbc_task.log'

function Write-MonitorLog {
    param([string]$Message)
    try {
        $dir = Split-Path -Parent $LogFile
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        $line = "[{0}] (user={1}) {2}" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $env:USERNAME, $Message
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
        $lines = @(Get-Content -LiteralPath $LogFile -ErrorAction SilentlyContinue)
        if ($lines.Count -gt 200) {
            Set-Content -LiteralPath $LogFile -Value ($lines[-120..-1]) -Encoding UTF8
        }
    }
    catch { }  # o log NUNCA derruba o monitor
}

# Rede de seguranca: loga qualquer erro terminante NAO tratado antes de o script morrer,
# para nunca mais termos um exit 1 sem pista (foi o caso do LastTaskResult=1 sem log).
trap {
    Write-MonitorLog ("ERRO NAO TRATADO: {0}: {1} | {2}" -f `
        $_.Exception.GetType().FullName, $_.Exception.Message, ($_.ScriptStackTrace -replace '\s+', ' '))
}

# Codigos de LastTaskResult que NAO representam falha (espelham monitoring.py).
$RESULT_SUCCESS   = 0        # 0x0        concluiu com sucesso
$RESULT_RUNNING   = 267009   # 0x00041301 em execucao no momento
$RESULT_NEVER_RUN = 267011   # 0x00041303 ainda nao executou

# 0x800710E0 (Win32 4320 = "operador/administrador recusou o pedido"). No Task Scheduler
# quase sempre = disparo sobreposto pulado (instancia anterior ainda rodava, com a regra
# "Do not start a new instance") ou End manual. Nao e falha do programa -> vira NOTA
# informativa (nao afeta healthy nem gera alerta), nao "problema".
$RESULT_REFUSED   = 2147946720

function Format-IsoLocal {
    param([datetime]$Value)
    $Value.ToString('yyyy-MM-ddTHH:mm:ss')
}

function Test-ValidDate {
    # LastRunTime/NextRunTime podem vir nulos ou com sentinela antigo (1899/1999).
    param($Value)
    return ($Value -is [datetime]) -and ($Value.Year -ge 2000)
}

$problems = New-Object System.Collections.Generic.List[string]
$notes = New-Object System.Collections.Generic.List[string]

$state = [ordered]@{
    task_name  = $TaskName
    found      = $false
    checked_at = (Format-IsoLocal (Get-Date))
    healthy    = $false
    problems   = @()
    notes      = @()
}

try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop

    $state.found = $true

    $stateStr = [string]$task.State                                  # Ready / Running / Disabled
    $enabled  = ($stateStr -ne 'Disabled') -and ($task.Settings.Enabled -ne $false)

    $lastRunValid = Test-ValidDate $info.LastRunTime
    $minutesSince = $null
    if ($lastRunValid) {
        $minutesSince = [int][math]::Round(((Get-Date) - $info.LastRunTime).TotalMinutes)
    }

    # [int64]: LastTaskResult e um HRESULT/exit code que pode passar do maximo de Int32
    # (ex.: 0x800710E0 = 2147946720). Int32 estouraria; Int64 comporta o valor.
    $result    = [int64]$info.LastTaskResult
    $resultHex = ('0x{0:X}' -f $result)
    $resultOk  = ($result -eq $RESULT_SUCCESS) -or ($result -eq $RESULT_RUNNING) -or ($result -eq $RESULT_NEVER_RUN)
    $missed    = [int]$info.NumberOfMissedRuns

    $state.state                 = $stateStr
    $state.enabled               = $enabled
    $state.last_run_time         = if ($lastRunValid) { Format-IsoLocal $info.LastRunTime } else { $null }
    $state.last_task_result      = $result
    $state.last_task_result_hex  = $resultHex
    $state.next_run_time         = if (Test-ValidDate $info.NextRunTime) { Format-IsoLocal $info.NextRunTime } else { $null }
    $state.number_of_missed_runs = $missed
    $state.minutes_since_last_run = $minutesSince

    # ---- Regras de saude ----
    if (-not $enabled) {
        $problems.Add('tarefa desabilitada') | Out-Null
    }
    if ($stateStr -eq 'Running') {
        if (($null -ne $minutesSince) -and ($minutesSince -gt $RunningMaxMin)) {
            $problems.Add("travada: em execucao ha $minutesSince min (limite $RunningMaxMin)") | Out-Null
        }
    }
    else {
        if (($null -ne $minutesSince) -and ($minutesSince -gt $NotRunMaxMin)) {
            $problems.Add("sem execucao ha $minutesSince min (limite $NotRunMaxMin)") | Out-Null
        }
    }
    if ($result -eq $RESULT_REFUSED) {
        $notes.Add("disparo agendado recusado ($resultHex) - instancia anterior ainda rodava (sobreposicao) ou tarefa encerrada manualmente; nao e falha do programa") | Out-Null
    }
    elseif (-not $resultOk) {
        $problems.Add("ultima execucao falhou (codigo $resultHex)") | Out-Null
    }
    if ($missed -gt 0) {
        $problems.Add("$missed gatilho(s) perdido(s)") | Out-Null
    }
}
catch {
    $problems.Add("falha ao consultar a tarefa: $($_.Exception.Message)") | Out-Null
}

# @(...) garante array JSON mesmo com 0 ou 1 elemento (o lado Python tambem se protege).
$state.problems = @($problems)
$state.notes    = @($notes)
$state.healthy  = $state.found -and ($problems.Count -eq 0)

# ---- Gravacao do estado (UTF-8 sem BOM) ----
# Preferencia: escrita atomica (tmp + move). Se o REPLACE do arquivo final falhar
# (ACL de arquivo, lock por leitor, etc.), cai para escrita direta — logando o motivo.
try {
    $dir = Split-Path -Parent $StateFile
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $json      = $state | ConvertTo-Json -Depth 4
    $tmp       = "$StateFile.tmp"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($tmp, $json, $utf8NoBom)
    try {
        Move-Item -LiteralPath $tmp -Destination $StateFile -Force
    }
    catch {
        Write-MonitorLog ("Move-Item falhou ({0}: {1}); tentando escrita direta." -f `
            $_.Exception.GetType().Name, $_.Exception.Message)
        [System.IO.File]::WriteAllText($StateFile, $json, $utf8NoBom)
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
    Write-MonitorLog ("OK gravado: healthy={0}, state={1}, result={2}" -f `
        $state.healthy, $state.state, $state.last_task_result_hex)
}
catch {
    Write-MonitorLog ("FALHA ao gravar '{0}': {1}: {2}" -f `
        $StateFile, $_.Exception.GetType().FullName, $_.Exception.Message)
    Write-Error "Falha ao gravar o estado em '$StateFile': $($_.Exception.Message)"
    exit 1
}

# Sai 0 mesmo com alerta: o problema e um DADO consumido pela API, nao uma falha do monitor.
$noteStr = if ($notes.Count -gt 0) { "  [notas: $([string]::Join('; ', @($notes)))]" } else { '' }
if ($state.healthy) {
    Write-Output "OK: tarefa '$TaskName' saudavel (State=$($state.state), last=$($state.minutes_since_last_run) min).$noteStr"
}
else {
    Write-Output "ALERTA: tarefa '$TaskName' -> $([string]::Join('; ', @($problems)))$noteStr"
}
exit 0
