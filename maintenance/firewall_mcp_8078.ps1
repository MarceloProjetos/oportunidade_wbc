[Console]::OutputEncoding = [Text.Encoding]::UTF8
<#
  firewall_mcp_8078.ps1 - fecha a porta do MCP (8078) para todos, exceto os IPs permitidos.
  F1 de docs/PLANO_MIRA_AGENTE_11.md, regra 9 (02/10/2026). Roda NA .11, como administrador.

  Por que BLOQUEIO e nao so uma regra de liberacao: no Firewall do Windows uma regra de
  bloqueio vence qualquer liberacao - inclusive a liberacao por PROGRAMA do python.exe, que
  abre TODAS as portas do Python e costuma existir. Entao a regra "OrcaView-MCP-8078-bloqueio"
  bloqueia a 8078 para todos os enderecos MENOS os permitidos (os intervalos sao calculados).
  A propria .11 (127.0.0.1) nao passa pelo firewall e continua chegando.

  Uso:
    .\maintenance\firewall_mcp_8078.ps1                                   # so mostra (nao muda nada)
    .\maintenance\firewall_mcp_8078.ps1 -Permitidos 192.168.0.90,192.168.0.229 -Aplicar
    .\maintenance\firewall_mcp_8078.ps1 -Remover                          # desfaz

  Conferir de fora: de uma maquina NAO permitida, Test-NetConnection 192.168.7.11 -Port 8078
  deve falhar (TcpTestSucceeded False); do .90, deve passar.
#>
param(
    [string[]]$Permitidos = @("192.168.0.90"),
    [int]$Porta = 8078,
    [switch]$Aplicar,
    [switch]$Remover
)

$ErrorActionPreference = "Stop"
$Nome = "OrcaView-MCP-$Porta-bloqueio"

function Para-Numero([string]$ip) {
    $b = ([Net.IPAddress]::Parse($ip)).GetAddressBytes()
    return ([uint64]$b[0] -shl 24) + ([uint64]$b[1] -shl 16) + ([uint64]$b[2] -shl 8) + [uint64]$b[3]
}

function Para-Ip([uint64]$n) {
    return "{0}.{1}.{2}.{3}" -f (($n -shr 24) -band 255), (($n -shr 16) -band 255), (($n -shr 8) -band 255), ($n -band 255)
}

# Every IPv4 address except the allowed ones, as ranges "a-b".
function Intervalos-Exceto([string[]]$ips) {
    $numeros = $ips | ForEach-Object { Para-Numero $_ } | Sort-Object -Unique
    $faixas = @()
    $inicio = [uint64]0
    foreach ($n in $numeros) {
        if ($n -gt $inicio) { $faixas += "{0}-{1}" -f (Para-Ip $inicio), (Para-Ip ($n - 1)) }
        $inicio = $n + 1
    }
    if ($inicio -le [uint64]4294967295) { $faixas += "{0}-255.255.255.255" -f (Para-Ip $inicio) }
    return $faixas
}

Write-Host "== Regras de entrada que tocam a porta $Porta hoje =="
$porPorta = Get-NetFirewallPortFilter -Protocol TCP -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -contains "$Porta" } | Get-NetFirewallRule |
    Where-Object { $_.Direction -eq "Inbound" }
if ($porPorta) {
    $porPorta | ForEach-Object { Write-Host ("  {0,-40} acao={1} ativa={2}" -f $_.DisplayName, $_.Action, $_.Enabled) }
} else {
    Write-Host "  (nenhuma regra por porta)"
}
$python = Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue |
    Where-Object { $_.Program -like "*python*" } | Get-NetFirewallRule |
    Where-Object { $_.Direction -eq "Inbound" -and $_.Action -eq "Allow" -and $_.Enabled -eq "True" }
if ($python) {
    Write-Host ""
    Write-Host "  ATENCAO: liberacao por PROGRAMA do Python (abre todas as portas dele):"
    $python | ForEach-Object { Write-Host ("    {0}" -f $_.DisplayName) }
    Write-Host "  O bloqueio abaixo vence essa liberacao so na porta $Porta."
}
Write-Host ""

if ($Remover) {
    Remove-NetFirewallRule -DisplayName $Nome -ErrorAction SilentlyContinue
    Write-Host "Regra '$Nome' removida: a porta $Porta volta ao que estava antes."
    exit 0
}

foreach ($ip in $Permitidos) {
    try { [void][Net.IPAddress]::Parse($ip) } catch { Write-Host "ERRO: '$ip' nao e um IPv4 valido."; exit 1 }
}
$faixas = Intervalos-Exceto $Permitidos
Write-Host ("Permitidos na {0}: {1}" -f $Porta, ($Permitidos -join ", "))
Write-Host ("Bloqueados: todo o resto ({0} faixas)." -f $faixas.Count)

if (-not $Aplicar) {
    Write-Host ""
    Write-Host "Nada foi alterado. Para aplicar, rode de novo com -Aplicar."
    exit 0
}

Remove-NetFirewallRule -DisplayName $Nome -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $Nome -Direction Inbound -Protocol TCP -LocalPort $Porta `
    -RemoteAddress $faixas -Action Block -Profile Any `
    -Description ("MCP da .11: so {0} chegam (docs/PLANO_MIRA_AGENTE_11.md, regra 9)." -f ($Permitidos -join ", ")) | Out-Null
Write-Host ""
Write-Host "Regra '$Nome' aplicada. Confira de uma maquina nao permitida:"
Write-Host "    Test-NetConnection 192.168.7.11 -Port $Porta"
