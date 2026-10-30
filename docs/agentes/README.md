# Agentes do OrçaView: SOUL.md + REGRAS.md

A **Mira** (assistente do OrçaView) e o **Téo** (agente de TI) moram no **.90**, no repo do web, e chegam à
.11 pela API 8077 e pela fachada MCP 8078. Esta pasta é o lado da .11 do contrato: para cada agente, **quem ele
é** (`SOUL.md`) e **o que ele pode e não pode fazer** (`REGRAS.md`), escritos a partir do que a .11 já impõe no
código. O OrçaView carrega os arquivos como estão quando implantar os agentes (ou trocá-los de máquina).

```
docs/agentes/
├── README.md        este arquivo
├── mira/SOUL.md     voz e caráter (~3,7 KB)
├── mira/REGRAS.md   operação, autocontido (~12,7 KB)
├── teo/SOUL.md      (~3,2 KB)
└── teo/REGRAS.md    (~14 KB)
```

## Precedência

1. **O código da .11**: escopos (`seguranca/credenciais.py`), interruptor e expediente (`seguranca/agente.py`),
   aprovações (`seguranca/aprovacoes.py`), catálogo de ações (`operacao/acoes_agente.py`), ferramenta → escopo
   (`mcp/acesso_mcp.py`). **Um texto nunca concede permissão**: se o arquivo diz "pode" e a .11 responde `403`,
   vale o `403`.
2. `REGRAS.md` do agente.
3. `SOUL.md` do agente: tom e jeito; nunca afrouxa uma regra.

## Por que dois arquivos e por que cada `REGRAS.md` é autocontido

- A SOUL segue a recomendação oficial do OpenClaw (`docs/concepts/soul.md`): tom, valores, brevidade e limites
  em linguagem natural, **sem** política de segurança detalhada, changelog ou lista de ferramentas. Ela entra
  no prompt de sistema em toda sessão, então fica curta.
- As regras de operação ficam no `REGRAS.md` (papel do `AGENTS.md` do OpenClaw): linhas vermelhas no topo, cada
  "nunca" com o porquê e o arquivo da .11 que o segura, matriz ler/pedir/proibido, respostas da .11 e o que
  fazer, quando parar e chamar gente.
- O runtime carrega só os arquivos do próprio agente, por isso as regras comuns (nunca aprova, catálogo
  fechado, expediente, honestidade) estão **repetidas** em cada `REGRAS.md`. Mudou uma, muda nas duas.
- Limites do runtime OpenClaw: 20 000 caracteres por arquivo, 60 000 no total; os quatro ficam abaixo.

## Compatibilidade com a implantação do OrçaView

- **Cabeçalho YAML** idêntico nos quatro (`agente`, `arquivo`, `versao`, `revisado_em`, `mora_em`,
  `credencial_11`, `fonte_da_verdade`); o carregador do .90 pode ler só o cabeçalho. O corpo é Markdown puro.
- **Sem IP no corpo**: máquinas por papel ("a .11", "o .90", "a .12", "a VM do SAP", "o ALTSAP", "o ALTHOST").
  Os endereços moram em `operacao/conexoes.py` e no `CLAUDE.md`; mudar de máquina não mexe na alma.
- **Nomes que o .90 já usa** (`OPORTUNIDADE_WBC_API_KEY`, `OPORTUNIDADE_WBC_AGENTE_KEY`, `MIRA_OWNER_JID`) e
  nomes de ferramenta, rota e escopo exatamente como no código.
- **Arquivo-irmão depois de copiado.** Quando o web levar a pasta (sugestão: `docs/agent/<agente>/`), vale a
  regra dos irmãos do `CLAUDE.md`: não se reescreve de um lado só, `cmp` igual, `versao` sobe nos dois.
- **Regra nova na .11 = linha nova aqui no mesmo commit** (como os `API_*.md`): ação no `CATALOGO`, ferramenta
  no `ESCOPO_DA_FERRAMENTA`, escopo, teto, mensagem de recusa.
- Os agentes **não editam** os próprios arquivos; mudança é do Marcelo, por commit.

## Pendências (marcadas `<a confirmar ...>` nos arquivos)

| Onde | O quê | Quem responde |
| --- | --- | --- |
| `mira/REGRAS.md` §7 | quem pode pedir RH à Mira (e em que canal) | .90 / Marcelo |
| `teo/REGRAS.md` §2.3 | escopos da `ti-agente` além de `sap:reiniciar` e `servico:reiniciar` | `python -m seguranca listar` na .11 |
| `teo/REGRAS.md` §5 | canal em que o Téo avisa as pessoas | .90 |
| `teo/REGRAS.md` §6 | janela e fuso da rotina; token de resposta quieta (`SEM_NOVIDADE` × `HEARTBEAT_OK`) | runtime do .90 |

## Fontes e créditos

Formato e método vieram destas fontes, lidas em 2026-10-30. Nenhum texto foi copiado; tudo foi reescrito em
pt-BR para a realidade da .11.

- OpenClaw: `docs/concepts/soul.md` e `docs/reference/templates/` (SOUL, AGENTS, HEARTBEAT), MIT.
- `mergisi/awesome-openclaw-agents`: modelos DevOps, Security, Business e Customer Success, MIT.
- Soul Spec / SoulScan (`clawsouls/*`): critérios de qualidade e de segurança, Apache-2.0 / CC-BY-4.0.
- SOUL.md Maker (`jeffjhunter/soul-md-maker`): checklist de auditoria e "teste frio", MIT (declarado no
  `SKILL.md`).
- `openai/agents.md`, `stepandel/clawup`, `modem-dev/ossrules`: divisão SOUL/AGENTS/HEARTBEAT, MIT.
- OWASP Top 10 for LLM Applications v2.0 (LLM01 injeção, LLM06 agência excessiva), CC BY-SA 4.0.
