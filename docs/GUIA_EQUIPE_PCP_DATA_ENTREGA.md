# Guia de implementação — "Pedir nova data de entrega" no app da Produção

**Para quem é:** a equipe do PCP **e o agente de IA** que vai escrever o código no app de vocês.
**O que vai ser construído:** a Produção pede uma nova data de entrega de um pedido de venda, e o app mostra a
resposta do Comercial.
**Versão:** 15/10/2026 (revisão 5, conferida por leitura independente: sem bloqueios). Este guia é completo: tudo que o agente precisa está aqui. A referência técnica
`API_DATA_ENTREGA_COMBINADA.md` existe, mas **não é necessária** para implementar.

---

## 0. Antes de começar

### 0.1 Para a equipe (pessoa): preencha este quadro e entregue junto com o guia

O agente **não sabe** estas coisas sobre o app de vocês. Preencha antes de pedir a implementação:

| Pergunta | Resposta da equipe |
| --- | --- |
| A. Nome da variável do `.env` com o endereço da `.11` (ex.: `SIS_API_BASE`) | |
| B. Nome da variável do `.env` com a chave da `.11` (ex.: `SIS_API_KEY`) | |
| C. Nome da função/dependência do backend que exige login (ex.: `usuario_logado`) | |
| D. Como saber se o usuário logado é da **Produção** (cargo, módulo ou grupo) | |
| E. De onde sai o **nome** do usuário logado (e, se não houver, o e-mail) | |
| F. Arquivo da tela do pedido no front, e de onde ela tira o **DocNum** | |
| G. Biblioteca HTTP que o backend já usa (`httpx` ou `requests`) | |
| H. Framework de testes do app (`pytest`?) | |
| I. Como o app manda a pessoa para o login quando a sessão expira (URL ou função) | |
| J. Há um proxy (nginx, IIS) na frente do backend? Se sim, qual o tempo limite dele? (precisa ser ≥ 70 s) | |

### 0.2 Para o agente: regras de trabalho

1. **Leia o guia inteiro antes de escrever código.**
2. **Confira o quadro 0.1.** Se alguma linha estiver vazia, **pare e pergunte à equipe**. Não chute.
3. **Siga os passos da parte 4 na ordem.** Só passe para o próximo quando o atual estiver "pronto".
4. **Não invente** endereços, campos, valores, mensagens nem números. Tudo está aqui.
5. **Não instale biblioteca nova** sem perguntar. Use a do quadro 0.1, linha G.
6. **Não mude** nada fora do app de vocês: nem o banco de dados (Supabase), nem o SAP, nem o servidor `.11`.
7. Se algo não estiver aqui: **pare e pergunte**.

---

## 1. A ideia em 6 frases

1. Todo pedido de venda tem uma **data de entrega no SAP**.
2. Às vezes a Produção não consegue cumprir essa data (máquina quebrou, fábrica cheia, etc.).
3. Agora a Produção pode **pedir** uma nova data pelo servidor `.11`.
4. O pedido de nova data fica **esperando**. Quem decide é o **Comercial**, numa tela do OrçaView.
5. O Comercial **aprova** ou **recusa**. Ele também pode mudar a data sozinho (por exemplo, quando o cliente pede).
6. **O SAP nunca muda.** A data que vale passa a se chamar **data combinada**.

O caminho completo:

```
Pessoa da Produção
   │ clica "Pedir nova data" no app de vocês
   ▼
Navegador (front do app de vocês)
   │ chama o BACKEND de vocês (com o login do app, sem chave nenhuma)
   ▼
Backend do app de vocês (FastAPI)
   │ chama o servidor .11 com a chave X-API-Key (que fica só no backend)
   ▼
Servidor .11 (192.168.7.11:8077)
   │ confere tudo e grava a solicitação como "pendente"
   ▼
OrçaView (tela Situação dos Pedidos)
   │ o Comercial aprova ou recusa
   ▼
O app de vocês lê a resposta na .11 e mostra para a Produção
```

---

## 2. Palavras que vocês precisam usar igual a nós

| Palavra | O que significa |
| --- | --- |
| **pedido** | o pedido de venda. Sempre identificado pelo **DocNum**, o número que aparece na tela (ex.: `84453`). Nunca o DocEntry. |
| **solicitação** | o pedido de **mudança de data**. Não confunda com "pedido". |
| **data do SAP** | a data de entrega que está no SAP. Campo `data_sap`. Nunca muda por causa disto. |
| **data combinada** | a data que vale agora. Campo `data_vigente`. Se ninguém mudou nada, é igual à do SAP. |
| **pendente** | solicitação esperando o Comercial decidir. |
| **aprovada / recusada** | a decisão do Comercial. |
| **atraso nosso** | dias de atraso por culpa interna (fábrica). Mudança pedida pelo **cliente** não conta. |

> ⚠️ Na tela do app, escreva **"solicitação de nova data"**, nunca "pedido de nova data".

> ⚠️ **Cuidado com a palavra `motivo`: ela aparece em dois lugares com sentidos diferentes.**
> - No **envio** (`POST`), `motivo` é a **razão da mudança** (`quebra_maquina`, etc.).
> - Nas **respostas de erro**, `motivo` é o **código do erro** (`data_no_passado`, etc.).
> Não misture: a função da parte 6 já cuida disso.

---

## 3. Caminhos errados (leia antes de começar)

Cada linha é um erro que parece uma boa ideia. **Não faça o da esquerda.** Faça o da direita.

