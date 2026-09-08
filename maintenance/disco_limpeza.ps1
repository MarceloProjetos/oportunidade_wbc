<#
.SYNOPSIS
    Limpeza CONSERVADORA do disco C: do servidor .11. Etapa 2 de 2 (rode disco_relatorio.ps1
    antes). Por padrao e DRY-RUN: so mostra o que apagaria. Para apagar de verdade: -Confirmar.

.DESCRIPTION
    Apaga SOMENTE o que o Windows ou o app regeram sozinhos:
      1. C:\Windows\SoftwareDistribution\Download  (cache do Windows Update; para/religa wuauserv)
      2. C:\Windows\Temp e Users\*\AppData\Local\Temp (arquivos com mais de -DiasTemp dias)
      3. C:\Windows\Logs\CBS\*.log e *.cab com mais de -DiasLogs dias
      4. C:\ProgramData\Microsoft\Windows\WER (relatorios de erro)
      5. Lixeira de todos os usuarios
      6. C:\Python\ServidorIntegracaoSAP\logs\*.log.* rotacionados com mais de -DiasLogs dias
         (o api.log/scheduler.log atuais NAO sao tocados)
      7. C:\WindowsAzure\Logs via o clean_azure_logs.ps1 que ja existe ao lado (KeepDays 1)
    NAO toca: shadow copies, backups, exports, state, pastas do SAP, arquivos em uso (pulados
    em silencio), nenhuma pasta e apagada (so arquivos).

    Precisa de Administrador (wuauserv, Lixeira dos outros usuarios, CBS).
    ASCII de proposito (PowerShell 5.1 le .ps1 sem BOM como ANSI).

.PARAMETER Confirmar
    Sem este switch NADA e apagado (dry-run).

.PARAMETER DiasTemp
    Idade minima (dias) para apagar arquivo em Temp. Default 2.

.PARAMETER DiasLogs
    Idade minima (dias) para apagar log rotacionado / CBS. Default 30.

.EXAMPLE
    # 1) Ver o que seria apagado:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\maintenance\disco_limpeza.ps1

.EXAMPLE
    # 2) Apagar de verdade (como Administrador):
    powershell -NoProfile -ExecutionPolicy Bypass -File .\maintenance\disco_limpeza.ps1 -Confirmar
#>
[CmdletBinding()]
param(
    [switch]$Confirmar,
    [int]$DiasTemp = 2,
    [int]$DiasLogs = 30
)

$ErrorActionPreference = 'SilentlyContinue'
$script:Liberado = 0
$script:Apagados = 0
$script:Pulados = 0
$modo = if ($Confirmar) { 'APAGANDO' } else { 'DRY-RUN (nada e apagado; use -Confirmar)' }

function Write-LimpLog {
    param([string]$Message)
    $line = "[{0}] (user={1}) {2}" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $env:USERNAME, $Message
    Write-Host $line
    try { Add-Content -Path (Join-Path $PSScriptRoot 'disco_limpeza.log') -Value $line -Encoding UTF8 } catch { }
}

function Apagar-Arquivos {
    param(
        [string]$Rotulo,
        [string]$Caminho,
        [string]$Filtro = '*',
        [int]$IdadeDias = 0,
        [switch]$Recurse
    )
    if (-not $Caminho -or $Caminho.Length -lt 8) { Write-LimpLog "RECUSADO caminho curto: '$Caminho'"; return }
    if (-not (Test-Path -LiteralPath $Caminho)) { Write-LimpLog ('{0}: pasta nao existe ({1})' -f $Rotulo, $Caminho); return }
    $limite = (Get-Date).AddDays(-$IdadeDias)
    $itens = Get-ChildItem -LiteralPath $Caminho -File -Force -Filter $Filtro -Recurse:$Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $limite }
    $bytes = ($itens | Measure-Object Length -Sum).Sum
    if (-not $bytes) { $bytes = 0 }
    Write-LimpLog ('{0}: {1} arquivo(s), {2:N2} GB ({3})' -f $Rotulo, @($itens).Count, ($bytes/1GB), $Caminho)
    if (-not $Confirmar) { return }
    foreach ($f in $itens) {
        try {
            Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop
            $script:Liberado += $f.Length
            $script:Apagados += 1
        } catch {
            $script:Pulados += 1   # em uso / sem permissao: pula
        }
    }
}

