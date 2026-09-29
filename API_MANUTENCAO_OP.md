# API — Manutenção de OP (buscar, liberar, replanejar e encerrar Ordens de Produção)

Documento para quem vai **consumir** a API. Ela faz, por JSON, o que a tela **Manutenção de
OP** do Controle de Produção faz (servidor `192.168.7.11`, porta **8080**):

- **buscar** as Ordens de Produção de um pedido de venda;
- **liberar** OPs (Planejada → Liberada);
- **replanejar** OPs (Liberada → Planejada) — recusando a que já tem saída de insumo lançada;
- **encerrar** OPs — escolhidas ou todas de um pedido — **com movimentação de estoque**
  (saída dos insumos + entrada do produto), em duas etapas: conferir e executar;
- **acompanhar** e **interromper** a execução.

A API não tem regra própria: ela chama as **mesmas funções da tela** (Replanejar, que não está na
tela, usa as mesmas da linha de comando), com as mesmas recusas e as **mesmas mensagens**. O que ela grava vai **direto ao SAP de produção** (`SBOALTAMIRAPROD`) pelo
Service Layer.

> **Desde 29/09/2026:** `POST /replanejar` (seção 4b) e o "Interromper" do Encerrar parando só
> entre uma OP e outra (seção 6).

---

## 0. Em uma olhada

Base: `http://192.168.7.11:8080/api/manutencao-op`

| Rota | O que faz | Grava no SAP? |
| --- | --- | --- |
| `GET /pedidos/{pedido}/ops` | OPs de um pedido, com filtros opcionais | não |
| `POST /liberar` | Libera as OPs informadas | **sim**, assim que a execução começa |
| `POST /replanejar` | Devolve as OPs informadas para Planejada | **sim**, assim que a execução começa |
| `POST /encerrar/conferir` | Monta o plano do encerramento e devolve um token | não |
| `POST /encerrar/executar` | Executa o plano conferido | **sim — irreversível** |
| `GET /execucoes/{id}` | Estado de uma execução | não |
| `POST /execucoes/{id}/cancelar` | Interrompe uma execução | não desfaz nada |

---

## 1. Antes de tudo — quatro fatos que mudam o jeito de integrar

1. **`DocNum` ≠ `DocEntry`.** A mesma OP tem dois números no SAP. **Esta API só aceita
   `DocNum`** — o número que aparece na tela do SAP, tanto de OP quanto de pedido. O `DocEntry`
   (chave interna) aparece em algumas respostas, só para conferência.

   | | O que é | Exemplo |
   | --- | --- | --- |
   | **DocNum** | O número da tela do SAP. É o que as pessoas falam. | OP `129850` |
   | **DocEntry** | A chave interna da tabela. | `131431` |

2. **Liberar, Replanejar e Encerrar rodam em segundo plano.** O `POST` responde **202** na hora, com o `id`
   da execução; o resultado vem depois, em `GET /execucoes/{id}`. Encerrar dezenas de OPs leva
   **minutos** (cerca de quatro chamadas ao SAP por OP).
3. **Uma execução por vez** no módulo Manutenção de OP — **somando a tela e a API**. Se já houver
   uma rodando (de qualquer origem), a nova é recusada com `409 ocupado` e a resposta diz qual
   está rodando.
4. **Encerrar lança estoque e não tem volta.** Para cada OP: libera (se estiver Planejada) →
   **saída dos insumos** → **entrada do produto** → encerra. Estornar é cancelar a entrada e a
   saída no SAP, à mão. **Não use para "fechar só o status"** de OPs cuja produção não passou
   pelo SAP: o encerramento baixaria insumo e daria entrada de produto na data de hoje.

---

## 2. Base URL e autenticação

```
http://192.168.7.11:8080/api/manutencao-op
```

Só é alcançável **de dentro da rede da empresa**.

Toda chamada exige o cabeçalho:

```
X-API-Key: <sua chave>
```