| ❌ Errado | Por que está errado | ✅ Certo |
| --- | --- | --- |
| Chamar `http://192.168.7.11:8077` direto do navegador (JavaScript do front) | A chave ficaria visível no navegador. E o servidor `.11` não aceita chamadas vindas de navegador (não tem CORS): a chamada falha. | O front chama o **backend de vocês**; só o backend chama a `.11`. |
| Gravar direto na tabela `pedidos_entrega_alteracoes` do Supabase (vocês têm a chave de serviço) | Pula todas as conferências (pedido fechado, data no passado, repetição, quem pediu). O Comercial passa a ver dados errados. | **Só** a rota `POST` da `.11` grava. O app **nunca** escreve nessa tabela. |
| Ler a tabela `pedidos_entrega_alteracoes` direto no Supabase | A regra das datas mora na `.11`. Recalculando no app, cedo ou tarde as telas mostram datas diferentes. | Leia pelos `GET` da `.11` e mostre os campos **prontos**. |
| Calcular no app a data combinada, o atraso ou o `alterada` | Duas contas que um dia discordam. | Use `data_vigente`, `atraso_nosso_dias`, `atrasado` e `alterada` como vêm. |
| Criar tabela ou coluna própria para guardar as solicitações | Vira uma segunda verdade, que fica velha. | Pergunte sempre à `.11`. |
| Usar a rota `/pedidos/<n>/situacao` para saber a data combinada | Ela traz só a situação do SAP, **sem** a data combinada. | Use `GET /pedidos/<n>/data-entrega`. |
| Usar o **DocEntry** (ou o id interno do app) na URL | É outro número. Troca um pelo outro e você mexe no pedido errado sem erro nenhum. | Sempre o **DocNum** (quadro 0.1, linha F). |
| Deixar o usuário digitar quem está pedindo, ou aceitar `solicitante` vindo do front | Qualquer um escreveria qualquer nome. | O **backend** preenche `solicitante` com o usuário logado e **ignora** o que vier do front. |
| Escolher o motivo sozinho (ex.: sempre `alteracao_cliente`) | `alteracao_cliente` faz o atraso não contar. Escolher por conta própria falseia o indicador. | A pessoa escolhe numa lista **sem opção pré-selecionada**. |
| Depois de erro de tempo (504) ou `base_indisponivel` (503), reenviar direto | A primeira tentativa pode ter gravado. | **Consulte** com o `GET`. Se a solicitação aparecer em `pendente`, mostre-a e **não** reenvie. Só deixe reenviar se não aparecer. |
| Mostrar "data alterada" logo depois de enviar | A data **não** muda no envio. Só quando o Comercial aprovar. | "Solicitação enviada. Aguardando o Comercial." |
| Mostrar os motivos ou a conversa para cliente ou representante | Os motivos são internos (ex.: quebra de máquina). | O app de vocês é só da equipe interna, então pode mostrar. O que **não** pode é levar motivo ou conversa para algo que vá ao cliente ou ao representante (PDF, e-mail, relatório externo). |
| Tratar `null` como `0` ou como "não" (ex.: `if (!atrasado)`) | `null` nas datas e no atraso quer dizer "não dá para saber agora". | Compare com `=== null` primeiro e mostre "—". |
| Colocar texto da resposta no HTML com `innerHTML` | Observação e nomes são digitados por pessoas. | Use `textContent` (ou escape). |
| Chamar o SAP (Service Layer) ou alterar a data no SAP | Ninguém muda o SAP por causa disto. | Nada de SAP. Só as rotas da `.11` deste guia. |
| Usar as ferramentas MCP no app | O MCP é para assistentes de IA conversarem, não para o app. | O app usa só as rotas HTTP. |

---

## 4. Passo a passo da implementação

> Este guia supõe: **backend Python (FastAPI)**, front em HTML/JavaScript, login pelo Supabase. Se o app for
> diferente, **mantenha a arquitetura** (front → backend → `.11`) e adapte só a linguagem.

### Passo 1 — Conferir o acesso (sem escrever código)

O Marcelo já deu à chave do backend de vocês os escopos `entrega:solicitar` e `entrega:ler`. **A chave não mudou.**

Rode na máquina do backend (troque `SUA_CHAVE` pela chave do `.env` e `84453` por um **DocNum** de pedido aberto):

PowerShell:
```
curl.exe -s -H "X-API-Key: SUA_CHAVE" http://192.168.7.11:8077/pedidos/84453/data-entrega
```

Bash (Linux/Git Bash):
```
curl -s -H "X-API-Key: SUA_CHAVE" http://192.168.7.11:8077/pedidos/84453/data-entrega
```

| O que voltou | Significa |
| --- | --- |
| JSON começando com `{"ok": true, "pedido": 84453, ...` | **pronto** — siga para o passo 2 |
| `{"ok": false, "error": "forbidden", ...}` | a chave não tem o escopo → pare e fale com o Marcelo |
| `{"ok": false, "error": "unauthorized"}` | chave errada ou faltando |
| uma página HTML com "Not Found" | o servidor `.11` está desatualizado → fale com o Marcelo |

**Pronto quando:** a resposta começa com `{"ok": true, "pedido": ...`.

### Passo 2 — Cliente da `.11` no backend

Crie **um arquivo** no backend (ex.: `cliente_data_entrega.py`) com as funções abaixo. Ele é o **único** lugar do
app que fala com a `.11`.

Regras:
- endereço e chave vêm do `.env` (quadro 0.1, linhas A e B); **nunca** escreva a chave no código;
- tempo de espera de **60 segundos**;
- as funções **nunca** levantam exceção: devolvem `(status_http, corpo_json)`.

Exemplo com `httpx` (se o app usar `requests`, troque as chamadas; a lógica é a mesma):

