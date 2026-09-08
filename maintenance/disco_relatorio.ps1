<#
.SYNOPSIS
    Relatorio de ocupacao do disco C: do servidor .11 (SOMENTE LEITURA). Etapa 1 de 2 da
    limpeza de disco: primeiro ver onde o espaco foi, depois rodar disco_limpeza.ps1.

.DESCRIPTION
    Nao apaga, nao move, nao para servico. Imprime:
      1. Espaco livre/total do C:.
      2. Tamanho dos "suspeitos de sempre" (Windows Update cache, temporarios, logs do Azure,
         CBS, Lixeira, logs/exports do ServidorIntegracaoSAP, shadow copies).
      3. As maiores pastas de primeiro nivel do C: e as maiores subpastas dentro delas.
    Pastas sem permissao sao puladas em silencio. Pode demorar alguns minutos num disco
    de 126 GB (le o tamanho de tudo).

    ASCII de proposito (PowerShell 5.1 le .ps1 sem BOM como ANSI).

.PARAMETER Top
    Quantas pastas mostrar em cada ranking. Default 12.

.PARAMETER Profundidade
    Ate que nivel abaixo de C:\ descer no ranking. Default 2.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\maintenance\disco_relatorio.ps1
#>
[CmdletBinding()]
param(
    [int]$Top = 12,
    [int]$Profundidade = 2
)

$ErrorActionPreference = 'SilentlyContinue'

function Tamanho-Pasta {
    param([string]$Caminho)
    if (-not (Test-Path -LiteralPath $Caminho)) { return $null }
    $soma = 0
    Get-ChildItem -LiteralPath $Caminho -Recurse -File -Force -ErrorAction SilentlyContinue |
        ForEach-Object { $soma += $_.Length }
    return $soma
}

function Fmt-GB {
    param($Bytes)
    if ($null -eq $Bytes) { return '   (nao existe)' }
    return ('{0,10:N2} GB' -f ($Bytes / 1GB))
}

$inicio = Get-Date
Write-Host ''
Write-Host ('=== Disco C: em {0} ({1}) ===' -f $env:COMPUTERNAME, $inicio.ToString('yyyy-MM-dd HH:mm'))
$d = Get-PSDrive -Name C
$total = $d.Used + $d.Free
Write-Host ('Total {0,8:N1} GB | Usado {1,8:N1} GB ({2:N1}%) | Livre {3,8:N1} GB' -f ($total/1GB), ($d.Used/1GB), (100*$d.Used/$total), ($d.Free/1GB))

Write-Host ''
Write-Host '=== Suspeitos de sempre ==='
$suspeitos = [ordered]@{
    'Windows Update cache (SoftwareDistribution\Download)' = 'C:\Windows\SoftwareDistribution\Download'
    'Windows\Temp'                                          = 'C:\Windows\Temp'
    'Windows\Logs\CBS'                                      = 'C:\Windows\Logs\CBS'
    'WindowsAzure\Logs (tem clean_azure_logs.ps1)'          = 'C:\WindowsAzure\Logs'
    'Windows\Installer'                                     = 'C:\Windows\Installer'
    'ProgramData\Microsoft\Windows\WER'                     = 'C:\ProgramData\Microsoft\Windows\WER'
    'Lixeira ($Recycle.Bin)'                                = 'C:\$Recycle.Bin'
    'ServidorIntegracaoSAP\logs'                            = 'C:\Python\ServidorIntegracaoSAP\logs'
    'ServidorIntegracaoSAP\exports'                         = 'C:\Python\ServidorIntegracaoSAP\exports'
    'ServidorIntegracaoSAP\state'                           = 'C:\Python\ServidorIntegracaoSAP\state'
    'Users\*\AppData\Local\Temp (todos)'                    = $null
}
foreach ($k in $suspeitos.Keys) {
    $p = $suspeitos[$k]
    if ($null -eq $p) {
        $soma = 0
        Get-ChildItem 'C:\Users' -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $t = Join-Path $_.FullName 'AppData\Local\Temp'
            $s = Tamanho-Pasta $t
            if ($s) { $soma += $s }
        }
        Write-Host ('{0}  {1}' -f (Fmt-GB $soma), $k)
    } else {
        Write-Host ('{0}  {1}' -f (Fmt-GB (Tamanho-Pasta $p)), $k)
    }
}

Write-Host ''
Write-Host '=== Shadow copies (VSS) ==='
$vss = & vssadmin.exe list shadowstorage 2>&1
if ($LASTEXITCODE -eq 0) {
    $vss | Where-Object { $_ -match 'Used|Allocated|Maximum|Usado|Alocado|Maximo' } | ForEach-Object { Write-Host ('   ' + $_.Trim()) }
} else {
    Write-Host '   (vssadmin nao respondeu - rode como Administrador)'
}

Write-Host ''
Write-Host ('=== Maiores pastas de C:\ (nivel 1) ===')
$nivel1 = @()
Get-ChildItem 'C:\' -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {
    $s = Tamanho-Pasta $_.FullName
    if ($null -ne $s) { $nivel1 += [pscustomobject]@{ Pasta = $_.FullName; Bytes = $s } }
}
$nivel1 = $nivel1 | Sort-Object Bytes -Descending
$nivel1 | Select-Object -First $Top | ForEach-Object { Write-Host ('{0}  {1}' -f (Fmt-GB $_.Bytes), $_.Pasta) }

if ($Profundidade -ge 2) {
    Write-Host ''
    Write-Host ('=== Dentro das {0} maiores (nivel 2) ===' -f [Math]::Min(4, $nivel1.Count))
    $nivel1 | Select-Object -First 4 | ForEach-Object {
        $pai = $_.Pasta
        Write-Host ('-- {0}' -f $pai)
        $filhos = @()
        Get-ChildItem -LiteralPath $pai -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $s = Tamanho-Pasta $_.FullName
            if ($null -ne $s) { $filhos += [pscustomobject]@{ Pasta = $_.FullName; Bytes = $s } }
        }
        $filhos | Sort-Object Bytes -Descending | Select-Object -First $Top | ForEach-Object {
            Write-Host ('   {0}  {1}' -f (Fmt-GB $_.Bytes), $_.Pasta)
        }
    }
}

Write-Host ''
Write-Host ('Relatorio levou {0:N0} s. Proxima etapa: disco_limpeza.ps1 -DryRun (ve o que seria apagado).' -f ((Get-Date) - $inicio).TotalSeconds)