Peça a chave ao Marcelo — ela não vai neste documento. **Só o cabeçalho vale**: a chave na URL
(`?key=`) e o login da tela (cookie) são recusados aqui. Sem chave, ou com chave errada: **401**.

Os `POST` recebem JSON: mande `Content-Type: application/json`.

### `solicitante` — quem pediu

Toda chamada que grava (e o cancelamento) exige `solicitante` no corpo: o nome ou login de
**quem pediu a operação**, de 1 a 80 caracteres, sem quebra de linha. Ele vai para o log do
servidor e para o histórico da tela **Execuções** ("por joao.silva · API"). Mande a pessoa que
apertou o botão no seu sistema, não o nome do sistema.

É o que você declara — a chave é uma só, então o servidor não tem como conferir. Não mande
senha, e-mail pessoal nem nada que não possa aparecer num log.

---

## 3. `GET /pedidos/{pedido}/ops` — buscar as OPs de um pedido

Leitura pura. `{pedido}` é o **DocNum do pedido de venda**.

```bash
curl "http://192.168.7.11:8080/api/manutencao-op/pedidos/83955/ops" -H "X-API-Key: SUA_CHAVE"
```

Filtros opcionais (query string), os mesmos da tela:

| Parâmetro | Valores | Como funciona |
| --- | --- | --- |
| `op_de` | DocNum de OP | Sozinho: **só essa** OP. Com `op_ate`: a faixa |
| `op_ate` | DocNum de OP | Exige `op_de` (sozinho → 400) |
| `status_de` | `P`, `R`, `L` ou `C` | Sozinho: só esse status. Com `status_ate`: a faixa |
| `status_ate` | `P`, `R`, `L` ou `C` | Exige `status_de` |

Status: `P` Planejada · `R` Liberada · `L` Encerrada · `C` Cancelada.

⚠️ A faixa de status é **alfabética** (`C < L < P < R`), não a ordem da vida da OP. "De `P` até
`L`" está invertida e volta **400** explicando como inverter. Para todos os status, omita os dois.

⚠️ Algumas mensagens de erro dos filtros citam os nomes da linha de comando (`--op-de`,
`--status-ate`): são as mesmas da tela e da CLI. Aqui os parâmetros são `op_de`, `status_ate`.

OPs **Canceladas não aparecem** na busca por pedido (como na tela). A ordem é DocNum decrescente.

Resposta:

```json
{
  "ok": true,
  "pedido": 83955,
  "total": 1,
  "ops": [
    {
      "op": 129850,
      "status": "P",
      "status_desc": "Planejada",
      "item": "PPLPRTGALVA175000000#0#0#1050",
      "produto": "PORTA PALETE GALVANIZADO 1750",
      "planejada": 12,
      "apontada": 0,
      "restante": 12,
      "baixada": 0,
      "data_pedido": "2026-05-28",
      "data_inicio": "2026-05-29",
      "data_vencimento": "2026-06-07",
      "cliente_codigo": "C011680",
      "cliente": "CLIENTE EXEMPLO LTDA",
      "acoes_possiveis": ["liberar", "encerrar"]
    }
  ]
}
```

### O campo que economiza seu trabalho: `acoes_possiveis`

Já vem calculado pelas mesmas regras que as ações aplicam. Use para habilitar e desabilitar
botões em vez de reimplementar a regra:

| Status da OP | `acoes_possiveis` |
| --- | --- |
| Planejada, apontada < planejada | `["liberar", "encerrar"]` |
| Planejada, apontada ≥ planejada | `["liberar"]` |
| Liberada, nada baixado, apontada < planejada | `["replanejar", "encerrar"]` |
| Liberada, nada baixado, apontada ≥ planejada | `["replanejar"]` |
| Liberada, com saída de insumo lançada (`baixada` > 0) | `["encerrar"]` se apontada < planejada, senão `[]` |
| Encerrada | `[]` |

