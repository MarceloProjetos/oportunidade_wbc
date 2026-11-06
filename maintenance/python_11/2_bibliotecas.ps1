# ETAPA 2 - para os 6 servicos, instala os lotes 1 a 3 um por vez e religa. Poucos minutos fora do ar.
# PRE-REQUISITO: 1_python.ps1 concluido (Python 3.14.10), PowerShell como Administrador.
# Lote que quebrar volta sozinho ao pip freeze de antes dele, e os seguintes nao entram.
. (Join-Path $PSScriptRoot 'comum.ps1')
Exigir-Administrador
Exigir-Maquina11

$atual = (& python --version 2>&1) -join ''
if ($atual -ne "Python $($script:Versao)") { Write-Host "ERRO: rode o 1_python.ps1 antes (achei '$atual'). Nada foi alterado." -ForegroundColor Red; exit 1 }

Exigir-CpLivre
Registrar 'inicio' "$env:USERNAME em $env:COMPUTERNAME - bibliotecas lotes 1 a 3"
$ok = $true
$workerAtivo = Parar-Stack
try {
    if (-not (Exigir-SemPython)) { throw 'ainda ha Python rodando' }
    foreach ($lote in '1', '2', '3') {
        $alvos = @(& python "$PSScriptRoot\lotes_11.py" alvos $lote)
        $antes = Join-Path $script:PastaPy "freeze_antes_lote$lote.txt"
        & python -m pip freeze 2>$null | Set-Content -Encoding Ascii $antes
        $saida = & python -m pip install --quiet --disable-pip-version-check @alvos 2>&1
        $pipOk = ($LASTEXITCODE -eq 0)
        Registrar 'pip' "lote $lote exit=$LASTEXITCODE $($alvos -join ' ') $(($saida | Select-Object -Last 3) -join ' ;; ')"
        $check = (& python -m pip check 2>&1) -join ' ;; '
        $checkOk = ($LASTEXITCODE -eq 0)
        $importa = & python "$PSScriptRoot\lotes_11.py" importar 2>&1
        $importaOk = ($LASTEXITCODE -eq 0)
        Registrar 'conferir' "lote $lote pip check: $check | $($importa -join ' ;; ')"
        if (-not ($pipOk -and $checkOk -and $importaOk)) {
            Registrar 'erro' "lote $lote quebrou: voltando ao freeze de antes dele"
            $volta = & python -m pip install --quiet --disable-pip-version-check -r $antes 2>&1
            Registrar 'pip' "volta do lote $lote exit=$LASTEXITCODE $(($volta | Select-Object -Last 2) -join ' ;; ')"
            $ok = $false
            break
        }
    }
    & python -m pip freeze 2>$null | Set-Content -Encoding Ascii (Join-Path $script:PastaPy 'freeze_11_depois.txt')
    Registrar 'freeze' ((Get-Content (Join-Path $script:PastaPy 'freeze_11_depois.txt')) -join ' ')
} catch {
    Registrar 'erro' $_.Exception.Message
    $ok = $false
} finally {
    Subir-Stack $workerAtivo
}
$ok = (Conferir-Stack $workerAtivo) -and $ok
Fechar $ok
