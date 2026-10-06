# Segurança da .11 — como operar

> **Regra acima de todas (Marcelo, 02/10/2026): segurança acrescenta, nunca tira função.** Tudo o
> que existe hoje continua disponível para quem já usa. As chaves novas recebem **todos** os escopos
> que o cliente usa hoje; o que muda é que cada uma fica identificada, registrada e com limites.

Guia de operação da F1 de `PLANO_MIRA_AGENTE_11.md (removido em 2026-10-06; historico no git)` (02/10/2026): credenciais com escopo,
auditoria, interruptor do agente e firewall do MCP. Tudo se administra **na .11**, no
PowerShell, dentro de `C:\Python\ServidorIntegracaoSAP`.

## O que mudou, em uma linha cada

- **Cada cliente tem a sua chave**, que abre só os seus escopos. A `OS_API_KEY` continua valendo
  como "chave-mestra" (tudo) — **nada quebra no dia do deploy**; os clientes migram um por vez.
- **Toda chamada fica registrada** em `logs\auditoria\<serviço>-AAAA-MM-DD.jsonl` (API, Controle
  de Produção e MCP), por **30 dias**. Nenhuma rota apaga esses arquivos.
- **Agente tem regras próprias**: interruptor (desliga em um passo) e escrita só em dia útil,
  das 7h às 19h. Pessoas não são afetadas.
- **MCP aceita uma chave por cliente** e recusa ferramenta fora do escopo. O `SIS_MCP_TOKEN`
  antigo continua valendo ("mcp-legado") até ser trocado.

## Os escopos

| Escopo | Abre |
| --- | --- |
| `leitura` | consultas da API 8077 e as ferramentas de leitura do MCP |
| `rh` | colaboradores (dados pessoais) |
| `os:sincronizar` | sincronizar OS de um pedido |
| `oportunidades:carga` | forçar a carga de oportunidades |
| `vendas_bi:carga` | forçar a carga do Vendas BI |
| `op:status` | mudar status de OP pela 8077 |
| `historico:apagar` | apagar os históricos de sincronização |
| `pedidos_wbc` | `/api/pedidos-wbc` (8080) |
| `manutencao_op` | `/api/manutencao-op` (8080) |
| `servico:reiniciar` | **pedir** o reinício de um dos 6 serviços (só roda com aprovação de uma pessoa) |
| `aprovar` | aprovar/recusar o que o agente pediu — pessoa ou backend que fala por uma, **nunca** o agente |
| `mcp` | conectar ao MCP (as ferramentas seguem os outros escopos) |
| `admin` | tudo (só a chave-mestra) |

`python -m seguranca escopos` mostra a mesma lista.

## Depois do deploy: criar as chaves e migrar os clientes

Cada `criar` mostra a chave **uma única vez** — copie na hora para o cliente. No arquivo fica só
um resumo (hash); não há como recuperar a chave depois (se perder, revogue e crie outra).

**1. O web do .90** — o que ele usa hoje: consultas, RH e sincronizar OS.

```powershell
python -m seguranca criar orcaview-90 --escopos leitura,rh,os:sincronizar --declara-usuario
```

No `.env` do .90, troque o valor de `OPORTUNIDADE_WBC_API_KEY` pela chave nova e reinicie o
backend. Confira a tela de status e a sincronização de OS.

**2. A outra equipe (Pedidos WBC)** — só a API dela.

```powershell
python -m seguranca criar equipe-pedidos --escopos pedidos_wbc
```

Entregue a chave nova e peça que troquem a `X-API-Key`. Depois disso a chave-mestra deixa de estar
com eles.

**3. O MCP (o próprio serviço, para chamar a API)** — o que as ferramentas dele precisam.

```powershell
python -m seguranca criar mcp-servico --escopos leitura,rh,os:sincronizar,oportunidades:carga
```

Em `mcp\.env`, troque `SIS_API_KEY` pela chave nova e `nssm restart OrcaView-MCP`. O MCP deixa de
poder liberar OP e apagar histórico — ele nunca precisou.

**4. Quem usa o MCP (seu Claude, e no futuro a Mira)** — um token por cliente.

```powershell
python -m seguranca criar mcp-marcelo --escopos mcp,leitura,rh,os:sincronizar,oportunidades:carga
```

No cliente MCP, troque o `Bearer` pelo token novo. Quando todos tiverem trocado, apague a linha
`SIS_MCP_TOKEN` do `mcp\.env` e reinicie o serviço: o token antigo para de valer.

**5. A Mira como agente (quando chegar a hora — F5)** — as mesmas funções que o MCP já tem hoje,
RH incluído (decisão 7), com as regras do agente (interruptor, expediente) e tudo registrado.

```powershell
python -m seguranca criar mira-agente --escopos mcp,leitura,rh,os:sincronizar,oportunidades:carga --agente --declara-usuario
```

As funções **novas** (processar pedido, reiniciar serviço) entram depois da F3, uma por vez.

## F3/F4: aprovações do agente (02/10/2026)

