# API — Situação dos Pedidos (SAP)

Documento para **quem vai consumir** os dados. Escrito para ser lido sem nos perguntar
nada: tudo que você precisa saber está aqui, inclusive o que costuma dar errado.

Ele descreve as duas formas de ler a **situação dos pedidos de venda no SAP** — se estão
liberados ou bloqueados no **Financeiro**, na **Produção** e na **Entrega**, com prazo,
sinal, condição de pagamento, montador, vendedor e valor:

- **API REST** (`192.168.7.11:8077`) — para código: script, integração, painel.
- **MCP** (`192.168.7.11:8078`) — para assistente de IA (Claude Desktop, Claude Code)
  responder em linguagem natural.

As duas leem exatamente a mesma coisa; a MCP é uma camada fina sobre a REST.

> Os endpoints de **Ordens de Serviço** (`/ordens-servico/...`) e de **Ordens de
> Produção** (`/ordens-producao/...`) são outra coisa e estão em `API_OS_INTEGRACAO.md` e
> `API_ORDENS_PRODUCAO.md`.

---

## 1. O que é, e o que **não** é

**É** uma leitura ao vivo do SAP HANA, da mesma view que desenha a tela "Situação dos
Pedidos" do OrçaView. O que você recebe é o que a tela mostra, com a mesma lógica de
normalização — não é um espelho, não é uma fila, não passa por banco intermediário.

**Não é:**

- **Não escreve nada.** Todos os endpoints deste documento são **somente leitura**.
- **Não é o histórico da empresa.** A view carrega apenas os **pedidos correntes**
  (≈237 em 24/08/2026). Um pedido de 2024 **não está lá** — veja a armadilha nº 2.
- **Não é tempo real ao segundo.** Há um cache de **120 segundos** (§9).

---

## 2. Antes de tudo: as cinco armadilhas

Leia esta seção inteira. Cada item aqui já custou caro para alguém.

### 2.1 `DocNum` ≠ `DocEntry`

O mesmo pedido tem **dois números** no SAP:

| | O que é | Exemplo |
| --- | --- | --- |
| **DocNum** | O número que aparece na tela do SAP. É o que as pessoas falam. | `84260` |
| **DocEntry** | A chave interna da tabela. Ninguém decora. | `19244` |

**Usamos DocNum por padrão.** Se você tem o DocEntry, acrescente `?chave=docentry`.
A resposta sempre traz os dois (`doc_num` e `doc_entry`), então dá para conferir.

Trocar um pelo outro **não dá erro** — devolve outro pedido, ou um 404.

### 2.2 `404` quer dizer "não está no recorte", **nunca** "está liberado"

Se você pedir um pedido que não está na view, a resposta é `404`. Isso significa **"não
consigo responder sobre esse pedido"**, e não "esse pedido está sem bloqueio".

A diferença importa: tratar 404 como "liberado" faz um sistema afirmar que um pedido está
livre quando ninguém sabe. A própria mensagem de erro diz isso, com essas palavras.

### 2.3 Bloqueado hoje ≠ bloqueado algum dia

Pedido **fechado** que já esteve bloqueado continua no recorte. Se você quer "o que está
travado **agora**", filtre por `status=aberto`.

⚠️ **Os defaults diferem entre a REST e a MCP, de propósito:**

| | Default de `status` |
| --- | --- |
| **REST** `GET /pedidos/situacao` | `todos` — espelha a tela do OrçaView |
| **MCP** `pedidos_bloqueados()` | **`aberto`** — quem pergunta "o que está travado?" quer o de hoje |

Se um número seu não bate com a tela, **é quase sempre isto**. Passe `status=todos` para
igualar.

### 2.4 O status vem canonizado

No SAP o gênero muda por coluna: "Liberad**o**" no Financeiro, "Liberad**a**" na Produção
e na Entrega. **A API entrega sempre `"Liberado"` ou `"Bloqueado"`**, nas três.

Se um dia aparecer um terceiro valor (o SAP mudou), ele passa **como veio**, sem ser
traduzido — é proposital, para o valor estranho ficar visível em vez de virar "Liberado"
por descuido. Não assuma que só existem dois valores; trate o desconhecido.

### 2.5 `prazo_entrega` é texto e **não tem ano**

