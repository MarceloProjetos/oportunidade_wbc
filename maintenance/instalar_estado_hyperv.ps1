<#
Installs the hourly Hyper-V report (estado_hyperv.ps1) on THIS host: steps 2 to 6 of docs/HYPERV_HOSTS.md in one go.
Written for the ALTHOSTBKP02 (03/11/2026); works on any host listed in operacao/hyperv.ORIGENS of the .11.

Run on the host, PowerShell as administrator, from the folder that holds this file AND estado_hyperv.ps1:
  .\instalar_estado_hyperv.ps1              asks for the key created on the .11 (python -m seguranca criar
                                            bkp02-hyperv --escopos hyperv:relatar), never shows it
  .\instalar_estado_hyperv.ps1 -Desfazer    removes the task, the key file and the script

The key only lets this host write its own report (scope hyperv:relatar); it reads nothing.
ASCII only and no double quotes (PS 5.1 reads a BOM-less file as ANSI).
#>
param(
    [switch]$Desfazer
)
$ErrorActionPreference = 'Stop'

$PASTA = 'C:\OrcaView'
$SCRIPT = Join-Path $PASTA 'estado_hyperv.ps1'
$PASTA_CHAVE = 'C:\ProgramData\OrcaView'
$CHAVE = Join-Path $PASTA_CHAVE 'hyperv_chave.txt'
$TAREFA = 'OrcaView-EstadoHyperV'

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Abra o PowerShell como administrador.' }
if (-not (Get-Command Get-VM -ErrorAction SilentlyContinue)) { throw ('Esta maquina (' + $env:COMPUTERNAME + ') nao tem Hyper-V: rode no host.') }

if ($Desfazer) {
    if (Get-ScheduledTask -TaskName $TAREFA -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TAREFA -Confirm:$false
        Write-Host ('[ok] tarefa ' + $TAREFA + ' removida')
    }
    foreach ($arquivo in @($CHAVE, $SCRIPT)) {
        if (Test-Path -LiteralPath $arquivo) { Remove-Item -LiteralPath $arquivo; Write-Host ('[ok] apagado ' + $arquivo) }
    }
    Write-Host 'Desfeito. Na .11, revogue a credencial: python -m seguranca revogar bkp02-hyperv'
    return
}

$origem = Join-Path $PSScriptRoot 'estado_hyperv.ps1'
if (-not (Test-Path -LiteralPath $origem)) { throw ('Falta o estado_hyperv.ps1 ao lado deste arquivo (' + $PSScriptRoot + ').') }
if (Get-ScheduledTask -TaskName $TAREFA -ErrorAction SilentlyContinue) { throw ('A tarefa ' + $TAREFA + ' ja existe aqui. Para refazer: -Desfazer antes.') }

# Step 2: the task runs the script as SYSTEM, so only SYSTEM and Administrators may change it
# (a folder at the root of C: inherits Authenticated Users: modify).
New-Item -ItemType Directory -Force $PASTA | Out-Null
& icacls.exe $PASTA /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'icacls falhou na pasta C:\OrcaView.' }
Copy-Item -LiteralPath $origem -Destination $SCRIPT -Force
Write-Host ('[ok] script em ' + $SCRIPT + ' (so SYSTEM e Administradores)')

# Step 3: read without sending, and show the short version to compare with the Hyper-V Manager.
$visto = (& $SCRIPT -SoMostrar | Out-String) | ConvertFrom-Json
Write-Host ''
Write-Host 'VMs que o relato enxerga:'
$visto.vms | ForEach-Object { Write-Host ('  ' + $_.nome + '  ' + $_.estado + '  heartbeat=' + $_.heartbeat) }
Write-Host ('Volumes: ' + (($visto.volumes | ForEach-Object { $_.letra + ' ' + $_.livre_gb + '/' + $_.total_gb + ' GB' }) -join ', '))
if (@($visto.erros).Count -gt 0) { Write-Host ('Avisos do script: ' + (@($visto.erros) -join ' | ')) -ForegroundColor Yellow }
Write-Host ''

# Step 4: the key, typed (or pasted) without echo, in a file only SYSTEM and Administrators read.
$segura = Read-Host 'Cole a chave criada na .11 (bkp02-hyperv) e tecle Enter' -AsSecureString
$texto = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($segura)).Trim()
if ($texto.Length -lt 20 -or $texto -match '\s') { throw 'Essa chave nao parece certa (curta ou com espaco). Nada foi gravado alem do script.' }
New-Item -ItemType Directory -Force $PASTA_CHAVE | Out-Null
Set-Content -Path $CHAVE -Value $texto -Encoding ascii
& icacls.exe $CHAVE /inheritance:r /grant:r '*S-1-5-18:R' '*S-1-5-32-544:F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'icacls falhou no arquivo da chave.' }
Write-Host ('[ok] chave em ' + $CHAVE)

# Step 5: one send by hand; the script prints nothing when the .11 accepted it.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ('& ' + [char]39 + $SCRIPT + [char]39)
if ($LASTEXITCODE -ne 0) {
    throw ('A .11 nao aceitou o relato (saida ' + $LASTEXITCODE + '). 401 = chave errada; 403 origem_nao_permitida = a .11 ainda nao conhece este host (falta o deploy). A tarefa NAO foi criada.')
}
Write-Host '[ok] 1o relato aceito pela .11'

# Step 6: every hour as SYSTEM, always -Command (never -File).
$argumento = '-NoProfile -ExecutionPolicy Bypass -Command ' + [char]34 + '& ' + [char]39 + $SCRIPT + [char]39 + [char]34
$acao = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argumento
$gatilho = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$config = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName $TAREFA -Action $acao -Trigger $gatilho -Settings $config -User 'SYSTEM' -RunLevel Highest | Out-Null
Write-Host ('[ok] tarefa ' + $TAREFA + ' de hora em hora, como SYSTEM')
Write-Host ''
Write-Host 'Pronto. O Teo e a Mira passam a ver este host em estado_hyperv.'
