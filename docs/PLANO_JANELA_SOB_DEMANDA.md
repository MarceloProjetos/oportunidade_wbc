# PLANO — Janela de busca sob demanda (Integração WBC)

> **Status em 2026-09-11:** **no ar na .11** — worker e painel com o código novo, conferido
> pelo log do ciclo #742 e pelas rotas do painel. 1.098 testes verdes.
>
> Uma correção depois do primeiro deploy: o card subiu **sem o formulário de armar**, porque
> estava amarrado ao `painel_pode_escrever`, falso em produção por desenho. Armar virou
> exceção a esse bloqueio (só a senha guarda), com o ok do Marcelo. **Pende um segundo
> `git pull` + restart**, e confirmar que `PAINEL_SENHA` está preenchida no `.env` da .11 —
> sem ela o card continua travado, agora dizendo isso na tela.

Tirar `MESES_DE_JANELA` de "constante de `.env` que só muda com restart" e transformá-la
num **pedido pontual, feito pela tela do painel**, que se gasta sozinho e volta ao padrão.
Quem é de vendas precisa alcançar uma oportunidade antiga sem chamar o TI.

---

## Números

| | |
|---|---|
| Padrão permanente | **6 meses** (`MESES_DE_JANELA` no `.env`, que continua existindo) |
| Teto pedível pela tela | **24 meses** |
| Teto de escrita hoje | **200** por ciclo (`LIMITE_DE_ESCRITA_POR_CICLO`) |
| Teto de escrita na janela máxima | **1.800** por ciclo (9×) |
| Oportunidades na janela de 6 meses | **1.785** (medido em ago/2026) |
| Intervalo entre ciclos | **180 s**, 07:00–20:00, seg–sex |
| Processos envolvidos | **2** — `OrcaView-WBC-Painel` e `OrcaView-WBC-Worker` |

---

## Onde está agora

Hoje a janela é lida uma vez, no arranque do worker, e congelada em `self._meses`
([`host/worker.py:133`](../wbcpython/host/worker.py)). Mudar de 6 para 24 exige editar o
`.env` na .11 e reiniciar o serviço — ou seja, exige o Marcelo. Não há nenhuma tela, nenhum
comando e nenhum registro de quem pediu o quê.

---

## §1 Arquitetura

O painel e o worker são **processos NSSM separados**. Não há memória compartilhada entre
eles: o único canal que os dois já tocam é o SQLite de acompanhamento
(`state/wbc_tracking.db`), onde a tabela `travas` já faz coordenação cross-process. É por
ali que o pedido viaja.

```
Usuário (vendas)
    │  arma "24 meses" no painel 8079 (pede PAINEL_SENHA)
    ▼
Painel WBC ──escreve──▶ state/wbc_tracking.db ──lê a cada ciclo──▶ Worker WBC
    ▲                        (pedido de janela)                        │
    │                                                                  │ usa a janela
    │◀──── mostra o estado, e a pergunta quando bate no teto ──────────┘ e o teto da banda
                                                                       │
                                                                       ▼
                                                              SAP (Service Layer)
```

---

## §2 Como o pedido se comporta

### Teto escalonado por banda

Uma janela maior devolve muito mais oportunidades represadas. Manter o teto de 200 faria o
ciclo estendido parar quase no começo; tirar o teto faria um ciclo escrever milhares de
documentos em produção sem ninguém olhando. O meio-termo é um teto que **cresce junto com
a janela**:

| Janela pedida | Multiplicador | Teto de escrita no ciclo |
|---|---|---|
| até 6 meses (padrão) | 1× | 200 |
| 7 a 12 meses | 3× | 600 |
| 13 a 18 meses | 6× | 1.200 |
| 19 a 24 meses | 9× | 1.800 |

### Máquina de estados

```
ocioso ──────armar(N)──────▶ armado(N)
                                 │
        ciclo terminou sem bater no teto ──▶ ocioso        (volta a 6, pedido cumprido)
        ciclo bateu no teto ─────────────▶ aguardando(N, faltaram M)
        ciclo com erro ──────────────────▶ armado(N), tentativa+1  (3 erros ▶ ocioso)

aguardando ──"sim, rodar outro ciclo"──▶ armado(N)
aguardando ──"não" ou expirou──────────▶ ocioso
qualquer estado ──restart / deploy─────▶ ocioso
```

Duas regras que sustentam o resto:

