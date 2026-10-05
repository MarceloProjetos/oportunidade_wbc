<#
.SYNOPSIS
    Limpa logs antigos de C:\WindowsAzure\Logs (mantem apenas os de hoje). Feito para rodar
    1x por dia de manha via Task Scheduler. Manutencao do servidor .11 (nao e do app SAP).

.DESCRIPTION
    Apaga os ARQUIVOS cuja data de modificacao e anterior a hoje 00:00 (ou seja, do dia
    anterior para tras). Seguranca:
      - Arquivos EM USO (travados pelo agente do Azure) sao PULADOS, sem erro.
      - NUNCA apaga pastas, so arquivos.
      - Recusa caminho vazio ou raiz de disco.
    Use -Install para registrar a tarefa diaria (SYSTEM) que roda isto. Use -DryRun para
    ver o que SERIA apagado, sem apagar.

    ASCII de proposito (PowerShell 5.1 le .ps1 sem BOM como ANSI).

.PARAMETER Path
    Pasta a limpar. Default: C:\WindowsAzure\Logs.

.PARAMETER KeepDays
    Dias-calendario a manter (1 = so hoje; 2 = hoje e ontem; ...). Default 1.

.PARAMETER Recurse
    Tambem limpa subpastas. Default: somente a pasta indicada.

.PARAMETER DryRun
    So lista o que seria apagado (nao apaga nada).

.PARAMETER Install
    Em vez de limpar, registra/atualiza a tarefa diaria (idempotente) + roda uma limpeza inicial.

.PARAMETER Time
    Horario da tarefa diaria (HH:mm). Default 06:00.

.EXAMPLE
    # 1) Teste sem apagar:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\maintenance\clean_azure_logs.ps1 -DryRun

.EXAMPLE
    # 2) Registrar a limpeza diaria as 06:00 (como Administrador):
    powershell -NoProfile -ExecutionPolicy Bypass -File .\maintenance\clean_azure_logs.ps1 -Install
#>
[CmdletBinding()]
param(
    [string]$Path = 'C:\WindowsAzure\Logs',
    [int]$KeepDays = 1,
    [switch]$Recurse,
    [switch]$DryRun,
    [switch]$Install,
    [string]$Time = '06:00',
    [string]$TaskName = 'OrcaView-Clean-Azure-Logs'
)

$ErrorActionPreference = 'Stop'

function Write-CleanLog {
    param([string]$Message)
    try {
        $log = Join-Path $PSScriptRoot 'clean_azure_logs.log'
        $line = "[{0}] (user={1}) {2}" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $env:USERNAME, $Message
        Add-Content -LiteralPath $log -Value $line -Encoding UTF8
        $lines = @(Get-Content -LiteralPath $log -ErrorAction SilentlyContinue)
        if ($lines.Count -gt 300) { Set-Content -LiteralPath $log -Value ($lines[-200..-1]) -Encoding UTF8 }
    }
    catch { }
}

if ($Install) {
    $self = $PSCommandPath
    if (-not $self -or -not (Test-Path -LiteralPath $self)) { throw 'Caminho deste script nao resolvido.' }
    $selfEsc = $self -replace "'", "''"
    $pathEsc = $Path -replace "'", "''"
    $inner = "& '$selfEsc' -Path '$pathEsc' -KeepDays $KeepDays" + $(if ($Recurse) { ' -Recurse' } else { '' })
    $arg   = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"$inner`""

    $action    = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg
    $trigger   = New-ScheduledTaskTrigger -Daily -At ([datetime]$Time)
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew

    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Write-Host "Tarefa ja existe; removendo para recriar..."
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description "Limpa $Path diariamente (mantem KeepDays=$KeepDays dia(s))." | Out-Null

    Write-Host "Tarefa '$TaskName' registrada: diaria as $Time (SYSTEM), limpando '$Path' (KeepDays=$KeepDays, Recurse=$($Recurse.IsPresent))."
    Write-Host "Rodando uma limpeza inicial agora..."
    Start-ScheduledTask -TaskName $TaskName
    return
}

# ---- Limpeza ----
if ([string]::IsNullOrWhiteSpace($Path) -or $Path -match '^[A-Za-z]:\\?$') {
    throw "Path invalido/perigoso: '$Path'."
}
if (-not (Test-Path -LiteralPath $Path)) {
    Write-CleanLog "Path nao existe: $Path (nada a fazer)."
    Write-Host "Path nao existe: $Path"
    return
}

$cutoff = (Get-Date).Date.AddDays(-([math]::Max(1, $KeepDays) - 1))
$files = @(Get-ChildItem -LiteralPath $Path -File -Recurse:$Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt $cutoff })

if ($DryRun) {
    $totMB = [math]::Round((($files | Measure-Object Length -Sum).Sum) / 1MB, 1)
    Write-Host "[DryRun] $($files.Count) arquivo(s) SERIAM apagados de '$Path' (< $($cutoff.ToString('yyyy-MM-dd')), recurse=$($Recurse.IsPresent)) - $totMB MB:"
    $files | Sort-Object LastWriteTime | Select-Object LastWriteTime, @{n = 'MB'; e = { [math]::Round($_.Length / 1MB, 2) } }, FullName | Format-Table -AutoSize
    return
}

$deleted = 0; $freed = 0L; $skipped = 0
foreach ($f in $files) {
    try {
        $sz = $f.Length
        Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop
        $deleted++; $freed += $sz
    }
    catch {
        $skipped++   # provavelmente em uso pelo agente do Azure -> ignora
    }
}

$msg = "limpeza '$Path' (< $($cutoff.ToString('yyyy-MM-dd')), recurse=$($Recurse.IsPresent)): removidos=$deleted, liberado=$([math]::Round($freed/1MB,1))MB, pulados(em uso)=$skipped"
Write-CleanLog $msg
Write-Host "OK: $msg"