Vem do SAP assim: `"21/09 A 25/09"`. Não dá para subtrair data disso.

Para conta, use **`prazo_fim`** (`"2026-09-25"`, ISO), que já calculamos, e **`dias_atraso`**
(positivo = passou do prazo, negativo = ainda há prazo). `prazo_fim` pode vir `null`
quando o texto não casa com o formato — `null` significa "não dá para afirmar", e é melhor
que um número inventado.

---

## 3. Qual dos dois usar

| Você quer... | Use |
| --- | --- |
| Um script, um painel, uma integração | **REST** (§5) |
| Um assistente de IA respondendo em português | **MCP** (§8) |
| Os dois | Pode. A MCP chama a REST por baixo. |

---

## 4. O que você precisa pedir para nós

Mande um único pedido com estes três itens — nada aqui é auto-serviço:

1. **Liberação de rede** para `192.168.7.11`, portas **8077** (REST) e/ou **8078** (MCP),
   a partir do IP de origem que você vai usar.
2. **A chave da API** (`X-API-Key`), se for usar REST.
3. **O token do MCP** (`Bearer`), se for usar MCP.

> **A chave e o token não estão neste documento e nunca devem estar.** Guarde-os em
> variável de ambiente ou cofre de segredos — **nunca** em código versionado, e **nunca**
> na URL de um navegador (fica no histórico).

Não há usuário/senha, não há OAuth. É só o cabeçalho.

---

## 5. REST — os dois endpoints

**Base:** `http://192.168.7.11:8077`
**Autenticação:** cabeçalho `X-API-Key: <sua-chave>` em toda chamada.
(Também aceitamos `Authorization: Bearer <chave>`.)

### 5.1 `GET /pedidos/<numero>/situacao` — um pedido

| Parâmetro | Onde | Valores | Default |
| --- | --- | --- | --- |
| `numero` | caminho | inteiro positivo — o **DocNum** | — |
| `chave` | query | `docnum` \| `docentry` | `docnum` |
| `campos` | query | `resumo` \| `completo` | **`completo`** |
| `recarregar` | query | `1` = ignora o cache | — |

Resposta `200`:

```json
{
  "ok": true,
  "gerado_em": "2026-08-24T15:12:07-03:00",
  "cache_idade_s": 34.2,
  "pedido": { "...": "os campos do §6" }
}
```

### 5.2 `GET /pedidos/situacao` — lista

Um endpoint só, porque é o mesmo recorte; o que muda é o filtro.

| Parâmetro | Valores | Default | Para quê |
| --- | --- | --- | --- |
| `bloqueio` | `qualquer` \| `financeiro` \| `producao` \| `entrega` \| `nenhum` | *(sem filtro)* | `qualquer` = travado em **pelo menos uma** etapa |
| `status` | `todos` \| `aberto` \| `fechado` | `todos` | ver armadilha 2.3 |
| `montador` | CNPJ, ou `__sem__` | *(todos)* | `__sem__` = pedidos **sem** montador definido |
| `busca` | texto livre | — | casa com cliente, código do cliente, nº do pedido e cotação WBC |
| `so_atrasados_fin` | `1` | — | só os que passaram dos 10 dias no Financeiro (§7) |
| `campos` | `resumo` \| `completo` | **`resumo`** | ver §9 |
| `recarregar` | `1` | — | ignora o cache |

Os filtros se **somam** (E lógico).

Resposta `200`:

```json
{
  "ok": true,
  "gerado_em": "2026-08-24T15:12:07-03:00",
  "cache_idade_s": 34.2,
  "kpis": {
    "total": 237,
    "atrasados": 42,
    "financeiro_bloqueado": 3,
    "producao_bloqueada": 10,
    "entrega_bloqueada": 10
  },
  "total_no_recorte": 237,
  "total_filtrado": 10,
  "pedidos": [ "..." ],
  "montadores": [
    { "cnpj": "00.000.000/0001-00", "nome": "MONTADORA EXEMPLO LTDA", "qtd": 7 }
  ]
}
```

⚠️ **Dois pontos que confundem quem lê pela primeira vez:**