$inicio = Get-Date
$livreAntes = (Get-PSDrive -Name C).Free
Write-LimpLog ('=== disco_limpeza em {0} - modo {1} - livre antes {2:N1} GB ===' -f $env:COMPUTERNAME, $modo, ($livreAntes/1GB))

# 1. Cache do Windows Update (regenera sozinho; para o servico para soltar os arquivos)
$wu = 'C:\Windows\SoftwareDistribution\Download'
if ($Confirmar) { Stop-Service wuauserv -Force -ErrorAction SilentlyContinue }
Apagar-Arquivos -Rotulo '1. Windows Update cache' -Caminho $wu -Recurse
if ($Confirmar) { Start-Service wuauserv -ErrorAction SilentlyContinue }

# 2. Temporarios
Apagar-Arquivos -Rotulo '2a. Windows\Temp' -Caminho 'C:\Windows\Temp' -IdadeDias $DiasTemp -Recurse
Get-ChildItem 'C:\Users' -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {
    $t = Join-Path $_.FullName 'AppData\Local\Temp'
    if (Test-Path -LiteralPath $t) {
        Apagar-Arquivos -Rotulo ('2b. Temp de ' + $_.Name) -Caminho $t -IdadeDias $DiasTemp -Recurse
    }
}

# 3. Logs do CBS (servicing) antigos
Apagar-Arquivos -Rotulo '3a. CBS *.log' -Caminho 'C:\Windows\Logs\CBS' -Filtro '*.log' -IdadeDias $DiasLogs
Apagar-Arquivos -Rotulo '3b. CBS *.cab' -Caminho 'C:\Windows\Logs\CBS' -Filtro '*.cab' -IdadeDias $DiasLogs

# 4. Relatorios de erro do Windows
Apagar-Arquivos -Rotulo '4. WER' -Caminho 'C:\ProgramData\Microsoft\Windows\WER' -IdadeDias $DiasTemp -Recurse

# 5. Lixeira (todos os usuarios)
$lix = Get-ChildItem 'C:\$Recycle.Bin' -Recurse -File -Force -ErrorAction SilentlyContinue
$lixBytes = ($lix | Measure-Object Length -Sum).Sum
if (-not $lixBytes) { $lixBytes = 0 }
Write-LimpLog ('5. Lixeira: {0} arquivo(s), {1:N2} GB' -f @($lix).Count, ($lixBytes/1GB))
if ($Confirmar) {
    try { Clear-RecycleBin -DriveLetter C -Force -ErrorAction Stop; $script:Liberado += $lixBytes } catch { Write-LimpLog ('   Lixeira: ' + $_.Exception.Message) }
}

# 6. Logs rotacionados do app (api.log.2026-08-01 etc.; o arquivo atual nao casa com o filtro)
Apagar-Arquivos -Rotulo '6. ServidorIntegracaoSAP logs rotacionados' -Caminho 'C:\Python\ServidorIntegracaoSAP\logs' -Filtro '*.log.*' -IdadeDias $DiasLogs

# 7. Logs do agente Azure - reusa o script que ja existe (ele mesmo pula arquivos em uso)
$azure = Join-Path $PSScriptRoot 'clean_azure_logs.ps1'
if (Test-Path -LiteralPath $azure) {
    Write-LimpLog '7. WindowsAzure\Logs via clean_azure_logs.ps1 (KeepDays 1)'
    if ($Confirmar) { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $azure -KeepDays 1 }
    else { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $azure -KeepDays 1 -DryRun }
}

$livreDepois = (Get-PSDrive -Name C).Free
Write-LimpLog ('=== fim: {0} apagado(s), {1} pulado(s) (em uso), {2:N2} GB liberados por contagem; livre agora {3:N1} GB (antes {4:N1}) - {5:N0} s ===' -f $script:Apagados, $script:Pulados, ($script:Liberado/1GB), ($livreDepois/1GB), ($livreAntes/1GB), ((Get-Date) - $inicio).TotalSeconds)
if (-not $Confirmar) { Write-Host 'Dry-run. Para apagar de verdade: -Confirmar (como Administrador).' }