```python
import os
import httpx

BASE_11 = os.environ["<LINHA A DO QUADRO>"]     # ex.: http://192.168.7.11:8077
CHAVE_11 = os.environ["<LINHA B DO QUADRO>"]
TEMPO = 60.0


def _chamar(metodo: str, caminho: str, *, params=None, json=None) -> tuple[int, dict]:
    """Chama a .11. Nunca levanta exceção: devolve (status, corpo)."""
    try:
        resp = httpx.request(metodo, f"{BASE_11}{caminho}", params=params, json=json,
                             headers={"X-API-Key": CHAVE_11}, timeout=TEMPO)
    except httpx.TimeoutException:
        return 504, {"ok": False, "motivo": "tempo_esgotado"}
    except httpx.HTTPError:
        return 502, {"ok": False, "motivo": "servidor_inacessivel"}
    if resp.status_code in (401, 403):
        # Problema da CHAVE do backend com a .11 (não do usuário). Vira código próprio,
        # para o 401 que o front recebe significar só "login do app expirou".
        return 502, {"ok": False, "motivo": "acesso_negado_11"}
    try:
        return resp.status_code, resp.json()
    except ValueError:
        # Página HTML em vez de JSON (servidor .11 desatualizado, proxy, erro interno).
        return 502, {"ok": False, "motivo": "resposta_invalida"}


def ler_data_entrega(docnum: int) -> tuple[int, dict]:
    return _chamar("GET", f"/pedidos/{docnum}/data-entrega")


def pedir_nova_data(docnum: int, data_nova: str, motivo: str,
                    observacao: str | None, solicitante: str) -> tuple[int, dict]:
    corpo = {"data_nova": data_nova, "motivo": motivo,
             "observacao": observacao or None, "solicitante": solicitante}
    return _chamar("POST", f"/pedidos/{docnum}/data-entrega/solicitacoes", json=corpo)


def listar_solicitacoes(situacao: str, dias: int) -> tuple[int, dict]:
    """situacao: 'pendente' | 'decididas' | 'todas'; dias: 1 a 90."""
    return _chamar("GET", "/pedidos/data-entrega/solicitacoes",
                   params={"situacao": situacao, "dias": dias})
```

> `tempo_esgotado`, `servidor_inacessivel`, `resposta_invalida` e `acesso_negado_11` são códigos **criados pelo
> backend de vocês**. Todos os outros vêm da `.11`. A parte 6 tem mensagem para todos.
> **Por que trocar o 401/403 da `.11`:** assim, quando o front recebe **401**, é sempre o **login do app** que
> expirou (e o app manda a pessoa entrar de novo), nunca a chave do backend.

**Pronto quando:** `ler_data_entrega(<DocNum aberto>)` devolve `(200, {...})` com o campo `data_vigente`.

### Passo 3 — Rotas do backend de vocês (o front só fala com elas)

| Rota do app de vocês | Quem pode | Faz |
| --- | --- | --- |
| `GET /api/pedidos/{docnum}/data-entrega` | qualquer usuário **logado** no app (todos são da equipe interna) | chama `ler_data_entrega` e **acrescenta** o campo `meu_nome` (o nome do usuário logado, exatamente como seria gravado) |
| `POST /api/pedidos/{docnum}/data-entrega/solicitar` | só usuário logado **da Produção** (quadro 0.1, linha D); os outros recebem **403** | recebe `data_nova`, `motivo`, `observacao`; preenche `solicitante`; chama `pedir_nova_data` |

Regras:
- `solicitante` = nome do usuário logado (quadro 0.1, linha E); sem nome, o e-mail; **cortado em 80 caracteres**
  (é o limite da `.11`). Calcule isso numa **função única** (`nome_do_solicitante`), usada pelas duas rotas: o `GET`
  devolve esse mesmo valor em `meu_nome`, para o front reconhecer as solicitações da pessoa. Se não houver nem nome
  nem e-mail, o `POST` responde **400** com `{"ok": false, "motivo": "solicitante_invalido"}` **sem** chamar a `.11`;
- o modelo de entrada do `POST` **não tem** o campo `solicitante`: o que o front mandar é ignorado;
- devolva ao front **o mesmo status HTTP e o mesmo JSON** que as funções do passo 2 devolveram (elas já trocam o
  401/403 da `.11` por `acesso_negado_11`);
- se o front mandar o corpo errado (campo faltando), o próprio FastAPI responde **422** com `detail` e sem `motivo`;
  a função da parte 6 trata isso.

Exemplo em FastAPI (troque `usuario_logado` e `usuario_da_producao` pelas dependências das linhas C e D):

```python
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


class PedidoNovaData(BaseModel):          # sem 'solicitante' de propósito
    data_nova: str
    motivo: str
    observacao: str | None = None


def nome_do_solicitante(usuario) -> str:
    """O nome EXATO que vai como 'solicitante' (e volta como 'solicitado_por')."""
    return (getattr(usuario, "nome", None) or getattr(usuario, "email", None) or "").strip()[:80]


@router.get("/api/pedidos/{docnum}/data-entrega")
def api_ler(docnum: int, usuario=Depends(usuario_logado)):
    status, corpo = ler_data_entrega(docnum)
    if status == 200:
        corpo["meu_nome"] = nome_do_solicitante(usuario)   # o front usa no conferirEnvio
    return JSONResponse(status_code=status, content=corpo)


@router.post("/api/pedidos/{docnum}/data-entrega/solicitar")
def api_pedir(docnum: int, dados: PedidoNovaData, usuario=Depends(usuario_da_producao)):
    nome = nome_do_solicitante(usuario)
    if not nome:
        return JSONResponse(status_code=400, content={"ok": False, "motivo": "solicitante_invalido"})
    status, corpo = pedir_nova_data(docnum, dados.data_nova, dados.motivo, dados.observacao, nome)
    return JSONResponse(status_code=status, content=corpo)
```

**Pronto quando:** logado no app, `GET /api/pedidos/<DocNum>/data-entrega` devolve o mesmo JSON do passo 1.

### Passo 4 — Tela: o botão, o formulário e o envio

**4.1 Ao abrir a tela do pedido**, o front chama `GET /api/pedidos/{docnum}/data-entrega` e decide o botão
**"Pedir nova data"** (aplique as linhas **de cima para baixo**; vale a primeira que casar):

| # | Condição | Botão |
| --- | --- | --- |
| 1 | a chamada falhou (status diferente de 200) | **desabilitado**, com a mensagem da tabela 4.1b |
| 2 | `status_pedido` é `"Fechado"` ou `"Cancelado"` | **escondido** |
| 3 | `pendente` não é `null` | **desabilitado**, com: "Já existe uma solicitação esperando o Comercial: [data_nova em DD/MM/AAAA] por [solicitado_por]." |
| 4 | qualquer outro caso (`status_pedido` `"Aberto"` ou `null`) | **habilitado** |

**4.1b Quando o `GET` falha** (vale para o 4.1 e para o passo 5; o `GET` **não** usa a função da parte 6):