- **`kpis` e `montadores` são sempre do recorte INTEIRO**, nunca do filtro. É igual à
  tela: o indicador diz **quantos existem**, o filtro diz **quais aparecem**. Quantos
  voltaram na sua chamada está em **`total_filtrado`**.
- **Filtro que não casa com nada devolve `200` com `"pedidos": []`**, nunca 404. "Não há
  nada bloqueado" é uma resposta legítima.

---

## 6. Dicionário de campos

### 6.1 Perfil `resumo` — 11 campos

São as colunas da tela, mais o alerta. É o default da lista, e serve para quase tudo.

`data_pedido` · `card_name` · `doc_num` · `sinal` · `financeiro` · `producao` ·
`entrega` · `prazo_entrega` · `atrasado` · `pymnt_group` · `alerta_liberacao`

### 6.2 Perfil `completo` — 35 campos

| Campo | Tipo | O que é |
| --- | --- | --- |
| `doc_num` | int | **O número do pedido** (o da tela do SAP). Ex.: `84260` |
| `doc_entry` | int | Chave interna do SAP. Ex.: `19244` |
| `data_pedido` | str \| null | Data do pedido, ISO `YYYY-MM-DD` |
| `card_code` | str | Código do cliente no SAP. Ex.: `C011840` |
| `card_name` | str | Nome do cliente |
| `group_num` | int \| null | Código do grupo de condição de pagamento |
| `pymnt_group` | str | Condição de pagamento **por extenso**. Ex.: `30% SINAL / 20% ENTREGA / 30% 45DDL` |
| **`financeiro`** | str | `"Liberado"` \| `"Bloqueado"` — ver 2.4 |
| **`producao`** | str | `"Liberado"` \| `"Bloqueado"` |
| **`entrega`** | str | `"Liberado"` \| `"Bloqueado"` |
| `sinal` | bool | O pedido exige sinal |
| `ddo` | bool | Condição 100% DDP, **sem** sinal |
| `integrar` | bool | Marcado para integração |
| `status_pedido` | str | `"Aberto"` \| `"Fechado"` (como o SAP escreve) |
| `prazo_entrega` | str | **TEXTO, sem ano.** Ex.: `"21/09 A 25/09"` — ver 2.5 |
| `prazo_fim` | str \| null | Fim da janela, ISO. **Use este para conta** |
| `data_entrega` | str \| null | Data de entrega registrada, ISO |
| `dias_atraso` | int \| null | Dias desde o fim do prazo. **Positivo = atrasado**; negativo = ainda há prazo |
| **`atrasado`** | bool | Está atrasado **hoje**. Pedido fechado é sempre `false` |
| `atrasado_sap` | bool | O valor **cru** do SAP. Fica `true` em pedido fechado que foi entregue com atraso — é o que permite dizer "foi entregue atrasado" |
| `dias_desde_pedido` | int \| null | Dias corridos desde `data_pedido` |
| `fin_liberacao_atrasada` | bool | Passou dos 10 dias no Financeiro — ver §7 |
| **`alerta_liberacao`** | str \| null | O texto pronto: `"Mais de 10 dias preso no financeiro (12 dias)"`, ou `null` |
| `data_lib_fin` | str \| null | Quando o Financeiro liberou, ISO |
| `data_lib_prod` | str \| null | Quando a Produção liberou, ISO |
| `data_pagto` | str \| null | Data de pagamento, ISO |
| `valor_total` | float | Valor do pedido |
| `moeda` | str | Ex.: `"R$"` |
| `vendedor` | str | Nome do vendedor |
| `cotacao_wbc` | str | Cotação WBC que originou o pedido. **Zero à esquerda faz parte** (`"00125283"`) — trate como texto |
| `versao_wbc` | str | Revisão da cotação (letra). Vem vazio em pedidos antigos |
| `peso` | float | Peso |
| `total_os` | int | Quantas Ordens de Serviço o pedido tem |
| `total_os_fechadas` | int | Quantas já foram fechadas |
| `montagem` | objeto | Ver abaixo |

**`montagem`:**

| Campo | Tipo | O que é |
| --- | --- | --- |
| `tipo` | str | Rótulo oficial do SAP. Ex.: `"MONTAGEM POR CONTA DE TERCEIROS"` |
| `tipo_cod` | str | Código (`"1"`, `"2"`, `"3"`, `"5"`, `"6"`, `"EXP"`) |
| `valor` | float | Valor da montagem |
| `montador` | str | Nome do montador; cai para o CNPJ se o cadastro não resolver |
| `montador_cnpj` | str | **É a chave** do filtro `montador=` — o nome se repete |

