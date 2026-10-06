# API dos Pedidos WBC — guia para clonar a tela *Integração de Pedidos (WBC)*

Este guia é para a equipe que vai construir, no próprio sistema, uma página **igual** à tela
**Integração de Pedidos (WBC)** do Controle de Produção (`http://192.168.7.11:8080/pedidos-wbc`):
a mesma lista, os mesmos botões, a mesma conferência, o mesmo acompanhamento e **o mesmo
resultado no SAP**.

> ⚠️ **Tudo o que esta API grava vai direto para o SAP de produção** (`SBOALTAMIRAPROD`):
> Processar **cria Ordens de Produção**, Reprocessar **cancela** Ordens de Produção. Não existe
> ambiente de teste. Listar e conferir **não gravam nada** — dá para construir e testar quase a
> página inteira sem risco ([seção 12](#12-como-testar-sem-estragar-nada)).

### Como ler este guia

| Você quer… | Vá para |
| --- | --- |
| Entender **do que se trata** antes de tudo | [1. A ideia em um minuto](#1-a-ideia-em-um-minuto) |
| **Só usar** uma página pronta, sem programar | [3. A página pronta](#3-o-caminho-mais-curto-a-página-pronta) |
| **Construir a sua** página, passo a passo | [4. Tutorial: sua página em 5 etapas](#4-tutorial-sua-página-em-5-etapas) |
| Ver **um pedido de verdade** passar pela tela inteira — cada objeto, cada mensagem, cada chamada | [5. Caso real: o pedido 84460](#5-caso-real-o-pedido-84460-do-começo-ao-fim) |
| Saber **o que um campo ou uma rota faz** | [7. Referência das rotas](#7-referência-das-rotas) |
| Deixar a página **com a mesma cara** da original | [8. Clonar a tela](#8-clonar-a-tela-peça-por-peça) e [9. Tema](#9-tema-cores-letras-medidas-e-ícones) |
| Saber **o que fazer quando dá erro** | [10. Erros](#10-quando-dá-errado-todos-os-erros) |
| Conferir se está **pronto para usar de verdade** | [13. Lista de verificação](#13-lista-de-verificação-antes-de-pôr-no-ar) |

---

## Sumário

1. [A ideia em um minuto](#1-a-ideia-em-um-minuto)
2. [O que você precisa](#2-o-que-você-precisa)
3. [O caminho mais curto: a página pronta](#3-o-caminho-mais-curto-a-página-pronta)
4. [Tutorial: sua página em 5 etapas](#4-tutorial-sua-página-em-5-etapas)
5. [Caso real: o pedido 84460, do começo ao fim](#5-caso-real-o-pedido-84460-do-começo-ao-fim)
6. [Conceitos que você precisa conhecer](#6-conceitos-que-você-precisa-conhecer)
7. [Referência das rotas](#7-referência-das-rotas) —
   [lista](#71-get-pedidos--a-lista) · [conferir](#72-post-processarconferir-e-reprocessarconferir) ·
   [executar](#73-post-processarexecutar-e-reprocessarexecutar) ·
   [acompanhar](#74-get-execucoesid--acompanhar) · [interromper](#75-post-execucoesidcancelar--interromper)
8. [Clonar a tela, peça por peça](#8-clonar-a-tela-peça-por-peça)
9. [Tema: cores, letras, medidas e ícones](#9-tema-cores-letras-medidas-e-ícones)
10. [Quando dá errado: todos os erros](#10-quando-dá-errado-todos-os-erros)
11. [Exemplos completos de código](#11-exemplos-completos-de-código) — JavaScript, Python, PowerShell
12. [Como testar sem estragar nada](#12-como-testar-sem-estragar-nada)
13. [Lista de verificação antes de pôr no ar](#13-lista-de-verificação-antes-de-pôr-no-ar)
14. [Boas práticas](#14-boas-práticas)
15. [Perguntas frequentes](#15-perguntas-frequentes)
16. [Suporte e histórico deste documento](#16-suporte-e-histórico-deste-documento)

---

## 1. A ideia em um minuto

### O caminho de um pedido

Um orçamento é fechado no **WBC**. A integração cria o **pedido de venda no SAP**. A partir daí
ele aparece nesta tela, em **Pedidos novos**, esperando alguém **processar** — é o processamento
que cria as **Ordens de Produção (OPs)** que a fábrica vai fabricar. Processado, o pedido passa
para **Pedidos integrados**. Se algo saiu errado, ele pode ser **reprocessado**: as OPs planejadas
são canceladas e o pedido volta para "Pedidos novos", para ser processado de novo.

```mermaid
flowchart LR
    W["Orçamento fechado<br/>no WBC"] -->|"a integração cria"| N["📋 Pedidos novos<br/>(pedido no SAP, sem OP)"]
    N -->|"Processar"| I["✅ Pedidos integrados<br/>(OPs criadas)"]
    I -->|"Reprocessar<br/>(cancela as OPs planejadas)"| N
```

### O que a sua página faz

A página é uma conversa de quatro chamadas com a API — sempre nesta ordem, e **sempre com uma
pessoa confirmando no meio**:

```mermaid
sequenceDiagram
    actor P as Pessoa
    participant S as Sua página
    participant A as API (192.168.7.11:8080)
    S->>A: 1. GET /pedidos — "quais pedidos existem?"
    A-->>S: a lista, 15 por página
    P->>S: marca pedidos e clica "Processar selecionados…"
    S->>A: 2. POST /processar/conferir — "o que aconteceria?"
    A-->>S: o plano + um token (NADA foi gravado)
    S->>P: mostra o plano e o aviso vermelho
    P->>S: clica "Confirmar e executar"
    S->>A: 3. POST /processar/executar — token + nome da pessoa
    A-->>S: 202 + id da execução (começou)
    loop a cada 2 segundos, até terminar
        S->>A: 4. GET /execucoes/{id}
        A-->>S: passo atual, % e as linhas do log
    end
```

### Por que é fácil clonar

A API **não tem regra própria**: ela chama **as mesmas funções da tela**. E tudo o que a tela
mostra e que depende do servidor — o rótulo do botão, o aviso vermelho, a mensagem de recusa, as
linhas do acompanhamento — chega **pronto** na resposta. A sua página só **desenha** o que recebe.
Quando uma regra muda na tela, muda na sua página junto, sem você mexer em nada.

---

## 2. O que você precisa

| O quê | Detalhe |
| --- | --- |
| **Endereço** | `http://192.168.7.11:8080/api/pedidos-wbc` |
| **Rede** | Só funciona **de dentro da rede da empresa**. De fora, a conexão é recusada. |
| **Chave** | Peça ao Marcelo (TI). Vai no cabeçalho `X-API-Key` de **toda** chamada. |
| **Nome de quem opera** | Toda gravação leva `solicitante` — o nome (ou login) da **pessoa** que pediu. |
| **Formato** | JSON na ida e na volta, em UTF-8. |
| **Navegador** | Pode chamar direto de uma página de outro servidor (CORS liberado) — mas leia a [seção 14](#14-boas-práticas) sobre a chave. |

Neste guia, `SUA_CHAVE` é onde vai a chave de verdade.

---

## 3. O caminho mais curto: a página pronta

[`docs/exemplos/pedidos_wbc_clone.html`](docs/exemplos/pedidos_wbc_clone.html) é o clone
completo da tela, feito **só com esta API**:

- as três telas — lista, conferência, execução — com os mesmos textos;
- o tema claro e escuro, as cores, as letras, os ícones e o "relevo" dos botões da tela original;
- o comportamento: botão que só habilita com pedido marcado e conta "(n)", paginação, aviso de
  execução em andamento, acompanhamento a cada 2 s, linhas de erro em vermelho, Interromper;
- **um arquivo só**, sem biblioteca e sem CDN (roda na rede interna sem internet).

**Para usar:**

1. Copie o arquivo para o seu servidor (ou abra direto no navegador).
2. Se precisar, troque o endereço na linha `const API_BASE = "http://192.168.7.11:8080";`.
3. Abra a página: ela pede **a chave** e **o seu nome** na primeira vez. Os dois ficam só naquela
   aba do navegador (`sessionStorage`) — fechou a aba, some. Nada vai para o código nem para a URL.

Ela foi conferida contra a própria API (com o SAP simulado): lista, paginação, conferência,
execução concluída, execução com falha, os dois temas e a largura de celular.

Se o seu sistema tem outro visual (React, Angular, Blazor…), use a página como **referência
executável**: o código está dividido nas mesmas três telas deste guia (`telaDaLista`,
`telaDaConferencia`, `telaDaExecucao`) e cada trecho está comentado.

---

## 4. Tutorial: sua página em 5 etapas

Cada etapa acrescenta **uma** coisa à página e termina com um **✔ ponto de conferência** — só siga
para a próxima quando ele bater. As etapas 1 a 4 **não gravam nada** no SAP; só a 5 grava.

Os exemplos são em JavaScript de navegador, sem biblioteca. A lógica é a mesma em qualquer
linguagem (veja a [seção 11](#11-exemplos-completos-de-código) para Python e PowerShell).

### Etapa 1 — Falar com a API

Uma função só faz todas as chamadas: põe a chave, manda JSON e transforma erro em exceção.

```javascript
const BASE = "http://192.168.7.11:8080";
const CHAVE = sessionStorage.getItem("chave");          // peça à pessoa; nunca escreva no código

async function api(metodo, caminho, corpo) {
  const r = await fetch(BASE + caminho, {
    method: metodo,
    headers: { "X-API-Key": CHAVE, ...(corpo ? { "Content-Type": "application/json" } : {}) },
    body: corpo ? JSON.stringify(corpo) : undefined,
  });
  const dados = await r.json();
  if (!dados.ok) throw Object.assign(new Error(dados.motivo), { status: r.status, tipo: dados.tipo, dados });
  return dados;
}

const lista = await api("GET", "/api/pedidos-wbc/pedidos?modo=novos");
console.log(lista.kpi, lista.pedidos);
```

**✔ Ponto de conferência:** no console aparece algo como `{valor: 2, rotulo: "Novos"}` e a lista
de pedidos — os mesmos que a tela original mostra em "Pedidos novos".

**Se der errado:** `401 sem_chave` → chave errada ou ausente. Erro de rede → você está fora da rede
da empresa, ou o servidor está fora (`GET /health`, sem chave, responde?).

### Etapa 2 — Desenhar a lista

Tudo de que a tela precisa veio na resposta. Repare que **nenhum texto é inventado**: o rótulo do
KPI, o texto da linha vazia e a contagem vêm da API.

```javascript
const moeda = (v) => v.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function desenhaLista(d) {
  kpiValor.textContent = d.kpi.valor;                       // "2"
  kpiRotulo.textContent = d.kpi.rotulo;                     // "Novos" ou "Integrados"
  paginaKpi.textContent = `${d.pagina}/${d.paginas}`;       // "1/7"
  corpoTabela.replaceChildren(...d.pedidos.map((p) => linha([
    caixa(p.oportunidade),                                  // o VALOR da caixa é a oportunidade
    negrito(p.pedido), p.oportunidade, etiqueta(p.wbc), p.cliente, moeda(p.total), p.criado,
  ])));
  if (!d.pedidos.length) corpoTabela.replaceChildren(linhaVazia(d.vazio));
  contagem.textContent = d.total ? `${d.primeiro}–${d.ultimo} de ${d.total} pedido(s)` : "";
}
```

(`linha`, `caixa`, `negrito`, `etiqueta` e `linhaVazia` são funções suas que criam os elementos —
sempre com `textContent`, nunca `innerHTML`: nome de cliente vem do SAP e pode ter `<` ou `&`.)

**✔ Ponto de conferência:** abra a sua página e a original lado a lado. Mesmos pedidos, mesma
ordem (o mais recente primeiro), mesmos totais, mesmo "1–15 de 94 pedido(s)" em "integrados".

**Detalhes que fazem diferença:** escolher o modo **já carrega** a lista (não há botão "Buscar");
enquanto carrega, mostre "Carregando…" e trave os rádios; mudar de página **perde a seleção**
([8.2](#82-tela-1--a-lista)).

### Etapa 3 — Selecionar pedidos

O botão começa **desabilitado** e conta quantos pedidos estão marcados. O texto vem pronto em
`acao.botao` e `acao.verbo`:

```javascript
function atualizaBotao(d) {
  const marcados = caixas.filter((c) => c.checked).length;
  botao.disabled = marcados === 0;
  botao.textContent = marcados ? `${d.acao.verbo} selecionados (${marcados})…` : d.acao.botao;
  dica.hidden = marcados > 0;                               // "Marque ao menos um pedido."
}
```

Em "Pedidos integrados", `acao.aviso` traz o aviso amarelo que vai **embaixo do botão** — mostre-o
sempre, antes de qualquer clique: Reprocessar cancela OPs.

**✔ Ponto de conferência:** marcar 2 pedidos mostra "Processar selecionados (2)…"; desmarcar todos
volta a "Processar selecionados…" desabilitado.

### Etapa 4 — Conferir (ainda sem gravar)

O botão **não executa**: ele pede o **plano**. O servidor relê a lista no SAP, confere que os
pedidos ainda estão lá e devolve o que seria feito, com um `token` de 10 minutos.

```javascript
botao.onclick = async () => {
  const oportunidades = caixas.filter((c) => c.checked).map((c) => Number(c.value));
  try {
    const { plano } = await api("POST", lista.acao.conferir, { oportunidades });
    mostraConferencia(plano);      // título, aviso vermelho, resumo, tabela, validade, 2 botões
  } catch (e) {
    if (e.tipo === "fora_da_lista") recarregaLista(e.message);   // alguém processou antes de você
    else mostraErro(e.message);
  }
};
```

A tela de conferência usa só o que está no `plano` ([8.3](#83-tela-2--a-conferência)): título
"Confirmar: `{operacao}`", a faixa vermelha com `aviso.destaque` em negrito + `aviso.texto`, as
linhas de `resumo_tela`, a tabela de `itens` e "Esta conferência vale até `{valido_ate}`".

**✔ Ponto de conferência:** a sua conferência mostra o mesmo texto e a mesma tabela da original para
o mesmo pedido. **Nada foi gravado** — o token simplesmente vence se ninguém confirmar.

### Etapa 5 — Executar e acompanhar (grava no SAP)

> ⚠️ A partir daqui grava de verdade. Faça o primeiro teste **só num pedido combinado com o PCP**.

```javascript
confirmar.onclick = async () => {
  confirmar.disabled = true;                                // UM clique: o token vale uma vez
  try {
    const { execucao } = await api("POST", plano.executar,
      { token: plano.token, solicitante: sessionStorage.getItem("nome") });
    acompanha(execucao.id);
  } catch (e) {
    if (e.tipo === "ocupado") mostraErro(e.message, e.dados.execucao_em_andamento);  // link para a que está rodando
    else mostraErro(e.message);
  }
};

async function acompanha(id) {
  const { execucao: x } = await api("GET", `/api/pedidos-wbc/execucoes/${id}`);
  desenhaExecucao(x);              // pílula pelo desfecho, barra pelo percentual, passo, linhas
  if (!x.terminada) setTimeout(() => acompanha(id), 2000);
}
```

O que pintar a cada consulta ([8.4](#84-tela-3--a-execução)):

| Da API | Na tela |
| --- | --- |
| `situacao` + `desfecho` | a pílula: "executando" laranja, "concluída" verde, "concluída com falhas" amarela, "erro" vermelha |
| `percentual` | a largura da barra (0 a 100 %) |
| `passo` | a frase em negrito embaixo da barra |
| `linhas` | o log; as que têm `⚠` em **vermelho** |
| `resultado` | quando `terminada: true`, o resumo (o que foi processado, o que deu erro) |

**✔ Ponto de conferência:** a execução aparece também na tela **Execuções** do Controle de Produção,
com o seu nome e "API". O desfecho e as linhas são os mesmos nas duas.

Pronto: a página faz tudo o que a original faz. Antes de entregar, passe pela
[lista de verificação](#13-lista-de-verificação-antes-de-pôr-no-ar).

---

## 5. Caso real: o pedido 84460, do começo ao fim

Esta seção leva **um pedido de verdade** por toda a tela de integração: o **84460**, da VETNIL,
que estava em "Pedidos novos". Para cada passo ela mostra **o que a pessoa vê** (cada objeto da
página, com o texto exato), **a chamada da API** que a sua página faz e **a resposta** que volta —
e as mensagens de cada caminho que pode dar errado.

**De onde vêm os dados.** A lista e a conferência são **respostas reais** da API da .11, tiradas
em 06/10/2026 às 13:19 — só leitura, nada foi gravado. O 84460 **não foi processado** para
escrever este guia (processar é decisão do PCP). Por isso, os passos que gravam usam o formato que
a API devolve e o **log real** de pedidos processados na véspera pela tela (o 84462 e o 84454):
o 84460 produz a mesma sequência de linhas, com os números dele.

| | O pedido deste exemplo |
| --- | --- |
| Pedido no SAP (`pedido`) | **84460** |
| Oportunidade (`oportunidade`) | **15397** — é este número que a seleção manda |
| Orçamento WBC (`wbc`) | **00125718** |
| Cliente (`cliente`) | C002960 — VETNIL INDUSTRIA E COM DE PROD VETERIN LTDA |
| Total (`total`) | 104.652,95 |
| Criado (`criado`) | 2026-10-02 |
| Nota espelho (`nota_espelho`) | não |

### 5.1 O roteiro inteiro

| Passo | A pessoa faz | A sua página chama | Grava no SAP? |
| --- | --- | --- | --- |
| 1 | Abre a página | `GET /api/pedidos-wbc/pedidos?modo=novos` | não |
| 2 | Marca a caixa do 84460 | nada | não |
| 3 | Clica "Processar selecionados (1)…" | `POST /api/pedidos-wbc/processar/conferir` | não |
| 4 | Lê o plano e clica "Confirmar e executar" | `POST /api/pedidos-wbc/processar/executar` | **sim** |
| 5 | Acompanha o andamento | `GET /api/pedidos-wbc/execucoes/{id}`, a cada 2 s | não |
| 6 | Volta e vê o 84460 em "Pedidos integrados" | `GET /api/pedidos-wbc/pedidos?modo=integrados` | não |

```mermaid
flowchart LR
    A["1. Lista<br/>84460 em Pedidos novos"] -->|"2. marca a caixa"| B["3. Conferência<br/>Nada foi gravado ainda"]
    B -->|"Cancelar"| A
    B -->|"4. Confirmar e executar"| C["5. Execução<br/>log a cada 2 s"]
    C -->|"Voltar para Pedidos WBC"| D["6. Lista<br/>84460 em Pedidos integrados"]
```

### 5.2 O mapa da página: cada objeto da integração

A tela tem três estados — **lista**, **conferência** e **execução**. Abaixo, todos os objetos de
cada um, com o texto exato e de onde ele vem. "Fixo" é texto que a sua página escreve; o resto
vem pronto da API. A barra do topo (a "Central Integração SAP" e o menu das outras telas) não é da
integração e fica fora.

#### Lista (`GET /pedidos`)

| # | Objeto | Texto na tela | Vem de | Aparece quando |
| --- | --- | --- | --- | --- |
| L1 | Ícone (sacola) + título | "Integração de Pedidos (WBC)" | fixo | sempre |
| L2 | Subtítulo | "Cria itens, recursos de rateio e a cascata de Ordens de Produção" | fixo | sempre |
| L3 | Faixa laranja, ícone de relógio | "Há uma execução em andamento neste módulo: `{nome}`. Uma nova só é aceita depois que ela terminar — duas ao mesmo tempo disputariam os mesmos pedidos e OPs." — o nome é um link para a execução | `execucao_em_andamento` (`nome`, `estado`) | alguém está executando |
| L4 | Faixa amarela | o `motivo` do erro: "Não foi possível ler o SAP agora — tente de novo em instantes." | erro `502 sap_indisponivel` | a lista não carregou |
| L5 | Rádio | "Pedidos novos" | fixo → `modo=novos` | sempre; marcado ao abrir |
| L6 | Rádio | "Pedidos integrados" | fixo → `modo=integrados` | sempre |
| L7 | Nota ao lado dos rádios | "Carregando…" | fixo | enquanto a chamada não volta (rádios travados) |
| L8 | KPI 1 (borda coral) | "3" / "NOVOS" | `kpi.valor` / `kpi.rotulo` | depois que a lista carregou |
| L9 | KPI 2 (borda azul) | "1/1" / "PÁGINA" | `pagina`/`paginas` | depois que a lista carregou |
| L10 | Cabeçalho da tabela | PEDIDO · OPORTUNIDADE · WBC · CLIENTE · TOTAL · CRIADO | fixo | sempre |
| L11 | Linha do pedido | ☐ · **84460** · 15397 · ●00125718 · C002960 — VETNIL… · 104.652,95 · 2026-10-02 | `pedidos[]` | um por pedido |
| L12 | Pílula amarela sob o número | "Nota espelho" | `pedidos[].nota_espelho` = `true` | só pedido Nota Espelho |
| L13 | Linha única da tabela | "Nenhum pedido neste modo." | `vazio` | lista vazia |
| L14 | Contagem | "**1–3** de **3** pedido(s)" | `primeiro`, `ultimo`, `total` | `total` > 0 |
| L15 | Paginação | "‹ anterior" · "página **1** de **7**" · "próxima ›" · "A seleção vale só para esta página — mudar de página a perde." | `pagina`, `paginas` | `paginas` > 1 |
| L16 | Botão vermelho, ícone ▷ | "Processar selecionados…" → com 1 marcado: "Processar selecionados (1)…" | `acao.botao`, `acao.verbo` | sempre; desabilitado sem marca |
| L17 | Dica ao lado do botão | "Marque ao menos um pedido." · lista vazia: "Nenhum pedido para processar." | fixo | nada marcado |
| L18 | Faixa amarela embaixo do botão | `acao.aviso` (o aviso do Reprocessar) | `acao.aviso` | só em "Pedidos integrados" |

A tela original tem mais um objeto ao lado do botão: a caixa **"forçar (ignora as checagens de
reprocessamento)"**. Ela **não existe na API** e **não deve existir na sua página** — ela duplica
OP; mandar `force` dá `400` com a mensagem "'forçar' não existe nesta API (decisão de 01/10/2026):
ele duplica OP. Use a tela do Controle de Produção se for mesmo preciso."

#### Conferência (`POST /processar/conferir`)

| # | Objeto | Texto na tela | Vem de |
| --- | --- | --- | --- |
| C1 | Ícone de escudo, quadrado **vermelho** + título | "Confirmar: Processar pedidos novos" | "Confirmar: " + `plano.operacao` |
| C2 | Subtítulo | "Nada foi gravado ainda — confira e confirme" | fixo |
| C3 | Faixa vermelha, ícone ⚠ | "**Cria Ordens de Produção**, itens e recursos no SAP, e marca o pedido como processado. Não há desfazer automático." | `plano.aviso.destaque` (negrito) + `plano.aviso.texto` |
| C4 | Resumo | "**1** pedido(s) selecionado(s)" | `plano.resumo_tela[]` |
| C5 | Nota | "O que será feito, exatamente — confira antes de confirmar:" | fixo |
| C6 | Tabela | PEDIDO · OPORTUNIDADE · WBC · CLIENTE · TOTAL → 84460 · 15397 · 00125718 · C002960 — VETNIL… · 104.652,95 | `plano.itens[]` (`total_texto`) |
| C7 | Validade | "Esta conferência vale até **2026-10-06 13:29:30**. Depois disso é preciso refazê-la — o que seria feito é recalculado sobre o estado atual do SAP." | `plano.valido_ate` (troque o `T` por espaço) |
| C8 | Botão verde, ícone ▷ | "Confirmar e executar" | manda `plano.token` para `plano.executar` |
| C9 | Botão vermelho | "Cancelar" | fixo — volta para a lista sem chamar nada |

#### Execução (`GET /execucoes/{id}`)

| # | Objeto | Texto na tela | Vem de |
| --- | --- | --- | --- |
| E1 | Ícone de relógio + título | "Processar pedidos novos" | `nome` |
| E2 | Subtítulo | "1 pedido(s): 84460 · por Ana Souza · API" | `descricao` + " · por `solicitante` · API" (quando `origem` = `api`) |
| E3 | Pílula amarela no subtítulo | "Nota espelho" | `descricao` contém "(nota espelho)" |
| E4 | Botão à direita do título | "← Voltar para Pedidos WBC" | fixo — volta para a lista |
| E5 | Pílula de situação | "na fila" · "executando" · "concluída" · "concluída com falhas" · "erro" · "cancelada" | `situacao` (+ " com falhas" se `desfecho` = `falhas`); cor pelo `desfecho` |
| E6 | Progresso | " — 1/1" | `passos_feitos`/`passos_total` |
| E7 | Duração | " — 16.0 s" | `duracao_segundos` |
| E8 | Início | "· 06/10/2026 13:31:31" | `criada_em` em `dd/mm/aaaa hh:mm:ss` |
| E9 | Barra de 6 px | largura e cor | `percentual` e `desfecho` |
| E10 | Frase em negrito | "Pedido 84460 (WBC 00125718): concluído." | `passo` |
| E11 | Log (fonte monoespaçada) | uma linha por evento; as com ⚠ em vermelho | `linhas[]` |
| E12 | Faixa vermelha | o texto do erro | `erro` (só quando a execução quebrou) |
| E13 | Botão "■ Interromper" + nota | "Para depois do pedido em curso — ele termina inteiro; os próximos não começam. **Não desfaz** o que já foi gravado no SAP." | `POST /execucoes/{id}/cancelar`; some quando `terminada` |
| E14 | Seção "RESULTADO" | o JSON de `resultado`, formatado | `resultado` (quando `terminada`) |

### 5.3 Passo 1 — abrir a página: a lista de pedidos novos

Ao abrir, a página carrega "Pedidos novos" direto (não há botão "Buscar"):

```http
GET /api/pedidos-wbc/pedidos?modo=novos
X-API-Key: SUA_CHAVE
```

Resposta real (06/10/2026, 13:19):

```json
{
  "ok": true,
  "modo": "novos",
  "kpi": { "valor": 3, "rotulo": "Novos" },
  "total": 3,
  "pagina": 1,
  "paginas": 1,
  "por_pagina": 15,
  "primeiro": 1,
  "ultimo": 3,
  "pedidos": [
    {
      "pedido": 84464, "oportunidade": 15433, "wbc": "00125737",
      "cliente_codigo": "C004271", "cliente_nome": "TAKII DO BRASIL LTDA",
      "cliente": "C004271 — TAKII DO BRASIL LTDA",
      "total": 7700, "criado": "2026-10-06", "nota_espelho": false
    },
    {
      "pedido": 84463, "oportunidade": 15609, "wbc": "00125869",
      "cliente_codigo": "C002421", "cliente_nome": "POLY BLOW INDUSTRIA E COMERCIO LTDA",
      "cliente": "C002421 — POLY BLOW INDUSTRIA E COMERCIO LTDA",
      "total": 1383.55, "criado": "2026-10-05", "nota_espelho": false
    },
    {
      "pedido": 84460, "oportunidade": 15397, "wbc": "00125718",
      "cliente_codigo": "C002960", "cliente_nome": "VETNIL INDUSTRIA E COM DE PROD VETERIN LTDA",
      "cliente": "C002960 — VETNIL INDUSTRIA E COM DE PROD VETERIN LTDA",
      "total": 104652.95, "criado": "2026-10-02", "nota_espelho": false
    }
  ],
  "vazio": null,
  "acao": {
    "tipo": "processar",
    "verbo": "Processar",
    "botao": "Processar selecionados…",
    "conferir": "/api/pedidos-wbc/processar/conferir",
    "aviso": null
  },
  "execucao_em_andamento": null
}
```

Como a página fica com essa resposta (os números são os objetos da [5.2](#52-o-mapa-da-página-cada-objeto-da-integração)):

```
L1 ┌──────┐ Integração de Pedidos (WBC)
   │  🛍  │ Cria itens, recursos de rateio e a cascata de Ordens de Produção        L2
   └──────┘
L5/L6 (●) Pedidos novos  ( ) Pedidos integrados     L8 ┃ 3  NOVOS ┃   L9 ┃ 1/1  PÁGINA ┃
L10 ┌───┬────────┬──────────────┬────────────┬────────────────────────────────┬────────────┬────────────┐
    │   │ PEDIDO │ OPORTUNIDADE │ WBC        │ CLIENTE                        │      TOTAL │ CRIADO     │
L11 │ ☐ │ 84464  │    15433     │ ●00125737  │ C004271 — TAKII DO BRASIL LTDA │   7.700,00 │ 2026-10-06 │
    │ ☐ │ 84463  │    15609     │ ●00125869  │ C002421 — POLY BLOW INDUSTRIA… │   1.383,55 │ 2026-10-05 │
    │ ☐ │ 84460  │    15397     │ ●00125718  │ C002960 — VETNIL INDUSTRIA E … │ 104.652,95 │ 2026-10-02 │
    └───┴────────┴──────────────┴────────────┴────────────────────────────────┴────────────┴────────────┘
L14 1–3 de 3 pedido(s)
L16 [ ▷ Processar selecionados… ] (desabilitado)   L17 Marque ao menos um pedido.
```

- **A lista é viva.** De manhã havia **1** pedido novo (só o 84460); às 13:19 eram **3** — o
  84463 e o 84464 chegaram do WBC. Nunca guarde a lista: releia sempre que voltar à página.
- **O pedido mais novo no SAP vem primeiro** (ordem de criação do pedido). Por isso o 84460, o mais
  antigo dos três, é a última linha.
- **Sem L3, L4, L12, L13, L15 e L18:** não havia execução rodando, a lista carregou, nenhum pedido é
  Nota Espelho, a lista não está vazia, cabe numa página e o modo é "novos".

Se der errado neste passo (respostas reais):

| Situação | HTTP | `tipo` | `motivo` | O que a página faz |
| --- | --- | --- | --- | --- |
| Chave ausente ou errada | 401 | `sem_chave` | "X-API-Key ausente ou incorreta." | pede a chave de novo |
| `modo` errado | 400 | `invalido` | "'modo' deve ser 'novos' ou 'integrados'." | erro de programação: corrija a chamada |
| `pagina` errada | 400 | `invalido` | "'pagina' deve ser um número inteiro a partir de 1." | idem |
| O SAP não respondeu | 502 | `sap_indisponivel` | "Não foi possível ler o SAP agora — tente de novo em instantes." | faixa amarela (L4); a página abre mesmo assim |

### 5.4 Passo 2 — marcar o 84460

Este passo **não chama a API**. A pessoa marca a caixa da linha do 84460 e a página só se
redesenha:

| Objeto | Antes | Depois de marcar o 84460 |
| --- | --- | --- |
| Caixa da linha (L11) | ☐ | ☑ — o **valor** da caixa é **15397** (a oportunidade), não 84460 |
| Botão (L16) | "Processar selecionados…", desabilitado | "**Processar selecionados (1)…**", habilitado |
| Dica (L17) | "Marque ao menos um pedido." | some |

O número entre parênteses é a última conferência visual antes de sair da lista: se a pessoa marcou
2 sem querer, o botão diz "(2)". Mudar de página **perde a seleção** (só existe a da página vista).

### 5.5 Passo 3 — conferir: o plano do 84460 (ainda sem gravar)

O clique no botão manda **as oportunidades marcadas** para `acao.conferir`:

```http
POST /api/pedidos-wbc/processar/conferir
X-API-Key: SUA_CHAVE
Content-Type: application/json

{ "oportunidades": [15397] }
```

O servidor **relê a lista no SAP** (não confia no que a página mostrava), confirma que o 15397
ainda está em "Pedidos novos" e devolve o plano. Resposta real:

```json
{
  "ok": true,
  "plano": {
    "token": "BJdtIicyoXsVI1SApD9g8IoWAaZW8z_m",
    "tipo": "processar",
    "operacao": "Processar pedidos novos",
    "valido_ate": "2026-10-06T13:29:30",
    "resumo": { "pedidos": 1 },
    "resumo_tela": [ { "valor": 1, "rotulo": "pedido(s) selecionado(s)" } ],
    "aviso": {
      "destaque": "Cria Ordens de Produção",
      "texto": ", itens e recursos no SAP, e marca o pedido como processado. Não há desfazer automático."
    },
    "executar": "/api/pedidos-wbc/processar/executar",
    "itens": [
      {
        "pedido": 84460,
        "oportunidade": 15397,
        "wbc": "00125718",
        "cliente_codigo": "C002960",
        "cliente_nome": "VETNIL INDUSTRIA E COM DE PROD VETERIN LTDA",
        "cliente": "C002960 — VETNIL INDUSTRIA E COM DE PROD VETERIN LTDA",
        "total": 104652.95,
        "total_texto": "104.652,95"
      }
    ]
  }
}
```

(Este token já venceu: valia até 13:29:30, 10 minutos depois da conferência, e ninguém confirmou —
é exatamente o que acontece quando a pessoa desiste. Nada foi gravado.)

A página troca a lista pela conferência:

```
C1 ┌──────┐ Confirmar: Processar pedidos novos
   │  🛡  │ Nada foi gravado ainda — confira e confirme                                  C2
   └──────┘
   ┌───────────────────────────────────────────────────────────────────────────────────┐
C3 │ ⚠ Cria Ordens de Produção, itens e recursos no SAP, e marca o pedido como           │
   │   processado. Não há desfazer automático.                                           │
   └───────────────────────────────────────────────────────────────────────────────────┘
C4 1 pedido(s) selecionado(s)
C5 O que será feito, exatamente — confira antes de confirmar:
C6 ┌────────┬──────────────┬──────────┬───────────────────────────────────────┬────────────┐
   │ PEDIDO │ OPORTUNIDADE │ WBC      │ CLIENTE                               │ TOTAL      │
   │ 84460  │ 15397        │ 00125718 │ C002960 — VETNIL INDUSTRIA E COM DE … │ 104.652,95 │
   └────────┴──────────────┴──────────┴───────────────────────────────────────┴────────────┘
C7 Esta conferência vale até 2026-10-06 13:29:30. Depois disso é preciso refazê-la — o que
   seria feito é recalculado sobre o estado atual do SAP.
C8 [ ▷ Confirmar e executar ] (verde)     C9 [ Cancelar ] (vermelho)
```

Guarde `plano.token` e `plano.executar` — são o que o botão verde vai mandar.

Se der errado neste passo (respostas reais):

| Situação | HTTP | `tipo` | `motivo` | O que a página faz |
| --- | --- | --- | --- | --- |
| Nada marcado | 400 | `invalido` | "Nenhum pedido selecionado." | não deveria acontecer: o botão fica desabilitado |
| O pedido saiu da lista (alguém processou antes, pela tela ou por outro sistema) | 409 | `fora_da_lista` | "Estes pedidos não estão mais na lista elegível e foram recusados: 15397. Refaça a busca — o estado no SAP mudou desde que a tela foi carregada." (com `"fora": ["15397"]`) | recarrega a lista e mostra o `motivo` numa faixa vermelha |
| Número que não é de oportunidade | 400 | `invalido` | "'oportunidades' deve conter só números de Oportunidade: recebido 'abc'." | erro de programação |
| `force` no corpo | 400 | `invalido` | "'forçar' não existe nesta API (decisão de 01/10/2026): ele duplica OP. Use a tela do Controle de Produção se for mesmo preciso." | tire o `force` |

(O `fora_da_lista` acima é a resposta real para a oportunidade 99999, com o número trocado pelo do
exemplo: a frase é a mesma.)

### 5.6 Passo 4 — confirmar e executar (grava no SAP)

> ⚠️ Este é o único passo que grava. Para o 84460 ele cria, no SAP de produção, o Detalhe do
> Orçamento, os itens que faltarem, os recursos de rateio `GGF_…` e **todas as Ordens de
> Produção** do pedido — e marca o pedido como processado. Não há desfazer automático.

Ao clicar em "Confirmar e executar", a página **desabilita o botão** (o token vale uma vez) e manda o
token com o nome da pessoa:

```http
POST /api/pedidos-wbc/processar/executar
X-API-Key: SUA_CHAVE
Content-Type: application/json

{ "token": "BJdtIicyoXsVI1SApD9g8IoWAaZW8z_m", "solicitante": "Ana Souza" }
```

A resposta é **`202`** e quer dizer só "**começou**" — o resultado ainda não existe. Para o 84460
ela tem esta forma (o `id` e o horário mudam a cada execução):

```json
{
  "ok": true,
  "execucao": {
    "id": "3f9a1c2b7d10",
    "nome": "Processar pedidos novos",
    "descricao": "1 pedido(s): 84460",
    "situacao": "na fila",
    "terminada": false,
    "com_falhas": false,
    "desfecho": "fila",
    "passo": "",
    "passos_feitos": 0,
    "passos_total": 0,
    "percentual": null,
    "criada_em": "2026-10-06T13:31:31",
    "duracao_segundos": null,
    "linhas": [],
    "resultado": null,
    "erro": null,
    "solicitante": "Ana Souza",
    "origem": "api",
    "parada_pedida": false,
    "parada_combinada": true,
    "estado": "/api/pedidos-wbc/execucoes/3f9a1c2b7d10"
  }
}
```

A página vai direto para a tela de execução e começa a consultar `execucao.estado`.

Se der errado neste passo:

| Situação | HTTP | `tipo` | `motivo` | Gastou o token? | O que a página faz |
| --- | --- | --- | --- | --- | --- |
| Sem `solicitante` (resposta real) | 400 | `invalido` | "Informe 'solicitante': quem pediu a operação (nome ou login), até 80 caracteres. Ele vai para o log e para o histórico de Execuções." | **não** | corrige e manda de novo, com o mesmo token |
| Sem `token` | 400 | `invalido` | "Informe o 'token' devolvido por /api/pedidos-wbc/processar/conferir." | — | volta para a lista |
| Outra execução rodando (pela tela ou pela API) | 409 | `ocupado` | "O módulo 'pedidos_wbc' já tem uma execução em andamento (Processar pedidos novos, iniciada às 13:30:02). Duas execuções simultâneas no mesmo módulo disputariam os mesmos pedidos e OPs." | **não** | mostra o `motivo` e o link "Acompanhar a execução em andamento" (`execucao_em_andamento.estado`); não tenta em laço |
| Token vencido (passou das 13:29:30) | 409 | `confirmacao_invalida` | "A confirmação venceu. O plano foi calculado sobre o estado do SAP de alguns minutos atrás e pode não valer mais; refaça a conferência." | — | volta para a lista para conferir de novo |
| Token já usado (duplo clique) | 409 | `confirmacao_invalida` | "Confirmação inválida ou já utilizada. Refaça a conferência antes de executar — o que seria feito precisa ser recalculado." | — | idem; por isso o botão é desabilitado no clique |
| Escrita desligada no servidor | 503 | `escrita_desabilitada` | o motivo do servidor | **não** | avisa o TI |

### 5.7 Passo 5 — acompanhar: o log, linha por linha

A cada 2 segundos a página chama o `estado` até `terminada: true`:

```http
GET /api/pedidos-wbc/execucoes/3f9a1c2b7d10
X-API-Key: SUA_CHAVE
```

Enquanto roda, vem `"situacao": "executando"`, `"desfecho": "rodando"` e as `linhas` crescendo.
Para mostrar como o log do 84460 vai ficar, abaixo está a resposta **real** do último pedido
processado antes dele — o **84462** (WBC 00124669), processado pela tela em 05/10/2026 às 13:31,
em 16 segundos. Foi lida pela mesma rota, `GET /api/pedidos-wbc/execucoes/be5449dd646b`. As linhas
repetidas de OP de semiacabado foram cortadas (`…`):

```json
{
  "ok": true,
  "execucao": {
    "id": "be5449dd646b",
    "nome": "Processar pedidos novos",
    "descricao": "1 pedido(s): 84462",
    "situacao": "concluída",
    "terminada": true,
    "com_falhas": false,
    "desfecho": "ok",
    "passo": "Pedido 84462 (WBC 00124669): concluído.",
    "passos_feitos": 1,
    "passos_total": 1,
    "percentual": 100,
    "criada_em": "2026-10-05T13:31:31",
    "duracao_segundos": 16.0,
    "linhas": [
      "13:31:31  Conectando à Service Layer (SBOALTAMIRAPROD)…",
      "13:31:31  Pedido 84462 (WBC 00124669) (1/1)…",
      "13:31:31  Pedido 00124669 (DocNum 84462): montando a tabela do orçamento...",
      "13:31:32      Estrutura detalhada com 43 item(ns) — montando o espelho do orçamento.",
      "13:31:33  Login na Service Layer efetuado (CompanyDB=SBOALTAMIRAPROD)",
      "13:31:33      Detalhe do Orçamento 546878 criado.",
      "13:31:33    Pedido congelado: atualizando os campos de controle.",
      "13:31:34    Detalhe do Orçamento do pedido: 546872 → 546878 (conferido no SAP).",
      "13:31:34  Pedido 00124669: lendo a estrutura de produto no WBC...",
      "13:31:35  Pedido 00124669: peso da linha 0 (item I000003, OrcItm 1, qtd 1): SAP 3.408,00 kg · esperado 3.408,58 kg (árvore do WBC 3.098,71 kg + 10%).",
      "13:31:35  Pedido 00124669: peso da linha 1 (item I000003, OrcItm 5, qtd 1): SAP 29,79 kg · esperado 29,79 kg (árvore do WBC 27,08 kg + 10%).",
      "13:31:35  Pedido 00124669: estrutura com 43 item(ns); garantindo que estejam cadastrados (uma consulta por item)...",
      "13:31:35    Item PPLGAR00000000000080 está na lista de solda: forçando grupo 332.",
      "13:31:37  Pedido 00124669: 2 grupo(s) de produção a processar.",
      "13:31:37  Pedido 00124669: grupo 1/2 (GrpCode=2, OrcItm=1, 8 item(ns))...",
      "13:31:37      Recurso de rateio GGF_00124669I0000035492252 criado (Code=GGF_00124669I0000035492252).",
      "13:31:37    OP criada: DocEntry=159674, item I000003 — TRAVESSA CHAPA GALV. esp 1,55 mm 405MM (1 un), 9 linha(s).",
      "13:31:37    OP de semiacabado criada: DocEntry=159675, item PPLTRAGALVA155000000#405#0#0 — TRAVESSA CHAPA GALV. esp 1,55 mm 405MM (84 un), 1 componente(s).",
      "…",
      "13:31:43    OP de semiacabado criada: DocEntry=159689, item PAR000PADRA000000000 — CONJ PARAFUSO PADRAO 5/16 x 5/8 Gr 8 (280 un), 2 componente(s).",
      "13:31:43    Gravando U_INO_OP=159674 em 1 linha(s) do pedido 20357.",
      "13:31:45  Pedido 00124669: grupo 2/2 (GrpCode=2, OrcItm=5, 1 item(ns))...",
      "13:31:45      Recurso de rateio GGF_00124669I00000380448 criado (Code=GGF_00124669I00000380448).",
      "13:31:45    OP criada: DocEntry=159690, item I000003 — PROTETOR COLUNA NOVO MED 300MM C/ CHUMB NORMAL (1 un), 2 linha(s).",
      "…",
      "13:31:46    Gravando U_INO_OP=159690 em 1 linha(s) do pedido 20357.",
      "13:31:47  Pedido 84462 (WBC 00124669): concluído."
    ],
    "resultado": {
      "sem_op": [],
      "com_erro": [],
      "sem_rateio": [],
      "processados": [ { "doc_num": "84462", "orc_num": "00124669" } ],
      "pesos_diferentes": []
    },
    "erro": null,
    "solicitante": null,
    "origem": "tela",
    "parada_pedida": false,
    "parada_combinada": false,
    "estado": "/api/pedidos-wbc/execucoes/be5449dd646b"
  }
}
```

Duas diferenças para o que a sua página vai ver: ali `"origem": "tela"` e `"solicitante": null`
(foi feito pela tela original); pela API vêm `"api"` e o nome da pessoa — e o subtítulo E2 ganha
" · por Ana Souza · API".

**O que cada linha quer dizer** — na ordem em que aparecem:

| Linha do log | O que aconteceu no SAP |
| --- | --- |
| "Conectando à Service Layer (SBOALTAMIRAPROD)…" | Começou. `SBOALTAMIRAPROD` é o SAP de produção. |
| "Pedido 84462 (WBC 00124669) (1/1)…" | Começou o pedido 1 de 1. Com 3 pedidos marcados, viria "(1/3)", "(2/3)"… |
| "montando a tabela do orçamento..." · "Estrutura detalhada com 43 item(ns)" | Leu o orçamento no WBC: 43 itens na árvore. |
| "Login na Service Layer efetuado" | Entrou no SAP para gravar. |
| "Detalhe do Orçamento 546878 criado." | **Gravou** o espelho do orçamento no SAP. |
| "Pedido congelado: atualizando os campos de controle." | **Marcou o pedido como processado** — antes da primeira OP (é por isso que não há desfazer automático). |
| "Detalhe do Orçamento do pedido: 546872 → 546878 (conferido no SAP)." | O pedido passou a apontar para o Detalhe novo, e o servidor releu o SAP para conferir. |
| "peso da linha 0 (…): SAP 3.408,00 kg · esperado 3.408,58 kg (árvore do WBC 3.098,71 kg + 10%)." | Conferência do peso de cada linha (regra: árvore do WBC + 10%). Diferença acima de 1% vira aviso ⚠ com a CAUSA ([7.4.1](#741-peso-diferente-e-a-causa)). |
| "garantindo que estejam cadastrados (uma consulta por item)..." | Confere cada item da árvore; cria o que faltar. |
| "Item … está na lista de solda: forçando grupo 332." | Regra da fábrica: item soldado vai para o grupo de produção 332. |
| "2 grupo(s) de produção a processar." · "grupo 1/2 (…)" | Cada grupo de produção vira uma cascata de OPs. |
| "Recurso de rateio GGF_… criado" | **Gravou** a linha de custo (transporte, embalagem, montagem) que entra na OP. |
| "OP criada: DocEntry=159674, item I000003 — … (1 un), 9 linha(s)." | **Criou a OP principal** do grupo. `DocEntry` é o número interno da OP no SAP. |
| "OP de semiacabado criada: DocEntry=159675, …" | **Criou a OP de cada peça intermediária** (travessa, diagonal, longarina…). |
| "Gravando U_INO_OP=159674 em 1 linha(s) do pedido 20357." | Ligou a linha do pedido à OP principal. 20357 é o número **interno** (DocEntry) do pedido 84462. |
| "Pedido 84462 (WBC 00124669): concluído." | Fim do pedido — é também o último `passo`. |

A tela de execução no fim (com os valores que o 84460 vai mostrar):

```
E1 ┌──────┐ Processar pedidos novos                            E4 [ ← Voltar para Pedidos WBC ]
   │  🕑  │ 1 pedido(s): 84460 · por Ana Souza · API                                      E2
   └──────┘
E5 (● CONCLUÍDA) E6 — 1/1  E7 — 16.0 s  E8 · 06/10/2026 13:31:31
E9 ███████████████████████████████████████████████████████████████████  (barra verde)
E10 Pedido 84460 (WBC 00125718): concluído.
E11 ┌ log ──────────────────────────────────────────────────────────────────────────┐
    │ 13:31:31  Conectando à Service Layer (SBOALTAMIRAPROD)…                        │
    │ 13:31:31  Pedido 84460 (WBC 00125718) (1/1)…                                   │
    │ …                                                                              │
    │ 13:31:47  Pedido 84460 (WBC 00125718): concluído.                              │
    └────────────────────────────────────────────────────────────────────────────────┘
E14 RESULTADO
    { "processados": [ { "doc_num": "84460", "orc_num": "00125718" } ], "com_erro": [], … }
```

O botão Interromper (E13) aparece enquanto `terminada` é `false` e some no fim. Para conferir o
resultado: `desfecho` = `ok`, o 84460 em `resultado.processados`, e `com_erro`, `sem_op` e
`sem_rateio` vazios.

### 5.8 Quando o pedido dá erro: um caso real

Em 02/10/2026 às 08:42 o processamento do **84454** falhou porque a Service Layer do SAP estava com
problema (resposta real de `GET /api/pedidos-wbc/execucoes/e8cef1e3f3c6`, resumida):

```json
{
  "situacao": "concluída",
  "terminada": true,
  "com_falhas": true,
  "desfecho": "falhas",
  "passo": "Pedido 84454 (WBC 00125793): ERRO — Falha no login da Service Layer: 400 {\n   \"error\" : {\n      \"code\" : 126,\n      \"message\" : {\n         \"lang\" : \"en-us\",\n         \"value\" : \"Unable to load OBServerDLL.dll\"\n      }\n   }\n}\n",
  "linhas": [
    "08:42:36  Conectando à Service Layer (SBOALTAMIRAPROD)…",
    "08:42:36  Pedido 84454 (WBC 00125793) (1/1)…",
    "08:42:36  Pedido 00125793 (DocNum 84454): montando a tabela do orçamento...",
    "08:42:39      Estrutura detalhada com 46 item(ns) — montando o espelho do orçamento.",
    "08:42:40  ⚠ Erro em preenche_tabela para orc_num=00125793",
    "08:42:40    Pedido congelado: atualizando os campos de controle.",
    "08:42:41  ⚠ Erro ao processar pedido novo orc_num=00125793",
    "08:42:42  ⚠ Pedido 84454 (WBC 00125793): ERRO — Falha no login da Service Layer: 400 {…}"
  ],
  "resultado": {
    "processados": [],
    "com_erro": [
      { "orc_num": "00125793", "doc_num": "84454", "motivo": "Falha no login da Service Layer: 400 {…}" }
    ],
    "sem_op": [], "sem_rateio": [], "pesos_diferentes": []
  }
}
```

O que a página mostra e por quê:

- **Pílula amarela "concluída com falhas"** — `situacao` diz "concluída" (a execução não quebrou),
  mas o `desfecho` é `falhas`: tem pedido em `com_erro`. Pinte pelo `desfecho`, nunca de verde.
- **As linhas com ⚠ em vermelho.**
- **O `passo` e o `motivo` têm quebras de linha** (`\n`) — o SAP devolveu um JSON de erro. Mostre com
  `white-space: pre-wrap`, como texto.
- **O que fazer depois:** volte à lista e veja onde o pedido está. Neste caso o 84454 continuou em
  "Pedidos novos" e foi processado de novo às 09:02, com sucesso. Se um pedido aparecer em
  "Pedidos integrados" com OP faltando, fale com o PCP antes de qualquer coisa ([6](#6-conceitos-que-você-precisa-conhecer), "O que Processar grava").

### 5.9 Passo 6 — depois: o 84460 em "Pedidos integrados"

Terminado o Processar, o 84460 **sai** de "Pedidos novos" e **entra** em "Pedidos integrados":

| | Antes (06/10, 13:19, real) | Depois de processar o 84460 |
| --- | --- | --- |
| `GET …/pedidos?modo=novos` → `kpi` | 3 Novos | 2 Novos (84464 e 84463) |
| `GET …/pedidos?modo=integrados` → `kpi` | 93 Integrados, 7 páginas | 94 Integrados |
| Onde o 84460 aparece | 3ª linha de "novos" | 1ª página de "integrados", entre o 84461 e o 84458 |

Em "Pedidos integrados" a página muda três objetos, tudo pela resposta (`acao`):

- o botão (L16) vira "**Reprocessar selecionados…**", com o ícone de raio;
- a dica (L17) fica "Marque ao menos um pedido." (lista vazia: "Nenhum pedido para reprocessar.");
- aparece a faixa amarela (L18), **antes de qualquer clique**: "**Reprocessar** cancela todas as OPs
  **planejadas** do pedido — de qualquer origem, inclusive as do addon — e **não recria**: o pedido
  volta para "Pedidos novos" e precisa ser processado de novo."

**Se o 84460 precisar ser refeito** (ex.: o orçamento mudou no WBC depois de processado), o caminho é
o mesmo, com as rotas do Reprocessar: marca a caixa (oportunidade 15397) →
`POST /api/pedidos-wbc/reprocessar/conferir` → conferência com o título "Confirmar: Reprocessar
pedidos integrados" e a faixa vermelha inteira do `aviso.texto` (sem `destaque`) →
`POST /api/pedidos-wbc/reprocessar/executar` → acompanhamento. O log real do 84454 reprocessado em
05/10/2026 às 15:15 (`903bf0c9d992`), resumido:

```
15:15:54  Pedido 84454 (WBC 00125793) (1/1)…
15:15:58      Detalhe do Orçamento 546895 criado.
15:15:58    Pedido congelado: atualizando os campos de controle e zerando U_INO_OP nas linhas.
15:15:58    Vinculando o pedido 84454 à Oportunidade 15507 (status WBC 60)...
15:15:59      Oportunidade 15507 marcada como Ganha.
15:15:59    20 OP(s) planejada(s) do pedido 84454 a cancelar antes de recriar.
15:15:59      OP 157883 cancelada (1/20): I000003 — Porta-Paletes (1 un).
…
15:16:03      OP 157902 cancelada (20/20): PAR000PADRA000000000 — CONJ PARAFUSO PADRAO 5/16 x 5/8 Gr 8 (372 un).
15:16:03    Pedido 84454 reintegrado.
15:16:03  Pedido 84454 (WBC 00125793): reprocessado — OPs planejadas canceladas; processe de novo em "Pedidos novos" para criar as OPs.
```

Atenção à linha "a cancelar **antes de recriar**": o Reprocessar **não recria** nada — a última
linha diz o que fazer: o pedido voltou para "Pedidos novos" e precisa de um **Processar** (os passos
1 a 5 de novo). O `resultado` vem com `"atualizados": [{"doc_num": "84454", "orc_num": "00125793"}]`.

### 5.10 Todas as mensagens da integração, num lugar só

Os textos que a pessoa pode ver nesta tela. "API" = vem pronto na resposta (use-o como veio);
"fixo" = a sua página escreve.

| Onde | Mensagem | Quando | Origem |
| --- | --- | --- | --- |
| Lista | "Carregando…" | enquanto a lista carrega | fixo |
| Lista | "Nenhum pedido neste modo." | lista vazia | API `vazio` |
| Lista | "Marque ao menos um pedido." | nada marcado | fixo |
| Lista | "Nenhum pedido para processar." / "…para reprocessar." | lista vazia | fixo |
| Lista | "A seleção vale só para esta página — mudar de página a perde." | mais de uma página | fixo |
| Lista | "Há uma execução em andamento neste módulo: … Uma nova só é aceita depois que ela terminar — duas ao mesmo tempo disputariam os mesmos pedidos e OPs." | execução rodando | fixo + API `execucao_em_andamento.nome` |
| Lista | "Reprocessar cancela todas as OPs planejadas do pedido — …" | "Pedidos integrados" | API `acao.aviso` |
| Lista | "Não foi possível ler o SAP agora — tente de novo em instantes." | `502 sap_indisponivel` | API `motivo` |
| Conferência | "Nada foi gravado ainda — confira e confirme" | sempre | fixo |
| Conferência | "**Cria Ordens de Produção**, itens e recursos no SAP, e marca o pedido como processado. Não há desfazer automático." | Processar | API `aviso` |
| Conferência | "Para cada pedido: grava uma tabela nova do orçamento (OrcDetalhe), … OP cancelada não volta." | Reprocessar | API `aviso.texto` |
| Conferência | "O que será feito, exatamente — confira antes de confirmar:" | sempre | fixo |
| Conferência | "Esta conferência vale até … Depois disso é preciso refazê-la — o que seria feito é recalculado sobre o estado atual do SAP." | sempre | fixo + API `valido_ate` |
| Conferência | "Estes pedidos não estão mais na lista elegível e foram recusados: … Refaça a busca — o estado no SAP mudou desde que a tela foi carregada." | `409 fora_da_lista` | API `motivo` |
| Conferência | "Nenhum pedido selecionado." | `400 invalido` | API `motivo` |
| Executar | "Informe 'solicitante': quem pediu a operação (nome ou login), até 80 caracteres. …" | `400 invalido` | API `motivo` |
| Executar | "O módulo 'pedidos_wbc' já tem uma execução em andamento (…). Duas execuções simultâneas no mesmo módulo disputariam os mesmos pedidos e OPs." | `409 ocupado` | API `motivo` |
| Executar | "A confirmação venceu. …" · "Confirmação inválida ou já utilizada. …" · "Esta confirmação é de outra operação e não vale aqui. …" | `409 confirmacao_invalida` | API `motivo` |
| Execução | "Para depois do pedido em curso — ele termina inteiro; os próximos não começam. Não desfaz o que já foi gravado no SAP." | ao lado do Interromper | fixo |
| Execução | "Interrupção pedida — o que está em curso termina inteiro e o próximo não começa. O que já foi gravado no SAP fica." | depois do Interromper (`cancelada: true`) | fixo |
| Execução | "Não havia execução em andamento para interromper." | depois do Interromper (`cancelada: false`) | fixo |
| Execução | "Execução não encontrada. Ela pode ter saído do histórico (ficam as 30 mais recentes) ou o serviço foi reiniciado antes de ela ser guardada." | `404 nao_encontrada` | API `motivo` |
| Execução | cada linha do log e o `passo` | sempre | API `linhas`, `passo` |
| Qualquer | "X-API-Key ausente ou incorreta." | `401 sem_chave` | API `motivo` |

### 5.11 O mesmo roteiro, em código

O roteiro completo do 84460 em Python — o mesmo da [seção 11](#11-exemplos-completos-de-código),
com a busca pelo número do pedido e a confirmação da pessoa no meio. Até o `input` nada é gravado:

```python
import os
import time

import requests

BASE = "http://192.168.7.11:8080"
CABECALHO = {"X-API-Key": os.environ["SIS_API_KEY"]}


def chama(metodo: str, caminho: str, corpo: dict | None = None) -> dict:
    r = requests.request(metodo, BASE + caminho, headers=CABECALHO, json=corpo, timeout=120)
    dados = r.json()
    if not dados.get("ok"):
        raise RuntimeError(f"{r.status_code} {dados['tipo']}: {dados['motivo']}")
    return dados


lista = chama("GET", "/api/pedidos-wbc/pedidos?modo=novos")
alvo = next((p for p in lista["pedidos"] if p["pedido"] == 84460), None)
if alvo is None:
    raise SystemExit("O 84460 não está em Pedidos novos (já foi processado?).")

plano = chama("POST", lista["acao"]["conferir"], {"oportunidades": [alvo["oportunidade"]]})["plano"]
print(f"Confirmar: {plano['operacao']}")
print(plano["aviso"]["destaque"] + plano["aviso"]["texto"])
for item in plano["itens"]:
    print(f"  {item['pedido']}  {item['oportunidade']}  {item['wbc']}  {item['cliente']}  {item['total_texto']}")
print(f"Esta conferência vale até {plano['valido_ate'].replace('T', ' ')}.")
if input("Digite SIM para executar: ").strip() != "SIM":
    raise SystemExit("Nada foi gravado.")

execucao = chama("POST", plano["executar"], {"token": plano["token"], "solicitante": "Ana Souza"})["execucao"]
vistas = 0
while True:
    for linha in execucao["linhas"][vistas:]:
        print(linha)
    vistas = len(execucao["linhas"])
    if execucao["terminada"]:
        break
    time.sleep(2)
    execucao = chama("GET", execucao["estado"])["execucao"]
print("Desfecho:", execucao["desfecho"])
print("Resultado:", execucao["resultado"])
```

Repare que nenhuma rota foi escrita à mão depois da primeira: `acao.conferir`, `plano.executar` e
`execucao.estado` vêm nas respostas. Se uma rota mudar de nome, o roteiro continua funcionando.

---

## 6. Conceitos que você precisa conhecer

### Glossário

| Palavra | O que quer dizer aqui |
| --- | --- |
| **Pedido** | O pedido de venda no SAP (o número que aparece no SAP, ex.: `84445`). |
| **Oportunidade** | A oportunidade de venda no SAP ligada ao pedido. É por ela que a seleção é feita. |
| **Orçamento WBC** | O orçamento no WBC que deu origem ao pedido (8 dígitos, ex.: `00124882`). |
| **OP** | Ordem de Produção: o que a fábrica fabrica. O Processar cria; o Reprocessar cancela as planejadas. |
| **Semiacabado** | Peça intermediária com OP própria (ex.: a longarina de um porta-paletes). |
| **Recurso de rateio** | Linha de custo (transporte, embalagem, montagem) que entra na OP, com código `GGF_…`. |
| **Processar** | Transformar um pedido novo em OPs. Grava no SAP. |
| **Reprocessar** | Desfazer as OPs planejadas de um pedido integrado e devolvê-lo a "novos". Grava no SAP. |
| **Plano** | O que o servidor **faria**, calculado na conferência. Não grava nada. |
| **Token** | O "comprovante" do plano conferido: vale 10 minutos, uma vez, para uma operação. |
| **Execução** | O trabalho rodando no servidor depois do Confirmar. Tem um `id` para acompanhar. |
| **Solicitante** | O nome da pessoa que mandou executar. Fica no histórico. |
| **Desfecho** | Como a execução terminou: `ok`, `falhas`, `erro` ou `cancelada` (ver abaixo). |
| **Peso da linha** | O peso de uma linha do pedido no SAP: árvore do WBC + 10%, para a linha inteira. |

### A vida de uma execução

```mermaid
stateDiagram-v2
    [*] --> fila: Confirmar (202)
    fila --> rodando: começa
    rodando --> ok: terminou sem erro
    rodando --> falhas: terminou, mas algum pedido deu erro
    rodando --> erro: a execução quebrou
    rodando --> cancelada: alguém interrompeu
    ok --> [*]
    falhas --> [*]
    erro --> [*]
    cancelada --> [*]
```

O `desfecho` é o nome do estado. **`falhas` não é `ok`**: a execução chegou ao fim, mas pelo menos
um pedido está em `resultado.com_erro` — mostre isso em amarelo, não em verde.

### Três números para o mesmo pedido

Cada linha da tela mostra três números, e cada um serve a uma coisa:

| Campo | Exemplo | O que é | Para que serve |
| --- | --- | --- | --- |
| `pedido` | `84445` | Nº do **pedido de venda no SAP** (DocNum) | É o que a pessoa procura no SAP. É o que a tela destaca em negrito. |
| `oportunidade` | `14232` | Nº da **oportunidade de venda no SAP** | É o que você **manda** para selecionar (`oportunidades`). É o valor da caixa de seleção da tela. |
| `wbc` | `00124882` | Nº do **orçamento no WBC** (8 dígitos) | É o que o processamento usa por dentro. Aparece como etiqueta laranja. |

Não troque um pelo outro: a seleção é **sempre** pela `oportunidade`.

### Pedidos novos × Pedidos integrados

| Modo | Quem aparece | O botão faz |
| --- | --- | --- |
| **Pedidos novos** (`novos`) | Pedidos vindos do WBC que **ainda não** foram processados | **Processar** |
| **Pedidos integrados** (`integrados`) | Pedidos **já** processados | **Reprocessar** |

Os dois mostram o **mais recente primeiro**, 15 por página.

### O que Processar grava (de verdade)

Para cada pedido: monta a tabela do orçamento, garante os itens cadastrados, cria os recursos
de rateio e **cria a cascata de Ordens de Produção** (uma por grupo de produção, com os
semiacabados). Antes da primeira OP, marca o pedido como **processado**.

⚠️ **Não há desfazer automático.** Se a execução cair no meio, o pedido pode ficar marcado
como processado com OPs faltando — o resultado da execução diz o que aconteceu, pedido a pedido.

### O que Reprocessar grava (de verdade)

Para cada pedido: grava uma tabela nova do orçamento, marca o pedido como **NÃO processado**,
revincula a oportunidade e **CANCELA todas as OPs PLANEJADAS** do pedido — de qualquer origem,
inclusive as criadas pelo addon do SAP. **Não recria as OPs**: o pedido volta para "Pedidos
novos" e precisa ser processado de novo. OP liberada ou encerrada não é tocada; OP cancelada
não volta.

### Conferir, depois executar (o plano e o token)

Nada grava "direto do botão". É sempre em duas etapas, como na tela:

1. **Conferir** — o servidor **relê a lista no SAP** (não confia no que a página mostrou),
   monta o plano e devolve um `token`;
2. **Executar** — você manda o `token`, e só o que foi conferido é executado.

O token vale **10 minutos**, **uma vez**, e só para **a mesma operação** (o de Processar não
executa Reprocessar). Vencido ou usado → confira de novo.

### Execução em segundo plano

Executar responde na hora (`202`) com o `id` da execução — **ainda não** com o resultado. Um
pedido médio leva uns 7 segundos; um grande passa de um minuto. Acompanhe pelo `estado` a cada
2 segundos até `terminada: true`.

### Uma execução por vez

O módulo Pedidos WBC roda **uma execução de cada vez** — e isso vale junto para a tela e para
a API (duas ao mesmo tempo disputariam os mesmos pedidos e OPs). Se outra estiver rodando, você
recebe `409 ocupado` com a execução que está em andamento.

### Solicitante

Toda gravação (e o Interromper) leva `solicitante`: **o nome da pessoa** que pediu. Vai para o
log do servidor e para o histórico da tela **Execuções**. Até 80 caracteres, sem quebra de linha.

### Não existe "forçar"

A tela tem uma opção "forçar (ignora as checagens de reprocessamento)". **Ela não existe nesta
API**, de propósito: ela duplica OP. Mandar `force` (ou `forcar`) em qualquer chamada dá `400`.

---

## 7. Referência das rotas

Todas exigem `X-API-Key`. Respostas de sucesso trazem `"ok": true`; erros trazem
`{"ok": false, "tipo": "...", "motivo": "..."}` ([seção 10](#10-quando-dá-errado-todos-os-erros)).

| Na tela | Na API |
| --- | --- |
| Escolher "Pedidos novos"/"integrados", mudar de página | `GET /api/pedidos-wbc/pedidos?modo=novos&pagina=1` |
| "Processar selecionados…" | `POST /api/pedidos-wbc/processar/conferir` |
| "Confirmar e executar" | `POST /api/pedidos-wbc/processar/executar` |
| "Reprocessar selecionados…" | `POST /api/pedidos-wbc/reprocessar/conferir` |
| "Confirmar e executar" (do Reprocessar) | `POST /api/pedidos-wbc/reprocessar/executar` |
| A tela de execução, que se atualiza sozinha | `GET /api/pedidos-wbc/execucoes/{id}` |
| "Interromper" | `POST /api/pedidos-wbc/execucoes/{id}/cancelar` |

### 7.1 `GET /pedidos` — a lista

| Parâmetro | Valores | Padrão |
| --- | --- | --- |
| `modo` | `novos` ou `integrados` | `novos` |
| `pagina` | inteiro a partir de 1 | `1` |

Página além do fim vira a última (como na tela: a lista encolhe conforme os pedidos são
processados, e o número da página pode estar velho).

```http
GET /api/pedidos-wbc/pedidos?modo=novos
X-API-Key: SUA_CHAVE
```

```json
{
  "ok": true,
  "modo": "novos",
  "kpi": { "valor": 1, "rotulo": "Novos" },
  "total": 1,
  "pagina": 1,
  "paginas": 1,
  "por_pagina": 15,
  "primeiro": 1,
  "ultimo": 1,
  "pedidos": [
    {
      "pedido": 84445,
      "oportunidade": 14232,
      "wbc": "00124882",
      "cliente_codigo": "C006973",
      "cliente_nome": "XCMG BRASIL INDUSTRIA LTDA",
      "cliente": "C006973 — XCMG BRASIL INDUSTRIA LTDA",
      "total": 867100,
      "criado": "2026-09-29",
      "nota_espelho": false
    }
  ],
  "vazio": null,
  "acao": {
    "tipo": "processar",
    "verbo": "Processar",
    "botao": "Processar selecionados…",
    "conferir": "/api/pedidos-wbc/processar/conferir",
    "aviso": null
  },
  "execucao_em_andamento": null
}
```

Em `modo=integrados` muda o rótulo do KPI e a ação, que passa a trazer o aviso que a tela mostra
embaixo da lista:

```json
{
  "kpi": { "valor": 93, "rotulo": "Integrados" },
  "pagina": 2, "paginas": 7, "primeiro": 16, "ultimo": 30,
  "acao": {
    "tipo": "reprocessar",
    "verbo": "Reprocessar",
    "botao": "Reprocessar selecionados…",
    "conferir": "/api/pedidos-wbc/reprocessar/conferir",
    "aviso": "Reprocessar cancela todas as OPs planejadas do pedido — de qualquer origem, inclusive as do addon — e não recria: o pedido volta para \"Pedidos novos\" e precisa ser processado de novo."
  }
}
```

| Campo | O que fazer com ele |
| --- | --- |
| `kpi` | O 1º cartão: número grande + rótulo em caixa alta. |
| `pagina`/`paginas` | O 2º cartão ("2/7", rótulo "Página") e a paginação. |
| `primeiro`/`ultimo`/`total` | "16–30 de 93 pedido(s)". |
| `pedidos[]` | As linhas da tabela. `total` é número: formate em reais ([8.5](#85-formatação)). |
| `vazio` | Com a lista vazia: o texto da linha única da tabela ("Nenhum pedido neste modo."). |
| `acao.botao` / `acao.verbo` | O rótulo do botão; com seleção vira "`{verbo}` selecionados (`n`)…". |
| `acao.conferir` | Para onde mandar a seleção. |
| `acao.aviso` | Só em integrados: o aviso amarelo embaixo do botão. |
| `execucao_em_andamento` | Não-nulo = há execução rodando: mostre o aviso com o link ([8.2](#82-tela-1--a-lista)). |

### 7.2 `POST /processar/conferir` e `/reprocessar/conferir`

**Não grava nada.** Relê a lista no SAP, monta o plano e devolve o token.

```http
POST /api/pedidos-wbc/processar/conferir
X-API-Key: SUA_CHAVE
Content-Type: application/json

{ "oportunidades": [14232] }
```

```json
{
  "ok": true,
  "plano": {
    "token": "0qiZJG20DUbqEOCsKkmhcNSti5SQTURI",
    "tipo": "processar",
    "operacao": "Processar pedidos novos",
    "valido_ate": "2026-10-01T14:29:59",
    "resumo": { "pedidos": 1 },
    "resumo_tela": [ { "valor": 1, "rotulo": "pedido(s) selecionado(s)" } ],
    "aviso": {
      "destaque": "Cria Ordens de Produção",
      "texto": ", itens e recursos no SAP, e marca o pedido como processado. Não há desfazer automático."
    },
    "executar": "/api/pedidos-wbc/processar/executar",
    "itens": [
      {
        "pedido": 84445,
        "oportunidade": 14232,
        "wbc": "00124882",
        "cliente_codigo": "C006973",
        "cliente_nome": "XCMG BRASIL INDUSTRIA LTDA",
        "cliente": "C006973 — XCMG BRASIL INDUSTRIA LTDA",
        "total": 867100,
        "total_texto": "867.100,00"
      }
    ]
  }
}
```

No Reprocessar, `aviso.destaque` vem vazio e `aviso.texto` é o aviso completo:

```json
"aviso": {
  "destaque": "",
  "texto": "Para cada pedido: grava uma tabela nova do orçamento (OrcDetalhe), marca o pedido como NÃO processado e zera o U_INO_OP das linhas, revincula a Oportunidade e CANCELA todas as OPs PLANEJADAS do pedido — de qualquer origem, inclusive as do addon. NÃO recria as OPs: o pedido volta para \"Pedidos novos\" e precisa ser processado de novo. OP liberada ou encerrada não é tocada; OP cancelada não volta."
}
```

| Campo | O que fazer com ele |
| --- | --- |
| `operacao` | O título: "Confirmar: `{operacao}`". |
| `aviso` | A faixa vermelha: `destaque` em **negrito**, seguido de `texto` (sem espaço entre os dois — o texto já começa com a vírgula). |
| `resumo_tela` | As linhas do resumo: `valor` em negrito + `rotulo`. |
| `itens` | A tabela: Pedido, Oportunidade, WBC, Cliente, Total (use `total_texto`, já formatado). |
| `valido_ate` | "Esta conferência vale até **2026-10-01 14:29:59**" (troque o `T` por espaço). |
| `token` + `executar` | O que o botão "Confirmar e executar" manda, e para onde. |

Recusas: lista vazia → `400`; número que não é de oportunidade → `400`; pedido que **não está
mais na lista** (processado por outra pessoa enquanto a página estava aberta) → `409
fora_da_lista`, com os números em `fora`. Nesse caso a página deve **recarregar a lista**.

### 7.3 `POST /processar/executar` e `/reprocessar/executar`

**Grava no SAP.** Manda o token da conferência e quem pediu.

```http
POST /api/pedidos-wbc/processar/executar
X-API-Key: SUA_CHAVE
Content-Type: application/json

{ "token": "0qiZJG20DUbqEOCsKkmhcNSti5SQTURI", "solicitante": "Ana Souza" }
```

Resposta **`202`** — a execução **começou**:

```json
{
  "ok": true,
  "execucao": {
    "id": "0776d866c322",
    "nome": "Processar pedidos novos",
    "descricao": "1 pedido(s): 84445",
    "situacao": "na fila",
    "terminada": false,
    "com_falhas": false,
    "desfecho": "fila",
    "passo": "",
    "passos_feitos": 0,
    "passos_total": 0,
    "percentual": null,
    "criada_em": "2026-10-01T14:20:00",
    "duracao_segundos": null,
    "linhas": [],
    "resultado": null,
    "erro": null,
    "solicitante": "Ana Souza",
    "origem": "api",
    "parada_pedida": false,
    "parada_combinada": true,
    "estado": "/api/pedidos-wbc/execucoes/0776d866c322"
  }
}
```

A ordem das checagens protege o token: **sem `solicitante`**, **escrita desligada** (`503`) ou
**módulo ocupado** (`409`) recusam **sem gastar o token** — dá para tentar de novo com o mesmo.
Token vencido, usado ou de outra operação → `409 confirmacao_invalida`: confira de novo.

### 7.4 `GET /execucoes/{id}` — acompanhar

O mesmo estado que a tela de execução consulta a cada 2 segundos. Rodando:

```json
{
  "ok": true,
  "execucao": {
    "id": "0776d866c322",
    "nome": "Processar pedidos novos",
    "descricao": "1 pedido(s): 84445",
    "situacao": "executando",
    "terminada": false,
    "desfecho": "rodando",
    "passo": "Pedido 84445 (WBC 00124882) (1/1)…",
    "passos_feitos": 0,
    "passos_total": 1,
    "percentual": 0,
    "duracao_segundos": 1.2,
    "linhas": [
      "14:20:00  Conectando à Service Layer (SBOALTAMIRAPROD)…",
      "14:20:00  Pedido 84445 (WBC 00124882) (1/1)…",
      "14:20:00  Pedido 00124882: montando a tabela do orçamento...",
      "14:20:01  Pedido 00124882: lendo a estrutura de produto no WBC..."
    ],
    "resultado": null,
    "solicitante": "Ana Souza",
    "origem": "api",
    "parada_combinada": true,
    "estado": "/api/pedidos-wbc/execucoes/0776d866c322"
  }
}
```

Terminada, sem problema:

```json
{
  "situacao": "concluída",
  "terminada": true,
  "com_falhas": false,
  "desfecho": "ok",
  "passo": "Pedido 84445 (WBC 00124882): concluído.",
  "passos_feitos": 1, "passos_total": 1, "percentual": 100,
  "duracao_segundos": 4.9,
  "resultado": {
    "processados": [ { "doc_num": "84445", "orc_num": "00124882" } ],
    "com_erro": [],
    "sem_op": [],
    "sem_rateio": [],
    "pesos_diferentes": []
  }
}
```

Terminada **com falhas** (a execução não quebrou, mas um pedido deu erro):

```json
{
  "situacao": "concluída",
  "terminada": true,
  "com_falhas": true,
  "desfecho": "falhas",
  "passo": "Pedido 84449 (WBC 00125640): ERRO — AddPedidoOportunidade retornou status != 0",
  "linhas": [
    "14:20:05  Conectando à Service Layer (SBOALTAMIRAPROD)…",
    "14:20:05  Pedido 84449 (WBC 00125640) (1/1)…",
    "14:20:05  Orçamento 00125640: lendo os itens no WBC...",
    "14:20:06  ⚠ Pedido 84449 (WBC 00125640): ERRO — AddPedidoOportunidade retornou status != 0"
  ],
  "resultado": {
    "atualizados": [],
    "com_erro": [
      { "orc_num": "00125640", "motivo": "AddPedidoOportunidade retornou status != 0", "doc_num": "84449" }
    ]
  }
}
```

**Leia o `desfecho`, não só a `situacao`.** "concluída" quer dizer que a execução não quebrou;
pedidos com erro aparecem em `resultado.com_erro` e fazem o desfecho virar `falhas`.

| `desfecho` | Quando | Pílula | Barra |
| --- | --- | --- | --- |
| `fila` | Ainda não começou | cinza | acento |
| `rodando` | Executando | laranja (acento) | acento |
| `ok` | Concluída, sem erro | verde | verde |
| `falhas` | Concluída, com pedido em `com_erro` | amarela ("concluída com falhas") | amarela |
| `cancelada` | Interrompida | amarela | vermelha |
| `erro` | A execução quebrou (`erro` traz o motivo) | vermelha | vermelha |

O que pode vir em `resultado`:

| Chave | Operação | O que é |
| --- | --- | --- |
| `processados` | Processar | Pedidos processados inteiros (`doc_num`, `orc_num`). |
| `atualizados` | Reprocessar | Pedidos reprocessados (OPs planejadas canceladas). |
| `com_erro` | as duas | Pedidos que falharam, com `motivo`. |
| `sem_op` | Processar | Grupos do pedido que **não geraram OP**, com `motivo` — o pedido "passou", mas parte dele não produziu nada. |
| `sem_rateio` | Processar | OPs criadas **sem a linha de rateio** (recurso recusado pelo SAP). |
| `nao_iniciados` | as duas | Pedidos que **não começaram** porque alguém interrompeu. |
| `pesos_diferentes` | Processar | Linhas do pedido com o **peso no SAP diferente** da árvore do WBC + 10%, com a **causa** — quem mudou a linha no SAP ([7.4.1](#741-peso-diferente-e-a-causa)). |

**Linhas com `⚠`** são erro ou atenção: pinte-as de vermelho (é o que a tela faz).

#### 7.4.1 Peso diferente e a causa

Ao processar, o servidor confere o peso de cada linha do pedido no SAP contra a regra da
integração: **nível 1 da árvore do WBC + 10%**, para a linha inteira, qualquer que seja a
quantidade. Acima de 1% de diferença, a linha entra em `resultado.pesos_diferentes` — e o
servidor lê o **histórico de alterações do SAP** para dizer quem mudou a linha.

O caso que motivou isto (pedido 84453, 01/10/2026): a integração criou a linha com quantidade 2
e 176,90 kg; depois uma pessoa mudou a quantidade para 1 no SAP, e **o SAP refaz o peso na mesma
proporção** — ficou 88,45 kg. Não é defeito da integração, e a resposta deixa isso explícito:

```json
"pesos_diferentes": [
  {
    "orc_num": "00125348",
    "doc_num": "84453",
    "linha": 0,
    "item": "I000003",
    "quantidade": 1.0,
    "peso_sap": 88.45,
    "peso_esperado": 176.9,
    "arvore_wbc": 160.82,
    "causa": {
      "tipo": "quantidade_mudada_no_sap",
      "usuario": "Adriano Fonseca",
      "momento": "2026-10-01T14:00:06",
      "quantidade_antes": 2.0,
      "quantidade_depois": 1.0,
      "peso_antes": 176.9,
      "peso_depois": 88.45,
      "integracao_gravou_certo": true,
      "texto": "CAUSA: Adriano Fonseca mudou a quantidade da linha 0 de 2 para 1 no SAP em 01/10/2026 às 14:00, e o SAP refez o peso na mesma proporção (176,90 → 88,45 kg). A integração tinha gravado o peso certo (176,90 kg) ao criar o pedido."
    }
  }
]
```

| Campo | O que é |
| --- | --- |
| `linha`, `item`, `quantidade` | A linha do pedido no SAP (`LineNum`), o item e a quantidade **atual**. |
| `peso_sap` | O peso que está no SAP agora. |
| `peso_esperado` | O que a regra dá: `arvore_wbc` × 1,10, 2 casas. |
| `causa` | `null` quando o histórico do SAP não mostra a mudança (ou não pôde ser lido). |
| `causa.tipo` | `quantidade_mudada_no_sap` (alguém mudou a quantidade e o SAP reescalou o peso) ou `peso_mudado_no_sap` (alguém digitou outro peso). |
| `causa.usuario` / `causa.momento` | Quem salvou a mudança no SAP, e quando (hora de Brasília). |
| `causa.*_antes` / `causa.*_depois` | Quantidade e peso antes e depois daquela mudança. |
| `causa.integracao_gravou_certo` | `true` = o pedido nasceu com o peso certo; a diferença veio depois. |
| `causa.texto` | A mesma frase que a tela mostra no acompanhamento — use-a para exibir. |

- **Não é erro**: o processamento segue, o `desfecho` continua `ok`; o pedido fecha com
  "**ATENÇÃO** — N linha(s) com peso diferente da árvore do WBC (veja a CAUSA acima)" no `passo` e
  nas `linhas`, com as linhas `⚠` do aviso e da CAUSA.
- **O peso não é corrigido sozinho.** Para acertar, o TI roda na .11
  `python -m wbcpython pesos --pedido 84453` (com `--simular` antes). Para não acontecer: não mude a
  quantidade da linha no SAP — ou avise que o peso precisa ser refeito.
- **Desde 05/10/2026, mais uma linha depois da CAUSA** diz o que a integração faz com aquele peso
  (a mesma regra do worker, `PLANO_PESO_REESCALADO.md (removido em 2026-10-06; historico no git)`): `⚠ a integração voltaria este peso
  para 176,90 kg, mas a correção automática ainda está em simulação: corrija à mão.` ou
  `⚠ a integração NÃO volta este peso sozinha: <motivo>.` Quando a correção automática for ligada,
  vira `a integração volta este peso para 176,90 kg sozinha.` — só para trocas de quantidade feitas
  dali em diante e salvas nos últimos 3 dias (o worker só olha esses; troca mais velha sai como
  `⚠ … NÃO volta este peso sozinha: a troca tem mais de 3 dias`). Só muda o log; os campos de
  `pesos_diferentes` são os mesmos.

### 7.5 `POST /execucoes/{id}/cancelar` — interromper

```http
POST /api/pedidos-wbc/execucoes/0776d866c322/cancelar
X-API-Key: SUA_CHAVE
Content-Type: application/json

{ "solicitante": "Ana Souza" }
```

```json
{ "ok": true, "cancelada": true, "entre_etapas": true }
```

A execução para **depois do pedido em curso**: ele termina inteiro, os seguintes não começam
(ficam em `resultado.nao_iniciados`). **Nada do que já foi gravado é desfeito.**
`"cancelada": false` = não havia o que interromper (já tinha terminado).

---

## 8. Clonar a tela, peça por peça

### 8.1 O fluxo

```mermaid
flowchart LR
    L["Tela 1 — Lista<br/>GET /pedidos"] -->|"marcar + botão"| C["Tela 2 — Conferência<br/>POST /…/conferir"]
    C -->|"Cancelar"| L
    C -->|"Confirmar e executar<br/>POST /…/executar"| E["Tela 3 — Execução<br/>GET /execucoes/{id} a cada 2 s"]
    E -->|"Voltar para Pedidos WBC"| L
    L -->|"link do aviso de execução em andamento"| E
```

Todas as telas abrem com o mesmo **título de página**: um quadrado de 52 px com o ícone, o
título (h1) e uma linha de subtítulo ([9.4](#94-componentes)).

### 8.2 Tela 1 — a lista

```
┌──────┐ Integração de Pedidos (WBC)
│  🛍  │ Cria itens, recursos de rateio e a cascata de Ordens de Produção
└──────┘
┌─────────────────────────────────────┐ ┏━━━━━━━━━━━━━━━━━━┓ ┏━━━━━━━━━━━━━━━━━━┓
│ (●) Pedidos novos  ( ) Pedidos integ.│ ┃ 1                ┃ ┃ 1/1              ┃
│                                     │ ┃ NOVOS            ┃ ┃ PÁGINA           ┃
└─────────────────────────────────────┘ ┗━━━━━━━━━━━━━━━━━━┛ ┗━━━━━━━━━━━━━━━━━━┛
┌────┬────────┬─────────────┬───────────┬──────────────────────────────┬────────────┬────────────┐
│ ☐  │ PEDIDO │ OPORTUNIDADE│ WBC       │ CLIENTE                      │      TOTAL │ CRIADO     │
├────┼────────┼─────────────┼───────────┼──────────────────────────────┼────────────┼────────────┤
│ ☐  │  84445 │       14232 │ ●00124882 │ C006973 — XCMG BRASIL IND…   │ 867.100,00 │ 2026-09-29 │
└────┴────────┴─────────────┴───────────┴──────────────────────────────┴────────────┴────────────┘
1–1 de 1 pedido(s)
[ ▷ Processar selecionados… ]  Marque ao menos um pedido.
```

**Topo (uma linha, três cartões):** o cartão da escolha do modo (largura automática) e dois
KPIs que dividem o resto. No celular, um embaixo do outro.

- Escolher um modo **já carrega** a lista (não há botão "Buscar"). Enquanto carrega: mostre
  "Carregando…" e **desabilite** os rádios (a leitura no SAP leva uns segundos).
- Ao abrir a página: carregue "Pedidos novos" direto.
- KPI 1: borda esquerda de 4 px na cor do **acento**; número = `kpi.valor`, rótulo = `kpi.rotulo`.
- KPI 2: borda esquerda na cor **info** (`#0dcaf0`); número = "`pagina`/`paginas`", rótulo "Página".
- Os KPIs só aparecem **depois** que a lista carregou.

**Tabela** — colunas e formato:

| Coluna | Campo | Formato |
| --- | --- | --- |
| (caixa) | `oportunidade` | caixa de seleção de 20×20 px, `aria-label` "Selecionar o pedido `{pedido}`" |
| Pedido | `pedido` | centralizado, **negrito**, algarismos alinhados |
| Oportunidade | `oportunidade` | centralizado |
| WBC | `wbc` | pílula laranja (acento) |
| Cliente | `cliente` | texto |
| Total | `total` | à direita, `867.100,00` |
| Criado | `criado` | `2026-09-29`, cor apagada, **sem quebrar** |

Com `nota_espelho: true` (pedido marcado como **Nota Espelho** no SAP, campo `U_U_INO_NotaEspelho`),
a tela põe embaixo do número do pedido uma pílula amarela (aviso) escrita "Nota espelho". Pedido e
Oportunidade ficam centralizados (cabeçalho e valor).

- Lista vazia: uma linha só, ocupando as 7 colunas, com o texto de `vazio` em cor apagada.
- Linhas pares com fundo levemente mais escuro; passar o mouse destaca a linha.
- A tabela **rola dentro do cartão** quando não cabe — a página nunca rola para o lado.

**Paginação** (só com `total` > 0): "**1–15** de **93** pedido(s)". Com mais de uma página:
"‹ anterior" (se `pagina` > 1), "página **1** de **7**", "próxima ›" (se `pagina` < `paginas`) e
a nota "A seleção vale só para esta página — mudar de página a perde."

**Botão de ação:**

| Estado | Rótulo | Habilitado | Dica ao lado |
| --- | --- | --- | --- |
| Nada marcado | `acao.botao` ("Processar selecionados…") | não | "Marque ao menos um pedido." |
| `n` marcados | "Processar selecionados (`n`)…" | sim | (some) |
| Lista vazia | `acao.botao` | não | "Nenhum pedido para processar." |

- Cor **vermelha** (botão de perigo), ícone ▷ (play) em Processar e ⚡ (raio) em Reprocessar.
- As reticências "…" são de propósito: o botão leva à conferência, não executa.
- Clique → `POST acao.conferir` com `{"oportunidades": [...]}` → Tela 2.
- Em integrados, embaixo do botão: o `acao.aviso` numa faixa **amarela** com ícone ⚠. A tela
  original põe em negrito "**Reprocessar**", "**planejadas**" e "**não recria**".

**Execução em andamento** (`execucao_em_andamento` não-nulo), acima da tabela, faixa laranja com
ícone de relógio: "Há uma execução em andamento neste módulo: `nome` (um link que abre a Tela 3 dessa execução). Uma
nova só é aceita depois que ela terminar — duas ao mesmo tempo disputariam os mesmos pedidos e OPs."

**A lista não carregou** (`502`): faixa amarela "Não foi possível carregar os pedidos agora —
recarregue a página (F5) para tentar de novo." — a página abre mesmo assim.

**Recusa na conferência** (`409 fora_da_lista`, etc.): recarregue a lista e mostre o `motivo`
numa faixa vermelha.

### 8.3 Tela 2 — a conferência

```
┌──────┐ Confirmar: Processar pedidos novos
│  🛡  │ Nada foi gravado ainda — confira e confirme
└──────┘   (ícone de escudo, quadrado VERMELHO)
┌─ cartão com borda vermelha ─────────────────────────────────────────────┐
│ ┌ faixa vermelha ─────────────────────────────────────────────────────┐ │
│ │ ⚠ **Cria Ordens de Produção**, itens e recursos no SAP, e marca …   │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│ **1** pedido(s) selecionado(s)                                          │
│ O que será feito, exatamente — confira antes de confirmar:              │
│ ┌ PEDIDO │ OPORTUNIDADE │ WBC │ CLIENTE │ TOTAL ┐                        │
│ └ 84445  │ 14232        │ 00124882 │ C006973 — XCMG … │ 867.100,00 ┘     │
│ Esta conferência vale até **2026-10-01 14:29:59**. Depois disso é        │
│ preciso refazê-la — o que seria feito é recalculado sobre o estado      │
│ atual do SAP.                                                           │
│ [ ▷ Confirmar e executar ] (verde)   [ Cancelar ] (vermelho)            │
└─────────────────────────────────────────────────────────────────────────┘
```

- Título "Confirmar: `{operacao}`"; subtítulo fixo "Nada foi gravado ainda — confira e confirme";
  ícone **escudo** no quadrado na variante **perigo** (vermelha).
- Faixa vermelha: ícone ⚠ + `aviso.destaque` em negrito + `aviso.texto`.
- Resumo: cada item de `resumo_tela` numa linha — `valor` em negrito maior + `rotulo`.
- Tabela: colunas Pedido, Oportunidade, WBC, Cliente, Total — aqui **todas à esquerda** e o Total
  já vem pronto em `total_texto`.
- **Confirmar e executar** (verde, ícone ▷): `POST plano.executar` com `token` e `solicitante`.
  **Um clique só** — desabilite o botão ao clicar (o token vale uma vez; um duplo clique mostraria
  "confirmação já utilizada" com a execução rodando).
- **Cancelar** (vermelho): volta para a lista sem chamar nada.
- `409 ocupado`: mostre o `motivo` e um link "Acompanhar a execução em andamento" para
  `execucao_em_andamento`.

### 8.4 Tela 3 — a execução

```
┌──────┐ Processar pedidos novos                       [ ← Voltar para Pedidos WBC ]
│  🕑  │ 1 pedido(s): 84445 · por Ana Souza · API
└──────┘
┌─────────────────────────────────────────────────────────────────────────┐
│ (● CONCLUÍDA) — 1/1 — 4.9 s · 01/10/2026 14:20:00                        │
│ ████████████████████████████████████████████████████████  (barra verde)  │
│ **Pedido 84445 (WBC 00124882): concluído.**                              │
│ ┌ log (fonte monoespaçada) ───────────────────────────────────────────┐ │
│ │ 14:20:00  Conectando à Service Layer (SBOALTAMIRAPROD)…             │ │
│ │ 14:20:05  Pedido 84445 (WBC 00124882): concluído.                   │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│ [ ■ Interromper ]  Para depois do pedido em curso — …                   │
│ ■ RESULTADO                                                             │
│ { "processados": [ … ] }                                                │
└─────────────────────────────────────────────────────────────────────────┘
```

- Título = `nome`; subtítulo = `descricao` + (se `origem` = `api`) " · por `solicitante` · API".
- Botão "← Voltar para Pedidos WBC" (sólido, cor do acento) alinhado à direita do título.
- Linha de estado: pílula (texto = `situacao`, ou "`situacao` com falhas" se `desfecho` =
  `falhas`; cor pela tabela da [7.4](#74-get-execucoesid--acompanhar)), " — `passos_feitos`/`passos_total`"
  (se `passos_total` > 0), " — `duracao_segundos` s" (se houver) e "· `criada_em`" em
  `dd/mm/aaaa hh:mm:ss`.
- Barra de 6 px: largura = `percentual` % (0 se nulo); cor pelo `desfecho`.
- `passo` em negrito embaixo da barra.
- Log: as `linhas`, uma por linha; as que contêm `⚠` em **vermelho e negrito**. Mantenha a
  rolagem **colada no fim** — mas se a pessoa rolou para cima para ler, não puxe de volta.
- `erro` não-nulo: faixa vermelha com o texto.
- **Interromper** (botão fantasma, ícone ■) + nota: "Para depois do pedido em curso — ele
  termina inteiro; os próximos não começam. **Não desfaz** o que já foi gravado no SAP."
  Depois do clique, mostre: "Interrupção pedida — o que está em curso termina inteiro e o próximo
  não começa. O que já foi gravado no SAP fica." (ou "Não havia execução em andamento para
  interromper." se `cancelada` = false).
- Terminada: esconda o Interromper; se houver `resultado`, mostre a seção "RESULTADO" com o JSON
  formatado (2 espaços) no mesmo estilo do log.
- **Consulta a cada 2 s** até `terminada: true`. `404`: pare e mostre o `motivo`. `503`: mostre
  o `motivo` + "Nova tentativa em 15 s." e tente em 15 s. Falha de rede: tente de novo em 5 s —
  **a execução continua no servidor** mesmo que a página perca a conexão.

### 8.5 Formatação

| Valor | Da API | Na tela | JavaScript |
| --- | --- | --- | --- |
| Total (lista) | `867100` | `867.100,00` | `v.toLocaleString("pt-BR", {minimumFractionDigits: 2, maximumFractionDigits: 2})` |
| Total (conferência) | `total_texto` | `867.100,00` | já vem pronto |
| Criado | `"2026-09-29"` | `2026-09-29` | como veio |
| Validade | `"2026-10-01T14:29:59"` | `2026-10-01 14:29:59` | `s.replace("T", " ")` |
| Início da execução | `"2026-10-01T14:20:00"` | `01/10/2026 14:20:00` | separar data e hora e inverter a data |
| Duração | `4.9` | `4.9 s` | como veio |

**Todo texto que vem da API é texto, não HTML.** Nome de cliente vem do SAP e pode ter `<`, `&`:
use `textContent` (ou o equivalente do seu framework), nunca `innerHTML` com valor da API.

---

## 9. Tema: cores, letras, medidas e ícones

A tela segue o padrão visual da "Central Integração SAP" (`casa/static/casa.css` +
`controleproducao/static/style.css`). **Escuro é o padrão**; claro é a alternativa.

### 9.1 Cores

| Token | Uso | Escuro (padrão) | Claro |
| --- | --- | --- | --- |
| `--pagina` | Fundo da página | `#1a1a1a` | `#eef1f5` |
| `--painel` | Fundo do log | `#242424` | `#ffffff` |
| `--cartao` | Cartões, tabela, botão fantasma | `#2d2d2d` | `#ffffff` |
| `--superficie` | Cabeçalho da tabela, fundo da barra | `#3a3a3a` | `#ebeff5` |
| `--borda` | Bordas de cartão e réguas | `#404040` | `#cbd5e1` |
| `--borda-forte` | Contorno de campo de texto | `#9ca3af` | `#64748b` |
| `--texto` | Texto principal | `#ffffff` | `#1f2733` |
| `--texto-2` | Texto apagado, rótulos | `#9ca3af` | `#5a6675` |
| `--acento` | O coral da casa: preenchimento e borda | `#da7756` | `#c45a3a` |
| `--acento-texto` | O coral como **texto/ícone** (contraste) | `#e8956e` | `#a94a2e` |
| `--perigo` | Ícone do escudo da conferência | `#f2726a` | `#b42318` |
| Linha `⚠` do log | | `#ff8a80` | `#b42318` |

Cores semânticas, **iguais nos dois temas**: sucesso `#28a745` (botão Confirmar, pílula/barra
"ok"), erro `#dc3545` (botão de ação da lista, Cancelar, faixa vermelha, pílula "erro"), aviso
`#ffc107` (faixa amarela, "com falhas"), info `#0dcaf0` (borda do KPI "Página").

Transparências do acento (fundo do quadrado do título, destaque da linha): 8 %, 13 %, 20 % e
40 % do `--acento` (`rgb(218 119 86 / .13)` no escuro, `rgb(196 90 58 / .12)` no claro).

**Uma cor de acento por tela**, com parcimônia — nunca fundo de área grande. O que grava no SAP
usa o **vermelho semântico**, não um segundo acento.

### 9.2 Letras

- Família: `"Inter", "Segoe UI", system-ui, -apple-system, Roboto, "Helvetica Neue", Arial, sans-serif`.
- Log e JSON: `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`.
- Tamanhos: texto `0.9375rem`; h1 do título `1.875rem` peso 700 (`1.5rem` no celular);
  subtítulo `0.875rem`; número do KPI `1.875rem` peso 700; rótulos em caixa alta `0.75rem` com
  espaçamento `.06em`–`.07em`; notas `0.8125rem`; nota ao lado do botão `0.75rem`.
- Números em colunas: `font-variant-numeric: tabular-nums`.
- A página inteira é ampliada **12,5 %** (`zoom: 1.125` no contêiner, máximo de 1600 px); no
  celular (≤ 760 px) fica em `zoom: .9`.

### 9.3 Medidas

| | |
| --- | --- |
| Raio de cartão | 14 px |
| Raio de item (KPI dentro de lista, faixa, botão, log) | 10 px |
| Raio de campo | 9 px |
| Pílula | 999 px |
| Espaçamento base | 4 · 8 · 12 · 16 · 20 · 24 · 32 · 48 px |
| Sombra de cartão (escuro) | `0 4px 10px rgb(0 0 0 / .30), 0 1px 3px rgb(0 0 0 / .22)` |
| Sombra de cartão (claro) | `0 1px 2px rgb(0 0 0 / .05), 0 6px 20px rgb(0 0 0 / .05)` |
| Foco do teclado | contorno 2 px `--acento-texto`, afastado 2 px |

### 9.4 Componentes

- **Título de página:** quadrado 52×52 px, raio 14, fundo acento 13 %, contorno interno acento
  40 %, ícone 26 px na cor `--acento-texto`. Variante perigo (conferência): fundo/contorno/ícone
  em `--perigo`.
- **Cartão:** fundo `--cartao`, borda 1 px `--borda`, raio 14, sombra; padding 20×22 px.
- **KPI:** cartão com borda esquerda de 4 px colorida; número grande + rótulo em caixa alta.
- **Tabela:** cabeçalho em `--superficie`, caixa alta 0.75rem `.07em`, cor apagada; células com
  padding 11×14 px; régua 1 px; linhas pares 45 % mais escuras; hover = acento 8 %.
- **Pílula:** caixa alta 0.75rem peso 600, raio 999, com uma bolinha de 6 px antes; texto na cor,
  fundo = 14 % da cor, borda = 40 % da cor.
- **Botão sólido** (o "relevo" da casa): gradiente vertical de **três paradas** — cor clareada
  76 % com branco (0 %), a cor (40 %), cor escurecida 80 % com preto (100 %) —, sem borda, texto
  branco com sombra `0 1px 2px rgb(0 0 0 / .30)`, sombra `0 3px 8px` da própria cor a 35 % +
  brilho interno `inset 0 1px 0 rgb(255 255 255 / .25)`. Hover sobe 2 px (só com mouse);
  clique desce 1 px; desabilitado = 55 % de opacidade, sem sombra.
- **Botão fantasma** (Interromper): fundo `--cartao`, borda `--borda`, hover com texto e borda
  no acento.
- **Faixa de aviso:** ícone 16 px + texto; fundo 12 % e borda 35 % da cor (vermelho, amarelo ou
  acento); o texto fica na cor normal, só o ícone e a borda levam a cor.
- **Barra de progresso:** trilho de 6 px em `--superficie`; preenchimento animado (`.35s`).
- **Log:** fundo `--painel`, borda, raio 10, padding 14×16, altura máxima 24rem com rolagem,
  `white-space: pre-wrap`, entrelinha 1.65.

> Navegadores antigos (Chrome/Edge < 111) não conhecem `color-mix()`: sem um plano B, os botões
> saem **sem fundo**. A página pronta tem o bloco `@supports not (color: color-mix(...))` que
> resolve isso — copie-o se usar `color-mix`.

### 9.5 Ícones

SVG inline, traço de 2 px, pontas arredondadas, `fill="none"`, `stroke="currentColor"`,
`viewBox="0 0 24 24"` — herdam a cor do contexto. Sem biblioteca e sem CDN.

| Nome | Onde | Desenho (`<path>`) |
| --- | --- | --- |
| pedidos | Título da lista | `M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z` · `M3 6h18` · `M16 10a4 4 0 0 1-8 0` |
| escudo | Título da conferência | `M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z` |
| execucoes | Título da execução, aviso de execução em andamento | `<circle cx="12" cy="12" r="9"/>` · `M12 7v5l3 2` |
| alerta | Faixas de aviso | `M12 9v4M12 17h.01` · `M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z` |
| play | Processar, Confirmar | `m5 3 14 9-14 9V3z` |
| raio | Reprocessar | `M13 2 3 14h9l-1 8 10-12h-9l1-8z` |
| parar | Interromper | `<rect x="5" y="5" width="14" height="14" rx="2"/>` |
| voltar | Voltar | `M19 12H5M12 19l-7-7 7-7` |

---

## 10. Quando dá errado: todos os erros

Todo erro tem o mesmo formato:

```json
{ "ok": false, "tipo": "fora_da_lista", "motivo": "Estes pedidos não estão mais na lista elegível e foram recusados: 99999. Refaça a busca — o estado no SAP mudou desde que a tela foi carregada.", "fora": ["99999"] }
```

**Decida pelo `tipo`; mostre o `motivo`** — ele é a frase da tela, escrita para a pessoa.

| HTTP | `tipo` | Quando | O que fazer |
| --- | --- | --- | --- |
| 400 | `invalido` | Corpo não é JSON; lista vazia ("Nenhum pedido selecionado."); número inválido; `modo`/`pagina` inválidos; sem `token`; sem `solicitante`; `force` enviado | Corrigir a chamada |
| 401 | `sem_chave` | Sem `X-API-Key` ou chave errada | Pedir a chave de novo |
| 403 | `sem_permissao` | A chave é válida, mas não tem o escopo desta API (desde 02/10/2026 cada cliente tem a sua chave) | Pedir ao TI a chave certa |
| 403 | `agente_bloqueado` | Chave de **agente**: interruptor desligado, ou escrita fora do expediente (seg–sex 7h–19h) | Mostrar o `motivo`; tentar no expediente |
| 404 | `nao_encontrada` | Execução que não existe (ou saiu do histórico das 30 mais recentes); rota errada | Parar de consultar |
| 405 | `metodo_invalido` | GET onde é POST (ou o contrário) | Corrigir a chamada |
| 409 | `fora_da_lista` | Pedido conferido não está mais na lista (alguém processou) | Recarregar a lista |
| 409 | `confirmacao_invalida` | Token vencido (10 min), já usado ou de outra operação | Conferir de novo |
| 409 | `ocupado` | Já há uma execução no módulo (pela tela ou pela API) | Acompanhar a de `execucao_em_andamento`; não tentar em laço |
| 502 | `sap_indisponivel` | O SAP não respondeu à leitura | Tentar de novo em instantes |
| 503 | `escrita_desabilitada` | Escrita desligada no servidor | Avisar o TI |
| 503 | `historico_indisponivel` | O histórico de execuções não respondeu | Tentar de novo em 15 s |

As mensagens de token:

| Caso | `motivo` |
| --- | --- |
| Vencido | "A confirmação venceu. O plano foi calculado sobre o estado do SAP de alguns minutos atrás e pode não valer mais; refaça a conferência." |
| Usado ou inexistente | "Confirmação inválida ou já utilizada. Refaça a conferência antes de executar — o que seria feito precisa ser recalculado." |
| De outra operação | "Esta confirmação é de outra operação e não vale aqui. Refaça a conferência da operação que quer executar." |

---

## 11. Exemplos completos de código

Os três fazem o mesmo: listam os pedidos novos, conferem um, **mostram o plano e pedem
confirmação a uma pessoa**, executam e acompanham até o fim.

### JavaScript (navegador ou Node 18+)

No Node, salve como `.mjs` (usa `await` fora de função).

```javascript
const API = "http://192.168.7.11:8080/api/pedidos-wbc";
const CHAVE = process.env.SIS_API_KEY;        // no navegador: peça à pessoa, não escreva no código
const SOLICITANTE = "Ana Souza";

async function chama(metodo, caminho, corpo) {
  const r = await fetch(caminho.startsWith("/api/") ? "http://192.168.7.11:8080" + caminho : API + caminho, {
    method: metodo,
    headers: { "X-API-Key": CHAVE, ...(corpo ? { "Content-Type": "application/json" } : {}) },
    body: corpo ? JSON.stringify(corpo) : undefined,
  });
  const dados = await r.json();
  if (!dados.ok) throw Object.assign(new Error(dados.motivo), { tipo: dados.tipo, dados });
  return dados;
}

const lista = await chama("GET", "/pedidos?modo=novos");
console.log(`${lista.kpi.valor} ${lista.kpi.rotulo}`);
const alvo = lista.pedidos[0];

const { plano } = await chama("POST", "/processar/conferir", { oportunidades: [alvo.oportunidade] });
console.log(`Confirmar: ${plano.operacao}\n${plano.aviso.destaque}${plano.aviso.texto}`);
for (const i of plano.itens) console.log(`  ${i.pedido}  ${i.wbc}  ${i.cliente}  ${i.total_texto}`);
// >>> Aqui uma PESSOA confirma. Sem confirmação, não chame o executar. <<<

const { execucao } = await chama("POST", plano.executar, { token: plano.token, solicitante: SOLICITANTE });
let estado = execucao;
while (!estado.terminada) {
  await new Promise((ok) => setTimeout(ok, 2000));
  estado = (await chama("GET", `/execucoes/${execucao.id}`)).execucao;
  console.log(estado.passo);
}
console.log(`Desfecho: ${estado.desfecho}`, estado.resultado);
```

### Python

```python
import os
import time

import requests  # pip install requests

BASE = "http://192.168.7.11:8080"
API = f"{BASE}/api/pedidos-wbc"
CABECALHO = {"X-API-Key": os.environ["SIS_API_KEY"]}
SOLICITANTE = "Ana Souza"


def chama(metodo: str, caminho: str, corpo: dict | None = None) -> dict:
    url = BASE + caminho if caminho.startswith("/api/") else API + caminho
    r = requests.request(metodo, url, headers=CABECALHO, json=corpo, timeout=60)
    dados = r.json()
    if not dados.get("ok"):
        raise RuntimeError(f"{r.status_code} {dados['tipo']}: {dados['motivo']}")
    return dados


lista = chama("GET", "/pedidos?modo=novos")
print(lista["kpi"]["valor"], lista["kpi"]["rotulo"])
alvo = lista["pedidos"][0]

plano = chama("POST", "/processar/conferir", {"oportunidades": [alvo["oportunidade"]]})["plano"]
print(f"Confirmar: {plano['operacao']}")
print(plano["aviso"]["destaque"] + plano["aviso"]["texto"])
for i in plano["itens"]:
    print(f"  {i['pedido']}  {i['wbc']}  {i['cliente']}  {i['total_texto']}")
if input("Digite SIM para executar: ").strip() != "SIM":
    raise SystemExit("Nada foi gravado.")

execucao = chama("POST", plano["executar"], {"token": plano["token"], "solicitante": SOLICITANTE})["execucao"]
while not execucao["terminada"]:
    time.sleep(2)
    execucao = chama("GET", f"/execucoes/{execucao['id']}")["execucao"]
    print(execucao["passo"])
print("Desfecho:", execucao["desfecho"], execucao["resultado"])
```

### PowerShell (Windows PowerShell 5.1)

```powershell
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Base = "http://192.168.7.11:8080"
$Api = "$Base/api/pedidos-wbc"
$Cab = @{ "X-API-Key" = $env:SIS_API_KEY }
$Solicitante = "Ana Souza"

function Chama($Metodo, $Url, $Corpo) {
    $p = @{ Method = $Metodo; Uri = $Url; Headers = $Cab }   # nao use $args: e variavel automatica
    if ($Corpo) {
        # PowerShell 5.1 manda o corpo em ANSI: converta para UTF-8 (nomes com acento)
        $p.Body = [Text.Encoding]::UTF8.GetBytes(($Corpo | ConvertTo-Json -Depth 5))
        $p.ContentType = "application/json; charset=utf-8"
    }
    try { Invoke-RestMethod @p }
    catch { $e = $_.ErrorDetails.Message | ConvertFrom-Json; throw "$($e.tipo): $($e.motivo)" }
}

$lista = Chama GET "$Api/pedidos?modo=novos"
"$($lista.kpi.valor) $($lista.kpi.rotulo)"
$alvo = $lista.pedidos[0]

$plano = (Chama POST "$Api/processar/conferir" @{ oportunidades = @($alvo.oportunidade) }).plano
"Confirmar: $($plano.operacao)"
"$($plano.aviso.destaque)$($plano.aviso.texto)"
$plano.itens | Format-Table pedido, wbc, cliente, total_texto
if ((Read-Host "Digite SIM para executar") -ne "SIM") { "Nada foi gravado."; return }

$ex = (Chama POST "$Base$($plano.executar)" @{ token = $plano.token; solicitante = $Solicitante }).execucao
while (-not $ex.terminada) {
    Start-Sleep -Seconds 2
    $ex = (Chama GET "$Api/execucoes/$($ex.id)").execucao
    $ex.passo
}
"Desfecho: $($ex.desfecho)"
```

---

## 12. Como testar sem estragar nada

**Não existe ambiente de teste: esta API grava no SAP de produção.** Mas quase tudo dá para
testar sem gravar.

**Pode chamar à vontade** — nada é gravado:

| Chamada | Para testar |
| --- | --- |
| `GET /health` | Se você alcança o servidor |
| `GET /pedidos` (os dois modos, várias páginas) | A chave, a lista, a paginação, o desenho da tabela |
| `POST /processar/conferir` e `/reprocessar/conferir` | A conferência inteira. O token vence sozinho em 10 minutos |
| `GET /execucoes/{id}` | O acompanhamento, com o `id` de uma execução antiga (ou um inventado → `404`) |
| `POST /processar/executar` **sem `solicitante`** | O tratamento de erro (`400`) — recusa antes de gravar, e o token continua valendo |
| `POST /processar/conferir` com `{"oportunidades": [99999]}` | A recusa `409 fora_da_lista` |

**Executar** (Processar ou Reprocessar): só num pedido **combinado antes com o PCP**. Processar
de verdade cria OPs; Reprocessar de verdade cancela OPs. Nenhum dos dois é "para testar".

A [página pronta](#3-o-caminho-mais-curto-a-página-pronta) é um bom jeito de ver tudo funcionando
antes de escrever a sua: abra, navegue, confira — só não confirme.

Tudo o que você grava aparece na tela **Execuções** do Controle de Produção, com o seu
`solicitante`.

---

## 13. Lista de verificação antes de pôr no ar

Passe por cada item antes de liberar a página para as pessoas usarem.

**Segurança**

- [ ] A chave **não** está no código-fonte nem na URL (variável de ambiente, cofre, ou pedida à pessoa e guardada só na aba).
- [ ] Todo texto que vem da API é exibido como **texto** (`textContent`), nunca como HTML.
- [ ] O `solicitante` é o **nome da pessoa** que está operando, não o nome do sistema.

**Comportamento igual ao da tela**

- [ ] Escolher o modo já carrega a lista; os rádios ficam travados enquanto carrega.
- [ ] O botão começa desabilitado e mostra "(n)" com a quantidade marcada.
- [ ] Em "Pedidos integrados", o aviso amarelo do Reprocessar aparece **antes** de qualquer clique.
- [ ] A conferência mostra o **aviso vermelho inteiro** (`aviso.destaque` + `aviso.texto`) e a validade.
- [ ] "Confirmar e executar" aceita **um clique só**.
- [ ] O acompanhamento consulta a cada 2 s e **para** em `terminada: true`.
- [ ] A pílula segue o **`desfecho`** (não só a `situacao`): `falhas` aparece amarelo.
- [ ] Linhas com `⚠` aparecem em vermelho; o `resultado` aparece no fim.

**Quando dá errado**

- [ ] `401` → pede a chave de novo.
- [ ] `409 fora_da_lista` → recarrega a lista e mostra o motivo.
- [ ] `409 ocupado` → mostra o link para a execução em andamento (não tenta em laço).
- [ ] `409 confirmacao_invalida` → volta para a lista para conferir de novo.
- [ ] `502`/`503` → mostra o `motivo` e deixa tentar de novo.
- [ ] Falha de rede durante o acompanhamento → continua tentando (a execução segue no servidor).

**Teste**

- [ ] Lista e conferência comparadas com a tela original, nos dois modos ([12](#12-como-testar-sem-estragar-nada)).
- [ ] Primeira execução real **num pedido combinado com o PCP**, acompanhada até o fim.

---

## 14. Boas práticas

1. **Conferir → mostrar o plano a uma pessoa → executar.** O plano existe para alguém ler.
   Executar sem mostrar é jogar fora a proteção.
2. **Mostre o `aviso` da conferência inteiro**, em vermelho. É ele que diz o que não tem volta.
3. **Use os textos que vêm da API** (`acao.botao`, `kpi.rotulo`, `aviso`, `motivo`, `vazio`) em
   vez de escrever os seus: quando a tela muda uma frase, o seu clone muda junto.
4. **Leia o `desfecho`**, não só a `situacao`: "concluída" com pedido em `com_erro` é `falhas`.
5. **Consulte o estado a cada 2 segundos** e pare em `terminada: true`.
6. **`409 ocupado`: acompanhe a execução que veio na resposta**, em vez de tentar em laço.
7. **`409 fora_da_lista`: recarregue a lista.** Alguém processou o pedido enquanto a página
   estava aberta.
8. **`solicitante` é a pessoa**, não o sistema ("Ana Souza", não "SistemaX").
9. **Um clique só no Confirmar**: desabilite o botão ao clicar.
10. **A chave é poderosa — guarde-a bem.** Ela é a mesma que abre as telas da .11 e grava no SAP.
    O ideal é chamar a API **pelo seu servidor** (a chave fica nele, a página fala só com ele).
    Se a página chamar direto do navegador, **não escreva a chave no código**: peça à pessoa e
    guarde só na aba (`sessionStorage`), como a página pronta faz.
11. **Texto da API é texto**: nunca `innerHTML` com valor que veio dela.

---

## 15. Perguntas frequentes

**Posso testar sem gravar nada?**
Sim — veja a [seção 12](#12-como-testar-sem-estragar-nada). Listar, conferir e acompanhar não gravam.

**Mandei executar e a resposta não trouxe o resultado. Deu certo?**
Ainda não se sabe: `202` quer dizer que a execução **começou**. Acompanhe pelo `estado` até
`terminada: true` e leia o `desfecho`.

**Por que a seleção é pela `oportunidade` e não pelo `pedido`?**
Porque é assim na tela (é o valor da caixa de seleção) e é a chave com que o servidor relê a
lista na conferência. O `pedido` é o que você **mostra**.

**Posso mandar vários pedidos de uma vez?**
Sim — `"oportunidades": [14232, 15289, …]` (só os da página que a pessoa está vendo, como na
tela). Eles rodam **um de cada vez**, em ordem; o acompanhamento diz em qual está.

**Mandei 5 pedidos e um não está mais na lista. Os outros 4 foram?**
Não. A conferência recusa **o lote inteiro** (`409 fora_da_lista`) dizendo quais saíram.
Recarregue a lista e confira de novo.

**Um pedido deu erro no meio da execução. Os outros pararam?**
Não. Cada pedido é independente: o que falhou vai para `resultado.com_erro` e a execução segue
para o próximo. O desfecho final fica `falhas`.

**Interrompi. O que foi feito é desfeito?**
Não. O pedido em curso termina inteiro; os seguintes ficam em `resultado.nao_iniciados`.

**Por que não tem "forçar"?**
Porque ele ignora as checagens que impedem OP duplicada. Se for mesmo preciso, é pela tela do
Controle de Produção, por quem conhece o caso.

**O processamento avisou "peso diferente". A integração errou?**
Leia `causa` em `resultado.pesos_diferentes` (ou a linha "CAUSA:" do acompanhamento). Nos casos
vistos até hoje, a integração gravou o peso certo e alguém **mudou a quantidade da linha no SAP**
depois — o SAP refaz o peso na mesma proporção. Veja a [7.4.1](#741-peso-diferente-e-a-causa).

**Reprocessei e as OPs não voltaram.**
É o comportamento: Reprocessar **cancela** as OPs planejadas e devolve o pedido a "Pedidos
novos". Para criar as OPs de novo, **processe** o pedido em "Pedidos novos".

**Por que recebi `409 ocupado` se não estou rodando nada?**
Alguém está — pela tela ou por outro sistema. A resposta traz quem (`solicitante`, `origem`) e o
`estado` para acompanhar.

**O token venceu. E agora?**
Confira de novo. O plano é recalculado sobre o estado atual do SAP — que pode ter mudado.

**A tela e a minha página podem ficar diferentes?**
Nas regras e nos textos que vêm da API, não: são as mesmas funções. No que é só desenho
(cores, posições), sim — por isso a [seção 9](#9-tema-cores-letras-medidas-e-ícones) e a página
pronta.

**Onde vejo o que já foi feito?**
Na tela **Execuções** do Controle de Produção (as 30 mais recentes, com quem pediu e por onde),
ou em `GET /execucoes/{id}`.

---

## 16. Suporte e histórico deste documento

- **O servidor está no ar?** `GET http://192.168.7.11:8080/health` (sem chave).
- **Chave, dúvida, campo faltando, `502`/`503`:** falar com o Marcelo (TI).
- **Uma mensagem não está clara** para mostrar ao usuário final: avise. Ela é a mesma da tela e
  melhora nos dois lugares ao mesmo tempo.
- **Código:** `controleproducao/modules/pedidos_wbc/api_router.py` (a API) e `acoes.py` (as
  regras, compartilhadas com a tela). Plano: `PLANO_API_PEDIDOS_WBC.md (removido em 2026-10-06; historico no git)`.

Os exemplos de resposta deste documento — fora a [seção 5](#5-caso-real-o-pedido-84460-do-começo-ao-fim),
que usa respostas reais da .11 — saíram da própria API, com o SAP simulado e dados
fictícios; só os nomes de pessoa e o servidor de exemplo foram ajustados. No SAP de verdade, as
`linhas` trazem mais passos (cada OP, item e recurso criado).

| Data | O que mudou |
| --- | --- |
| 01/10/2026 | Primeira versão: listar, conferir, executar (Processar e Reprocessar, sem "forçar"), acompanhar e interromper; página pronta `docs/exemplos/pedidos_wbc_clone.html` |
| 01/10/2026 | `resultado.pesos_diferentes`: linha com peso diferente da árvore do WBC, com a **causa** lida do histórico do SAP (quem mudou, quando, de quanto para quanto) — [7.4.1](#741-peso-diferente-e-a-causa) |
| 01/10/2026 | Guia mais didático: "A ideia em um minuto" (caminho do pedido e a conversa com a API em desenho), tutorial em 5 etapas com pontos de conferência, glossário, a vida de uma execução e lista de verificação antes de pôr no ar |
| 05/10/2026 | Campo novo na lista: `nota_espelho` (`true` = pedido marcado como Nota Espelho no SAP); a tela o mostra como pílula "Nota espelho" sob o número do pedido. Pedido e Oportunidade passaram a centralizados |
| 05/10/2026 | `execucao.descricao`: o pedido com Nota Espelho sai como `84454 (nota espelho)` (ex.: `2 pedido(s): 84201, 84454 (nota espelho)`); as telas Execuções mostram a pílula "Nota espelho" |
| 05/10/2026 | Linha nova no acompanhamento do Processar, depois da CAUSA: o que a integração faz com o peso reescalado — [7.4.1](#741-peso-diferente-e-a-causa). Os campos não mudaram |
| 06/10/2026 | Seção nova [5. Caso real: o pedido 84460, do começo ao fim](#5-caso-real-o-pedido-84460-do-começo-ao-fim): o mapa de todos os objetos da tela (lista, conferência, execução) com o texto exato e de onde vem, as respostas reais da lista e da conferência, o log real de um Processar e de um Reprocessar explicado linha por linha, um erro real e o catálogo de todas as mensagens. As seções seguintes foram renumeradas (6 a 16) |