`baixada` é quanto dos insumos da OP já saiu do estoque (soma do que foi baixado). Mais que zero
quer dizer que houve **saída de insumo lançada** — e aí a OP não volta para Planejada (seção 4b).

---

## 4. `POST /liberar` — Planejada → Liberada

```bash
curl -X POST "http://192.168.7.11:8080/api/manutencao-op/liberar" \
     -H "X-API-Key: SUA_CHAVE" -H "Content-Type: application/json" \
     -d '{"ops": [129850, 129851], "solicitante": "joao.silva"}'
```

| Campo | Obrigatório | Valores |
| --- | --- | --- |
| `ops` | **sim** | Lista de DocNum de OP (número ou texto só com dígitos) |
| `solicitante` | **sim** | Quem pediu (seção 2) |

Para liberar todas as OPs de um pedido: busque (seção 3) e mande os números.

Regras, iguais às da tela:

- Se **alguma** OP da lista estiver Encerrada ou Cancelada, **o lote inteiro** é recusado
  (`409 status_terminal`, com as OPs em `detalhes`) e nada é gravado.
- OP que **já está Liberada** não é regravada: vai para `ignoradas` com "já estava Liberada".
  Por isso **repetir a chamada é seguro**.
- Se **algum número não existir** no SAP, **o lote inteiro** é recusado (`404 nao_encontrada`,
  com os números em `detalhes`) — um erro de digitação não some no meio das outras.

Resposta — **202**, a execução começou:

```json
{
  "ok": true,
  "execucao": {
    "id": "2b5bcade16ec",
    "nome": "Liberar OPs",
    "descricao": "129850",
    "situacao": "na fila",
    "terminada": false,
    "com_falhas": false,
    "desfecho": "fila",
    "passo": "",
    "passos_feitos": 0,
    "passos_total": 0,
    "percentual": null,
    "criada_em": "2026-09-29T12:05:54",
    "duracao_segundos": null,
    "linhas": [],
    "resultado": null,
    "erro": null,
    "solicitante": "joao.silva",
    "origem": "api",
    "parada_pedida": false,
    "estado": "/api/manutencao-op/execucoes/2b5bcade16ec"
  }
}
```

Os campos estão explicados na seção 6.

Acompanhe pelo `estado` (seção 6). No fim, `resultado` traz:

| Lista | O que tem |
| --- | --- |
| `alteradas` | OPs liberadas |
| `ignoradas` | OPs que não precisaram de nada, com `motivo` |
| `com_erro` | OPs que o SAP recusou, com `motivo` — as outras seguem normalmente |

Cada OP vem como `{"doc_num", "doc_entry", "status", "item_code", "planejada", "apontada",
"pedido", "baixada"}` — `status` é o que ela tinha **antes** da execução.

---

## 4b. `POST /replanejar` — Liberada → Planejada

```bash
curl -X POST "http://192.168.7.11:8080/api/manutencao-op/replanejar" \
     -H "X-API-Key: SUA_CHAVE" -H "Content-Type: application/json" \
     -d '{"ops": [157426], "solicitante": "joao.silva"}'
```

Mesmo corpo, mesma resposta (**202**) e mesmas regras de lote do Liberar (seção 4) — mais uma:

- **OP com saída de insumo lançada não volta para Planejada.** Se **alguma** OP Liberada da lista
  já teve insumo baixado, **o lote inteiro** é recusado com `409 saida_lancada` (as OPs e o
  quanto foi baixado em `detalhes`) e nada é gravado. A saída precisa ser **cancelada no SAP**
  antes; senão ficaria estoque movimentado numa OP Planejada. Se não foi possível saber quanto
  foi baixado, a OP também é recusada (`"baixado": "desconhecido"`).
- OP que **já está Planejada** vai para `ignoradas` ("já estava Planejada"), sem gravar.

Use `acoes_possiveis` da busca: `"replanejar"` só aparece quando a OP está Liberada e nada foi
baixado. Replanejar **não existe na tela** Manutenção de OP — só aqui e na linha de comando, com a
mesma regra.