| Status do `GET` | O que fazer |
| --- | --- |
| 401 | mandar para o login (quadro 0.1, linha I) |
| 404 com `motivo` `pedido_nao_encontrado` | "Pedido não encontrado na integração." |
| qualquer outro | "Não deu para ler a situação da data agora. Tente abrir o pedido de novo em alguns minutos." |

`status_pedido` só pode ser `"Aberto"`, `"Fechado"`, `"Cancelado"` ou `null` (não se sabe).
Pedido **fechado** ou **cancelado** também traz `data_sap` (a data de entrega que ficou no SAP): mostre-a como
informação, junto com "Pedido fechado no SAP". Não é erro de lançamento.
**Entrega futura (desde 16/10/2026):** o SAP fecha o pedido já na NF de simples faturamento para entrega futura,
antes de a mercadoria sair. Esse pedido vem `status_pedido: "Aberto"` com `faturamento: "entrega_futura"` e
`remessa` `"nenhuma"` ou `"parcial"`: ele **continua em produção** — não o tire da fila. O botão segue a tabela acima
como em qualquer pedido aberto. Se o POST desse pedido voltar `409 entrega_futura` (ele já saiu da tela do Comercial),
mostre a mensagem da parte 6. Quando a remessa sai toda, ele passa a vir `"Fechado"`.
`faturamento`, `remessa` e `faturado_em` vêm `null` em pedido aberto comum.
`pendente`, quando existe, tem **os mesmos campos** de um item da conversa (parte 7).

**4.2 O formulário** (abre ao clicar no botão):

| Campo | Tipo | Regra |
| --- | --- | --- |
| Nova data | `<input type="date" required>` | mínimo = hoje; máximo = hoje + 365 dias (as duas datas no **horário de Brasília**); o valor já sai como `AAAA-MM-DD` |
| Motivo | `<select required>` | primeira opção vazia: "Escolha o motivo" (valor `""`); depois as 4 da tabela abaixo; **nenhuma pré-selecionada** |
| Observação | `<textarea maxlength="1000">` | opcional |

As 4 opções do motivo (o **valor** vai no envio; o **texto** aparece na tela). O Comercial usa **as mesmas 4**:

| Valor | Texto na tela | Conta como atraso nosso? |
| --- | --- | --- |
| `producao_sobrecarregada` | Produção sobrecarregada | sim |
| `tempo_producao_apertado` | Tempo de produção apertado | sim |
| `quebra_maquina` | Quebra de máquina | sim |
| `alteracao_cliente` | Alteração por parte do cliente | não |

Abaixo da lista, um aviso fixo: *"Use 'Alteração por parte do cliente' só quando o cliente pediu."*

Para o mínimo e o máximo da data, use exatamente isto (o "hoje" é o de **Brasília**, mesmo que o computador esteja
em outro fuso; 365 dias corridos, a mesma conta da `.11`):

```javascript
function hojeBrasilia(somarDias = 0) {
  // 'en-CA' formata como AAAA-MM-DD; o fuso é o de Brasília.
  const hoje = new Date().toLocaleDateString('en-CA', { timeZone: 'America/Sao_Paulo' });
  const d = new Date(hoje + 'T12:00:00Z');          // meio-dia UTC: somar dias não vira o dia
  d.setUTCDate(d.getUTCDate() + somarDias);
  return d.toISOString().slice(0, 10);
}
// Recalcule a CADA abertura do formulário (ele pode ficar aberto depois da meia-noite).
inputData.min = hojeBrasilia(0);
inputData.max = hojeBrasilia(365);
```

Para a observação, `maxlength="1000"` basta. Se a `.11` ainda recusar (`observacao_longa`), a mensagem explica.

**4.3 O envio** — use exatamente este fluxo:

```javascript
async function enviarNovaData(docnum, dataNova, motivo, observacao) {
  let status = 0, corpo = {};
  try {
    const resp = await fetch(`/api/pedidos/${docnum}/data-entrega/solicitar`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data_nova: dataNova, motivo, observacao: observacao || null }),
    });
    status = resp.status;
    try { corpo = await resp.json(); } catch (_) { corpo = {}; }
  } catch (_) {
    status = 0;   // a resposta não chegou: o envio PODE ter sido gravado
  }
  return { status, corpo };
}
```

Ao clicar em **Enviar**:
0. chame `form.reportValidity()`; se devolver `false`, pare (o navegador já mostra o que falta);
1. desabilite **Enviar** (evita clique duplo);
2. chame `enviarNovaData(...)`;
3. passe `status` e `corpo` para `traduzirResposta` (parte 6) e mostre o `texto` que ela devolver;
4. siga a `acao` que ela devolver:
   - `"sucesso"` → feche o formulário e mostre a situação (parte 5): na resposta **201**, use `corpo.entrega`;
     no **200** `ja_existia`, chame o `GET` de novo; quando o sucesso veio do `conferirEnvio`, use o próprio
     resultado do `GET` que ele recebeu;
   - `"corrigir"` → deixe o formulário aberto e reabilite **Enviar**;
   - `"consultar"` → chame o `GET` e use a função `conferirEnvio` abaixo. Ela procura **na conversa inteira**
     (não só no `pendente`), porque o Comercial pode já ter decidido entre o envio e a consulta:
   - `"login"` → mande a pessoa para o login (quadro 0.1, linha I);
   - `"desistir"` → feche o formulário e mostre o texto;
   - `"esperar"` → reabilite **Enviar** só depois de `corpo.retry_after_s` segundos (a `.11` sempre manda esse campo).

```javascript
// enviado = {data_nova, motivo} que a pessoa mandou; situacao = o JSON do GET do backend de vocês.
// O nome vem de situacao.meu_nome (o backend acrescenta, já cortado igual ao gravado). Não monte o nome no front.
// Garantias da .11: a 'conversa' vem INTEIRA (até 500 itens por pedido, muito acima do real) e o
// 'solicitado_por' é gravado exatamente como enviado (só sem espaços nas pontas; maiúsculas não mudam).
function conferirEnvio(statusGet, situacao, enviado) {
  const meuNome = situacao && situacao.meu_nome;
  if (statusGet !== 200) {
    return { acao: 'desistir', texto: 'Não deu para conferir agora. Abra o pedido de novo em alguns minutos.' };
  }
  const minha = [...(situacao.conversa || [])].reverse().find((a) =>
    a.origem === 'producao_api' && a.data_nova === enviado.data_nova
    && a.motivo === enviado.motivo && a.solicitado_por === meuNome);
  if (minha) {
    const estado = { pendente: 'aguardando o Comercial', aprovada: 'já aprovada', recusada: 'já recusada' };
    return { acao: 'sucesso', texto: `Solicitação registrada (${estado[minha.situacao] || minha.situacao}).` };
  }
  if (situacao.pendente) {
    return traduzirResposta(409, { motivo: 'ja_ha_pendente', pendente: situacao.pendente });
  }
  return { acao: 'corrigir', texto: 'Não foi gravado. Pode enviar de novo.' };
}
```

