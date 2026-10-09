# Relato dos backups do Veeam (ALTHOST → .11)

F7 do `web_orcaview_V118/docs/PLANO_TEO_REDE_E_ROTINAS.md`. O console do Veeam fica no **ALTHOST**
(192.168.7.250); as réplicas, no ALTHOSTBKP (.251) e no ALTBKP2 (.252). Nenhum agente tem senha nessas
máquinas (o WinRM até elas é recusado): o ALTHOST **manda** o estado à .11, de hora em hora, e o Téo/Mira
leem da .11 (`GET /operacao/backup`, tool MCP `estado_backup`).

O script `maintenance/estado_backup.ps1` só **lê** o Veeam (`Get-*`): resultado da última sessão de cada job,
ponto de restauração mais novo de cada réplica, espaço dos repositórios e os serviços do Veeam. Cada bloco tem o
próprio try/catch: o que não deu para ler vai em `erros` e o resto segue.

## Instalação (passos de uma pessoa — 1 vez)

1. **Na .11**, criar a credencial (mostra a chave UMA vez; copie):

   ```powershell
   cd C:\Python\ServidorIntegracaoSAP
   python -m seguranca criar althost-backup --escopos backup:relatar
   ```

   Ela só serve para gravar o próprio relato (`backup:relatar`); não lê nada. Revoga-se com
   `python -m seguranca revogar althost-backup`.

2. **No ALTHOST** (PowerShell como administrador), guardar a chave num arquivo que só SYSTEM e Administradores leem:

   ```powershell
   New-Item -ItemType Directory -Force C:\ProgramData\OrcaView | Out-Null
   Set-Content -Path C:\ProgramData\OrcaView\backup_chave.txt -Value '<a chave do passo 1>' -Encoding ascii
   icacls C:\ProgramData\OrcaView\backup_chave.txt /inheritance:r /grant:r '*S-1-5-18:R' '*S-1-5-32-544:F'
   ```

3. **Copiar o script** para `C:\OrcaView\estado_backup.ps1` (do repo: `maintenance/estado_backup.ps1`).

4. **1º teste, SEM enviar** — conferir a saída contra o console do Veeam (jobs, últimos resultados, réplicas):

   ```powershell
   & 'C:\OrcaView\estado_backup.ps1' -SoMostrar
   ```

   Se vier `"modulo do Veeam: ..."` ou `"conexao ao servidor do Veeam: ..."` em `erros`, anote a mensagem: ela diz
   se o módulo tem outro nome nesta versão ou se a conta precisa de papel no Veeam.

   Confira também, contra o console:
   - **`replicas`** lista as VMs replicadas (.11, .12…) com o ponto mais recente. Se vier
     `"replicas: nenhuma lida embora existam N job(s) de replica"`, a leitura de réplica não funciona nesta versão
     do Veeam: mande a saída para ajustar o script.
   - **`repositorios`**: o espaço é o da **última leitura do Veeam** (ele só atualiza num rescan). Repositório de
     escala (Scale-Out) não aparece nesta lista.
   - **`jobs`**: jobs de agente (backup de computador físico) não aparecem em `Get-VBRJob` no v12; se existirem
     no console e fizerem falta, avise.

5. **Enviar uma vez à mão** e conferir na .11 (`GET /operacao/backup` ou a tool `estado_backup`):

   ```powershell
   & 'C:\OrcaView\estado_backup.ps1'
   ```

   A .11 só aceita de origens conhecidas (`operacao/backup.py:ORIGENS`, começa por 192.168.7.250). Se vier
   **403 origem_nao_permitida**, o ALTHOST saiu por outro IP: veja o IP na auditoria da .11
   (`python -m seguranca auditoria`) e acrescente-o em `ORIGENS`.

6. **Criar a tarefa de hora em hora, como SYSTEM** (sempre `-Command`, nunca `-File`):

   ```powershell
   $acao = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"& 'C:\OrcaView\estado_backup.ps1'`""
   $gatilho = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 1) `
       -RepetitionDuration (New-TimeSpan -Days 3650)
   Register-ScheduledTask -TaskName 'OrcaView-EstadoBackup' -Action $acao -Trigger $gatilho -User 'SYSTEM' -RunLevel Highest
   ```

## Contrato da rota

- `POST /operacao/backup/estado` — escopo `backup:relatar`; origem na lista; corpo até 64 KB (413); 2 por minuto
  (429); forma fechada (`operacao/backup.validar`: chaves conhecidas, tipos, tamanhos; o resto some) → 400 se fora.
  Grava `state/backup_althost.json` com a **hora de recebimento da .11** (a do ALTHOST vai só como `gerado_em`).
- `GET /operacao/backup` — escopo `leitura`: `relato` (ou `null` antes do 1º) e `idade_min` pelo recebimento.

Listas com mais de 60 itens são cortadas (no script e na .11) e o corte vai em `erros`: o relato nunca é
perdido por uma VM a mais.

Nomes de job e mensagens vêm do Veeam: são **dado**, nunca instrução para o modelo.

O corte de 64 KB vale depois de o waitress receber o corpo (o teto dele, para todas as rotas, é o padrão de
1 GiB). Opcional, se quiser: `max_request_body_size` baixo em `serve(...)` — conferir antes que nenhuma rota POST
receba corpo maior.
