<#
  levantamento_acessos_11.ps1 - quem usa cada porta, cada chave e o RDP da .11. SO LEITURA.

  Roda NA .11, PowerShell como administrador, dentro de C:\Python\ServidorIntegracaoSAP:
    .\maintenance\levantamento_acessos_11.ps1
    .\maintenance\levantamento_acessos_11.ps1 -Dias 7      # janela menor (padrao 30 = toda a auditoria)

  Nao muda nada: nenhuma regra, servico, chave ou arquivo existente. So grava o relatorio em
  logs\levantamento_acessos_<data>.txt (o mesmo texto que aparece na tela).

  Why: before closing any port (firewall) or retiring the master key / SIS_MCP_TOKEN, every
  current caller must be known and kept (rule "seguranca acrescenta, nunca tira funcao").
  Secrets are never printed: .env values show only "preenchida" / "vazia" / "ausente".
#>
param(
    [int]$Dias = 30,
    [switch]$Forcar
)

# After param(): any statement before it turns the param block into a plain expression.
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$ErrorActionPreference = 'Stop'
$Raiz = Split-Path -Parent $PSScriptRoot
$Linhas = New-Object System.Collections.Generic.List[string]

function L([string]$texto = '') {
    Write-Host $texto
    $script:Linhas.Add($texto)
}

function Secao([string]$titulo) {
    L ''
    L ('== ' + $titulo + ' ' + ('=' * [Math]::Max(3, 70 - $titulo.Length)))
}

function Falhou([string]$onde, $excecao) {
    $msg = [string]$excecao.Exception.Message
    if ($msg.Length -gt 200) { $msg = $msg.Substring(0, 200) }
    L ('  (nao consegui ler ' + $onde + ': ' + $msg + ')')
}

# Commands pasted on the wrong machine already happened once (ALTSAP taken for the .12).
if ($env:COMPUTERNAME -ne 'SAPBusinessOneI' -and -not $Forcar) {
    Write-Host ('Esta maquina e ' + $env:COMPUTERNAME + ', nao a .11 (SAPBusinessOneI). Nada foi feito.')
    Write-Host 'Se for mesmo aqui, rode de novo com -Forcar.'
    exit 1
}

$desde = (Get-Date).AddDays(-$Dias)
L ('Levantamento de acessos - ' + $env:COMPUTERNAME + ' - ' + (Get-Date).ToString('yyyy-MM-dd HH:mm') + ' - ultimos ' + $Dias + ' dias')

# ---------------------------------------------------------------------------------------------
Secao 'Firewall do Windows'
try {
    Get-NetFirewallProfile | ForEach-Object { L ('  {0,-8} ligado={1}' -f $_.Name, $_.Enabled) }
} catch { Falhou 'o firewall' $_ }

# ---------------------------------------------------------------------------------------------
Secao 'Portas abertas para a rede (fora do 127.0.0.1)'
try {
    $procs = @{}
    Get-CimInstance Win32_Process | ForEach-Object { $procs[[int]$_.ProcessId] = $_ }
    $escuta = Get-NetTCPConnection -State Listen |
        Where-Object { $_.LocalAddress -notin @('127.0.0.1', '::1') } |
        Sort-Object LocalPort -Unique
    foreach ($c in $escuta) {
        $p = $procs[[int]$c.OwningProcess]
        $nome = '?'
        if ($p) {
            $nome = $p.Name
            if ($p.Name -eq 'python.exe' -and $p.CommandLine) {
                # Lazy up to the first "python[.exe]" followed by blanks: the path itself has "Python314".
                $cmd = ($p.CommandLine -replace '^.*?python(\.exe)?"?\s+', '').Trim()
                if ($cmd.Length -gt 50) { $cmd = $cmd.Substring(0, 50) }
                $nome = 'python ' + $cmd
            }
        }
        L ('  {0,-6} {1}' -f $c.LocalPort, $nome)
    }
} catch { Falhou 'as portas' $_ }

# ---------------------------------------------------------------------------------------------
Secao 'Quem esta conectado AGORA (por porta e IP de origem)'
try {
    $portasDeInteresse = @(445, 3389, 8077, 8078, 8079, 8080)
    $agora = Get-NetTCPConnection -State Established |
        Where-Object { $portasDeInteresse -contains $_.LocalPort -and $_.RemoteAddress -notin @('127.0.0.1', '::1') } |
        Group-Object LocalPort, RemoteAddress | Sort-Object Name
    if (-not $agora) { L '  (ninguem nessas portas neste momento)' }
    foreach ($g in $agora) { L ('  {0,-28} conexoes={1}' -f $g.Name, $g.Count) }
} catch { Falhou 'as conexoes' $_ }