---

## 5. Encerrar — conferir, mostrar, executar

Duas etapas, como na tela. A primeira calcula e **não grava**; a segunda só aceita o que a
primeira calculou.

### 5.1 `POST /encerrar/conferir`

Mande **as OPs** ou **o pedido** — um dos dois, nunca ambos:

```bash
# todas as OPs de um pedido
curl -X POST "http://192.168.7.11:8080/api/manutencao-op/encerrar/conferir" \
     -H "X-API-Key: SUA_CHAVE" -H "Content-Type: application/json" \
     -d '{"pedido": 83955}'

# OPs escolhidas
curl -X POST "http://192.168.7.11:8080/api/manutencao-op/encerrar/conferir" \
     -H "X-API-Key: SUA_CHAVE" -H "Content-Type: application/json" \
     -d '{"ops": [129850, 129851]}'
```

Resposta — o plano:

```json
{
  "ok": true,
  "plano": {
    "token": "V3VbL1eIR6MfZ5fkBAiIa10s85l9Djuw",
    "operacao": "Encerrar OPs do pedido 83955",
    "valido_ate": "2026-09-29T12:15:54",
    "resumo": {"a_encerrar": 2, "a_liberar_antes": 1, "listadas": 2},
    "itens": [
      {
        "ordem": 1, "op": 129850, "doc_entry": 131431,
        "item": "PPLPRTGALVA175000000#0#0#1050",
        "planejada": 12, "apontada": 0, "status": "P", "status_atual": "Planejada",
        "acao": "LIBERAR + saída + entrada + encerrar", "processar": true
      },
      {
        "ordem": 2, "op": 129851, "doc_entry": 131432,
        "item": "PPLPRTGALVA175000000#0#0#1050",
        "planejada": 12, "apontada": 4, "status": "R", "status_atual": "Liberada",
        "acao": "saída + entrada + encerrar", "processar": true
      }
    ]
  }
}
```

- **A ordem é calculada, não escolhida:** filha antes da mãe, porque a saída de insumo de uma
  OP "mãe" consome o que a OP "filha" produz. É a ordem que a execução vai seguir.
  Dependência circular entre OPs → `409 ciclo`, nada é feito.
- **`acao` por OP:**

  | Situação da OP | `acao` | `processar` |
  | --- | --- | --- |
  | Planejada, apontada < planejada | `LIBERAR + saída + entrada + encerrar` | `true` |
  | Liberada, apontada < planejada | `saída + entrada + encerrar` | `true` |
  | apontada ≥ planejada | `ignorada (apontada = planejada)` | `false` |
  | Encerrada | `já encerrada — ignorada` | `false` |
  | Cancelada | `cancelada — não pode ser encerrada` | `false` |

  Uma OP Planejada é **liberada antes**: no SAP, só se aponta produção em OP Liberada.
- Com `ops`, número que **não existe** no SAP recusa tudo (`404`, com os números em `detalhes`).
- Nenhuma OP com `processar: true` → `409 nada_a_encerrar`, com os `itens` e o motivo de cada um.
- O **token vale 10 minutos e uma vez só**. Reiniciar o servidor também o invalida. Deixar
  vencer é o jeito de desistir — não há rota de cancelar plano.

**Mostre o plano à pessoa antes de executar.** É para isso que ele existe.

### 5.2 `POST /encerrar/executar`

```bash
curl -X POST "http://192.168.7.11:8080/api/manutencao-op/encerrar/executar" \
     -H "X-API-Key: SUA_CHAVE" -H "Content-Type: application/json" \
     -d '{"token": "V3VbL1eIR6MfZ5fkBAiIa10s85l9Djuw", "solicitante": "pcp.ana"}'
```

Resposta — **202**, igual ao Liberar (seção 4), com `nome` = a `operacao` do plano.