> **Datas:** todas as datas são `YYYY-MM-DD` (sem hora), exceto `gerado_em`, que é ISO
> completo com fuso (`-03:00`). Campo sem valor vem **`null`**, nunca `""` nem `0`.

---

## 7. A regra dos 10 dias

Um pedido ganha `fin_liberacao_atrasada = true` (e o texto em `alerta_liberacao`) quando
as **três** condições valem ao mesmo tempo:

1. `financeiro == "Bloqueado"`;
2. o pedido está **em aberto** (fechado nunca alarma);
3. passaram **mais de 10 dias** desde a `data_pedido` — estritamente mais: no 10º dia
   ainda não alarma.

É a mesma regra do alerta interno do OrçaView — **uma regra, uma implementação**. Não a
recalcule do seu lado: se o limite mudar, ele muda aqui e a sua tela acompanha sozinha.

Para listar só esses: `GET /pedidos/situacao?so_atrasados_fin=1`.

---

## 8. MCP

A fachada MCP expõe as mesmas consultas como **tools**, para um assistente de IA usar.

**Endpoint:** `http://192.168.7.11:8078/mcp` (transporte *Streamable HTTP*)
**Autenticação:** cabeçalho `Authorization: Bearer <token>`

### 8.1 Registrar no cliente

No Claude Desktop / Claude Code, adicione ao arquivo de configuração de MCP:

```json
{
  "mcpServers": {
    "sap-pedidos": {
      "type": "http",
      "url": "http://192.168.7.11:8078/mcp",
      "headers": { "Authorization": "Bearer SEU_TOKEN_AQUI" }
    }
  }
}
```

O servidor expõe outras tools além destas três (saúde da integração, sincronizações). As
que interessam aqui:

### 8.2 As três tools

| Tool | Responde a | Argumentos |
| --- | --- | --- |
| `situacao_pedido` | "o pedido 84260 está preso onde?" | `pedido` (DocNum), `chave` = `docnum` \| `docentry` |
| `pedidos_bloqueados` | "o que está travado?" | `bloqueio` (default `qualquer`), `status` (default **`aberto`**) |
| `panorama_pedidos` | "como está a carteira?" | `campos` = `resumo` (default) \| `completo` |

As três são **somente leitura** e declaram `readOnlyHint`, então o cliente sinaliza ao
usuário que são consulta, não ação.

### 8.3 Perguntas que funcionam

- *"O pedido 84260 está preso onde?"* → `situacao_pedido`
- *"Quais pedidos estão bloqueados no financeiro?"* → `pedidos_bloqueados(bloqueio="financeiro")`
- *"Tem alguma coisa presa na produção?"* → `pedidos_bloqueados(bloqueio="producao")`
- *"Quantos pedidos estão atrasados?"* → `panorama_pedidos` (leia `kpis.atrasados`)
- *"O que está preso há mais de 10 dias?"* → `pedidos_bloqueados(bloqueio="financeiro")`
  e leia o campo `alerta_liberacao` de cada pedido. **Não existe tool separada para
  isso** — são poucos pedidos e o texto já vem pronto.

### 8.4 Cuidados com IA

- **Prefira a tool específica.** `panorama_pedidos` traz a carteira inteira (~74 KB) e
  gasta contexto à toa quando a pergunta era sobre um pedido.
- **Não deixe o modelo concluir "está liberado" a partir de um 404** (armadilha 2.2). A
  descrição da tool avisa, mas vale reforçar no seu prompt.
- **`cache_idade_s`** diz de quantos segundos atrás é o retrato. Se a resposta precisa ser
  do instante, diga isso ao usuário em vez de afirmar que é tempo real.

---

## 9. Limites e boas práticas