# ---------------------------------------------------------------------------------------------
Secao 'Auditoria das chaves (api 8077, controleproducao 8080, mcp 8078)'
L '  cliente "chave-mestra" = OS_API_KEY (inclui quem entra pelo navegador com o login das telas)'
L '  cliente "mcp-legado"   = SIS_MCP_TOKEN antigo;  cliente "(sem chave)" = recusado sem credencial'
$pastaAud = Join-Path $Raiz 'logs\auditoria'
$reCliente = [regex]'"cliente": (?:"([^"]*)"|null)'
$reIp = [regex]'"ip": (?:"([^"]*)"|null)'
$reRota = [regex]'"(?:rota|alvo)": "([^"]*)"'
$reTs = [regex]'"ts": "([^"]*)"'
try {
    $grupos = @{}
    $arquivos = Get-ChildItem $pastaAud -Filter '*-????-??-??.jsonl' -ErrorAction Stop |
        Where-Object { $_.BaseName.Substring($_.BaseName.Length - 10) -ge $desde.ToString('yyyy-MM-dd') }
    $total = 0
    foreach ($arq in $arquivos) {
        $servico = $arq.BaseName.Substring(0, $arq.BaseName.Length - 11)
        # ReadLines + regex instead of ConvertFrom-Json: weeks of audit are too many lines for PS 5.1 JSON.
        foreach ($linha in [IO.File]::ReadLines($arq.FullName)) {
            $total++
            $m = $reCliente.Match($linha)
            $cliente = '(sem chave)'
            if ($m.Success -and $m.Groups[1].Success) { $cliente = $m.Groups[1].Value }
            $m = $reIp.Match($linha)
            $ip = '-'
            if ($m.Success -and $m.Groups[1].Success) { $ip = $m.Groups[1].Value }
            $chave = $servico + '|' + $cliente + '|' + $ip
            $g = $grupos[$chave]
            if (-not $g) {
                $g = @{ servico = $servico; cliente = $cliente; ip = $ip; n = 0; primeiro = ''; ultimo = ''; rotas = @{} }
                $grupos[$chave] = $g
            }
            $g.n = $g.n + 1
            $ts = $reTs.Match($linha).Groups[1].Value
            if ($ts) {
                if (-not $g.primeiro -or $ts -lt $g.primeiro) { $g.primeiro = $ts }
                if ($ts -gt $g.ultimo) { $g.ultimo = $ts }
            }
            $m = $reRota.Match($linha)
            if ($m.Success) {
                # Numbers and query strings collapsed so the route list stays short.
                $r = ($m.Groups[1].Value -replace '\?.*$', '') -replace '\d+', '<n>'
                $g.rotas[$r] = 1 + [int]$g.rotas[$r]
            }
        }
    }
    L ('  ' + $arquivos.Count + ' arquivos, ' + $total + ' chamadas')
    foreach ($g in ($grupos.Values | Sort-Object { $_.servico }, { $_.cliente }, { -$_.n })) {
        $top = ($g.rotas.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 3 |
            ForEach-Object { $_.Key + ' x' + $_.Value }) -join ', '
        $p = if ($g.primeiro.Length -ge 10) { $g.primeiro.Substring(0, 10) } else { '?' }
        $u = if ($g.ultimo.Length -ge 16) { $g.ultimo.Substring(0, 16).Replace('T', ' ') } else { '?' }
        L ('  {0,-16} {1,-16} {2,-15} n={3,-7} {4} .. {5}' -f $g.servico, $g.cliente, $g.ip, $g.n, $p, $u)
        if ($top) { L ('      ' + $top) }
    }
} catch { Falhou 'a auditoria' $_ }

# ---------------------------------------------------------------------------------------------
Secao 'Chaves registradas (python -m seguranca listar)'
try {
    Push-Location $Raiz
    # 'Stop' would turn any stderr line of a native command into a terminating error in PS 5.1.
    $ErrorActionPreference = 'Continue'
    $env:PYTHONIOENCODING = 'utf-8'
    try {
        $saida = & python -m seguranca listar 2>&1
        foreach ($s in $saida) { L ('  ' + [string]$s) }
    } finally { Pop-Location; $ErrorActionPreference = 'Stop' }
} catch { Falhou 'as chaves' $_ }

# ---------------------------------------------------------------------------------------------
Secao 'Configuracao sensivel nos .env (so se esta preenchida, nunca o valor)'
function Estado-Env([string]$arquivo, [string[]]$nomes, [string[]]$mostrarValor) {
    $caminho = Join-Path $Raiz $arquivo
    if (-not (Test-Path $caminho)) { L ('  ' + $arquivo + ': arquivo nao existe'); return }
    $conteudo = Get-Content $caminho -Encoding UTF8
    foreach ($nome in $nomes) {
        $linha = $conteudo | Where-Object { $_ -match ('^\s*' + [regex]::Escape($nome) + '\s*=') } | Select-Object -Last 1
        if (-not $linha) { $estado = 'ausente' }
        else {
            $valor = ($linha -replace '^[^=]*=', '').Trim().Trim('"').Trim("'")
            if (-not $valor) { $estado = 'vazia' }
            elseif ($mostrarValor -contains $nome) { $estado = $valor }
            else { $estado = 'preenchida' }
        }
        L ('  {0,-12} {1,-20} {2}' -f $arquivo, $nome, $estado)
    }
}
try {
    Estado-Env '.env' @('OS_API_KEY', 'STATUS_ID', 'PAINEL_SENHA', 'SL_USERNAME', 'SL_VERIFY_SSL', 'OP_SL_VERIFY_SSL') @('SL_USERNAME', 'SL_VERIFY_SSL', 'OP_SL_VERIFY_SSL')
    Estado-Env 'mcp\.env' @('SIS_MCP_TOKEN', 'SIS_API_KEY') @()
} catch { Falhou 'os .env' $_ }