- Só entram as OPs com `processar: true`, **na ordem do plano**.
- O token **só é gasto quando a execução começa**: se o módulo estiver ocupado (`409 ocupado`),
  faltar `solicitante` (`400`) ou a escrita estiver desligada no servidor (`503`), ele continua
  valendo e pode ser usado de novo dentro dos 10 minutos.
- Token vencido, já usado ou desconhecido → `409 confirmacao_invalida`: confira de novo.

No fim, `resultado` traz:

| Lista | O que tem |
| --- | --- |
| `finalizadas` | OPs encerradas, com `saida_docentry` e `entrada_docentry` (os documentos de estoque; `saida_docentry` vem vazio quando não havia insumo a baixar) e `foi_liberada` |
| `com_erro` | OPs que falharam, com `etapa` (onde parou), `motivo` e `liberacao` |
| `puladas` | OPs que **dependiam** de uma que falhou — não foram tentadas, para não faltar o insumo que ela produziria |
| `ignoradas` | OPs sem nada a fazer, com `motivo` |
| `interrompidas` | OPs que **não começaram** porque a execução foi interrompida (seção 6) |

`liberacao` numa OP com erro:

- `"desfeita"` — nada foi lançado, e a OP voltou para Planejada;
- `"mantida (saída já lançada)"` — ⚠️ **a saída dos insumos já foi lançada** e a OP ficou
  Liberada. A saída precisa ser **cancelada no SAP** antes de qualquer outra coisa nessa OP;
- `"falhou (...)"` — nada foi lançado, mas a OP ficou Liberada (a volta para Planejada falhou).

---

## 6. Acompanhar e interromper

### `GET /execucoes/{id}`

É o mesmo estado que a tela **Execuções** consulta, mais quem pediu. Consulte a cada **2
segundos** e pare quando `terminada` for `true`.

```bash
curl "http://192.168.7.11:8080/api/manutencao-op/execucoes/2b5bcade16ec" -H "X-API-Key: SUA_CHAVE"
```

| Campo | O que é |
| --- | --- |
| `id`, `nome`, `descricao` | A execução |
| `situacao` | `na fila` · `executando` · `concluída` · `erro` · `cancelada` |
| `desfecho` | **O campo a ler** (sem acento): `fila` · `rodando` · `ok` · `falhas` · `erro` · `cancelada` |
| `terminada` | `true` quando acabou (de qualquer jeito) |
| `com_falhas` | Terminou, mas o `resultado` tem OPs em `com_erro` |
| `passo`, `passos_feitos`, `passos_total`, `percentual` | Progresso |
| `linhas` | O log da execução, uma linha por passo (até 500) |
| `resultado` | O que foi feito (seções 4, 4b e 5.2); `null` enquanto roda |
| `erro` | A mensagem, quando `situacao` = `erro` |
| `criada_em`, `duracao_segundos` | Horários (hora local do servidor) |
| `solicitante`, `origem` | Quem pediu, e `api` ou `tela` |
| `parada_pedida` | `true` depois de um pedido de interrupção, enquanto a OP em curso termina |
| `estado` | O caminho desta mesma consulta |

⚠️ **`situacao: "concluída"` não quer dizer que deu tudo certo.** Quer dizer que a execução não
quebrou. Se alguma OP falhou, `desfecho` é `falhas` e as OPs estão em `resultado.com_erro`.

Esta rota só enxerga execuções da Manutenção de OP. Depois de terminar, a execução continua
consultável — o servidor guarda **as 30 mais recentes**, e elas sobrevivem a um reinício.
Mais antiga que isso → `404`.

### `POST /execucoes/{id}/cancelar`

```bash
curl -X POST "http://192.168.7.11:8080/api/manutencao-op/execucoes/2b5bcade16ec/cancelar" \
     -H "X-API-Key: SUA_CHAVE" -H "Content-Type: application/json" \
     -d '{"solicitante": "joao.silva"}'
```

