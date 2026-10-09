<#
Estado dos backups do Veeam, lido no ALTHOST e enviado a .11 (F7 do PLANO_TEO_REDE_E_ROTINAS do web).

READ ONLY on the Veeam side: it only calls Get-* cmdlets. It runs on the ALTHOST (192.168.7.250, where the
Veeam console lives) as a scheduled task, every hour, as SYSTEM, and POSTs one JSON to
http://192.168.7.11:8077/operacao/backup/estado with a key of scope backup:relatar (never an agent key).
Every block is in its own try/catch: what could not be read goes to "erros" and the rest still goes.

ASCII only (PS 5.1 reads a BOM-less file as ANSI). The task calls it with -Command, never -File:
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& 'C:\OrcaView\estado_backup.ps1'"

First run (by a person, PowerShell as administrator), WITHOUT sending, to compare with the console:
  & 'C:\OrcaView\estado_backup.ps1' -SoMostrar
#>
param(
    [string]$Url = 'http://192.168.7.11:8077/operacao/backup/estado',
    [string]$ArquivoChave = 'C:\ProgramData\OrcaView\backup_chave.txt',
    [switch]$SoMostrar
)

$VERSAO = 2
$ITENS_MAX = 60
$ErrorActionPreference = 'Stop'
$saida = [ordered]@{
    versao_do_script = $VERSAO
    gerado_em = (Get-Date).ToString('s')
    host = $env:COMPUTERNAME
    jobs = @()
    replicas = @()
    repositorios = @()
    servicos = @()
    erros = @()
}

function Erro([string]$onde, $excecao) {
    $msg = [string]$excecao.Exception.Message
    if ($msg.Length -gt 200) { $msg = $msg.Substring(0, 200) }
    $script:saida.erros += ($onde + ': ' + $msg)
}

function ParaIso($valor) {
    # Veeam returns DateTime.MinValue (0001-01-01) for "not finished": that is no date.
    if ($null -eq $valor) { return $null }
    try { $d = [datetime]$valor } catch { return $null }
    if ($d.Year -lt 2000) { return $null }
    return $d.ToString('s')
}

# 1. The Veeam services on this machine (they answer even when the module does not load).
try {
    Get-Service -Name 'Veeam*' | ForEach-Object {
        $saida.servicos += [ordered]@{ nome = $_.Name; estado = [string]$_.Status }
    }
} catch { Erro 'servicos' $_ }

# 2. The module (v11+: Veeam.Backup.PowerShell; older: the snap-in) and the local server session.
$moduloOk = $false
try {
    Import-Module Veeam.Backup.PowerShell -WarningAction SilentlyContinue
    $moduloOk = $true
} catch {
    try { Add-PSSnapin VeeamPSSnapin; $moduloOk = $true } catch { Erro 'modulo do Veeam' $_ }
}
if ($moduloOk) {
    try {
        if (-not (Get-VBRServerSession -ErrorAction SilentlyContinue)) { Connect-VBRServer -Server localhost }
    } catch { Erro 'conexao ao servidor do Veeam' $_ }
}

if ($moduloOk) {
    # 3. Each job and its last session.
    try {
        foreach ($j in (Get-VBRJob -WarningAction SilentlyContinue)) {
            $s = $null
            try { $s = $j.FindLastSession() } catch { $s = $null }
            $item = [ordered]@{
                nome = [string]$j.Name
                tipo = [string]$j.JobType
                habilitado = [bool]$j.IsScheduleEnabled
                resultado = $null; estado = $null; inicio = $null; fim = $null
            }
            if ($s) {
                $item.resultado = [string]$s.Result
                $item.estado = [string]$s.State
                $item.inicio = ParaIso $s.CreationTime
                $item.fim = ParaIso $s.EndTime
            }
            $saida.jobs += $item
        }
    } catch { Erro 'jobs' $_ }

    # 4. The newest restore point of each replicated VM. Get-VBRReplica is the documented way; older
    # builds only list them through Get-VBRBackup.
    try {
        $reps = @()
        try { $reps = @(Get-VBRReplica -WarningAction SilentlyContinue) } catch { $reps = @() }
        if ($reps.Count -eq 0) { $reps = @(Get-VBRBackup | Where-Object { [string]$_.JobType -eq 'Replica' }) }
        foreach ($b in $reps) {
            $pontos = @(Get-VBRRestorePoint -Backup $b)
            foreach ($grupo in ($pontos | Group-Object -Property Name)) {
                $ultimo = $grupo.Group | Sort-Object -Property CreationTime -Descending | Select-Object -First 1
                $saida.replicas += [ordered]@{ job = [string]$b.JobName; vm = [string]$grupo.Name
                                               ultimo_ponto = (ParaIso $ultimo.CreationTime) }
            }
        }
    } catch { Erro 'replicas' $_ }
    # A replica job with no replica read is a reading failure, not "nothing to report" (review F7a).
    $jobsDeReplica = @($saida.jobs | Where-Object { $_.tipo -match 'Replica' }).Count
    if ($jobsDeReplica -gt 0 -and $saida.replicas.Count -eq 0) {
        $saida.erros += ('replicas: nenhuma lida embora existam ' + $jobsDeReplica + ' job(s) de replica')
    }

    # 5. Free space of the backup repositories.
    try {
        foreach ($r in (Get-VBRBackupRepository)) {
            $c = $r.GetContainer()
            $saida.repositorios += [ordered]@{
                nome = [string]$r.Name
                total_gb = [math]::Round([double]$c.CachedTotalSpace.InGigabytes, 1)
                livre_gb = [math]::Round([double]$c.CachedFreeSpace.InGigabytes, 1)
            }
        }
    } catch { Erro 'repositorios' $_ }
}

# The .11 accepts at most $ITENS_MAX items per list: cut here and say so, never lose the whole report.
foreach ($lista in 'jobs', 'replicas', 'repositorios', 'servicos') {
    if ($saida[$lista].Count -gt $ITENS_MAX) {
        $saida.erros += ($lista + ': ' + $saida[$lista].Count + ' itens, so os primeiros ' + $ITENS_MAX + ' foram')
        $saida[$lista] = @($saida[$lista] | Select-Object -First $ITENS_MAX)
    }
}

$json = $saida | ConvertTo-Json -Depth 5 -Compress
if ($SoMostrar) {
    $saida | ConvertTo-Json -Depth 5
    return
}

try {
    $chave = (Get-Content -Path $ArquivoChave -Raw).Trim()
} catch {
    [Console]::Error.WriteLine('sem a chave em ' + $ArquivoChave)
    exit 2
}
# UTF-8 bytes: PS 5.1 would send a string body as ISO-8859-1 and break accented job names.
$corpo = [System.Text.Encoding]::UTF8.GetBytes($json)
try {
    Invoke-RestMethod -Method Post -Uri $Url -Headers @{ 'X-API-Key' = $chave } -Body $corpo `
        -ContentType 'application/json; charset=utf-8' -TimeoutSec 30 | Out-Null
} catch {
    [Console]::Error.WriteLine('a .11 recusou ou nao respondeu: ' + $_.Exception.Message)
    exit 1
}
