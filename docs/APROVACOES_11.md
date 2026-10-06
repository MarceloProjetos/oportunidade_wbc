# Aprovações do agente na .11 — o contrato (F3/F4 → F5)

> F3/F4 de `PLANO_MIRA_AGENTE_11.md (removido em 2026-10-06; historico no git)` (02/10/2026). Este arquivo é o contrato que a Mira
> (.90, F5) segue para levar os pedidos ao **canal privado do WhatsApp** e à conversa. A .11
> já funciona sozinha: hoje uma pessoa aprova na Central (`http://192.168.7.11:8077/inicio`).

## A regra que não muda

**O modelo nunca aprova.** O agente só **pede**; o pedido não tem poder nenhum. Quem aprova é
uma pessoa identificada, e é o gesto dela — clique na Central, ou a resposta `aprovar 4821`
tratada **por código, antes do modelo** — que chama a .11. O agente nunca recebe o escopo
`aprovar` (`credenciais.PROIBIDOS_AO_AGENTE`): não tem como aprovar o próprio pedido.

## O fluxo

```
agente (MCP)                 .11 (8077)                        pessoa
  pedir_processar_pedido ──► POST /aprovacoes ── prévia + código ──► Central / WhatsApp
                             (nada executa)                         "aprovar 4821"
                             POST /aprovacoes/4821/aprovar ◄──────── (clique ou .90)
                             executa (as mesmas rotas das pessoas)
  acompanhar_aprovacao ────► GET /aprovacoes/4821 ── executado / falhou / recusado / expirado
```

| Ação | Quem pode pedir (escopo) | Quem aprova (decisão 4) | Teto |
|---|---|---|---|
| `sincronizar_os` (pedido) | `os:sincronizar` | qualquer pessoa identificada | 30/h |
| `forcar_carga` | `oportunidades:carga` | qualquer pessoa identificada | 4/h |
| `processar_pedido` (pedido) | `pedidos_wbc` | `pcp` ou `admin` | 10/h |
| `reiniciar_servico` (um dos 6) | `servico:reiniciar` | só `admin` | 3/h |

- O pedido vale **30 minutos** e é decidido **uma vez** (duas aprovações — tela e WhatsApp — nunca
  rodam as duas: a troca de estado é um UPDATE atômico).
- **Processar** executa exatamente o plano mostrado: na aprovação o Controle de Produção confere de
  novo e, se pedido, oportunidade ou valor mudaram, **recusa** ("o plano mudou"). Reprocessar e
  "forçar" não existem para o agente.
- **Reiniciar**: Controle de Produção com execução em andamento é recusado; o worker para por
  arquivo e, se estiver parado, não é ligado por aqui; a API se reinicia por um processo destacado e
  responde antes de cair.
- A execução passa pelas rotas de sempre com `X-SIS-Usuario` = a pessoa que aprovou: a auditoria
  da API e do Controle de Produção registra **quem aprovou**, não o agente.

## As rotas

| Rota | Escopo | O quê |
|---|---|---|
| `POST /aprovacoes` | o da ação | `{acao, parametros, motivo}` → `201 {aprovacao, como_aprovar}`. `X-SIS-Pedido-Por` e `X-SIS-Usuario` só valem de cliente com `declara_usuario` (o MCP carimba o cliente real) |
| `GET /aprovacoes[?estado=pendente]` | `leitura` | lista, mais novos primeiro |
| `GET /aprovacoes/<id ou código>` | `leitura` | um pedido; o processado traz `execucao_atual` do Controle de Produção |
| `POST /aprovacoes/<id ou código>/aprovar` | `aprovar` | `{pessoa?, canal}` → `202` e executa em segundo plano |
| `POST /aprovacoes/<id ou código>/recusar` | `aprovar` | `{pessoa?, canal, motivo}` |

Quem decide: a tela manda `pessoa` (o nome digitado; o cookie prova a chave, não quem a tem). Um
backend com `declara_usuario` (o .90) manda `X-SIS-Usuario` (a pessoa) e `X-SIS-Papel` (`admin`,
`pcp`…) e pode dizer `canal: "whatsapp"` ou `"mira"`. Recusas: `403 tipo=papel` (papel errado),
`409 ja_decidido`/`expirado`, `404 nao_encontrado`.

## F5 — o grupo da Mira no WhatsApp (.90, web V118.439)

Código no repo do web: `backend/services/mira_aprovacoes.py` (+ a interceptação no topo de
`_processar_um_turno`, em `mira_whatsapp_gateway.py`). Guia do lado de lá: `docs/agent/whatsapp.md`.

1. **Cartão no grupo.** Um vigia no .90 (tarefa no loop principal, a cada 30 s, só onde a Mira roda) lê
   `GET /aprovacoes?estado=pendente` e posta cada pedido novo no grupo: código, o que é, a prévia, quem
   pediu, quem aprova, até quando vale, e as duas linhas "Para aprovar, responda: aprovar 4821" /
   "Para recusar: recusar 4821". Quando o pedido termina (executado, falhou, recusado ou expirou — por
   qualquer canal, inclusive a Central), o desfecho volta ao grupo.
2. **A decisão, antes do modelo.** A mensagem INTEIRA tem de ser o comando: `aprovar 4821`,
   `Aprovo 4821.`, `aprovar 48 21` (o áudio transcrito pontua e separa número) ou `recusar 4821 motivo`.
   Só vale do dono (`MIRA_OWNER_JID`). O .90 chama `POST /aprovacoes/4821/aprovar|recusar` com
   `canal: "whatsapp"`, `X-SIS-Usuario` = o nome do dono (ASCII) e `X-SIS-Papel: admin`.
3. **A Mira também pede** (`pedir_processar_pedido`, `pedir_reiniciar_servico`, `pedir_forcar_carga`;
   `sincronizar_pedido_os` segue no `CONFIRMAR` de sempre — regra 0). Ela pede com **outra** credencial.

**Duas credenciais, de propósito:**

| No .90 (`.env`) | Cliente na .11 | Escopos | Faz |
|---|---|---|---|
| `OPORTUNIDADE_WBC_API_KEY` | `orcaview-90` (declara usuário) | os de antes **+ `aprovar`** | decide — só pelo `aprovar 4821` do dono |
| `OPORTUNIDADE_WBC_AGENTE_KEY` | `mira-agente` (**`--agente`**, declara usuário) | `leitura`, `oportunidades:carga`, `pedidos_wbc`, `servico:reiniciar` | só **pede** |

Um modelo enganado por um texto encaminhado alcança só a chave de agente — que a .11 nunca deixa
aprovar (`PROIBIDOS_AO_AGENTE`) e que obedece ao interruptor e ao expediente (seg–sex 7h–19h). No
.90 há catraca: a rota de decisão só aparece em `decidir()`, e só o gateway chama `decidir()`.

- O cartão sai da conta do dono e **volta** pelo `message_create`: o anti-eco do Node é a 1ª trava; a
  mensagem inteira ter de ser o comando é a 2ª (teste: nada que o bot posta vira decisão).
- O vigia guarda em memória o que já anunciou: depois de um restart do .90, um pedido ainda pendente é
  anunciado de novo (inofensivo — cada pedido é decidido uma vez).
- Cada volta do vigia é um GET auditado na .11 (~2,9 mil linhas/dia de `orcaview-90`); a auditoria não
  foi afrouxada para isso.