Resposta: `{"ok": true, "cancelada": true, "entre_etapas": true}` — `cancelada` é `false` se
ela já tinha terminado.

Como a execução para depende do que ela faz:

- **Encerrar** (`entre_etapas: true`): **a OP em curso termina a cadeia** (saída → entrada →
  encerra, ou o próprio erro) e as próximas **não começam** — elas vão para
  `resultado.interrompidas`, e a execução termina com `situacao: "cancelada"`. Nunca fica uma OP
  com o insumo baixado e o produto sem entrada por causa do "Interromper". Uma chamada ao SAP que
  trave faz a parada esperar o tempo limite do servidor (até 60 s). Se o pedido chegar com a
  última OP já em andamento, nada fica de fora e a execução termina `concluída`.
- **Liberar e Replanejar** (`entre_etapas: false`): para entre uma OP e outra — cada OP é uma
  gravação só.

⚠️ **Interromper não desfaz nada.** O que já foi gravado no SAP continua lá.

---

## 7. Códigos de resposta

Todo erro vem no mesmo formato, com uma frase pronta para mostrar ao usuário (a mesma da tela):

```json
{
  "ok": false,
  "tipo": "status_terminal",
  "motivo": "1 OP(s) selecionada(s) estão em status terminal (Encerrada ou Cancelada) e não admitem mudança. Nenhuma OP foi alterada — refaça a busca, porque a lista mudou desde que a tela foi carregada.",
  "detalhes": [{"op": 129852, "item": "PPLPRTGALVA175000000#0#0#1050", "status": "Encerrada"}]
}
```

**Decida pelo `tipo`**, não pelo texto do `motivo` — o texto pode mudar.

| HTTP | `tipo` | O que aconteceu | O que fazer |
| --- | --- | --- | --- |
| `200` | — | Leitura, plano ou cancelamento | Seguir |
| `202` | — | A execução começou | Acompanhar pelo `estado` |
| `400` | `invalido` | Número, faixa ou corpo inválido; `ops` e `pedido` juntos (ou nenhum); `solicitante` ou `token` faltando | Corrigir a chamada |
| `401` | `sem_chave` | `X-API-Key` faltando ou errada | Conferir a chave |
| `404` | `nao_encontrada` | Alguma OP informada não existe (lote inteiro recusado, números em `detalhes`); encerrar um pedido sem OP (a busca devolve lista vazia); execução inexistente ou de outro módulo; rota errada | Conferir o número |
| `405` | `metodo_invalido` | `GET` onde é `POST` (ou o contrário) | Corrigir a chamada |
| `409` | `status_terminal` | Liberar ou Replanejar com OP Encerrada ou Cancelada — lote inteiro recusado (`detalhes`) | Buscar de novo e mandar sem ela |
| `409` | `saida_lancada` | Replanejar com OP que já teve insumo baixado — lote inteiro recusado (`detalhes` com o `baixado`) | Cancelar a saída no SAP antes, ou mandar sem ela |
| `409` | `ciclo` | Encerrar: OPs que dependem umas das outras em círculo (`detalhes`) | Encerrar uma a uma, ou corrigir a estrutura no SAP |
| `409` | `nada_a_encerrar` | Nenhuma OP em condição de encerrar (`itens`) | Nada a fazer |
| `409` | `confirmacao_invalida` | Token vencido, já usado ou desconhecido | Conferir de novo |
| `409` | `ocupado` | Já há execução no módulo, da tela ou da API (`execucao_em_andamento`) | Acompanhar essa; tentar depois |
| `502` | `sap_indisponivel` | O SAP não respondeu à leitura | Tentar em instantes |
| `503` | `escrita_desabilitada` | A escrita está desligada no servidor | Avisar o TI |
| `503` | `historico_indisponivel` | O histórico de execuções não respondeu | Tentar em instantes |

`409 ocupado` traz a execução que está rodando:

```json
{
  "ok": false,
  "tipo": "ocupado",
  "motivo": "O módulo 'manutencao_op' já tem uma execução em andamento (Liberar OPs, iniciada às 12:05:54). Duas execuções simultâneas no mesmo módulo disputariam os mesmos pedidos e OPs.",
  "execucao_em_andamento": {
    "id": "2b5bcade16ec", "nome": "Liberar OPs", "descricao": "129850",
    "origem": "api", "solicitante": "joao.silva", "criada_em": "2026-09-29T12:05:54",
    "estado": "/api/manutencao-op/execucoes/2b5bcade16ec"
  }
}
```

---

## 8. Exemplos completos

### Python

```python
import time

import requests

SERVIDOR = "http://192.168.7.11:8080"
BASE = f"{SERVIDOR}/api/manutencao-op"
HEADERS = {"X-API-Key": "SUA_CHAVE"}
TIMEOUT = (5, 60)   # (conexao, leitura): a conferencia de um pedido grande le bastante do SAP


class RecusaDaApi(RuntimeError):
    def __init__(self, resposta):
        corpo = resposta.json()
        super().__init__(corpo["motivo"])       # frase pronta para mostrar ao usuario
        self.tipo = corpo["tipo"]
        self.corpo = corpo


def _json(resposta):
    if not resposta.ok:
        raise RecusaDaApi(resposta)
    return resposta.json()


def ops_do_pedido(pedido, **filtros):
    r = requests.get(f"{BASE}/pedidos/{pedido}/ops", headers=HEADERS, params=filtros, timeout=TIMEOUT)
    return _json(r)["ops"]


def acompanhar(execucao):
    """Consulta a cada 2 s ate terminar; devolve o estado final."""
    while not execucao["terminada"]:
        time.sleep(2)
        r = requests.get(SERVIDOR + execucao["estado"], headers=HEADERS, timeout=TIMEOUT)
        execucao = _json(r)["execucao"]
    return execucao


def liberar(numeros, quem):
    r = requests.post(f"{BASE}/liberar", headers=HEADERS, timeout=TIMEOUT,
                      json={"ops": numeros, "solicitante": quem})
    final = acompanhar(_json(r)["execucao"])
    if final["desfecho"] != "ok":                # "falhas", "erro" ou "cancelada"
        # "erro" traz a mensagem em final["erro"]; "cancelada" pode nao ter resultado nenhum
        falhas = (final["resultado"] or {}).get("com_erro")
        raise RuntimeError(f"Liberar terminou '{final['desfecho']}': {final['erro'] or falhas}")
    return final["resultado"]


def encerrar_pedido(pedido, quem, pessoa_confirma):
    plano = _json(requests.post(f"{BASE}/encerrar/conferir", headers=HEADERS,
                                timeout=TIMEOUT, json={"pedido": pedido}))["plano"]
    if not pessoa_confirma(plano):               # mostre plano["itens"]; a pessoa decide
        return None                              # o token vence sozinho em 10 min
    r = requests.post(f"{BASE}/encerrar/executar", headers=HEADERS, timeout=TIMEOUT,
                      json={"token": plano["token"], "solicitante": quem})
    return acompanhar(_json(r)["execucao"])


# uso: liberar o que estiver Planejada no pedido
try:
    planejadas = [op["op"] for op in ops_do_pedido(83955) if "liberar" in op["acoes_possiveis"]]
    if planejadas:
        print(liberar(planejadas, quem="joao.silva"))
except RecusaDaApi as recusa:
    if recusa.tipo == "ocupado":
        print("Ja ha execucao rodando:", recusa.corpo["execucao_em_andamento"]["id"])
    else:
        print(recusa.tipo, "-", recusa)
```

### PowerShell (5.1)