> O front chama **`/api/...` do próprio app**, sem chave nenhuma. A chave fica só no backend.

**Pronto quando:** enviar uma solicitação de teste mostra "Solicitação enviada" e, ao reabrir o pedido, o botão fica
desabilitado com o aviso de pendente.

### Passo 5 — Tela: mostrar a situação do pedido

Use a resposta do `GET` (exemplo completo na parte 7). **Escreva tudo com `textContent`.**

**Formatos de exibição** (use sempre estes):
- datas (`AAAA-MM-DD`) → **DD/MM/AAAA**;
- data e hora (`solicitado_em`, `decidido_em`, que vêm em **UTC** e terminam em `+00:00`) → **DD/MM/AAAA HH:MM** no
  horário de Brasília. Em JavaScript:
  ```javascript
  new Date(iso).toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo',
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
  ```
- motivo (valor) → o **texto** da tabela 4.2.

**O que mostrar:**

| Campo | Mostrar |
| --- | --- |
| `data_vigente` | "Entrega combinada: [data]". Se for `null`: "Entrega combinada: —". |
| `alterada` = `true` | ao lado: "(no SAP: [data_sap])". Se `alterada` = `false`, não mostre nada. |
| `atraso_nosso_dias` | se for número maior que 0: "[N] dias de atraso nosso". Se 0 ou `null`: nada. |
| `atrasado` | `true` → "**Atrasado**" · `false` → "No prazo" · `null` → "—". **Teste `null` primeiro.** |
| `pendente` | se não for `null`: "Esperando o Comercial: nova data [data_nova] — [motivo] — pedida por [solicitado_por] em [solicitado_em]". |
| `sap_disponivel` = `false` | um aviso discreto: "Dados do SAP indisponíveis agora; mostrando o que se sabe." |
| `conversa` | a lista abaixo |
| `cache_idade_s`, `solicitado_cliente`, `data_referencia`, `gera_atraso`, `id`, `atrasado_fonte`, `meu_nome` | **não mostre** |

**Regra geral para vazios:** qualquer campo `null` que for aparecer na tela vira "—". Nunca escreva "null".
Mostre nomes e e-mails **como vierem** (`solicitado_por` costuma ser nome; `decidido_por`, e-mail).

**A conversa** (lista na ordem em que vem, mais antiga primeiro). Uma linha por item:

| Condição do item | Texto |
| --- | --- |
| `origem` = `producao_api` | "Produção pediu [data_nova] — [motivo] — por [solicitado_por] em [solicitado_em]" |
| `origem` = `comercial` | "Comercial mudou para [data_nova] — [motivo] — em [decidido_em]" |
| e, se `situacao` = `aprovada` e `origem` = `producao_api` | "Aprovada por [decidido_por] em [decidido_em]" |
| e, se `situacao` = `recusada` | "Recusada por [decidido_por] em [decidido_em]: [motivo_recusa]" (numa recusa, os três sempre vêm preenchidos) |
| e, se `situacao` = `pendente` | "Esperando o Comercial" |
| e, se `observacao` não for `null` | a observação, em itálico, abaixo |

`motivo` nunca vem `null`. Numa mudança do Comercial (`origem: comercial`), `decidido_em` sempre vem preenchido.

> A solicitação **aparece na hora** no `GET` depois de enviada (a conversa não tem atraso). Só os dados do SAP
> podem ter até 2 minutos de atraso.

**Pronto quando:** depois que o Comercial decidir a solicitação de teste, a tela mostra a decisão sem mexer em código.

### Passo 6 — (Opcional) Lista do dia

**Só faça se a equipe pedir**, e pergunte onde a rotina roda e para quem ela avisa. O backend chama:

```python
status, corpo = listar_solicitacoes("decididas", 1)   # aprovadas e recusadas nas últimas 24 h
```

Resposta (exemplo):

```json
{"ok": true, "situacao": "decididas", "dias": 1, "total": 1,
 "solicitacoes": [{"id": 812, "doc_num": 84453, "data_nova": "2026-11-27", "motivo": "quebra_maquina",
   "situacao": "aprovada", "origem": "producao_api", "solicitado_por": "Ana Souza",
   "decidido_por": "adriano@altamira.com.br", "decidido_em": "2026-11-10T15:40:00+00:00"}]}
```

Valores fora do permitido (`situacao` diferente dessas três, `dias` fora de 1 a 90) → `422` com
`"motivo": "parametro_invalido"`.

Outras opções: `listar_solicitacoes("pendente", 1)` (todas as pendentes; `dias` não importa) e
`listar_solicitacoes("todas", 7)`. Inclui as mudanças que o Comercial fez sozinho (`origem: "comercial"`).

### Passo 7 — Testes automáticos do app (framework do quadro 0.1, linha H)

Os testes **substituem** o cliente da `.11` por um falso. **Nunca** chamem a `.11` de verdade num teste.

1. nenhum HTML/JS servido ao navegador contém a chave nem `192.168.7.11`;
2. o `POST` do backend usa o usuário logado como `solicitante`, mesmo se o front mandar outro nome; e o `GET`
   devolve `meu_nome` igual ao `solicitante` que o `POST` enviaria (inclusive para um nome com mais de 80 caracteres);
