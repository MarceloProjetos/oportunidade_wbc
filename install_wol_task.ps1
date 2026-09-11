<#
.SYNOPSIS
    Registra a tarefa que ACORDA O .90 (ALTSERVIDOR-IA) toda vez que a .11 liga.
    Rode UMA vez, no servidor 192.168.7.11, como Administrador.

.DESCRIPTION
    A corrente inteira, para ninguem precisar remontar depois:

        .11 liga -> tarefa OrcaView-WOL-AltservidorIA (boot + 30s)
                 -> wake_altservidor_ia.py manda o magic packet
                 -> .90 acorda
                 -> autologon do .90 (~16s)
                 -> tarefa "OrcaView Stack AtLogon" (+60s) roda
                    tools\ops\subir_stack_boot.ps1 do web_orcaview_V118
                 -> npm run start:all-detailed  = OrcaView no ar

    Ou seja: a unica peca que faltava era esta. O lado do .90 ja existia e
    funciona; nao se mexe nele.

    A .11 REINICIA TODO DIA por volta das 06:12 -- entao, na pratica, isto vira
    um "liga o .90 toda manha". Se o .90 tiver sido desligado de proposito, o
    proximo reboot da .11 vai religar. Quem nao quiser isso: -Desinstalar.

    Por que o gatilho e boot+30s e nao boot puro: o adaptador de rede da .11
    ainda esta subindo no instante do boot, e broadcast que sai antes da porta
    do switch convergir se perde. Por isso, alem do atraso, o script insiste:
    reenvia a rodada a cada -Reenviar segundos ate o .90 responder ou a janela
    de -Wait acabar. Uma rodada custa 1,2 KB.

    Roda como SYSTEM (LogonType ServiceAccount): dispensa senha e roda com
    ninguem logado -- que e exatamente o caso no boot.

    Chama o python.exe DIRETO, com caminho absoluto resolvido aqui na
    instalacao. Nao ha PowerShell no meio: e o jeito mais curto e foge do bug
    conhecido do -File sob SYSTEM (ver install_monitor_task.ps1).

    Idempotente: se a tarefa ja existir, e removida e recriada.

.PARAMETER Wait
    Janela total, em segundos, que a tarefa espera o .90 responder ao ping.

.PARAMETER Reenviar
    Reenvia a rodada de magic packets a cada N segundos dentro da janela.

.PARAMETER DelaySeconds
    Atraso entre o boot da .11 e o disparo.

.PARAMETER Desinstalar
    Remove a tarefa e sai. Nao apaga o script nem o log.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install_wol_task.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install_wol_task.ps1 -Desinstalar
#>
[CmdletBinding()]
param(
    [string]$TaskName     = 'OrcaView-WOL-AltservidorIA',
    [int]$Wait            = 600,
    [int]$Reenviar        = 60,
    [int]$DelaySeconds    = 30,
    [switch]$Desinstalar
)

$ErrorActionPreference = 'Stop'

# --- Desinstalacao: sai antes de qualquer verificacao de caminho -------------
if ($Desinstalar) {
    $existente = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existente) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Tarefa '$TaskName' removida. O .90 nao sera mais acordado pela .11."
    }
    else {
        Write-Host "Tarefa '$TaskName' nao existe. Nada a fazer."
    }
    exit 0
}

# --- O script e o python ----------------------------------------------------
$scriptPath = Join-Path $PSScriptRoot 'wake_altservidor_ia.py'
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "wake_altservidor_ia.py nao encontrado em '$scriptPath'."
}

# Caminho ABSOLUTO do python, resolvido agora. O PATH do SYSTEM nao e o PATH da
# sessao interativa; deixar 'python' solto na acao da tarefa e o tipo de coisa
# que falha calada no proximo boot, meses depois.
$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = 'C:\Program Files\Python312\python.exe' }
if (-not (Test-Path -LiteralPath $python)) {
    throw "python.exe nao encontrado (nem no PATH, nem em '$python'). Instale ou ajuste o caminho."
}

# logs\ esta inteiro no .gitignore; o proprio script corta o arquivo em 1 MB.
$logDir = Join-Path $PSScriptRoot 'logs'
if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logPath = Join-Path $logDir 'wol_altservidor_ia.log'

Write-Host "Registrando '$TaskName':"
Write-Host "  python : $python"
Write-Host "  script : $scriptPath"
Write-Host "  gatilho: boot da .11 + ${DelaySeconds}s"
Write-Host "  janela : ${Wait}s, reenviando a cada ${Reenviar}s"
Write-Host "  log    : $logPath"

$argumentos = '"{0}" --wait {1} --reenviar {2} --log "{3}"' -f $scriptPath, $Wait, $Reenviar, $logPath
$action = New-ScheduledTaskAction -Execute $python -Argument $argumentos -WorkingDirectory $PSScriptRoot

# Gatilho no boot. O Delay nao e parametro do cmdlet: sai como propriedade.
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT{0}S' -f $DelaySeconds

$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

# O limite de execucao tem de ser MAIOR que a janela de espera, senao o Windows
# mata a tarefa no meio e o log fica pela metade sugerindo falha de rede.
$limite = [Math]::Max(30, [int][Math]::Ceiling($Wait / 60.0) + 10)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes $limite) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$existente = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existente) {
    Write-Host "Tarefa ja existe; removendo para recriar..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description 'No boot da .11, manda Wake-on-LAN para o ALTSERVIDOR-IA (192.168.0.90) ate ele responder. O OrcaView sobe sozinho la.' | Out-Null

Write-Host "`nTarefa registrada."
Write-Host "Ela NAO foi disparada agora de proposito: com o .90 ligado, o script"
Write-Host "so responderia 'ja esta ligada' e nao provaria nada."
Write-Host ""
Write-Host "Para testar o caminho de rede sem desligar nada:"
Write-Host "  $python `"$scriptPath`" --dry-run"
Write-Host "  $python `"$scriptPath`" --no-wait     (o .90 ligado responde 'JA responde ao ping')"
Write-Host ""
Write-Host "O teste de verdade e no dia em que o .90 estiver DESLIGADO:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'  e acompanhar o log."
Write-Host ""
Write-Host "ANTES DISSO, confirme na BIOS do .90 (nunca foi conferido):"
Write-Host "  Power On By PCI-E = Enabled   |   ErP Ready = Disabled"
Write-Host "Com ErP ligado o Windows reporta WOL habilitado do mesmo jeito e a maquina nao acorda."