```powershell
$base = 'http://192.168.7.11:8080/api/manutencao-op'
$headers = @{ 'X-API-Key' = 'SUA_CHAVE' }

# buscar
$ops = (Invoke-RestMethod "$base/pedidos/83955/ops" -Headers $headers).ops
$planejadas = @($ops | Where-Object { $_.acoes_possiveis -contains 'liberar' } | ForEach-Object { $_.op })

# liberar (o corpo vai em UTF-8 por causa de acentos no solicitante)
$corpo = @{ ops = $planejadas; solicitante = 'joao.silva' } | ConvertTo-Json
$bytes = [Text.Encoding]::UTF8.GetBytes($corpo)
$exec = (Invoke-RestMethod "$base/liberar" -Method Post -Headers $headers `
          -ContentType 'application/json; charset=utf-8' -Body $bytes).execucao

# acompanhar
while (-not $exec.terminada) {
  Start-Sleep -Seconds 2
  $exec = (Invoke-RestMethod ("http://192.168.7.11:8080" + $exec.estado) -Headers $headers).execucao
}
"$($exec.desfecho): $($exec.passo)"
```

No PowerShell 5.1, uma resposta 4xx/5xx vira exceção; o JSON do erro está em
`$_.ErrorDetails.Message` dentro do `catch`.

---

## 9. Recomendações para quem integra

1. **Busque antes de agir** e use `acoes_possiveis` para habilitar os botões. Deixar o usuário
   descobrir pelo erro é pior.
2. **Encerrar: conferir → mostrar o plano → executar.** O plano diz exatamente o que vai
   acontecer, em que ordem, e quais OPs serão liberadas antes. Ninguém deveria confirmar sem ver.
3. **Leia `desfecho`, não só `situacao`.** "concluída" com OPs em `com_erro` é `falhas`.
4. **Consulte o estado a cada 2 segundos** e pare em `terminada: true`. Não é preciso mais rápido.
5. **Repetir Liberar ou Replanejar é seguro** (OP já no destino é ignorada sem gravar).
   **Repetir Encerrar não é possível** (token de uso único) — e é de propósito.
6. **`409 ocupado`: acompanhe a execução que veio na resposta** em vez de tentar de novo em laço.
7. **`solicitante` é a pessoa**, não o sistema. É ele que aparece no histórico quando alguém
   perguntar "quem encerrou esta OP?".
8. **Não use Encerrar para limpar status.** Ele movimenta estoque (seção 1, item 4).
9. **Replanejar só com `"replanejar"` em `acoes_possiveis`.** OP com saída lançada é recusada: a
   saída se cancela no SAP, não aqui.

---

## 10. E a API da porta 8077 (`/ordens-producao`)?

A API do Servidor de Integração na porta 8077 (`API_ORDENS_PRODUCAO.md`) também consulta e
libera **uma** OP. Diferenças:

| | 8077 `/ordens-producao/{n}` | 8080 `/api/manutencao-op` (esta) |
| --- | --- | --- |
| Consultar uma OP pelo número | ✅ | via busca do pedido |
| Liberar | ✅ uma por chamada, na hora | ✅ em lote, em segundo plano |
| Replanejar (voltar para Planejada) | ⛔ | ✅ em lote, recusando OP com saída lançada |
| Encerrar | ⛔ 400 desde 28/09/2026 | ✅ **com** saída e entrada de estoque |
| Entra na trava de uma execução por vez e no histórico de Execuções | não | sim |
| Registra quem pediu | não | sim (`solicitante`) |

Para integração nova, use **esta** API. O futuro da rota de escrita da 8077 ainda vai ser
decidido; quem a usa será avisado antes de qualquer mudança.

---

## 11. Suporte

- Saúde, aberto e sem chave: `GET http://192.168.7.11:8080/health` — `ok`, `producao`
  (grava em produção?), `ocupado` (há execução rodando?), `chave_configurada`, `historico`.
- Dúvida, campo faltando, `502`/`503`: falar com o Marcelo (TI).

Se um `motivo` de erro não estiver claro o suficiente para ser mostrado ao usuário final,
avise — a mensagem é a mesma da tela e pode ser melhorada nos dois lugares ao mesmo tempo.