3. o `POST` responde **403** para quem não é da Produção, sem chamar a `.11`;
4. resposta `201` → texto "Solicitação enviada…";
5. resposta `200` com `ja_existia: true` → texto "Essa solicitação já tinha sido enviada…";
6. resposta `409` `ja_ha_pendente` → texto com quem pediu (do campo `pendente`);
7. resposta `504` `tempo_esgotado` e `status = 0` → ação `"consultar"`, nunca reenvio automático; e
   `conferirEnvio` acha a solicitação **já decidida** na conversa (não manda reenviar);
8. `atrasado: null` aparece como "—" (e `false` como "No prazo").

**Pronto quando:** os 8 passam.

### Passo 8 — Teste junto com o Comercial

Combine um horário com o Marcelo e uma pessoa do Comercial. Escolham **juntos um pedido aberto** (o DocNum).

1. No app, peça uma nova data (motivo "Tempo de produção apertado", observação "TESTE"). Esperado: "Solicitação enviada".
2. Abra o pedido de novo. Esperado: botão desabilitado com o aviso de pendente.
3. O Comercial abre a **Situação dos Pedidos** no OrçaView, clica no card **"Solicitações de Data"**, abre o pedido e
   **recusa** com o texto "teste".
4. No app, abra o pedido. Esperado: a conversa mostra "Recusada por [quem recusou] em [data e hora]: teste" e o
   botão volta a ficar habilitado.

**Pronto quando:** os 4 dão o esperado.

---

## 5. As três rotas da `.11` (referência rápida)

Todas em `http://192.168.7.11:8077`, com o cabeçalho `X-API-Key: <chave>`. **Só o backend chama.**

| Método e caminho | Para quê |
| --- | --- |
| `GET /pedidos/<docnum>/data-entrega` | situação de um pedido: datas, pendente e conversa |
| `POST /pedidos/<docnum>/data-entrega/solicitacoes` | pedir nova data |
| `GET /pedidos/data-entrega/solicitacoes?situacao=...&dias=...` | lista (passo 6) |

Corpo do `POST`:

```json
{"data_nova": "2026-11-27", "motivo": "quebra_maquina",
 "observacao": "Prensa 3 parada até 20/11.", "solicitante": "Ana Souza"}
```

Regras que a `.11` confere: `data_nova` de **hoje até hoje + 365 dias** (Brasília) e diferente da data combinada
atual; `motivo` um dos 4; `observacao` até 1000 caracteres (quebra de linha pode); `solicitante` até 80 caracteres.
**Uma pendente por pedido.** Repetir **a mesma `data_nova` e o mesmo `motivo`** de uma pendente devolve
`ja_existia: true` e não cria nada novo.

---

## 6. Respostas: o que mostrar e o que fazer

### 6.1 A função que decide (copie para o front)

Ela recebe o `status` e o `corpo` e devolve `{acao, texto}`. **Não decida em outro lugar.**

```javascript
const TEXTO_MOTIVO = {
  producao_sobrecarregada: 'Produção sobrecarregada',
  tempo_producao_apertado: 'Tempo de produção apertado',
  quebra_maquina: 'Quebra de máquina',
  alteracao_cliente: 'Alteração por parte do cliente',
};

const fmtData = (iso) => (iso ? String(iso).slice(0, 10).split('-').reverse().join('/') : '—');

const MENSAGENS = {
  // código do erro → [ação, texto]   (a tabela 6.3 diz o status e a origem de cada um)
  data_invalida: ['corrigir', 'Data inválida.'],
  motivo_invalido: ['corrigir', 'Escolha um dos 4 motivos.'],
  observacao_longa: ['corrigir', 'A observação passou de 1000 caracteres.'],
  observacao_invalida: ['corrigir', 'A observação tem um caractere que não é aceito.'],
  solicitante_invalido: ['desistir', 'Não foi possível identificar quem está pedindo. Entre de novo no app.'],
  corpo_invalido: ['corrigir', 'Preencha o formulário de novo.'],
  data_no_passado: ['corrigir', 'A nova data não pode ser no passado.'],
  data_muito_distante: ['corrigir', 'A nova data passa de 365 dias a partir de hoje. Confira o ano.'],
  data_igual_a_vigente: ['corrigir', 'Essa já é a data combinada do pedido.'],
  pedido_fechado: ['desistir', 'Este pedido está fechado no SAP.'],
  entrega_futura: ['desistir', 'Pedido faturado para entrega futura: combine a data direto com o Comercial.'],
  pedido_cancelado: ['desistir', 'Este pedido foi cancelado no SAP.'],
  sem_data_sap: ['desistir', 'Este pedido não tem data de entrega no SAP.'],
  pedido_nao_encontrado: ['desistir', 'Pedido não encontrado. Confira o número.'],
  fora_do_recorte: ['desistir', 'Este pedido não aparece na tela do Comercial. Avise o suporte.'],
  docnum_ambiguo: ['desistir', 'Problema com o número deste pedido. Avise o suporte.'],
  chave_nao_suportada: ['desistir', 'Erro no app (número do pedido). Avise o suporte.'],
  indeterminado: ['desistir', 'O SAP não respondeu agora. Nada foi gravado. Tente em alguns minutos.'],
  base_indisponivel: ['consultar', 'Não deu para confirmar a gravação. Conferindo…'],
  tempo_esgotado: ['consultar', 'O servidor demorou para responder. Conferindo…'],
  base_nao_configurada: ['desistir', 'Erro de configuração no servidor. Avise o suporte.'],
  servidor_inacessivel: ['desistir', 'O servidor de integração não respondeu. Tente em alguns minutos.'],
  resposta_invalida: ['desistir', 'O servidor de integração respondeu algo inesperado. Avise o suporte.'],
  acesso_negado_11: ['desistir', 'O app ainda não tem permissão no servidor de integração. Avise o suporte.'],
  parametro_invalido: ['desistir', 'Erro no app (parâmetro da lista). Avise o suporte.'],
};

function traduzirResposta(status, corpo) {
  corpo = corpo || {};
  if (status === 201) {
    return { acao: 'sucesso', texto: 'Solicitação enviada. Agora o Comercial decide. A data ainda NÃO mudou.' };
  }
  if (status === 200 && corpo.ja_existia === true) {
    const s = corpo.solicitacao || {};
    return { acao: 'sucesso',
             texto: `Essa mesma solicitação (${fmtData(s.data_nova)}, ${TEXTO_MOTIVO[s.motivo] || '—'}) já estava `
                  + `registrada por ${s.solicitado_por || '—'}. Nada novo foi criado `
                  + '(a observação enviada agora não foi guardada).' };
  }
  if (status === 409 && corpo.motivo === 'ja_ha_pendente') {
    const p = corpo.pendente || {};
    return { acao: 'desistir',
             texto: `Já existe uma solicitação esperando o Comercial: ${fmtData(p.data_nova)} `
                  + `(${TEXTO_MOTIVO[p.motivo] || '—'}) por ${p.solicitado_por || '—'}.` };
  }
  // 401 que chega ao front é SEMPRE o login do app (o backend troca o 401/403 da .11 por acesso_negado_11).
  if (status === 401) return { acao: 'login', texto: 'Sua sessão expirou. Entre de novo.' };
  if (corpo.error === 'rate_limited') {
    return { acao: 'esperar', texto: `Muitos envios seguidos. Aguarde ${corpo.retry_after_s} segundos.` };
  }
  if (status === 403) return { acao: 'desistir', texto: 'Você não tem permissão para pedir nova data.' };
  // A resposta não chegou: o envio pode ter sido gravado. Nunca "tente de novo" direto.
  if (status === 0) return { acao: 'consultar', texto: 'A resposta não chegou. Conferindo…' };
  // 422 sem código = o próprio FastAPI recusou o corpo (campo faltando ou do tipo errado).
  if (status === 422 && !corpo.motivo) return { acao: 'corrigir', texto: 'Confira os campos do formulário.' };
  // 5xx sem código = página de erro de um proxy ou do servidor: pode ter gravado.
  if (status >= 500 && !corpo.motivo) return { acao: 'consultar', texto: 'O servidor não respondeu direito. Conferindo…' };
  // 400 sem código = número do pedido inválido na URL (não deve acontecer com o DocNum do app).
  if (status === 400 && !corpo.motivo) return { acao: 'desistir', texto: 'Número do pedido inválido. Avise o suporte.' };
  const m = MENSAGENS[corpo.motivo];
  if (m) return { acao: m[0], texto: m[1] };
  return { acao: 'desistir', texto: 'Erro inesperado. Avise o suporte.' };
}
```