- **Em `aguardando`, os ciclos automáticos voltam a 6 meses.** Sem isso o worker continuaria
  varrendo 24 meses a cada 180 s enquanto a pergunta espera resposta — que é exatamente o
  "ciclo pesado rodando sozinho" que este desenho existe para evitar.
- **O padrão é sempre 6.** Reinício, deploy, expiração e pedido cumprido levam todos ao
  mesmo lugar. Não existe caminho em que a janela estendida fique ligada por esquecimento.

---

## §3 Fatos que travam o desenho

1. **Painel e worker são processos separados** (5 serviços NSSM na .11). Nada de variável
   global, nada de `Settings` em memória: o estado tem que viver no SQLite.
2. **`self._meses` congela no arranque** (`host/worker.py:133`). Reler a janela a cada ciclo
   é trabalho de código, não consequência automática de mudar onde o valor mora.
3. **O teto corta o ciclo no meio** (`host/worker.py:314`) e o log já diz quantas ficaram —
   esse número é a matéria-prima da pergunta ao usuário.
4. **`OOPR.OpenDate` é a data de abertura da oportunidade**, não a da última alteração do
   orçamento (`config.py:180`). Uma oportunidade de 2025 alterada ontem só aparece com
   janela larga. É isso que o texto de ajuda na tela precisa dizer, em português de vendas.
5. **O worker só roda 07:00–20:00, seg–sex.** Pedido armado às 20:05 na sexta só age segunda
   de manhã. A tela tem que dizer isso ao armar, senão parece que não funcionou.
6. **Escrita é em produção de verdade.** `WBC_BLOCK_PRODUCTION_WRITES=false` na .11 desde
   02/09. Cancelamento de cotação e criação de pedido **não se desfazem**.
7. **`_acrescentar_colunas_novas` só faz `ADD COLUMN` anulável e sem default de servidor** —
   é a única migração que SQLite e PostgreSQL aceitam sem reescrever a tabela.
8. **`MESES_DE_JANELA` no `.env` não sai.** Vira o valor de retorno, não uma constante morta.
9. **`MESES_DE_JANELA_DIRIGIDA` (busca por número de orçamento) não é tocada** por este plano.

---

## §4 Fases

### F0 — Janela relida a cada ciclo ✅
**O que passa a ser possível:** nada, para o usuário. O comportamento fica idêntico ao de
hoje — e é esse o critério de aceite.

- `WorkerIntegracao` deixa de congelar `self._meses` e passa a resolver a janela no início
  de cada `executar_ciclo`;
- duas colunas de auditoria em `execucoes`: `meses_da_janela` e `teto_de_escrita`, ambas
  anuláveis (regra do fato 7), para que cada execução registre com o que rodou;
- suíte `tests/wbc` verde sem alteração de expectativa.

> ⚠️ **Risco desta fase:** é a única que mexe no caminho quente do worker sem entregar nada
> visível. Se ela quebrar, quebra a integração inteira. Vai sozinha, e vai primeiro.

### F1 — O pedido existe, sem tela ✅
**O que passa a ser possível:** armar uma janela estendida pela CLI e ver o worker obedecer.

- tabela de uma linha no acompanhamento com: janela pedida, estado, quem pediu, quando,
  quantas faltaram, tentativas;
- a máquina de estados do §2, com testes cobrindo cada transição;
- comandos `wbcpython janela --ver | --armar N | --limpar`;
- volta a `ocioso` no arranque do worker (fato 8 do desenho: restart sempre zera).

### F2 — Teto escalonado por banda ✅
**O que passa a ser possível:** um ciclo de 24 meses escreve até 1.800 documentos em vez de
parar em 200.

- função pura banda→multiplicador, testada nas quatro faixas e nas bordas (6, 7, 12, 13, 18, 19, 24);
- teto duro absoluto acima do escalonamento (decisão 8);
- o log do ciclo passa a dizer a janela **e** o teto que está valendo.

### F3 — O card no painel ✅
**O que passa a ser possível:** alguém de vendas arma 24 meses sozinho, pelo navegador.

- card "Janela de busca" no painel 8079, com o estado atual sempre visível;
- texto de ajuda escrito para vendas: o que a janela faz, que conta pela **abertura da
  oportunidade**, por que o padrão é 6, que vale só para a próxima passada e que fora do
  expediente o efeito é no dia seguinte;
- exige `PAINEL_SENHA` (decisão 5), como todo comando que chega ao SAP;
- enquanto armado: mostra o valor pedido, quem pediu e quando.