| | |
| --- | --- |
| **Cache** | 120 s, compartilhado por todos os clientes. Duas chamadas seguidas veem o **mesmo** retrato. `recarregar=1` força ida ao SAP — **use com parcimônia** |
| **Tamanho** | Carteira inteira: ~**74 KB** em `resumo`, ~**237 KB** em `completo` (3,2×). Prefira `resumo` |
| **Volume** | ~237 pedidos hoje. Não há paginação: o recorte cabe numa resposta |
| **Rate limit** | Não há nas leituras. O cache é a proteção — **não faça polling mais rápido que 120 s**, não adianta nada e só ocupa o servidor |
| **Escrita** | Nenhuma. Se precisar mudar algo no SAP, fale conosco |
| **Disponibilidade** | Se o SAP HANA cair, respondemos **503** com a explicação. Não é erro seu; tente de novo |

**Sugestão de uso saudável:** consulte sob demanda. Se precisar de painel que atualiza
sozinho, um ciclo de **2 a 5 minutos** é mais que suficiente — o dado por trás não muda
mais rápido que isso.

---

## 10. Erros

| Código | O que aconteceu | O que fazer |
| --- | --- | --- |
| **400** | O número não é inteiro positivo | Corrija o valor |
| **401** | Chave ausente ou errada | Confira o cabeçalho `X-API-Key` |
| **404** | Pedido **fora do recorte da view** | **Não é "sem bloqueio"** (2.2). Confira se é DocNum mesmo |
| **409** | O número casa com mais de um pedido | Consulte por `chave=docentry`. Não deve acontecer — se acontecer, **avise-nos** |
| **422** | Parâmetro fora do domínio (ex.: `bloqueio=comercial`) | A mensagem lista os valores aceitos. Tentar de novo não adianta |
| **502** | Falha inesperada do nosso lado | Tente de novo; se persistir, avise-nos |
| **503** | SAP HANA indisponível | Tente de novo em alguns minutos |

Todo erro vem com corpo JSON e **mensagem em português explicando o caso**:

```json
{
  "ok": false,
  "error": "pedido 70000 fora do recorte da view (ela carrega so os pedidos correntes) - isto NAO quer dizer que ele esteja sem bloqueio",
  "pedido": 70000,
  "chave": "doc_num"
}
```

Sempre teste `ok` antes de ler o resto. **Não trate erro por código HTTP só** — a
mensagem carrega a informação que evita a conclusão errada.

---

## 11. Exemplos

Nos exemplos abaixo a chave vem de variável de ambiente. **Não cole a chave no código.**

### 11.1 `curl`

```bash
curl -s -H "X-API-Key: $SAP_API_KEY" \
  "http://192.168.7.11:8077/pedidos/84260/situacao"
```

```bash
curl -s -H "X-API-Key: $SAP_API_KEY" \
  "http://192.168.7.11:8077/pedidos/situacao?bloqueio=qualquer&status=aberto"
```

```bash
curl -s -H "X-API-Key: $SAP_API_KEY" \
  "http://192.168.7.11:8077/pedidos/situacao?so_atrasados_fin=1"
```

### 11.2 PowerShell

```powershell
$H = @{ "X-API-Key" = $env:SAP_API_KEY }
$r = Invoke-RestMethod -Uri "http://192.168.7.11:8077/pedidos/situacao?bloqueio=financeiro" -Headers $H
$r.pedidos | Select-Object doc_num, card_name, financeiro, alerta_liberacao | Format-Table
```

### 11.3 Python

```python
import os
import requests

BASE = "http://192.168.7.11:8077"
SESSAO = requests.Session()
SESSAO.headers["X-API-Key"] = os.environ["SAP_API_KEY"]


def situacao_do_pedido(doc_num: int) -> dict | None:
    """Situação de um pedido — None quando ele não está no recorte da view.

    None significa "não sei", NUNCA "está liberado" (ver §2.2 do documento).
    """
    r = SESSAO.get(f"{BASE}/pedidos/{doc_num}/situacao", timeout=45)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()["pedido"]


def travados_agora() -> list[dict]:
    """Pedidos em aberto bloqueados em pelo menos uma etapa."""
    r = SESSAO.get(f"{BASE}/pedidos/situacao", timeout=60,
                   params={"bloqueio": "qualquer", "status": "aberto"})
    r.raise_for_status()
    return r.json()["pedidos"]


p = situacao_do_pedido(84260)
if p is None:
    print("84260 não está no recorte da view — não dá para afirmar nada sobre ele")
else:
    print(f"{p['doc_num']} {p['card_name']}")
    print(f"  financeiro={p['financeiro']} producao={p['producao']} entrega={p['entrega']}")
    if p["alerta_liberacao"]:
        print(f"  ⚠ {p['alerta_liberacao']}")

for x in travados_agora():
    print(f"{x['data_pedido']}  {x['doc_num']:<7} {x['card_name'][:34]:<34} "
          f"F={x['financeiro']:<9} P={x['producao']:<9} E={x['entrega']}")
```

