# Organograma do escritório — contrato da .11

> F2 do `docs/PLANO_ORGANOGRAMA.md` (02/11/2026). Fonte: `operacao/organograma.toml`; código:
> `operacao/organograma.py`. Quem consome: as agentes no .90 (F3), o MCP e a página inicial da Central.

**Quem é fonte de quê** (reconciliação com o .90, 02/11/2026): o **.90** é a fonte do que cada agente faz
(`mira_agentes.CAPACIDADES`: cada ferramenta com um dono), das instruções (`docs/agentes/`) e das tarefas entre
agentes (`mira_tarefas`). A **.11** é a fonte das **pessoas** (cargo, substitutos do diretor, quem aprova o
quê), dos **temas** em linguagem de gente e da **conferência** cargo × credencial da .11. Um teste no .90
confere os dois cadastros.

**Regra:** o cargo nunca dá permissão. Estas rotas só dizem quem cuida do quê e a quem passar. O que cada
cliente pode fazer continua sendo o escopo da credencial (`docs/SEGURANCA_11.md`), e quem aprova continua
sendo o papel (`docs/APROVACOES_11.md`). Nada que autoriza lê o organograma.

## Rotas (API 8077, escopo `leitura`)

### `GET /operacao/organograma`

```json
{
  "ok": true,
  "versao": 1,
  "membros": [{"id": "mira", "tipo": "agente", "nome": "Mira", "cargo": "...", "cuida_de": ["pedido"],
               "nao_faz": ["..."], "escala_para": ["pcp", "comercial", "marcelo"], "credencial": "mira-agente",
               "pede": ["processar_pedido"], "escopos_esperados": ["leitura"], "apresentacao": "..."},
              {"id": "marcelo", "tipo": "pessoa", "nome": "Marcelo", "cargo": "Diretor", "aprova": ["admin"],
               "contato": "dono", "...": "..."}],
  "temas": [{"id": "ti_servidor", "dono": "teo", "substitutos": ["marcelo"], "descricao": "...", "exemplos": []}],
  "situacao": {"arquivo": "organograma.toml", "em_uso": true, "valido_desde": "2026-11-02T09:00:00",
               "arquivo_valido": true, "erros": []},
  "conferencia": null
}
```

- `conferencia` vem **só para administrador** (cookie da Central, chave-mestra): é a comparação ficha ×
  credencial de cada agente (`situacao`: `ok`, `sobram`, `faltam`, `sem_credencial`,
  `credencial_nao_e_agente`, `escopo_proibido`, `sem_acesso_a_11` — agente que não fala com a .11, como a
  Lia, que não reprova; listas `faltam`, `sobram`, `proibidos`). Nunca traz hash.
  Para qualquer outro cliente com `leitura`, inclusive as agentes, vem `null`: que escopo cada agente tem
  não é informação para todo leitor.
- `situacao.arquivo_valido: false` = o arquivo no disco foi editado errado; a resposta é a **última versão
  válida** e `erros` diz onde (nunca repete o valor — pode ser um telefone).
- `contato` é um apelido; o contato de verdade fica no `.env` do .90 (repo público).

### `GET /operacao/organograma?para=<id>`

A ficha de um membro com nomes resolvidos (`escala_para` vira `[{id, nome, cargo, tipo}]`) e a lista
curta de temas (`id`, `dono`, `descricao`). Para agente, também:

- `pede`: `[{acao, titulo}]` (do `acoes_agente.CATALOGO`);
- `texto_cargo`: a seção **"Seu cargo"** do prompt, pronta. O .90 cola como está (uma fonte só); a última
  linha é sempre a regra: *"O cargo não te dá poder: se uma ferramenta recusar, é a credencial que manda…"*.

`id` desconhecido → `404 {error: "membro_desconhecido", membros: [...]}`. Maiúsculas não importam.

### `GET /operacao/organograma/temas/<tema>`

```json
{"ok": true, "pedido": "financeiro", "casou": false, "tema": "outro", "descricao": "...", "exemplos": [],
 "dono": {"id": "marcelo", "nome": "Marcelo", "cargo": "Diretor", "tipo": "pessoa"},
 "substitutos": [{"id": "felipe", "...": "..."}, {"id": "gabriel", "...": "..."}]}
```

Tema que não existe **não é 404**: vai para `outro` com `casou: false`, então sempre há um dono.

### Erros comuns

| Código | Quando |
| --- | --- |
| 401 | sem credencial |
| 403 `sem_permissao` | credencial sem `leitura` |
| 503 `organograma_invalido` | o arquivo é inválido e a .11 nunca teve uma versão boa desde que subiu (`erros` diz onde) |

## MCP (8078)

`organograma(para="")` e `quem_cuida_de(tema)`, as duas com escopo `leitura` no `ESCOPO_DA_FERRAMENTA`.

## Na .11

- Editar: `operacao/organograma.toml` (git). A API relê sozinha quando o arquivo muda — sem restart.
- Conferir: `python -m operacao.organograma conferir` (sai 1 se um agente vai ser recusado) ou o card
  "Organograma" na página inicial da Central.
- Tirar ou dar escopo: **só** `python -m seguranca` (decisão do Marcelo). A conferência nunca muda nada.
