# 🪪 Colaboradores Kairos — guia de integração

> **Quem trabalha nas três empresas, por empresa e por setor, com cargo, matrícula e
> situação.** Uma chamada HTTP — ou uma pergunta em português, pelo assistente.
> Escrito para você integrar **sem precisar perguntar nada**.

| | |
|---|---|
| **Endpoint** | `GET http://192.168.7.11:8077/rh/colaboradores` |
| **Autenticação** | `X-API-Key` (a mesma dos outros endpoints da 8077) |
| **Atualização** | 1× por dia útil, às **12:40** |
| **Volume hoje** | 251 linhas = **85 ativos + 166 desligados** · 3 empresas |
| **Resposta** | 13–58 KB · ~0,2 s (medido, §8) |
| **Escreve algo?** | Não. Somente leitura. |

**Índice** · [1. O que é](#1-o-que-é-e-o-que-não-é) · [2. Comece por aqui](#2-comece-por-aqui-30-segundos)
· [3. A chamada](#3-a-chamada) · [4. A resposta](#4-a-resposta)
· [5. Status](#5-o-contrato-do-status-a-linha-do-desligado-não-some)
· [6. Sem expediente](#6-sem-expediente--leia-antes-de-usar) · [7. Frescor](#7-frescor-saber-de-quando-é-o-dado)
· [8. Custo e cache](#8-custo-e-cache-com-que-frequência-chamar) · [9. Setores de hoje](#9-os-setores-que-existem-hoje)
· [10. Receitas](#10-receitas-prontas) · [11. MCP](#11-mcp--perguntar-em-português)
· [12. Erros](#12-erros-e-o-que-fazer) · [13. Armadilhas](#13-armadilhas)
· [14. Perguntas frequentes](#14-perguntas-frequentes) · [15. Uso do dado](#15-uso-do-dado-são-pessoas)
· [16. Estabilidade](#16-estabilidade-do-contrato) · [17. Por dentro](#17-por-dentro-para-quem-mantém)

> Os endpoints de **Ordens de Serviço**, **Ordens de Produção** e **Situação dos Pedidos**
> são outra coisa: `API_OS_INTEGRACAO.md`, `API_ORDENS_PRODUCAO.md` e
> `API_SITUACAO_PEDIDOS.md`.

---

## 1. O que é (e o que **não** é)

**É um espelho diário do Kairos**, o sistema de ponto. Uma vez por dia útil, às 12:40, o
OrçaView web lê as três empresas no Kairos e grava o quadro no Supabase; esta API lê esse
espelho e serve para você.

```mermaid
flowchart LR
    K["<b>Kairos</b><br/>o ponto, 3 empresas"] -->|"12:40, dias úteis"| W["<b>Carga diária</b><br/>OrçaView web (.90)"]
    W -->|"grava o espelho"| S[("<b>Supabase</b><br/>kairos_colaboradores")]
    S -->|"só lê"| A["<b>Servidor .11</b><br/>API 8077 · MCP 8078"]
    A --> V["<b>Você</b><br/>1 GET, ou 2 tools"]
```

**O que ele não é:**

- ❌ **Não é tempo real.** Uma admissão de hoje de manhã só aparece depois das 12:40.
- ❌ **Não é a folha.** Não há salário, CPF, endereço ou banco.
- ❌ **Não escreve nada.** Não existe verbo de escrita neste endpoint.
- ❌ **A API não fala com o Kairos.** Ela lê o espelho. Se o Kairos cair, você continua
  recebendo o último quadro bom — e o campo `desatualizado` avisa.

> [!IMPORTANT]
> **Você NÃO precisa de acesso ao Supabase** — nem ao banco, nem ao painel, nem a um
> conector do Supabase no Claude. Quem lê o banco é esta API; é exatamente para isso que
> ela existe. Você precisa de **uma coisa só**: a `X-API-Key` da 8077 (ou o token do MCP,
> se for usar pelo assistente).
>
> Pedir acesso ao banco abriria **122 tabelas dos três aplicativos** para resolver o que
> um endereço HTTP já resolve — e, com as proteções de linha que essas tabelas têm, o
> acesso só funcionaria com a chave que ignora todas elas. Não é o caminho.

---

## 2. Comece por aqui (30 segundos)

```bash
curl -s "http://192.168.7.11:8077/rh/colaboradores?empresa=tecnequip&somente_ativos=1" \
     -H "X-API-Key: SUA_CHAVE"
```

Voltou um JSON com `"ok": true`? Acabou a configuração — o resto deste documento é sobre
o que vem dentro.

> [!TIP]
> No navegador (que não envia cabeçalho), use `?key=SUA_CHAVE` na URL.
> `Authorization: Bearer SUA_CHAVE` também funciona.

---

## 3. A chamada

| Parâmetro | Valores | O que faz |
|---|---|---|
| `empresa` | `altamira` · `tecnequip` · `proalta` | Restringe a uma empresa. Ausente = as três. |
| `somente_ativos` | `1` | Só quem está na ativa. **Ausente = todos**, cada um com seu `status`. |

Os dois são opcionais e combinam. Não há paginação: a resposta vem inteira (§8).

> [!WARNING]
> **Empresa desconhecida é recusada com `400`** — de propósito. Um erro de digitação
> devolveria o quadro de **outra empresa** com HTTP 200, e ninguém perceberia.

```jsonc
// GET /rh/colaboradores?empresa=xyz   → HTTP 400
{
  "ok": false,
  "error": "empresa invalida: use uma de altamira, tecnequip, proalta",
  "empresas_validas": ["altamira", "tecnequip", "proalta"]
}

// GET /rh/colaboradores   (sem a chave)   → HTTP 401
{ "ok": false, "error": "unauthorized" }
```

---

## 4. A resposta

O aninhamento é **empresa → setor → colaboradores**. **Cargo é campo da pessoa**, não um
nível: aninhar por cargo criaria grupos de uma pessoa só na maioria dos casos.

```jsonc
{
  "ok": true,
  "atualizado_em":     "2026-09-01T10:41:43.115705+00:00",  // UTC — use para comparar
  "atualizado_em_br":  "2026-09-01T07:41:43-03:00",         // Brasília — use para mostrar
  "desatualizado":     false,
  "carga_esperada_em": "2026-08-31T12:40-03:00",            // slot usado na conta
  "total": 56,                                              // linhas nesta resposta
  "somente_ativos": true,
  "empresas": [
    {
      "empresa": "tecnequip",
      "total": 56,
      "setores": [
        {
          "setor": "PRODUÇÃO",
          "total": 32,
          "colaboradores": [
            {
              "person_id": 123,                    // ← a chave estável
              "nome": "Fulano da Silva",
              "matricula": "100204",               // crachá — NÃO é o person_id
              "cargo": "Operador de Máquina",
              "status": "ativo",
              "em_ferias_ou_afastado": false,
              "sem_expediente_desde": null,
              "data_admissao": "2021-12-01",
              "data_desligamento": null
            }
          ]
        }
      ]
    }
  ]
}
```

### Os campos

| Campo | Tipo | O que é |
|---|---|---|
| `person_id` | inteiro | Id no Kairos. **Use como chave** (junto com `empresa`). |
| `nome` | texto | Como está no cadastro. Há homônimos — não use como chave. |
| `matricula` | texto \| null | Número do crachá. É o que casa com os eventos de ponto. |
| `cargo` | texto \| null | Descrição do cargo. **Nulo é comum entre desligados** — veja abaixo. |
| `status` | texto | `ativo` · `desligado` · `ausente` — veja a §5. |
| `em_ferias_ou_afastado` | booleano | Sem expediente. **Leia a §6 antes de usar.** |
| `sem_expediente_desde` | data \| null | Início da ausência; `null` com a flag ligada = começou antes da janela. |
| `data_admissao` | data \| null | Admissão. |
| `data_desligamento` | data \| null | Preenchida quando o desligamento tem registro. |

### O que os dados realmente parecem (medido em 01/09/2026, nas 251 linhas)

| Observação | Número |
|---|---|
| `matricula` nula | **0** — todo mundo tem crachá |
| `data_admissao` nula | **0** |
| `cargo` nulo | **120**, e **todos são desligados** (entre ativos: zero) |
| `data_desligamento` nula em quem está `desligado` | **0** — sempre vem preenchida |
| `sem_expediente_desde` preenchida | **0** — hoje as 3 ausências começaram antes da janela (§6) |

Ou seja: **para quem está ativo, você pode contar com nome, matrícula, setor, cargo e
admissão preenchidos.** Nos desligados, cargo costuma faltar — o Kairos não guarda o
cargo de quem saiu com a mesma disciplina.

### Uma linha de quem saiu

```jsonc
{
  "person_id": 987,
  "nome": "Ciclana de Souza",
  "matricula": "100150",
  "cargo": "Supervisor de Expedição",
  "status": "desligado",
  "em_ferias_ou_afastado": false,
  "sem_expediente_desde": null,
  "data_admissao": "2016-03-02",
  "data_desligamento": "2026-07-22"      // ← o que muda em relação a um ativo
}
```

Quem está sem setor no Kairos entra no grupo `"SEM SETOR"` — ninguém é escondido por
causa de um campo em branco. Os `total` de empresa e setor são **contagens prontas**: não
precisa somar o array.

---

## 5. O contrato do status: a linha do desligado **não some**

Esta é a regra mais importante daqui, e a razão de o campo existir.

| `status` | Significa | Quando aparece | Hoje |
|---|---|---|---|
| ✅ `ativo` | Está no quadro. | Veio na carga e não tem desligamento. | 85 |
| ⬜ `desligado` | Saiu, com registro. | Desligamento conhecido — veja `data_desligamento`. | 166 |
| 🟠 `ausente` | Sumiu do cadastro. | Deixou de vir na carga sem registro de desligamento. | 0 |

> [!NOTE]
> **Por que isso importa para você:** se as linhas sumissem, todo programa que guarda
> `person_id` quebraria — ou perderia o histórico — no dia em que alguém fosse desligado.
> Aqui a linha permanece e muda de estado.
>
> Para o quadro atual, filtre por `status == "ativo"` ou peça `?somente_ativos=1`;
> para histórico, peça tudo.

**`ausente` é o caso raro e vale entender:** a pessoa deixou de aparecer no Kairos sem
que houvesse registro de desligamento. Não é erro seu nem nosso — é o cadastro de origem
que mudou sem explicar. Trate como "não está mais no quadro", mas sem data.

---

## 6. Sem expediente — leia antes de usar

> [!CAUTION]
> **O Kairos não diz "férias".** A API dele **não tem** evento de férias nem de
> afastamento. O sinal aqui é indireto: a **ausência** do evento "Horas a Trabalhar" em
> dias que a empresa já fechou.
>
> Então `em_ferias_ou_afastado: true` quer dizer **"está sem expediente"** — pode ser
> férias, atestado, licença ou afastamento, e **o dado não distingue**. Se o seu uso
> exige separar férias de afastamento, este campo não serve: a informação não existe na
> origem.

- Liga com **3 ou mais dias úteis processados** sem expediente — feriado ou dia que o
  Kairos ainda não fechou **não** conta.
- `sem_expediente_desde: null` **com a flag ligada** = a ausência começou antes da janela
  de 30 dias: sabemos que está fora, não desde quando. **É o caso de todos hoje.**
- Quem **nunca** bate ponto (isento de relógio) **não** é marcado.
- Hoje são **3 pessoas em 85 ativas** — é sinal raro, não estado comum.

---

## 7. Frescor: saber de quando é o dado

| Campo | Para que serve |
|---|---|
| `atualizado_em` | Carimbo da última carga, em **UTC**. É o campo para comparar em código. |
| `atualizado_em_br` | O mesmo instante em horário de Brasília. É o campo para **mostrar**. |
| `desatualizado` | `true` = a carga do último 12:40 de dia útil não chegou. |
| `carga_esperada_em` | Contra qual horário a conta acima foi feita. |

> [!NOTE]
> **Não é "mais velho que N horas".** Na segunda de manhã o dado **é** de sexta, e isso
> está certo — a carga só roda em dia útil, e a conta considera feriados nacionais. A
> pergunta que `desatualizado` responde é *"o último horário que já deveria ter rodado
> produziu carga?"*.

**O que fazer quando vier `true`:** continue usando o dado — ele é o último bom conhecido
e não está corrompido. Só **não o apresente como de hoje**: mostre o `atualizado_em_br` ao
lado. Não trave o seu fluxo por causa disso; se persistir por mais de um dia útil, avise.

---

## 8. Custo e cache: com que frequência chamar

Medido em 01/09/2026, da rede interna:

| Chamada | Tamanho | Tempo |
|---|---:|---:|
| `?empresa=tecnequip&somente_ativos=1` | 13,6 KB | 0,25 s |
| `?somente_ativos=1` (as 3 empresas) | 21,0 KB | 0,18 s |
| sem filtro (tudo, com histórico) | 58,2 KB | 0,20 s |

> [!TIP]
> **O dado muda uma vez por dia. Cacheie.** Chamar a cada requisição do seu sistema é
> desperdício dos dois lados. O padrão recomendado:
>
> - guarde a resposta e o `atualizado_em` junto;
> - reconsulte a cada **30–60 minutos** (ou quando o usuário pedir "atualizar");
> - se o `atualizado_em` não mudou, nada mudou — pode reaproveitar o que já tem.
>
> Não há paginação nem `If-Modified-Since`: a resposta vem inteira, e ela é pequena.

---

## 9. Os setores que existem hoje

Referência para quem precisa mapear setores. **Isto muda** — quem manda é o Kairos, e a
lista abaixo é de 01/09/2026, contando **só ativos**:

| Empresa | Ativos | Setores |
|---|---:|---|
| **altamira** | 14 | EXPEDIÇÃO 1 · FERRAMENTARIA 1 · PINTURA 4 · PRODUÇÃO 8 |
| **proalta** | 15 | ADM FINANCEIRO 6 · COMERCIAL 2 · LIMPEZA 1 · OPERACIONAL 1 · RECURSOS HUMANOS 2 · SUPRIMENTOS 3 |
| **tecnequip** | 56 | DESENVOLVIMENTO DE PROJETOS 7 · EXPEDIÇÃO 8 · FERRAMENTARIA 6 · LIMPEZA 1 · PRODUÇÃO 32 · QUALIDADE 1 · T.I 1 |

> [!WARNING]
> **Sem `?somente_ativos=1` aparece um setor `"Principal"` que não existe na prática.**
> São **121 pessoas, todas desligadas** (nenhum ativo) — é onde o Kairos deixa quem saiu.
> Se você for montar uma lista de setores para uma tela ou um filtro, use
> `?somente_ativos=1`, ou vai oferecer "Principal" ao usuário.

Note que os setores são escritos em MAIÚSCULAS no Kairos (menos o tal `"Principal"`), e
que `T.I` tem ponto. Compare sem depender de caixa nem de acento.

---

## 10. Receitas prontas

### Quantas pessoas por setor, no terminal

```bash
curl -s "http://192.168.7.11:8077/rh/colaboradores?empresa=tecnequip&somente_ativos=1" \
     -H "X-API-Key: SUA_CHAVE" \
| jq -r '.empresas[].setores[] | "\(.total)\t\(.setor)"'
```

```text
7   DESENVOLVIMENTO DE PROJETOS
8   EXPEDIÇÃO
6   FERRAMENTARIA
1   LIMPEZA
32  PRODUÇÃO
1   QUALIDADE
1   T.I
```

### Quem saiu nos últimos 90 dias

```bash
curl -s "http://192.168.7.11:8077/rh/colaboradores" -H "X-API-Key: SUA_CHAVE" \
| jq -r --arg corte "$(date -d '-90 days' +%Y-%m-%d)" '
    .empresas[].setores[].colaboradores[]
    | select(.status == "desligado" and .data_desligamento >= $corte)
    | "\(.data_desligamento)  \(.matricula)  \(.nome)"' | sort
```

### Python — quem está na produção

```python
import requests

BASE = "http://192.168.7.11:8077"
r = requests.get(
    f"{BASE}/rh/colaboradores",
    params={"empresa": "tecnequip", "somente_ativos": 1},
    headers={"X-API-Key": CHAVE},
    timeout=20,
)
r.raise_for_status()
dados = r.json()

# avise quando o dado envelheceu, em vez de mostrar como se fosse de agora
if dados["desatualizado"]:
    print(f"⚠ dado de {dados['atualizado_em_br']} — a carga do dia não chegou")

for empresa in dados["empresas"]:
    for setor in empresa["setores"]:
        if setor["setor"] == "PRODUÇÃO":
            for p in setor["colaboradores"]:
                print(p["matricula"], p["nome"], "—", p["cargo"])
```

### Python — sincronizar com o seu cadastro (o caso do `status`)

```python
# A linha de quem saiu CONTINUA vindo: é assim que você descobre o desligamento
# sem precisar comparar duas fotos do quadro.
dados = requests.get(f"{BASE}/rh/colaboradores",
                     headers={"X-API-Key": CHAVE}, timeout=30).json()

for empresa in dados["empresas"]:
    for setor in empresa["setores"]:
        for p in setor["colaboradores"]:
            chave = (empresa["empresa"], p["person_id"])       # a chave real
            if p["status"] == "ativo":
                cadastro.ativar(chave, nome=p["nome"], setor=setor["setor"],
                                cargo=p["cargo"], matricula=p["matricula"])
            else:
                cadastro.desativar(chave, quando=p["data_desligamento"],
                                   motivo=p["status"])          # desligado | ausente
```

### JavaScript — índice por `person_id`

```javascript
const resp = await fetch(
  "http://192.168.7.11:8077/rh/colaboradores",          // sem filtro = todos, com status
  { headers: { "X-API-Key": chave } }
);
const dados = await resp.json();

const porId = new Map();
for (const e of dados.empresas)
  for (const s of e.setores)
    for (const p of s.colaboradores)
      porId.set(`${e.empresa}:${p.person_id}`, { ...p, empresa: e.empresa, setor: s.setor });

// quem saiu continua aqui, com status — é isso que evita quebrar o seu cadastro
const desligados = [...porId.values()].filter(p => p.status !== "ativo");
```

---

## 11. MCP — perguntar em português

Duas *tools*, ambas somente leitura, sobre o mesmo endpoint:

| Tool | Use quando | Devolve |
|---|---|---|
| `listar_colaboradores(empresa, setor, somente_ativos, limite)` | "Quem está na produção da Tecnequip?" · "Fulano ainda trabalha aqui?" | Os nomes, agrupados por setor. |
| `resumo_colaboradores(empresa, somente_ativos)` | "Quantos na expedição?" · "Como o quadro se distribui?" | Só as contagens — cabe na conversa mesmo com o quadro inteiro. |

- **O default das tools é `somente_ativos=true`** (na REST é o contrário: sem o parâmetro
  vêm todos). Passe `false` para ver quem saiu.
- **`setor` casa sem acento e por pedaço**: `"producao"` acha `"PRODUÇÃO"`. Errar o nome
  não devolve lista vazia calada — vem `setores_disponiveis` para tentar de novo.
- **`limite` (padrão 200) corta os nomes, nunca as contagens**: vêm `truncado`,
  `mostrando` e `omitidos` por setor.
- Recurso anexável `sap-integracao://colaboradores` — o quadro ativo em contagens, de
  propósito sem nomes.

### Como registrar no seu cliente

É um **conector personalizado** apontando para a `.11` — **não** o conector "Supabase" do
diretório da Anthropic, que é outra coisa (SQL direto no banco) e não serve aqui.

```bash
claude mcp add --transport http servidor-integracao-sap \
  http://192.168.7.11:8078/mcp \
  --header "Authorization: Bearer SEU_TOKEN_MCP" --scope user
```

No Claude Desktop, o caminho é o mesmo endereço cadastrado como conector remoto.

> [!WARNING]
> **O MCP só responde de dentro da rede.** `192.168.7.11` é endereço interno: o Claude na
> web (claude.ai) **não alcança**. Funciona em cliente rodando numa máquina da empresa
> (Claude Code, Claude Desktop). Se o seu caso é usar pelo navegador, o caminho é a API
> REST chamada pelo seu próprio código.

O token do MCP é **diferente** da `X-API-Key` da REST — peça os dois ao Marcelo.

---

## 12. Erros e o que fazer

| Código | Quando | O que fazer |
|---|---|---|
| `200` | Sucesso — inclusive lista vazia. | Lista vazia é resposta válida (ex.: filtro que não casa ninguém). |
| `400` | `empresa` desconhecida. | A resposta traz `empresas_validas`. Corrija o valor. |
| `401` | Sem chave, ou chave errada. | Confira o cabeçalho `X-API-Key`. |
| `404` | Rota não encontrada. | O servidor está numa versão anterior ao endpoint. Avise — é `git pull` + restart na `.11`. |
| `502` | Falha ao ler o espelho. | Problema no Supabase. Tente de novo em alguns minutos; se persistir, avise. |

Todo erro vem com `"ok": false` e um `error` em texto — **cheque o `ok` antes do conteúdo**,
não o código HTTP sozinho.

---

## 13. Armadilhas

1. **`matricula` não é `person_id`.** O `person_id` é o Id do cadastro; a matrícula é o
   crachá. Use `person_id` como chave.
2. **A chave real é o par `empresa` + `person_id`.** A mesma pessoa pode aparecer em duas
   empresas, com Ids diferentes.
3. **Nome não é chave.** Há homônimos, e a grafia muda com o tempo.
4. **`total` conta linhas, não pessoas ativas.** Sem `?somente_ativos=1` ele inclui
   desligados e ausentes.
5. **O setor `"Principal"` é só de desligados** (§9) — não ofereça num filtro de tela.
6. **`cargo` costuma faltar em desligados** (§4) — não use como obrigatório no histórico.
7. **Setor vem do Kairos.** Mudança de setor lá aparece aqui na carga seguinte — não há
   como forçar antes das 12:40.
8. **Não guarde cópia sem carimbo.** Se for armazenar do seu lado, guarde junto o
   `atualizado_em` — senão ninguém sabe de quando é.

---

## 14. Perguntas frequentes

**Como eu descubro que alguém foi desligado?**
A linha dele continua vindo, com `status: "desligado"` e `data_desligamento`. Você não
precisa comparar a foto de hoje com a de ontem — veja a receita de sincronização na §10.

**Posso usar a matrícula como chave no meu banco?**
Prefira `empresa` + `person_id`. A matrícula é estável na prática, mas é um número de
crachá: quem reingressa pode receber outro.

**E se a pessoa mudar de empresa dentro do grupo?**
Ela aparece nas duas: desligada numa, ativa na outra. Como a chave inclui a empresa, os
dois registros convivem sem conflito.

**Com que frequência devo chamar?**
No máximo de hora em hora — o dado muda uma vez por dia (§8).

**Preciso tratar paginação?**
Não. A resposta vem inteira, e são dezenas de KB (§8).

**O `em_ferias_ou_afastado` serve para calcular férias?**
Não. Ele diz "sem expediente", e não distingue férias de atestado ou afastamento (§6).
Para férias formais, a fonte é o RH, não esta API.

**Posso pedir campos novos (e-mail, telefone, centro de custo)?**
Fale com o Marcelo. Alguns existem no Kairos e podem ser espelhados; outros são dado
sensível e não entram sem decisão.

---

## 15. Uso do dado: são pessoas

Isto é cadastro de colaboradores — nome, cargo, setor, quem está afastado. Duas regras de
bom senso, e uma consequência prática:

- **Uso interno.** Não republique em sistema aberto, não mande para fora da empresa, não
  exponha numa tela pública.
- **A chave é sua responsabilidade.** A `X-API-Key` dá acesso a todo o endpoint. Não a
  coloque em repositório, front-end ou aplicativo distribuído — ela vive no servidor de
  vocês.
- **Se for guardar do seu lado**, guarde só o que usa, e trate o desligamento: o
  `status` existe para você saber quando parar de exibir alguém.

---

## 16. Estabilidade do contrato

- **Campos podem ser acrescentados** sem aviso — `atualizado_em_br` e `carga_esperada_em`
  nasceram assim, em 01/09/2026. Escreva o seu parser tolerante a chaves novas.
- **Campos existentes não são renomeados nem mudam de sentido** sem falar com quem
  consome. Se precisar acontecer, o aviso vem antes.
- **Valores de `status` são um conjunto fechado** (`ativo`, `desligado`, `ausente`).
  Ainda assim, trate o inesperado como "não ativo" em vez de quebrar.
- Mudanças ficam registradas no `CHANGELOG.md` deste repositório.

---

## 17. Por dentro (para quem mantém)

- **Tabela:** `kairos_colaboradores` no Supabase (chave `empresa` + `person_id`), escrita
  **só** pelo `web_orcaview_V117` com a service key; esta API lê com a mesma chave.
- **Este repositório não tem credencial do Kairos** e não deve ganhar uma: quem fala com
  o Kairos é o `.90`, onde já vive o cliente com todas as armadilhas de tenant tratadas.
- **Leitura paginada** de 1000 em 1000 — o PostgREST corta em 1000 linhas com HTTP 200, e
  a tabela só cresce (linha nunca é deletada).
- **Plano e decisões:** `web_orcaview_V117/docs/PLANO_ESPELHO_COLABORADORES_KAIROS.md`.
- **Código:** rotas em `api.py` (`/rh/colaboradores`), tools em `mcp/mcp_server.py`,
  testes em `tests/test_api.py` e `tests/test_mcp_colaboradores.py`.

---

*Dúvidas, chave de acesso ou pedido de campo novo: Marcelo.*