### 11.4 Resposta completa de um pedido

Estrutura real; **valores ilustrativos**.

```json
{
  "ok": true,
  "gerado_em": "2026-08-24T15:12:07-03:00",
  "cache_idade_s": 34.2,
  "pedido": {
    "doc_num": 84260,
    "doc_entry": 19244,
    "data_pedido": "2026-08-12",
    "card_code": "C000000",
    "card_name": "CLIENTE EXEMPLO LTDA",
    "group_num": 1186,
    "pymnt_group": "30% SINAL / 20% ENTREGA / 30% 45DDL / 10% 65DDL / 10% 85DDL",
    "financeiro": "Bloqueado",
    "producao": "Bloqueado",
    "entrega": "Bloqueado",
    "sinal": true,
    "ddo": false,
    "integrar": false,
    "status_pedido": "Aberto",
    "prazo_entrega": "21/09 A 25/09",
    "prazo_fim": "2026-09-25",
    "data_entrega": "2026-09-23",
    "dias_atraso": -32,
    "atrasado": false,
    "atrasado_sap": false,
    "dias_desde_pedido": 12,
    "fin_liberacao_atrasada": true,
    "alerta_liberacao": "Mais de 10 dias preso no financeiro (12 dias)",
    "data_lib_fin": null,
    "data_lib_prod": null,
    "data_pagto": null,
    "valor_total": 100000.00,
    "moeda": "R$",
    "vendedor": "NOME DO VENDEDOR",
    "cotacao_wbc": "00125283",
    "versao_wbc": "B",
    "peso": 4.0,
    "total_os": 0,
    "total_os_fechadas": 0,
    "montagem": {
      "tipo": "MONTAGEM POR CONTA DE TERCEIROS",
      "tipo_cod": "3",
      "valor": 135000.0,
      "montador": "MONTADORA EXEMPLO LTDA",
      "montador_cnpj": "00.000.000/0001-00"
    }
  }
}
```

---

## 12. Combinado de compatibilidade

- **Campos novos podem aparecer sem aviso.** Escreva um cliente que **ignora o que não
  conhece** — não quebre com chave a mais.
- **Campo existente não muda de nome nem de tipo sem avisarmos antes.**
- **Os defaults dos parâmetros são contrato** (`chave=docnum`, `campos=resumo` na lista,
  `status=aberto` na tool MCP). Se você depende de um deles, **passe explícito** — custa
  nada e blinda seu código.
- **Trate `null`.** Vários campos são legitimamente nulos (`prazo_fim`, `data_pagto`,
  `alerta_liberacao`, as datas de liberação).

---

## 13. Antes de nos chamar

| Sintoma | Quase sempre é |
| --- | --- |
| "Meu número não bate com a tela" | O `status` (armadilha 2.3). Tente `status=todos` |
| "Esse pedido existe, mas dá 404" | Ele não está no recorte da view (só os correntes), **ou** você mandou DocEntry sem `chave=docentry` |
| "O dado está velho" | Cache de 120 s. Veja `cache_idade_s` |
| "Deu 401" | Cabeçalho `X-API-Key` ausente, com espaço, ou chave errada |
| "Deu 503" | O SAP HANA está fora. Espere e tente de novo |
| "A resposta está enorme" | Você está em `campos=completo`. Use `resumo` |

**Vale nos avisar na hora:** um `409`, um `503` que não passa em ~15 minutos, um campo que
mudou de tipo, ou qualquer número que divirja da tela do OrçaView de forma consistente.

---

*Servidor de Integração SAP · `192.168.7.11` · atualizado em 2026-08-24.*
*Runbook interno: `docs/PLANO_SITUACAO_PEDIDOS_MCP.md`.*
