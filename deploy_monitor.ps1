<#
.SYNOPSIS
    Deploy do monitor da tarefa "Integracao WBC": copia os arquivos (se necessario),
    registra/atualiza a tarefa de 10 min e reinicia a API. Rode no servidor como Administrador.

.DESCRIPTION
    Passos:
      1. Copia config.py monitoring.py api.py monitor_wbc_task.ps1 install_monitor_task.ps1
         de -Source para -Dest (pulado se Source == Dest, ex.: apos um "git pull" no proprio Dest).
      2. Roda install_monitor_task.ps1 no Dest (registra a tarefa a cada 10 min + execucao inicial).
      3. Reinicia o servico da API (-ServiceName) para recarregar o codigo Python.

.PARAMETER Source
    Pasta de origem dos arquivos. Default: a pasta deste script.

.PARAMETER Dest
    Pasta do projeto em producao. Default: C:\Python\oportunidade_wbc.

.PARAMETER ServiceName
    Nome do servico (NSSM) da API a reiniciar. Default: OrcaView-OS-API.

.PARAMETER SkipRestart
    Nao reinicia a API (faca o restart manualmente depois).

.EXAMPLE
    # No servidor, apos git pull, dentro de C:\Python\oportunidade_wbc:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy_monitor.ps1

.EXAMPLE
    # Copiando de um compartilhamento/pasta para producao:
    .\deploy_monitor.ps1 -Source '\\maquina\repo\ServidorIntegracaoSAP' -Dest 'C:\Python\oportunidade_wbc'
#>
[CmdletBinding()]
param(
    [string]$Source = $PSScriptRoot,
    [string]$Dest = 'C:\Python\oportunidade_wbc',
    [string]$ServiceName = 'OrcaView-OS-API',
    [switch]$SkipRestart
)

$ErrorActionPreference = 'Stop'

# Precisa de Administrador (Register-ScheduledTask como SYSTEM e restart de servico).
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw 'Rode este script como Administrador (botao direito > Executar como administrador).'
}

$files = @('config.py', 'monitoring.py', 'api.py', 'monitor_wbc_task.ps1', 'install_monitor_task.ps1')

if (-not (Test-Path -LiteralPath $Source)) { throw "Origem nao existe: $Source" }
if (-not (Test-Path -LiteralPath $Dest))   { throw "Destino nao existe: $Dest" }
$srcFull = (Resolve-Path -LiteralPath $Source).Path
$dstFull = (Resolve-Path -LiteralPath $Dest).Path

if ($srcFull -ne $dstFull) {
    Write-Host "1/3 Copiando arquivos:"
    Write-Host "    de:   $srcFull"
    Write-Host "    para: $dstFull"
    foreach ($f in $files) {
        $sp = Join-Path $srcFull $f
        if (-not (Test-Path -LiteralPath $sp)) { throw "Arquivo ausente na origem: $sp" }
        Copy-Item -LiteralPath $sp -Destination (Join-Path $dstFull $f) -Force
        Write-Host "    copiado: $f"
    }
}
else {
    Write-Host "1/3 Source == Dest ($dstFull): arquivos ja no lugar (git pull). Copia pulada."
}

Write-Host "`n2/3 Registrando/atualizando a tarefa de monitoramento (10 min) + execucao inicial..."
& (Join-Path $dstFull 'install_monitor_task.ps1')

if ($SkipRestart) {
    Write-Host "`n3/3 -SkipRestart: reinicie o servico '$ServiceName' manualmente p/ a API recarregar."
}
else {
    Write-Host "`n3/3 Reiniciando o servico '$ServiceName'..."
    $nssm = Get-Command nssm -ErrorAction SilentlyContinue
    if ($nssm) {
        & nssm restart $ServiceName
    }
    else {
        Restart-Service -Name $ServiceName -ErrorAction Stop
    }
    Write-Host "    servico reiniciado."
}

Write-Host "`nOK. Confira: http://localhost:8077/status?checks=tarefa"
