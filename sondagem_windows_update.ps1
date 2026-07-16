# Sondagem de Windows Update / reboot pendente - SOMENTE LEITURA.
#
# Consolida as 3 sondagens que rodaram na .12 (F0 + F0.2 + F0.3) num script unico.
# Roda em qualquer uma das duas maquinas; nada aqui e especifico da .11.
#
# POR QUE EXISTE: o PLANO_WINDOWS_UPDATE (repo SAP_RDP) foi desenhado com medicoes feitas
# numa estacao Windows 11 pt-BR. A sondagem na .12 (Server 2019 en-US) derrubou o nucleo do
# plano: wuauserv DISABLED => todo o COM do Windows Update falha (0x80070422), e o servidor
# esta ha 611 dias sem patch. A .11 pode estar num mundo completamente diferente - e o
# desenho depende de saber qual.
#
# GARANTIA: nao instala, nao baixa, nao agenda, nao reinicia, NAO habilita servico, nao
# muda configuracao nenhuma. So le registro, servicos, WMI e o historico local.
#
# Uso:  powershell -ExecutionPolicy Bypass -File .\sondagem_windows_update.ps1

$ErrorActionPreference = 'SilentlyContinue'
$r = [ordered]@{}

# ============ 1. Identidade / locale / ciclo de boot ============
$os = Get-CimInstance Win32_OperatingSystem
$r.host        = $env:COMPUTERNAME
$r.os          = $os.Caption
$r.build       = "$($os.Version) ($($os.BuildNumber))"
$r.os_language = $os.OSLanguage          # 1033 = en-US, 1046 = pt-BR
$r.culture     = (Get-Culture).Name
$r.boot        = $os.LastBootUpTime
$r.uptime_h    = [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalHours, 1)
$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$r.build_ubr   = "$($cv.CurrentBuildNumber).$($cv.UBR)"   # nivel de patch, independente
$r.os_install  = $os.InstallDate

