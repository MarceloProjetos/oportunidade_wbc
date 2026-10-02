# Aprovações do agente na .11 — o contrato (F3/F4 → F5)

> F3/F4 de `docs/PLANO_MIRA_AGENTE_11.md` (02/10/2026). Este arquivo é o contrato que a Mira
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

## F5 — o canal privado do WhatsApp (no .90; nada feito ainda)

Levantado no código do .90 em 02/10/2026 (só leitura):

- O grupo privado já tem um caminho **sem o modelo**: `mira_confirmacao.detectar_confirmacao`
  (`^/?confirmar\s+(\d{4})$`) no topo de `_processar_um_turno`
  (`backend/services/mira_whatsapp_gateway.py`). Voz transcrita passa pelo mesmo lugar. **Por isso
  o código da .11 é de 4 dígitos.**
- O que a F5 precisa:
  1. **Cartão no grupo** quando nasce um pedido (o .90 consulta `GET /aprovacoes?estado=pendente`
     ou recebe o retorno do pedido). Texto que **não comece** pelo comando — ex.: "… Para aprovar,
     responda: aprovar 4821".
  2. **Interceptar antes do modelo**, ao lado do `confirmar`: `^/?(aprovar|recusar)\s+(\d{4})$` na
     mensagem inteira → `POST /aprovacoes/<código>/aprovar|recusar` com `canal: "whatsapp"`,
     `X-SIS-Usuario` = o dono, `X-SIS-Papel: admin`. A credencial do .90 (`orcaview-90`) precisa
     de `aprovar` e do escopo de cada ação que ela for pedir.
  3. Responder no grupo com o resultado (`GET /aprovacoes/<código>`).
- ⚠️ **Cuidados do .90** (memória e `docs/agent/whatsapp.md`): mexer no WhatsApp só com pedido
  explícito do Marcelo, com teste-catraca; os cartões saem da conta dele e **voltam** pelo
  `message_create` — só o anti-eco do Node os separa. O regex ancorado na mensagem inteira é a
  segunda trava: um cartão ecoado nunca casa com `aprovar 4821` sozinho.