### F4 — A pergunta ✅
**O que passa a ser possível:** o ciclo que estourou o teto não morre calado — ele devolve a
decisão para quem pediu.

- ao bater no teto, o ciclo termina o que está fazendo, grava tudo e vai para `aguardando`;
- o card mostra: "a janela de N meses encontrou mais do que cabe num ciclo; faltaram M
  oportunidades" + botões **Rodar outro ciclo** e **Voltar para 6 meses**;
- "Rodar outro ciclo" também exige senha — é escrita nova no SAP.

### F5 — Fechar as bordas ✅
**O que passa a ser possível:** confiar que a janela estendida nunca fica ligada sozinha.

- expiração automática do estado `aguardando` (decisão 7);
- histórico na tela: últimos pedidos, quem armou, o que cada ciclo estendido escreveu;
- `docs/wbc/DECISOES.md` ganha a seção, e o `.env.example` explica que `MESES_DE_JANELA`
  virou o padrão de retorno.

---

## §5 Decisões

Todas fechadas.

**1 · Critério de devolução — ✅ decidido (Marcelo, 11/09/2026).**
Não é "um ciclo e volta". O teto de escrita cresce por banda (3×, 6×, 9×); se o ciclo
estourar esse teto, ele para, grava tudo e **pergunta** se o usuário quer rodar outro ciclo.
Cumprido sem estourar, volta a 6 sozinho.

**2 · Multiplicador da quarta banda — ✅ 9× (Marcelo, 11/09/2026).**
Progressão aritmética (3, 6, 9), não dobra: 1.800 escritas no pior caso, com passo constante
e previsível para quem mexer nos números depois.

**3 · Ciclo estendido que termina com erro — ✅ não consome o pedido**, com teto de 3 tentativas. Um erro de rede não pode
custar o pedido; um erro persistente não pode virar loop de ciclo pesado a cada 180 s.

**4 · A janela estendida vale para "Verificar pendentes" e "Simular um ciclo"? — ✅ sim, para os dois.** São os dois comandos que não escrevem no SAP, e é com
eles que se enxerga o tamanho do estrago antes de autorizá-lo. O card deve sugerir
"Verificar pendentes" antes de armar.

**5 · Armar exige `PAINEL_SENHA`? — ✅ sim** (e "rodar outro ciclo" também; **limpar não**),
**e é exceção ao bloqueio de produção** (corrigido em 11/09, depois do deploy). Armar 24 meses não escreve no SAP diretamente, mas é a causa direta de
até 1.800 escritas irreversíveis. O painel já separa "quem entra" (cookie da `OS_API_KEY`)
de "quem manda escrever" (senha); este botão é do segundo grupo.

**6 · Tabela nova no acompanhamento? — ✅ sim**: `pedido_de_janela`, uma linha (`id=1`), mais
duas colunas anuláveis em `execucoes`.
A justificativa (a regra é não inchar sem ela): é estado compartilhado entre dois processos,
com ciclo de vida próprio e necessidade de auditoria. Não cabe em `Execucao.detalhe`, e o
arquivo `state/wbc_worker.stop` — o precedente de sinal painel→worker — carrega um bit, não
um valor com dono, data e contador.

**7 · Validade do estado `aguardando` sem resposta — ✅ 15 minutos (Marcelo, 11/09/2026).**
Ajustado da recomendação original ("fim do expediente"), que era longa demais. Vencido o prazo,
a janela volta ao padrão e o log registra **onde o ciclo parou** — o orçamento da retomada e
quantas ficaram —, para que a expiração nunca seja silenciosa. `JANELA_ESPERA_MINUTOS` no `.env`.

**8 · Teto duro absoluto — ✅ 2.000 escritas**, acima de qualquer banda
(`TETO_ABSOLUTO_DE_ESCRITA`). O escalonamento é uma regra; o teto
duro é a rede embaixo dela, para o dia em que alguém mexer nos multiplicadores.

---

## Fora de escopo

- `MESES_DE_JANELA_DIRIGIDA` (a janela da busca por número de orçamento);
- qualquer mudança no `LIMITE_DE_ESCRITA_POR_CICLO` para a janela padrão de 6 meses;
- perfis por usuário no painel — a autorização continua sendo cookie + senha, como hoje.

---

*Projeto: ServidorIntegracaoSAP (192.168.7.11) · pacote `wbcpython/` · painel 8079.*
*A árvore `D:\ProjetoAltamira\WBCPython\` é legado pós-virada de 08/09/2026 e não recebe este trabalho.*
