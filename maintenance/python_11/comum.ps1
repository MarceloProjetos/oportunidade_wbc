# Shared by the Python update steps of the .11. Dot-sourced; does nothing by itself.
# Stop and start mirror deploy_update.bat: same guard, same order, same stop file for the worker.
# Not 'Stop': on PowerShell 5.1 any stderr line of a redirected native command (pip, nssm) would
# become a terminating error. The exit codes decide, as in the deploy.
$ErrorActionPreference = 'Continue'
$script:Raiz = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:PastaPy = 'C:\ProgramData\py31410'
$script:Versao = '3.14.10'
$script:Instalador = Join-Path $script:PastaPy 'python-3.14.10-amd64.exe'
$script:TamanhoInstalador = 33498520
$script:Servicos = 'OrcaView-OS-API', 'OrcaView-MCP', 'OrcaView-Scheduler', 'OrcaView-WBC-Painel', 'OrcaView-ControleProducao', 'OrcaView-WBC-Worker'
Set-Location $script:Raiz
$env:PYTHONUTF8 = '1'

# One line per step in logs\deploy.log: GET /operacao/deploy and the MCP tool ultimo_deploy read it,
# so the agent follows the result without anyone pasting the screen.
function Registrar([string]$Etapa, [string]$Texto) {
    $cultura = [Globalization.CultureInfo]::InvariantCulture
    $momento = (Get-Date).ToString('ddd MM/dd/yyyy HH:mm:ss.ff', $cultura)
    $limpo = ($Texto -replace '[^\x20-\x7E]', '?')
    Add-Content -Path 'logs\deploy.log' -Encoding Ascii -Value "$momento | $Etapa | $limpo"
    Write-Host "[$Etapa] $limpo"
}

function Exigir-Administrador {
    $id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host 'ERRO: abra o PowerShell como Administrador. Nada foi alterado.' -ForegroundColor Red
        exit 1
    }
}

function Exigir-Maquina11 {
    $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { $_.IPAddress }
    if ($ips -notcontains '192.168.7.11') {
        Write-Host 'ERRO: este script so roda na .11. Nada foi alterado.' -ForegroundColor Red
        exit 1
    }
}

function Porta-Do-Env([string]$Chave, [string]$Padrao) {
    $linha = Select-String -Path '.env' -Pattern "^$Chave=" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($linha) { return ($linha.Line -split '=', 2)[1].Trim() }
    return $Padrao
}

function Servico-Estado([string]$Nome) {
    $s = Get-Service -Name $Nome -ErrorAction SilentlyContinue
    if ($s) { return [string]$s.Status }
    return 'Ausente'
}