# ============ 2. Reboot pendente (registro; NAO depende do wuauserv) ============
$m = @()
if (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') { $m += 'CBS' }
if (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') { $m += 'WU' }
$pfro = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations).PendingFileRenameOperations
if ($pfro) { $m += "PFRO($($pfro.Count))" }
$a = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ActiveComputerName' -Name ComputerName).ComputerName
$b = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ComputerName' -Name ComputerName).ComputerName
if ($a -and $b -and $a -ne $b) { $m += 'Rename' }
$r.reboot_pendente = [bool]$m.Count
$r.reboot_motivos  = $(if ($m.Count) { $m -join ',' } else { '-' })

# ============ 3. O agente esta vivo? E a politica manda o que? ============
# Start: 2=Automatic 3=Manual 4=DISABLED. Na .12 e 4 (desligado na mao).
$r.wuauserv_start = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\wuauserv' -Name Start).Start
foreach ($s in 'wuauserv','UsoSvc') {
    $sv = Get-Service -Name $s
    $r["svc_$s"] = $(if ($sv) { "$($sv.Status)/$((Get-CimInstance Win32_Service -Filter "Name='$s'").StartMode)" } else { 'ausente' })
}
# AUOptions: 1=nunca 2=avisa antes de baixar 3=baixa e avisa 4=BAIXA E INSTALA SOZINHO
$pol = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'
$r.gpo_existe = Test-Path $pol
$au = Get-ItemProperty "$pol\AU"
$r.gpo_AUOptions    = $au.AUOptions
$r.gpo_NoAutoUpdate = $au.NoAutoUpdate
$r.gpo_ScheduledDay = $au.ScheduledInstallDay
$r.wsus = (Get-ItemProperty $pol -Name WUServer).WUServer
$r.sccm = [bool](Get-Service -Name CcmExec)

# ============ 4. COM do Windows Update (falha se o agente estiver Disabled) ============
try {
    $t0 = Get-Date
    $sea = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()
    $n = $sea.GetTotalHistoryCount()
    $r.com_hist_total = $n
    $h = $(if ($n -gt 0) { $sea.QueryHistory(0, [Math]::Min(400, $n)) } else { @() })
    # Filtro OBRIGATORIO: Operation=1 (install) e ResultCode 2/3 (sucesso). Sem isso, um
    # update ABORTADO seria reportado como "ultimo patch".
    $ok = @($h | Where-Object { $_.Operation -eq 1 -and ($_.ResultCode -eq 2 -or $_.ResultCode -eq 3) })
    $r.com_validos = $ok.Count
    $r.com_ms      = [math]::Round(((Get-Date) - $t0).TotalMilliseconds)
    $u = $ok | Sort-Object Date -Descending | Select-Object -First 1
    $r.com_ultimo  = $(if ($u) { "{0:yyyy-MM-dd} {1}" -f $u.Date, $u.Title.Substring(0, [Math]::Min(45, $u.Title.Length)) })
    # ClientApplicationID e o unico classificador locale-independente (Categories vem vazio).
    $r.com_clients = (($ok | Group-Object ClientApplicationID | Sort-Object Count -Descending |
                       Select-Object -First 3 | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join ' | ')
} catch { $r.com_erro = $_.Exception.Message.Substring(0, [Math]::Min(90, $_.Exception.Message.Length)) }

# ============ 5. A MEDICAO que decide a arquitetura: busca de pendentes ============
# Online=$false usa o cache local do agente; NAO vai a rede e NAO instala nada.
# Na estacao custou 8-30s - caro demais p/ o caminho sincrono do /usage (~1s).
try {
    $t0 = Get-Date
    $s2 = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()
    $s2.Online = $false
    $res = $s2.Search("IsInstalled=0 and IsHidden=0")
    $r.busca_ms  = [math]::Round(((Get-Date) - $t0).TotalMilliseconds)
    $r.pendentes = $res.Updates.Count
} catch { $r.busca_erro = $_.Exception.Message.Substring(0, [Math]::Min(90, $_.Exception.Message.Length)) }

# ============ 6. Get-HotFix: funciona SEM o agente (e WMI/CBS) ============
try {
    $t0 = Get-Date
    $hf = @(Get-HotFix -ErrorAction Stop | Sort-Object InstalledOn -Descending)
    $r.hotfix_ms     = [math]::Round(((Get-Date) - $t0).TotalMilliseconds)
    $r.hotfix_total  = $hf.Count
    $r.hotfix_ultimo = $(if ($hf.Count) { "{0} em {1:yyyy-MM-dd}" -f $hf[0].HotFixID, $hf[0].InstalledOn })
    $r.hotfix_top3   = (($hf | Select-Object -First 3 | ForEach-Object { "{0}/{1:yyyy-MM-dd}" -f $_.HotFixID, $_.InstalledOn }) -join ' | ')
} catch { $r.hotfix_erro = $_.Exception.Message }

# ============ 7. CBS\Packages com a data REAL (FILETIME em 2 REG_DWORD) ============
# CUIDADO: o PS devolve REG_DWORD como Int32 COM SINAL, entao o dword baixo vem NEGATIVO
# com frequencia. O -band 0xFFFFFFFF remonta certo (validado em 300 datas).
$t0 = Get-Date
$linhas = @()
foreach ($k in (Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\Packages')) {
    $p = Get-ItemProperty $k.PSPath
    if ($p.CurrentState -ne 112) { continue }          # 112 = Installed
    if ($null -eq $p.InstallTimeHigh -or $null -eq $p.InstallTimeLow) { continue }
    $ft = ([int64]$p.InstallTimeHigh -shl 32) -bor ([int64]$p.InstallTimeLow -band 0xFFFFFFFFL)
    if ($ft -le 0) { continue }
    $kb = ([regex]'KB\d{6,}').Match($k.PSChildName).Value
    $linhas += [pscustomobject]@{ KB = $kb; Data = [DateTime]::FromFileTimeUtc($ft) }
}
$r.cbs_ms = [math]::Round(((Get-Date) - $t0).TotalMilliseconds)
$r.cbs_instalados = $linhas.Count
$comKb = $linhas | Where-Object { $_.KB } | Sort-Object Data -Descending
$r.cbs_com_kb     = $comKb.Count
$r.cbs_kb_recente = $(if ($comKb.Count) { "{0:yyyy-MM-dd} {1}" -f $comKb[0].Data, $comKb[0].KB })
$r.cbs_top3       = (($comKb | Select-Object -First 3 | ForEach-Object { "{0:yyyy-MM-dd}/{1}" -f $_.Data, $_.KB }) -join ' | ')

# ============ 8. Log de Setup (3a fonte independente) ============
try {
    $ev = @(Get-WinEvent -FilterHashtable @{LogName='Setup'} -MaxEvents 200 -ErrorAction Stop)
    $r.setup_eventos = $ev.Count
    $inst = @($ev | Where-Object { $_.Id -eq 2 })      # Id 2 = pacote instalado
    $r.setup_instalacoes = $inst.Count
    $r.setup_ultima = $(if ($inst.Count) { "{0:yyyy-MM-dd HH:mm} :: {1}" -f $inst[0].TimeCreated, $inst[0].Message.Substring(0, [Math]::Min(55, $inst[0].Message.Length)) })
} catch { $r.setup_erro = $_.Exception.Message.Substring(0, [Math]::Min(70, $_.Exception.Message.Length)) }

# ============ 9. Defender: se atualiza por FORA do wuauserv ============
# Contexto sem o qual "N dias sem patch" e lido como pior do que e.
try {
    $mp = Get-MpComputerStatus -ErrorAction Stop
    $r.defender_assinatura = $mp.AntivirusSignatureVersion
    $r.defender_idade_dias = $mp.AntivirusSignatureAge
    $r.defender_tempo_real = $mp.RealTimeProtectionEnabled
} catch { $r.defender_erro = $_.Exception.Message.Substring(0, [Math]::Min(60, $_.Exception.Message.Length)) }

# ============ 10. venv de producao (pywin32 e dependencia nova aqui?) ============
foreach ($p in 'C:\Python\ServidorIntegracaoSAP\venv\Scripts\python.exe', 'C:\MCP\SAP_RDP\venv\Scripts\python.exe') {
    if (Test-Path $p) {
        $r.venv = $p
        $r.venv_python  = (& $p -c "import sys; print(sys.version.split()[0])" 2>&1)
        $r.venv_pywin32 = (& $p -c "import win32com; print('sim')" 2>&1) -replace "`r?`n", ' '
        break
    }
}

# ============ 11. VEREDITO: ha quanto tempo esta maquina nao ganha patch? ============
$datas = @()
if ($comKb.Count) { $datas += $comKb[0].Data }
if ($hf.Count)    { $datas += $hf[0].InstalledOn }
if ($inst.Count)  { $datas += $inst[0].TimeCreated }
if ($datas.Count) {
    $novo = ($datas | Sort-Object -Descending)[0]
    $r.VEREDITO_ultimo_patch = "{0:yyyy-MM-dd}" -f $novo
    $r.VEREDITO_dias_sem_patch = [math]::Round(((Get-Date) - $novo).TotalDays)
    $r.VEREDITO_fontes = "cbs=$($r.cbs_kb_recente) | hotfix=$($r.hotfix_ultimo) | setup=$($r.setup_ultima -replace ' ::.*','')"
}

[pscustomobject]$r
