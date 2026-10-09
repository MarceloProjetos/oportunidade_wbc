<#
Estado de um host Hyper-V (ALTSAP; o ALTHOST pode usar o mesmo), enviado a .11 (F5c3 do PLANO_TEO_REDE_E_ROTINAS do web).

READ ONLY: it only calls Get-* cmdlets, netsh/route are not used. It runs on the host as a scheduled task,
every hour, as SYSTEM, and POSTs one JSON to http://192.168.7.11:8077/operacao/hyperv/estado with a key of
scope hyperv:relatar (never an agent key). The .11 knows which host sent it by the origin address.
Every block is in its own try/catch: what could not be read goes to "erros" and the rest still goes.

What it reads: each VM (state, Heartbeat, uptime, CPU, memory), the checkpoints, each VM disk (differencing
.avhdx or not, chain length, size), the host volumes, the virtual switches and VMQ, the host's errors of the
last hour (System + Hyper-V admin logs, grouped), and IPv6 on the host (who announces it on the LAN).

ASCII only (PS 5.1 reads a BOM-less file as ANSI). The task calls it with -Command, never -File:
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& 'C:\OrcaView\estado_hyperv.ps1'"

First run (by a person, PowerShell as administrator), WITHOUT sending, to compare with the Hyper-V Manager:
  & 'C:\OrcaView\estado_hyperv.ps1' -SoMostrar
#>
param(
    [string]$Url = 'http://192.168.7.11:8077/operacao/hyperv/estado',
    [string]$ArquivoChave = 'C:\ProgramData\OrcaView\hyperv_chave.txt',
    [switch]$SoMostrar
)

$VERSAO = 1
$ITENS_MAX = 60
$ErrorActionPreference = 'Stop'
$saida = [ordered]@{
    versao_do_script = $VERSAO
    gerado_em = (Get-Date).ToString('s')
    host = $env:COMPUTERNAME
    vms = @()
    checkpoints = @()
    discos = @()
    volumes = @()
    switches = @()
    vmq = @()
    eventos = @()
    ipv6 = [ordered]@{ desligado = $null; descoberta_roteador = $null; rotas = @(); vizinhos = @(); enderecos = @() }
    erros = @()
}

function Erro([string]$onde, $excecao) {
    $msg = [string]$excecao.Exception.Message
    if ($msg.Length -gt 200) { $msg = $msg.Substring(0, 200) }
    $script:saida.erros += ($onde + ': ' + $msg)
}

function Gb($bytes) {
    if ($null -eq $bytes) { return $null }
    return [math]::Round([double]$bytes / 1GB, 1)
}

# 1. The VMs. Heartbeat "LostCommunication" = the guest Windows stopped answering the host.
$vms = @()
try {
    $vms = @(Get-VM)
    foreach ($v in $vms) {
        $saida.vms += [ordered]@{
            nome = [string]$v.Name
            estado = [string]$v.State
            status = [string]$v.Status
            heartbeat = [string]$v.Heartbeat
            uptime_h = [math]::Round($v.Uptime.TotalHours, 1)
            cpu_pct = [double]$v.CPUUsage
            memoria_gb = (Gb $v.MemoryAssigned)
        }
    }
} catch { Erro 'vms' $_ }

# 2. Checkpoints (a Veeam recovery checkpoint should live only during the replica job).
try {
    foreach ($c in ($vms | Get-VMSnapshot)) {
        $saida.checkpoints += [ordered]@{
            vm = [string]$c.VMName
            nome = [string]$c.Name
            tipo = [string]$c.SnapshotType
            criado_em = $c.CreationTime.ToString('s')
        }
    }
} catch { Erro 'checkpoints' $_ }

# 3. Each VM disk: the file in use, whether it is a differencing disk and how long its chain is.
try {
    foreach ($d in ($vms | Get-VMHardDiskDrive)) {
        # A pass-through disk has no Path: one disk must never cost the ones after it (review F5c3a).
        $item = [ordered]@{ vm = [string]$d.VMName; arquivo = $null; diferencial = $null; cadeia = $null
                            tamanho_gb = $null }
        try {
            if (-not $d.Path) { $saida.discos += $item; continue }
            $item.arquivo = Split-Path -Leaf ([string]$d.Path)
            try { $vhd = Get-VHD -Path $d.Path } catch { $vhd = Get-VHD -Path $d.Path -VMId $d.VMId }
            $item.diferencial = ([string]$vhd.VhdType -eq 'Differencing')
            $item.tamanho_gb = (Gb $vhd.FileSize)
            $n = 1
            $pai = $vhd.ParentPath
            while ($pai -and $n -lt 20) {
                $n++
                try { $pai = (Get-VHD -Path $pai).ParentPath } catch { $pai = $null }
            }
            $item.cadeia = $n
        } catch { Erro ('disco de ' + $item.vm) $_ }
        $saida.discos += $item
    }
} catch { Erro 'discos' $_ }