> A função já formata as datas e troca o código do motivo pelo texto. Mostre o `texto` como vier.
> Ela vale **só para a resposta do `POST`** (envio). Os `GET` seguem as tabelas 4.1 e 5.

### 6.2 Os corpos de resposta, completos

**201 — gravada:**
```json
{"ok": true, "ja_existia": false,
 "solicitacao": {"id": 812, "doc_num": 84453, "data_nova": "2026-11-27", "data_anterior": "2026-11-20",
   "data_sap": "2026-11-20", "motivo": "quebra_maquina", "gera_atraso": true,
   "observacao": "Prensa 3 parada até 20/11.", "origem": "producao_api", "situacao": "pendente",
   "solicitado_por": "Ana Souza", "solicitado_cliente": "<nome da chave>",
   "solicitado_em": "2026-11-10T12:00:00+00:00", "decidido_por": null, "decidido_em": null,
   "motivo_recusa": null},
 "entrega": {"ok": true, "pedido": 84453, "status_pedido": "Aberto", "sap_disponivel": true,
   "cache_idade_s": 12, "data_sap": "2026-11-20", "data_vigente": "2026-11-20",
   "data_referencia": "2026-11-20", "alterada": false, "atraso_nosso_dias": 0, "atrasado": false,
   "atrasado_fonte": "sap", "pendente": {"...": "os mesmos campos de 'solicitacao' acima"},
   "conversa": ["...a 'solicitacao' acima e as anteriores, mais antiga primeiro"]},
 "aviso": "Nada mudou ainda: a data so muda quando o Comercial aprovar no OrcaView. O SAP nao e alterado."}
```

**200 — já existia:**
```json
{"ok": true, "ja_existia": true,
 "solicitacao": {"id": 812, "doc_num": 84453, "data_nova": "2026-11-27", "data_anterior": "2026-11-20",
   "data_sap": "2026-11-20", "motivo": "quebra_maquina", "gera_atraso": true,
   "observacao": "a observação da PRIMEIRA vez", "origem": "producao_api", "situacao": "pendente",
   "solicitado_por": "Ana Souza", "solicitado_cliente": "<nome da chave>",
   "solicitado_em": "2026-11-10T12:00:00+00:00", "decidido_por": null, "decidido_em": null,
   "motivo_recusa": null},
 "aviso": "Nada mudou ainda: a data so muda quando o Comercial aprovar no OrcaView. O SAP nao e alterado."}
```

**409 — já há outra pendente:**
```json
{"ok": false, "motivo": "ja_ha_pendente", "pedido": 84453,
 "error": "ja existe uma solicitacao pendente para este pedido - o Comercial decide antes",
 "pendente": {"id": 811, "doc_num": 84453, "data_nova": "2026-12-01", "data_anterior": "2026-11-20",
   "data_sap": "2026-11-20", "motivo": "producao_sobrecarregada", "gera_atraso": true, "observacao": null,
   "origem": "producao_api", "situacao": "pendente", "solicitado_por": "Carlos",
   "solicitado_cliente": "<nome da chave>", "solicitado_em": "2026-11-09T12:00:00+00:00",
   "decidido_por": null, "decidido_em": null, "motivo_recusa": null}}
```

**Qualquer outra recusa da `.11`** (400, 404, 409, 422, 503):
```json
{"ok": false, "motivo": "data_no_passado", "error": "a nova data (2026-11-09) ja passou", "pedido": 84453}
```

**Acesso e limite** (código no `error`; os dois primeiros o backend de vocês troca por `acesso_negado_11`, parte 4, passo 2 — o front nunca os vê):
```json
{"ok": false, "error": "unauthorized"}
{"ok": false, "error": "forbidden", "tipo": "sem_permissao", "motivo": "A credencial ... nao tem o escopo ..."}
{"ok": false, "error": "rate_limited", "retry_after_s": 12, "motivo": "Trava anti-loop: ..."}
```

