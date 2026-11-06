# ETAPA 1 - para os 6 servicos, instala o Python 3.14.10 por cima e religa. Poucos minutos fora do ar.
# PRE-REQUISITO: 0_medir.ps1 rodado (instalador baixado e assinatura valida), PowerShell como Administrador,
# com o mesmo usuario que instalou o Python atual. Recusa sozinho se algo nao estiver certo.
. (Join-Path $PSScriptRoot 'comum.ps1')
Exigir-Administrador
Exigir-Maquina11

$atual = (& python --version 2>&1) -join ''
if ($atual -eq "Python $($script:Versao)") { Write-Host "Ja esta em $atual. Nada a fazer." -ForegroundColor Green; exit 0 }
if ($atual -notlike 'Python 3.14.*') { Write-Host "ERRO: esperava Python 3.14.x e achei '$atual'. Nada foi alterado." -ForegroundColor Red; exit 1 }

if (-not (Test-Path $script:Instalador)) { Write-Host 'ERRO: rode o 0_medir.ps1 antes. Nada foi alterado.' -ForegroundColor Red; exit 1 }
$sig = Get-AuthenticodeSignature $script:Instalador
if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notlike '*Python Software Foundation*') {
    Write-Host "ERRO: assinatura do instalador invalida ($($sig.Status)). Nada foi alterado." -ForegroundColor Red; exit 1
}

# Same folder and same options: site-packages, PATH and the worker's absolute path stay valid.
$pythonExe = (Get-Command python).Source
$alvo = Split-Path $pythonExe -Parent
$worker = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\OrcaView-WBC-Worker\Parameters' -ErrorAction SilentlyContinue).Application
if ($worker -and ($worker -ine $pythonExe)) {
    Write-Host "ERRO: o worker usa '$worker', mas o PATH usa '$pythonExe'. Me avise. Nada foi alterado." -ForegroundColor Red; exit 1
}
$uninstall = 'Software\Microsoft\Windows\CurrentVersion\Uninstall'
function Pacotes($Raiz) { Get-ChildItem "${Raiz}:\$uninstall" -ErrorAction SilentlyContinue | ForEach-Object { Get-ItemProperty $_.PSPath } | Where-Object { $_.DisplayName -like 'Python 3.14*' } }
$hklm = @(Pacotes 'HKLM'); $hkcu = @(Pacotes 'HKCU')
$todosUsuarios = Test-Path 'HKLM:\Software\Python\PythonCore\3.14\InstallPath'
$bundle = @($hklm + $hkcu | Where-Object { $_.DisplayName -match '^Python 3\.14\.\d+ \(64-bit\)$' })
if ($bundle.Count -eq 0) {
    Write-Host "ERRO: o instalador do Python atual nao aparece para o usuario $env:USERNAME. Rode com o usuario que instalou. Nada foi alterado." -ForegroundColor Red; exit 1
}
$comPath = @($hklm + $hkcu | Where-Object { $_.DisplayName -like '*Add to Path*' }).Count -gt 0
$comLauncher = @(Get-ChildItem "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall" -ErrorAction SilentlyContinue | ForEach-Object { Get-ItemProperty $_.PSPath } | Where-Object { $_.DisplayName -eq 'Python Launcher' }).Count -gt 0
$opcoes = @('/quiet', "InstallAllUsers=$([int]$todosUsuarios)", "PrependPath=$([int]$comPath)", "Include_launcher=$([int]$comLauncher)", "InstallLauncherAllUsers=$([int]$comLauncher)", "TargetDir=$alvo", '/log', (Join-Path $script:PastaPy 'install.log'))

Exigir-CpLivre
Registrar 'inicio' "$env:USERNAME em $env:COMPUTERNAME - Python $($script:Versao)"
Registrar 'opcoes' ($opcoes -join ' ')
$chaveMaquina = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
$pathAntes = (Get-Item $chaveMaquina).GetValue('Path', $null, 'DoNotExpandEnvironmentNames')
$pathAntes | Set-Content -Encoding Ascii (Join-Path $script:PastaPy 'PATH_maquina_antes.txt')

$ok = $false
$workerAtivo = Parar-Stack
try {
    if (Exigir-SemPython) {
        $p = Start-Process -FilePath $script:Instalador -ArgumentList $opcoes -Wait -PassThru
        Registrar 'instalador' "exit=$($p.ExitCode)"
        $dll = (Get-Item (Join-Path $alvo 'python314.dll')).VersionInfo.FileVersion
        $versao = (& $pythonExe --version 2>&1) -join ''
        Registrar 'versao' "$versao dll=$dll"
        Registrar 'pipcheck' ((& $pythonExe -m pip check 2>&1) -join ' ;; ')
        $importa = & $pythonExe "$PSScriptRoot\lotes_11.py" importar 2>&1
        $importaOk = ($LASTEXITCODE -eq 0)
        Registrar 'importar' ($importa -join ' ;; ')
        $pathIgual = ((Get-Item $chaveMaquina).GetValue('Path', $null, 'DoNotExpandEnvironmentNames') -eq $pathAntes)
        Registrar 'path' "PATH da maquina igual: $pathIgual"
        $ok = ($p.ExitCode -eq 0) -and ($versao -eq "Python $($script:Versao)") -and ($dll -eq $script:Versao) -and $importaOk -and $pathIgual
    }
} catch {
    Registrar 'erro' $_.Exception.Message
} finally {
    Subir-Stack $workerAtivo
}
$ok = (Conferir-Stack $workerAtivo) -and $ok
Fechar $ok