# 4. The host volumes (a differencing disk grows until the volume is full).
try {
    foreach ($v in (Get-Volume | Where-Object { $_.DriveLetter -and $_.DriveType -eq 'Fixed' })) {
        $saida.volumes += [ordered]@{ letra = [string]$v.DriveLetter; total_gb = (Gb $v.Size)
                                      livre_gb = (Gb $v.SizeRemaining) }
    }
} catch { Erro 'volumes' $_ }

# 5. Virtual switches and VMQ on the physical adapters.
try {
    foreach ($s in (Get-VMSwitch)) {
        $saida.switches += [ordered]@{ nome = [string]$s.Name; tipo = [string]$s.SwitchType
                                       placa = [string]$s.NetAdapterInterfaceDescription }
    }
} catch { Erro 'switches' $_ }
try {
    foreach ($q in (Get-NetAdapterVmq -ErrorAction SilentlyContinue)) {
        $saida.vmq += [ordered]@{ placa = [string]$q.Name; ligado = [bool]$q.Enabled }
    }
} catch { Erro 'vmq' $_ }

# 6. The host's errors of the last hour, grouped by log/source/id (System: critical and error; the
#    Hyper-V admin logs: warnings too).
$desde = (Get-Date).AddHours(-1)
$achados = @()
foreach ($consulta in @(
        @{ LogName = 'System'; Level = 1, 2; StartTime = $desde },
        @{ LogName = 'Microsoft-Windows-Hyper-V-VMMS-Admin', 'Microsoft-Windows-Hyper-V-Worker-Admin'
           Level = 1, 2, 3; StartTime = $desde })) {
    try {
        $achados += @(Get-WinEvent -FilterHashtable $consulta -MaxEvents 300 -ErrorAction Stop)
    } catch {
        if ([string]$_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { Erro 'eventos' $_ }
    }
}
foreach ($g in ($achados | Group-Object -Property LogName, ProviderName, Id | Sort-Object Count -Descending |
                Select-Object -First 20)) {
    $ultimo = $g.Group | Sort-Object TimeCreated -Descending | Select-Object -First 1
    $saida.eventos += [ordered]@{
        log = [string]$ultimo.LogName; fonte = [string]$ultimo.ProviderName; id = [int]$ultimo.Id
        nivel = [string]$ultimo.Level; n = [int]$g.Count; ultimo = $ultimo.TimeCreated.ToString('s')
    }
}

# 7. IPv6 on the host: is it off, who announces a default route, which link-local neighbours answer, which
#    addresses came from router advertisements (09/10: a device announced fd00::/fd32:: prefixes).
try {
    $reg = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters' `
        -Name DisabledComponents -ErrorAction SilentlyContinue
    $saida.ipv6.desligado = ($null -ne $reg -and ([uint32]$reg.DisabledComponents -band 0xFF) -eq 0xFF)
    $ifs = @(Get-NetIPInterface -AddressFamily IPv6 -ErrorAction SilentlyContinue |
             Where-Object { $_.InterfaceAlias -notlike 'Loopback*' })
    if ($ifs.Count -gt 0) {
        $saida.ipv6.descoberta_roteador = [bool](@($ifs | Where-Object { [string]$_.RouterDiscovery -eq 'Enabled' }).Count)
    }
    foreach ($r in @(Get-NetRoute -AddressFamily IPv6 -DestinationPrefix '::/0' -ErrorAction SilentlyContinue)) {
        $saida.ipv6.rotas += [ordered]@{ proximo = [string]$r.NextHop; placa = [string]$r.InterfaceAlias }
    }
    foreach ($n in @(Get-NetNeighbor -AddressFamily IPv6 -State Reachable, Stale -ErrorAction SilentlyContinue |
                     Where-Object { $_.IPAddress -like 'fe80*' } | Select-Object -First 10)) {
        $saida.ipv6.vizinhos += [ordered]@{ ip = [string]$n.IPAddress; mac = [string]$n.LinkLayerAddress }
    }
    foreach ($a in @(Get-NetIPAddress -AddressFamily IPv6 -ErrorAction SilentlyContinue |
                     Where-Object { $_.IPAddress -notlike 'fe80*' -and $_.IPAddress -ne '::1' } | Select-Object -First 10)) {
        $saida.ipv6.enderecos += [ordered]@{ ip = [string]$a.IPAddress; origem = [string]$a.PrefixOrigin }
    }
} catch { Erro 'ipv6' $_ }

# The .11 accepts at most $ITENS_MAX items per list: cut here and say so, never lose the whole report.
foreach ($lista in 'vms', 'checkpoints', 'discos', 'volumes', 'switches', 'vmq', 'eventos') {
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
# UTF-8 bytes: PS 5.1 would send a string body as ISO-8859-1 and break accented names.
$corpo = [System.Text.Encoding]::UTF8.GetBytes($json)
try {
    Invoke-RestMethod -Method Post -Uri $Url -Headers @{ 'X-API-Key' = $chave } -Body $corpo `
        -ContentType 'application/json; charset=utf-8' -TimeoutSec 30 | Out-Null
} catch {
    [Console]::Error.WriteLine('a .11 recusou ou nao respondeu: ' + $_.Exception.Message)
    exit 1
}