**Número de pedido inválido** (ex.: letras na URL): `400` com `{"ok": false, "error": "..."}`, sem `motivo`. Não
deve acontecer se o DocNum vier do app.

### 6.3 Todos os códigos (status e origem)

| Código (`motivo`) | HTTP | Quem cria | Ação |
| --- | --- | --- | --- |
| `corpo_invalido` | 400 | `.11` | corrigir |
| `data_invalida` | 400 | `.11` | corrigir |
| `motivo_invalido` | 400 | `.11` | corrigir |
| `observacao_longa` | 400 | `.11` | corrigir |
| `observacao_invalida` | 400 | `.11` | corrigir |
| `solicitante_invalido` | 400 | `.11` ou backend de vocês | desistir |
| `chave_nao_suportada` | 400 | `.11` (não deve acontecer: o app usa o DocNum) | desistir |
| `pedido_nao_encontrado` | 404 | `.11` | desistir |
| `fora_do_recorte` | 404 | `.11` | desistir |
| `ja_ha_pendente` | 409 | `.11` | desistir |
| `pedido_fechado` | 409 | `.11` | desistir |
| `entrega_futura` | 409 | `.11` | desistir |
| `pedido_cancelado` | 409 | `.11` | desistir |
| `sem_data_sap` | 409 | `.11` | desistir |
| `docnum_ambiguo` | 409 | `.11` (raro: problema de cadastro no SAP) | desistir |
| `data_no_passado` | 422 | `.11` | corrigir |
| `data_muito_distante` | 422 | `.11` | corrigir |
| `data_igual_a_vigente` | 422 | `.11` | corrigir |
| `parametro_invalido` | 422 | `.11` (só na lista do passo 6) | desistir |
| `indeterminado` | 503 | `.11` | desistir |
| `base_indisponivel` | 503 | `.11` | consultar |
| `base_nao_configurada` | 503 | `.11` | desistir |
| `rate_limited` (no campo `error`) | 429 | `.11` | esperar |
| `tempo_esgotado` | 504 | backend de vocês | consultar |
| `servidor_inacessivel` | 502 | backend de vocês | desistir |
| `resposta_invalida` | 502 | backend de vocês | desistir |
| `acesso_negado_11` | 502 | backend de vocês | desistir |
| (sem resposta, `status = 0`) | — | front | consultar |
| (422 sem `motivo`: corpo errado) | 422 | FastAPI de vocês | corrigir |
| (5xx sem `motivo`: proxy ou erro interno) | 5xx | proxy/servidor | consultar |
| (400 sem `motivo`: número inválido) | 400 | `.11` | desistir |
| (login do app expirou) | 401 | backend de vocês | login |
| (não é da Produção) | 403 | backend de vocês | desistir |

---

## 7. Exemplo completo da resposta do `GET`

```json
{
  "ok": true,
  "pedido": 84453,
  "status_pedido": "Aberto",
  "sap_disponivel": true,
  "cache_idade_s": 34,
  "data_sap": "2026-11-20",
  "data_vigente": "2026-11-27",
  "data_referencia": "2026-11-20",
  "alterada": true,
  "atraso_nosso_dias": 7,
  "atrasado": true,
  "atrasado_fonte": "combinada",
  "pendente": null,
  "conversa": [
    {"id": 812, "doc_num": 84453, "origem": "producao_api", "data_nova": "2026-11-27",
     "data_anterior": "2026-11-20", "data_sap": "2026-11-20", "motivo": "quebra_maquina",
     "gera_atraso": true, "observacao": "Prensa 3 parada até 20/11.", "situacao": "aprovada",
     "solicitado_por": "Ana Souza", "solicitado_cliente": "<nome da chave>",
     "solicitado_em": "2026-11-10T12:00:00+00:00",
     "decidido_por": "adriano@altamira.com.br", "decidido_em": "2026-11-10T15:40:00+00:00",
     "motivo_recusa": null}
  ]
}
```

---

## 8. Lista final (o agente confere antes de dizer "pronto")

- [ ] O quadro 0.1 estava preenchido; nada foi chutado.
- [ ] O front **nunca** chama `192.168.7.11` e **nunca** contém a chave.
- [ ] O app **nunca** lê nem grava a tabela `pedidos_entrega_alteracoes`.
- [ ] O app **não calcula** data combinada nem atraso.
- [ ] Sempre o **DocNum**.
- [ ] `solicitante` vem do usuário logado, pelo backend; o modelo de entrada não tem esse campo.
- [ ] Motivo escolhido pela pessoa; sem pré-seleção.
- [ ] Todas as respostas do **envio** (`POST`) passam por `traduzirResposta`.
- [ ] O backend troca o 401/403 da `.11` por `acesso_negado_11`; o 401 do front leva ao login.
- [ ] `"consultar"` faz o `GET` antes de deixar reenviar.
- [ ] `null` aparece como "—" e é testado antes de `true`/`false`.
- [ ] Texto da resposta entra na tela por `textContent`.
- [ ] Motivos e conversa nunca vão para PDF, e-mail ou relatório de cliente ou representante.
- [ ] Os 8 testes do passo 7 passam.
- [ ] O teste com o Comercial (passo 8) deu o esperado.

---

## 9. Perguntas frequentes

**A data muda na hora que a Produção pede?** Não. Só quando o Comercial aprovar.

**O SAP é alterado?** Nunca.

**E se o Comercial mudar a data sem a Produção pedir?** Aparece na conversa com `origem: "comercial"`. Essa é a data
que a Produção deve cumprir.

**Posso cancelar uma solicitação enviada por engano?** Ainda não. Peça ao Comercial para recusar com o texto
"pedido por engano".

**O que é `atrasado_fonte`?** `sap` = calculado pela data do SAP (ninguém mudou a data); `combinada` = pela data
combinada. Não precisa mostrar.

**E o MCP (`192.168.7.11:8078`)?** É para assistentes de IA conversarem com o servidor. **O app não usa.**

**Algo não está aqui?** Pare e pergunte ao Marcelo. Não invente.