function Esperar-Parar([string]$Nome, [int]$Segundos) {
    for ($i = 0; $i -lt $Segundos; $i++) {
        if ((Servico-Estado $Nome) -in 'Stopped', 'Ausente') { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

# Fail closed, like the deploy: stopping the Controle de Producao mid-run leaves OPs half made in SAP.
function Exigir-CpLivre {
    $porta = Porta-Do-Env 'CP_PORTA' '8080'
    $resposta = (& curl.exe -s -m 20 "http://127.0.0.1:$porta/health/ocupado" 2>$null) -join ''
    if ($resposta -eq '0') { return }
    if ((Servico-Estado 'OrcaView-ControleProducao') -ne 'Running') { return }
    Registrar 'abortado' "Controle de Producao ocupado ou sem resposta ($resposta) - nada alterado"
    Write-Host 'ERRO: o Controle de Producao tem execucao em andamento. Espere terminar e rode de novo.' -ForegroundColor Red
    exit 1
}

# Returns whether the worker was running: it only comes back if it was.
function Parar-Stack {
    $workerAtivo = (Servico-Estado 'OrcaView-WBC-Worker') -eq 'Running'
    foreach ($s in 'OrcaView-ControleProducao', 'OrcaView-MCP', 'OrcaView-OS-API', 'OrcaView-Scheduler', 'OrcaView-WBC-Painel') {
        & nssm stop $s 2>&1 | Out-Null
    }
    if ($workerAtivo) {
        if (-not (Test-Path 'state')) { New-Item -ItemType Directory 'state' | Out-Null }
        Set-Content -Path 'state\wbc_worker.stop' -Encoding Ascii -Value "parada pedida pela atualizacao do Python em $(Get-Date)"
        Write-Host 'Worker WBC: termina o ciclo atual; espera ate 90 s...'
        & nssm stop 'OrcaView-WBC-Worker' 2>&1 | Out-Null
        if (-not (Esperar-Parar 'OrcaView-WBC-Worker' 90)) { Registrar 'erro' 'worker nao parou em 90 s' }
    }
    foreach ($s in $script:Servicos) { [void](Esperar-Parar $s 30) }
    return $workerAtivo
}

function Exigir-SemPython {
    for ($i = 0; $i -lt 30; $i++) {
        $vivos = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'python.exe', 'pythonw.exe' })
        if ($vivos.Count -eq 0) { return $true }
        Start-Sleep -Seconds 2
    }
    foreach ($p in $vivos) { Registrar 'erro' "python ainda rodando: $($p.ProcessId) $($p.CommandLine)" }
    return $false
}

function Subir-Stack([bool]$WorkerAtivo) {
    foreach ($s in 'OrcaView-OS-API', 'OrcaView-MCP', 'OrcaView-Scheduler', 'OrcaView-WBC-Painel', 'OrcaView-ControleProducao') {
        & nssm start $s 2>&1 | Out-Null
    }
    if ($WorkerAtivo) {
        [void](Esperar-Parar 'OrcaView-WBC-Worker' 30)
        & nssm start 'OrcaView-WBC-Worker' 2>&1 | Out-Null
    }
    Start-Sleep -Seconds 8
}

function Conferir-Http([string]$Nome, [string]$Url) {
    $cod = ''
    for ($i = 0; $i -lt 12; $i++) {
        $cod = (& curl.exe -s -o NUL -m 10 -w '%{http_code}' $Url 2>$null) -join ''
        if ($cod -eq '200') { break }
        Start-Sleep -Seconds 5
    }
    Registrar 'health' "$Nome HTTP $cod"
    return ($cod -eq '200')
}

# The /status field speaks only for the API process: each service's real python.exe is what counts.
function Conferir-Stack([bool]$WorkerAtivo) {
    $ok = $true
    $ok = (Conferir-Http 'API 8077' "http://127.0.0.1:$(Porta-Do-Env 'OS_API_PORT' '8077')/health") -and $ok
    $ok = (Conferir-Http 'painel WBC' "http://127.0.0.1:$(Porta-Do-Env 'PAINEL_PORTA' '8079')/entrar") -and $ok
    $ok = (Conferir-Http 'controle producao' "http://127.0.0.1:$(Porta-Do-Env 'CP_PORTA' '8080')/health") -and $ok
    $mcp = New-Object Net.Sockets.TcpClient
    try { $mcp.Connect('127.0.0.1', 8078); Registrar 'health' 'MCP 8078 porta aberta' }
    catch { Registrar 'health' 'MCP 8078 sem resposta'; $ok = $false }
    finally { $mcp.Close() }
    foreach ($s in $script:Servicos) {
        $estado = Servico-Estado $s
        Registrar 'servico' "$s $estado"
        if ($estado -ne 'Running' -and ($s -ne 'OrcaView-WBC-Worker' -or $WorkerAtivo)) { $ok = $false }
    }
    $caminhos = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' } | ForEach-Object { $_.ExecutablePath } | Sort-Object -Unique
    Registrar 'python' "$(& python --version 2>&1) em: $($caminhos -join '; ')"
    return $ok
}

function Fechar([bool]$Ok) {
    if ($Ok) {
        Registrar 'fim' 'OK'
        Write-Host '===== TERMINOU OK =====' -ForegroundColor Green
    } else {
        Registrar 'fim' 'AVISOS - ver as linhas acima'
        Write-Host '===== TERMINOU COM AVISOS: me avise =====' -ForegroundColor Yellow
    }
}