O agente só **pede** escrita; uma pessoa aprova na Central (`/inicio`, seção "Aprovações do
agente") ou respondendo `aprovar 4821` no grupo privado da Mira (F5, abaixo). Contrato e
regras: `docs/APROVACOES_11.md`. Depois do deploy, para as ferramentas `pedir_*` do MCP funcionarem
(só acrescenta; a chave de ninguém muda):

```powershell
python -m seguranca acrescentar mcp-servico --escopos pedidos_wbc,servico:reiniciar --declara-usuario
python -m seguranca acrescentar mcp-marcelo --escopos pedidos_wbc,servico:reiniciar
```

O `--declara-usuario` do `mcp-servico` é o que deixa a .11 registrar **qual** cliente do MCP pediu
(o MCP carimba o cliente real; o modelo não consegue fingir outro).

## F5: aprovar pelo grupo da Mira (02/10/2026)

Duas linhas na .11 — só acrescentam; nenhuma chave existente muda:

```powershell
python -m seguranca acrescentar orcaview-90 --escopos aprovar
```

```powershell
python -m seguranca criar mira-agente --escopos leitura,oportunidades:carga,pedidos_wbc,servico:reiniciar --agente --declara-usuario
```

O segundo mostra uma chave nova **uma vez**. Ela vai no `.env` da raiz do OrçaView **no .90** (o mesmo
arquivo da `OPORTUNIDADE_WBC_API_KEY`), numa linha nova:

```
OPORTUNIDADE_WBC_AGENTE_KEY=<a chave que apareceu>
```

e o backend do .90 é reiniciado. Sem essa linha, os cartões e o `aprovar 4821` funcionam do mesmo jeito;
só a Mira não consegue **pedir** (ela diz isso). Por que duas: `docs/APROVACOES_11.md`, seção F5.

## O dia a dia

```powershell
python -m seguranca listar                         # quem tem chave, escopos, ativo ou revogado
python -m seguranca acrescentar altamira-view --escopos rh   # mesma chave, só ganha escopo; o cliente não muda nada
python -m seguranca revogar equipe-pedidos         # a chave para de valer na próxima chamada
```

**Cliente migrado recebendo 403?** É escopo que ele usava com a chave-mestra e ficou de fora
(regra 0: acrescenta, nunca tira). Veja qual e acrescente com o comando acima:

```powershell
Get-Content logs\auditoria\api-AAAA-MM-DD.jsonl | ConvertFrom-Json | Where-Object { $_.status -eq 403 } | Group-Object cliente, escopo | Select-Object Count, Name
python -m seguranca auditoria api --ultimas 30     # últimas chamadas à API (também: controleproducao, mcp)
python -m seguranca auditoria mcp --dia 2026-10-02
```

**Desligar o agente** (sem reiniciar nada; vale na próxima chamada):

```powershell
python -m seguranca desligar-agente --motivo "investigando"     # nada passa
python -m seguranca desligar-agente --so-escrita                # só consulta
python -m seguranca religar-agente
```

## Firewall do MCP (8078)

**O firewall do Windows da .11 está DESLIGADO** (Marcelo, 02/10/2026): nenhuma regra de firewall tem
efeito nesta máquina — nem a da 8078, nem a `OrcaView-ControleProducao-8080`. A proteção que vale é a das
chaves com escopo + auditoria. Ligar o firewall da máquina mexe em TODAS as portas (8077–8080, RDP,
compartilhamentos) e é decisão separada, dele; o script fica pronto para esse dia. Conferir:
`Get-NetFirewallProfile | Select-Object Name, Enabled`.

Hoje a 8078 responde para a rede inteira. O script fecha para todos, menos os IPs permitidos — **com o
firewall ligado**.

**Antes de fechar, descubra quem usa** (regra "não tirar função"): depois do deploy, a auditoria
registra o IP de cada chamada ao MCP. Deixe passar alguns dias úteis e veja os IPs:

```powershell
Get-Content logs\auditoria\mcp-*.jsonl | ConvertFrom-Json | Group-Object ip | Select-Object Count, Name
```

Coloque **todos** esses IPs em `-Permitidos`. Primeiro rode **sem** `-Aplicar` — ele só mostra:

```powershell
.\maintenance\firewall_mcp_8078.ps1 -Permitidos 192.168.0.90,192.168.0.229
```

Conferido o que vai acontecer, aplique:

```powershell
.\maintenance\firewall_mcp_8078.ps1 -Permitidos 192.168.0.90,192.168.0.229 -Aplicar
```

- O `192.168.0.229` é o notebook do Marcelo **hoje**, pelo Wi-Fi (DHCP): reserve esse IP no
  roteador, ou ele muda e o MCP para de responder para o notebook.
- Desfazer: `.\maintenance\firewall_mcp_8078.ps1 -Remover`.
- A 8077 **não** é fechada por firewall: as pessoas abrem a Sincronização no navegador de qualquer
  PC. Ela fica protegida pelas chaves com escopo.

## Respostas novas para quem usa chave com escopo

| HTTP | `tipo` | Quando |
| --- | --- | --- |
| 403 | `sem_permissao` | A chave não tem o escopo da rota/ferramenta. |
| 403 | `agente_bloqueado` | Chave de agente com o interruptor desligado, ou escrita fora do expediente. |

Quem usa a chave-mestra não vê nenhuma das duas.
