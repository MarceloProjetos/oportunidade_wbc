# ETAPA 0 - so leitura: nao para servico, nao instala nada.
# PRE-REQUISITO: PowerShell como Administrador, na .11, com o usuario que voce sempre usa la.
# Baixa o instalador para C:\ProgramData\py31410 e registra o resultado em logs\deploy.log.
. (Join-Path $PSScriptRoot 'comum.ps1')
Exigir-Administrador
Exigir-Maquina11
Registrar 'inicio' "$env:USERNAME em $env:COMPUTERNAME - medir Python"

Registrar 'python' "$(& python --version 2>&1) | $((& where.exe python) -join '; ')"
$worker = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\OrcaView-WBC-Worker\Parameters' -ErrorAction SilentlyContinue).Application
Registrar 'worker' "Application do NSSM: $worker"
$proc = Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'python.exe', 'pythonw.exe' } | ForEach-Object { "$($_.ExecutablePath) <- $(([string]$_.CommandLine).Substring(0, [Math]::Min(60, ([string]$_.CommandLine).Length)))" }
Registrar 'processos' ($proc -join ' ;; ')

foreach ($h in 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall', 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall', 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall') {
    $achados = Get-ChildItem $h -ErrorAction SilentlyContinue | ForEach-Object { Get-ItemProperty $_.PSPath } | Where-Object { $_.DisplayName -like 'Python *' } | ForEach-Object { $_.DisplayName -replace ' \(64-bit\)', '' }
    Registrar 'registro' "$h : $($achados -join ', ')"
}
foreach ($h in 'HKLM:\Software\Python\PythonCore\3.14', 'HKCU:\Software\Python\PythonCore\3.14') {
    if (Test-Path "$h\InstallPath") { Registrar 'pep514' "$h : $((Get-ItemProperty "$h\InstallPath").'(default)')" }
}
$tarefas = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { ($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -match 'python|\.py\b' } | ForEach-Object { $_.TaskName }
Registrar 'tarefas' "com Python: $($tarefas -join ', ')"
Registrar 'cp' "ocupado=$((& curl.exe -s -m 20 "http://127.0.0.1:$(Porta-Do-Env 'CP_PORTA' '8080')/health/ocupado" 2>$null) -join '')"

$freeze = & python -m pip freeze 2>$null
Registrar 'freeze' ($freeze -join ' ')
Registrar 'pipcheck' ((& python -m pip check 2>&1) -join ' ;; ')

if (-not (Test-Path $script:PastaPy)) { New-Item -ItemType Directory -Force $script:PastaPy | Out-Null }
$freeze | Set-Content -Encoding Ascii (Join-Path $script:PastaPy 'freeze_11_antes.txt')
if (-not (Test-Path $script:Instalador) -or (Get-Item $script:Instalador).Length -ne $script:TamanhoInstalador) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = 'SilentlyContinue'
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "https://www.python.org/ftp/python/$($script:Versao)/python-$($script:Versao)-amd64.exe" -OutFile $script:Instalador
    } catch {
        Registrar 'instalador' "download falhou: $($_.Exception.Message)"
    }
}
if (Test-Path $script:Instalador) {
    $sig = Get-AuthenticodeSignature $script:Instalador
    Registrar 'instalador' "tamanho=$((Get-Item $script:Instalador).Length) assinatura=$($sig.Status) $($sig.SignerCertificate.Subject)"
}

$alvos = @()
foreach ($l in '1', '2', '3') { $alvos += @(& python "$PSScriptRoot\lotes_11.py" alvos $l) }
$rel = Join-Path $script:PastaPy 'dryrun.json'
$saida = & python -m pip install --dry-run --quiet --disable-pip-version-check --report $rel @alvos 2>&1
Registrar 'dryrun' "pip exit=$LASTEXITCODE alvos: $($alvos -join ' ') $(($saida | Select-Object -Last 3) -join ' ;; ')"
if (Test-Path $rel) { Registrar 'dryrun' ((& python "$PSScriptRoot\lotes_11.py" conferir $rel) -join ' ;; ') }

Fechar $true
Write-Host 'Pronto. Nao precisa colar nada: eu leio o resultado pelo MCP.' -ForegroundColor Green