# ---------------------------------------------------------------------------------------------
Secao ('Quem entrou por RDP (ultimos ' + $Dias + ' dias)')
try {
    $ev = Get-WinEvent -FilterHashtable @{
        LogName = 'Microsoft-Windows-TerminalServices-LocalSessionManager/Operational'; Id = 21, 25; StartTime = $desde
    } -ErrorAction Stop
    $rdp = $ev | ForEach-Object {
        [pscustomobject]@{ Usuario = [string]$_.Properties[0].Value; Origem = [string]$_.Properties[2].Value; Quando = $_.TimeCreated }
    } | Group-Object Usuario, Origem | Sort-Object Count -Descending
    foreach ($g in $rdp) {
        $ultimo = ($g.Group | Sort-Object Quando -Descending | Select-Object -First 1).Quando.ToString('yyyy-MM-dd HH:mm')
        L ('  {0,-45} vezes={1,-4} ultima={2}' -f $g.Name, $g.Count, $ultimo)
    }
} catch {
    if ($_.FullyQualifiedErrorId -match 'NoMatchingEventsFound') { L '  (nenhuma entrada por RDP no periodo)' } else { Falhou 'o log do RDP' $_ }
}

# ---------------------------------------------------------------------------------------------
Secao 'Senhas erradas no Windows (evento 4625, ultimos 7 dias, por IP e usuario)'
try {
    $falhas = Get-WinEvent -FilterHashtable @{ LogName = 'Security'; Id = 4625; StartTime = (Get-Date).AddDays(-7) } -MaxEvents 20000 -ErrorAction Stop |
        ForEach-Object { [pscustomobject]@{ Usuario = [string]$_.Properties[5].Value; Ip = [string]$_.Properties[19].Value } } |
        Group-Object Ip, Usuario | Sort-Object Count -Descending | Select-Object -First 15
    foreach ($g in $falhas) { L ('  {0,-45} tentativas={1}' -f $g.Name, $g.Count) }
} catch {
    if ($_.FullyQualifiedErrorId -match 'NoMatchingEventsFound') { L '  (nenhuma senha errada no periodo)' } else { Falhou 'o log de seguranca' $_ }
}

# ---------------------------------------------------------------------------------------------
Secao 'Compartilhamentos de rede'
try {
    L ('  SMB1 ligado: ' + (Get-SmbServerConfiguration).EnableSMB1Protocol)
    Get-SmbShare | Where-Object { $_.Name -notmatch '\$$' } |
        ForEach-Object { L ('  pasta {0,-20} {1}' -f $_.Name, $_.Path) }
    $sess = Get-SmbSession -ErrorAction Stop
    if (-not $sess) { L '  (ninguem conectado nos compartilhamentos agora)' }
    foreach ($s in $sess) { L ('  conectado: {0,-18} {1}' -f $s.ClientComputerName, $s.ClientUserName) }
} catch { Falhou 'os compartilhamentos' $_ }

# ---------------------------------------------------------------------------------------------
Secao 'Administradores desta maquina'
try {
    Get-LocalGroupMember -SID 'S-1-5-32-544' | ForEach-Object { L ('  {0,-40} {1}' -f $_.Name, $_.ObjectClass) }
} catch {
    # Get-LocalGroupMember fails on orphaned domain SIDs; net localgroup still lists the rest.
    # The group name depends on the OS language, so it comes from the well-known SID.
    $ErrorActionPreference = 'Continue'
    try {
        $grupo = (New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-544').Translate([Security.Principal.NTAccount]).Value.Split('\')[-1]
        & net localgroup $grupo 2>$null | Where-Object { $_ } | ForEach-Object { L ('  ' + $_) }
    } catch { Falhou 'os administradores' $_ }
    $ErrorActionPreference = 'Stop'
}

# ---------------------------------------------------------------------------------------------
Secao 'Contas dos servicos'
try {
    Get-CimInstance Win32_Service | Where-Object { $_.Name -like 'OrcaView-*' -or $_.DisplayName -match 'BankPlus' } |
        Sort-Object Name | ForEach-Object { L ('  {0,-30} {1,-12} {2}' -f $_.Name, $_.State, $_.StartName) }
} catch { Falhou 'os servicos' $_ }

# ---------------------------------------------------------------------------------------------
$destino = Join-Path $Raiz ('logs\levantamento_acessos_' + (Get-Date).ToString('yyyyMMdd_HHmm') + '.txt')
try {
    [IO.File]::WriteAllLines($destino, $Linhas, (New-Object Text.UTF8Encoding($false)))
    Write-Host ''
    Write-Host ('Relatorio gravado em ' + $destino)
} catch { Write-Host ('Nao consegui gravar o relatorio: ' + $_.Exception.Message) }
