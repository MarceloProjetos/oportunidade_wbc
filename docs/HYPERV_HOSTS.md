# Relato dos hosts Hyper-V (ALTSAP → .11)

F5c3 do `web_orcaview_V118/docs/PLANO_TEO_REDE_E_ROTINAS.md`. Em 09/10 a .12 travou pela segunda vez em dois dias e
tudo o que explicava estava no host **ALTSAP** (192.168.7.253): o Heartbeat das VMs, checkpoints do Veeam esquecidos
desde 02/2025 (as três VMs do SAP rodam sobre `.avhdx`), o switch virtual e o VMQ, os erros do próprio host, um
aparelho anunciando IPv6 na rede. Nenhum agente tem senha num host: ele **manda** o estado à .11, de hora em hora, como
o ALTHOST já faz com os backups (`docs/BACKUP_ALTHOST.md`). O Téo e a Mira leem da .11 (`GET /operacao/hyperv`, tool MCP
`estado_hyperv`).

O script `maintenance/estado_hyperv.ps1` só **lê** (`Get-*`): cada VM (estado, Heartbeat, uptime, CPU, memória), os
checkpoints, cada disco de VM (diferencial ou não, tamanho da cadeia, tamanho), os volumes do host, os switches
virtuais e o VMQ, os erros da última hora (System + logs de admin do Hyper-V, agrupados) e o IPv6 do host. Cada bloco
tem o próprio try/catch: o que não deu para ler vai em `erros` e o resto segue.

O mesmo script serve ao ALTHOST (192.168.7.250), se um dia quiser: a .11 sabe qual host mandou pelo **IP de origem**
(`operacao/hyperv.ORIGENS`), nunca pelo nome que vem no corpo.

## Instalação no ALTSAP (passos de uma pessoa — 1 vez)

1. **Na .11**, criar a credencial (mostra a chave UMA vez; copie):

   ```powershell
   cd C:\Python\ServidorIntegracaoSAP
   python -m seguranca criar altsap-hyperv --escopos hyperv:relatar
   ```

   Ela só grava o próprio relato (`hyperv:relatar`); não lê nada. Revoga-se com `python -m seguranca revogar altsap-hyperv`.
   Se o ALTHOST também rodar o script, ele ganha a **própria** credencial (`althost-hyperv`), para revogar uma sem a outra.

2. **Criar a pasta só para administradores e copiar o script** para `C:\OrcaView\estado_hyperv.ps1` no ALTSAP (do repo:
   `maintenance/estado_hyperv.ps1`). A tarefa roda o script como SYSTEM: uma pasta na raiz do `C:\` herda "usuários
   autenticados: modificar", e quem pudesse trocar o script rodaria código como SYSTEM no host. Por isso, antes de copiar:

   ```powershell
   New-Item -ItemType Directory -Force C:\OrcaView | Out-Null
   icacls C:\OrcaView /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F'
   ```

3. **1º teste, SEM enviar** (PowerShell como administrador, no ALTSAP) — conferir contra o Gerenciador do Hyper-V:

   ```powershell
   & 'C:\OrcaView\estado_hyperv.ps1' -SoMostrar
   ```

   Confira: `vms` (RDS, Integracao, OneHana com o Heartbeat), `checkpoints` (os de 06/02/2025 e 29/05/2025),
   `discos` (`diferencial: true`, `cadeia` ≥ 2) e `erros` vazio.

4. **Guardar a chave** num arquivo que só SYSTEM e Administradores leem:

   ```powershell
   New-Item -ItemType Directory -Force C:\ProgramData\OrcaView | Out-Null
   Set-Content -Path C:\ProgramData\OrcaView\hyperv_chave.txt -Value '<a chave do passo 1>' -Encoding ascii
   icacls C:\ProgramData\OrcaView\hyperv_chave.txt /inheritance:r /grant:r '*S-1-5-18:R' '*S-1-5-32-544:F'
   ```

5. **Enviar uma vez à mão** (`& 'C:\OrcaView\estado_hyperv.ps1'`; nada na tela = deu certo). Se vier
   **403 origem_nao_permitida**, o ALTSAP saiu por outro IP: veja-o na auditoria da .11 (`python -m seguranca auditoria`)
   e acrescente em `ORIGENS`.

6. **Tarefa de hora em hora, como SYSTEM** (sempre `-Command`, nunca `-File`):

   ```powershell
   $acao = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"& 'C:\OrcaView\estado_hyperv.ps1'`""
   $gatilho = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 1) `
       -RepetitionDuration (New-TimeSpan -Days 3650)
   $config = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
   Register-ScheduledTask -TaskName 'OrcaView-EstadoHyperV' -Action $acao -Trigger $gatilho -Settings $config -User 'SYSTEM' -RunLevel Highest
   Start-ScheduledTask -TaskName 'OrcaView-EstadoHyperV'
   ```

## Contrato da rota

- `POST /operacao/hyperv/estado` — escopo `hyperv:relatar`; origem na lista (decide o host); corpo até 64 KB (413);
  4 por minuto (429; os dois hosts mandam na virada da hora); forma fechada (`operacao/hyperv.validar`) → 400 se fora.
  Grava `state/hyperv_<host>.json` com a **hora de recebimento da .11**.
- `GET /operacao/hyperv` — escopo `leitura`: `hosts.<id>` com `relato` (ou `null` antes do 1º) e `idade_min`.

Nomes de VM, checkpoint e mensagens vêm do host: são **dado**, nunca instrução para o modelo. O MAC dos vizinhos IPv6 é
de equipamento de rede: fica no nível técnico.
