# Riscos de executar a integração em produção

> **Documento histórico do WBCPython standalone (até 08/09/2026).** O projeto virou o pacote
> `wbcpython/` do ServidorIntegracaoSAP: onde se lê `uv run wbcpython …`, hoje é
> `python -m wbcpython …` na raiz do repositório; `uv sync` é `pip install -r requirements.txt`;
> `python/WBCPython` é esta raiz; `ai_spec/` está explicado em `ai_spec/00_index.md`.
> Como rodar hoje: [`README.md`](README.md) desta pasta.

> **Data:** 01/09/2026 · **Ambiente medido:** `SBOALTAMIRAPROD` (leitura), WBC
> `WBCCAD` (leitura) · **Commit:** `633075f`
>
> Todos os números deste documento foram **medidos**, não estimados. A medição
> rodou a mesma máquina de estados do ciclo em memória, sem abrir sessão de
> escrita no Service Layer e sem alterar o `.env` — o schema de produção entrou
> por parâmetro. Nada foi gravado em produção para produzir este relatório.

---

## Resumo para quem decide

A integração **não pode ser ligada em produção do jeito que está** — e isso é
proteção, não defeito: o worker recusa iniciar contra produção (`cli.py:283`).
Liberar exige uma alteração deliberada de código ou configuração, que é a linha
mais perigosa do projeto.

Se fosse ligada hoje, com a janela de 3 meses, o **primeiro ciclo** faria isto:

| Efeito | Quantidade | Valor |
|---|---|---|
| Oportunidades avaliadas | 810 | — |
| **Cotações canceladas** | **27** | R$ 10.363,82 |
| **Oportunidades encerradas** | **27** | — |
| **Pedidos criados** | **4** | **R$ 170.973,41** |
| Cotações atualizadas | 4 | — |
| Pedidos atualizados | 1 | — |
| Snapshots do OrcDetalhe gravados | ~33 | — |

Dois desses efeitos são **irreversíveis**: uma cotação cancelada no SAP não
volta, e um pedido criado precisa ser cancelado à mão.

E o mais importante:

> ### 🔴 O sistema legado está rodando em produção **hoje**
>
> Criou **26 cotações em 01/09/2026** e um pedido no mesmo dia — média de 20 a 30
> cotações por dia útil ao longo de agosto. Ligar a integração nova sem desligar
> o legado coloca **dois processos escrevendo nos mesmos documentos, com a mesma
> chave** (`U_INO_COTWBC`).

---

## 1. 🔴 Os dois integradores rodando ao mesmo tempo

**Evidência.** Cotações com `U_INO_COTWBC` criadas em produção:

| Data | Cotações |
|---|---|
| 01/09/2026 | 26 |
| 31/08/2026 | 20 |
| 28/08/2026 | 30 |
| 27/08/2026 | 22 |
| 26/08/2026 | 15 |

Último pedido criado: **01/09/2026**. O legado está vivo e produtivo.

**Por que é grave.** As duas integrações usam a mesma chave de reencontro
(`U_INO_COTWBC`) e a mesma guarda ("já existe cotação para este orçamento?").
Entre a leitura da guarda e o `POST` existe uma janela de alguns segundos. Se as
duas passarem por ela ao mesmo tempo para o mesmo orçamento, **as duas criam** —
e o orçamento fica com duas cotações abertas, cada uma com um valor. A trava de
execução única do worker (`travas`) protege contra dois workers **nossos**; ela
não tem como saber do legado.

Há um segundo conflito, mais silencioso: o legado reescreve `U_INO_StatusWBC` a
cada passagem e a integração nova só quando o valor difere. Não se corrompem
mutuamente, mas o histórico da oportunidade passa a ter duas origens.

**O que fazer.** Desligar o legado **antes** de ligar o novo, não depois. Se
houver um período de convivência, que seja com o novo em modo prévia
(`wbcpython pendentes`), que não escreve.

**Como confirmar que o legado parou:** a consulta acima; se o número de cotações
do dia zerar, parou.

---

## 2. 🔴 A trava de produção precisa ser desligada — e ela é a única defesa

Hoje o comportamento é:

| Situação | O que acontece |
|---|---|
| Apontado para produção, trava **ativa** | Leituras passam; qualquer escrita levanta `ProductionWriteBlocked` |
| Apontado para produção, trava **desativada** | O worker **recusa iniciar** (`cli.py:283`) |

Ou seja: **não existe caminho para escrever em produção sem editar código ou
`.env` de propósito**. Isso é bom, e a passagem para produção precisa tratar
essa edição como o evento de risco que ela é — não como um detalhe de
configuração.

A trava **falha fechada**: `WBC_PRODUCTION_COMPANY_DB` vazio ou com erro de
digitação faz nenhum destino ser reconhecido como produção, e por isso a
configuração incompleta bloqueia em vez de liberar (`safety.py`).

**Risco residual:** quem editar o `.env` para produção precisa mudar **três**
valores coerentemente — `WBC_ENVIRONMENT`, `SL_COMPANY_DB` e `HANA_SCHEMA`.
Mudar só o primeiro deixa a integração lendo produção e escrevendo em
homologação, ou o contrário. Ver o item 6.

---

## 3. 🔴 27 cotações canceladas no primeiro ciclo — divergência deliberada

A integração nova **cancela a cotação quando a oportunidade é encerrada**; o
legado nunca fez isso. Medido em produção:

| | |
|---|---|
| Oportunidades encerradas (Status `L` ou `W`) | 3.453 |
| **…com a cotação ainda aberta** | **3.331 (96%)** |

Na janela de 3 meses, 27 dessas entram no primeiro ciclo — todas com **SitCode
99** (cancelado no WBC), somando **R$ 10.363,82**. Semanticamente está certo: o
orçamento foi cancelado no WBC e a cotação não deveria seguir aberta. Mas:

- **é irreversível** — cotação cancelada no SAP não volta;
- **é visível para o comercial**, que verá 27 cotações sumirem do pipeline de uma
  vez;
- **cresce com a janela**: 27 em 3 meses, **79 em 6 meses**, **96 em 9 meses**.

**O que fazer.** Rodar `wbcpython ciclo --cancelados` primeiro, isolando a leva:
ela trata só o SitCode 99 e confina o efeito a um tipo de mudança. E avisar o
comercial antes, não depois.

---

## 4. 🟠 R$ 170.973,41 em pedidos criados de uma vez

Quatro orçamentos em SitCode 60 estão **sem pedido em produção** e ganhariam um
no primeiro ciclo:

| Orçamento | Itens | Valor |
|---|---|---|
| `00125084` | 3 | R$ 150.623,36 |
| `00124884` | 2 | R$ 11.540,10 |
| `00125035` | 4 | R$ 7.977,63 |
| `00125043` | 1 | R$ 832,32 |
| **Total** | | **R$ 170.973,41** |

Nenhum deles tem pedido não-cancelado hoje — não é duplicação, é trabalho que o
legado não fez. **Isso é um sintoma, não um benefício**: vale entender *por que*
o legado não os criou antes de deixar o novo criar. A hipótese registrada em
`RETOMADA.md` (item 1) é o filtro fixo `= '00121819'` no binário de produção.

**O que fazer.** Conferir os quatro à mão antes, e criar um por vez com
`wbcpython ciclo --orcamento <n>`.

---

## 5. 🟠 O banco de acompanhamento é o mesmo arquivo nos dois ambientes

```
TRACKING_DB_URL=sqlite:///./wbcpython_tracking.db
```

Caminho relativo, sem nome de ambiente. Apontar para produção **sem trocar esta
linha** mistura o histórico de produção com as 886 linhas de homologação que já
estão lá — e o painel passa a somar os dois.

**O que fazer.** Um arquivo (ou banco) por ambiente, e PostgreSQL em produção:
SQLite não sobrevive bem a um worker de serviço e um dashboard lendo ao mesmo
tempo em disco de rede.

Depois de apontar para produção, rodar uma vez:

```bash
uv run wbcpython datas-de-abertura
```

Sem isso o painel não consegue aplicar a janela do worker sobre o histórico.

---

## 6. 🟠 Três variáveis que precisam mudar juntas

| Variável | Homologação | Produção |
|---|---|---|
| `WBC_ENVIRONMENT` | `homolog` | `prod` |
| `SL_COMPANY_DB` | `SBOALTAMIRAHOMOLOG` | `SBOALTAMIRAPROD` |
| `HANA_SCHEMA` | `SBOALTAMIRAHOMOLOG` | `SBOALTAMIRAPROD` |

`SL_COMPANY_DB` é a company que **recebe a escrita**; `HANA_SCHEMA` são as views
de relatório. A leitura das oportunidades usa o **primeiro**, de propósito
(`infrastructure/hana/oportunidades.py`), justamente para que não seja possível
ler de uma company e escrever noutra. Mas os dois valores desalinhados ainda
produzem relatório de um ambiente com dados do outro.

`uv run wbcpython env` mostra os três juntos numa linha só. Use antes de
qualquer execução.

---

## 7. 🟠 Certificado TLS aceito sem validação

```
SL_VERIFY_SSL=false
```

O Service Layer usa certificado autoassinado, e hoje a validação está desligada.
Em produção isso significa que **credenciais e documentos trafegam sem garantia
de que o servidor é quem diz ser** dentro da rede interna. Não é o risco mais
provável desta lista, mas é o único que envolve credencial.

**O que fazer.** Emitir certificado válido da CA interna e apontar
`SL_CA_BUNDLE`, ou aceitar formalmente o risco. Está em `RETOMADA.md` (item 5)
desde o começo, sem decisão.

---

## 8. 🟡 Divergências deliberadas em relação ao legado

Cada uma foi decidida com dado e está registrada em `DECISOES.md`. Juntas, elas
significam que **os documentos gerados pela integração nova não são idênticos aos
do legado** — e alguém do negócio vai notar.

| O quê | Legado | Novo | Efeito visível |
|---|---|---|---|
| Cotação no encerramento | fica aberta | **é cancelada** | 27 no 1º ciclo (item 3) |
| Preço da linha | `Price` | `Price` | `SpecPrice` sai `N` no lugar de `R` |
| `U_ORCIMP_REVISAO` | grava vazio | grava vazio | sem fallback silencioso |
| `Weight1` do pedido | 1 kg em 127 linhas, 0 em 64 | líquido × 1,10 truncado | peso passa a existir sempre |
| Espelhar SitCode | a cada passagem | só quando difere | ~90% menos escritas |

O marcador `SpecPrice` é o mais fácil de notar num relatório: em 2026 produção
tem **231.669 linhas com `R`** e 634 com `N`. As nossas sairão `N`.

---

## 9. 🟡 Volume, teto e ritmo

| | |
|---|---|
| Teto de escrita por ciclo | 200 (`LIMITE_DE_ESCRITA_POR_CICLO`) |
| Escritas previstas no 1º ciclo (3 meses) | 33 |
| Intervalo do worker | 300 s |

O teto não seria atingido com a janela de 3 meses. **Com 6 meses seriam 104 e
com 9, 149** — ainda abaixo, mas a margem encolhe. O teto existe justamente para
que um erro de janela não vire uma enxurrada de documentos.

**Atenção ao intervalo:** 5 minutos com o legado ainda ligado multiplica a
chance da corrida do item 1.

---

## 10. 🟡 O que nunca rodou contra o SAP de verdade

Validado em homologação, contra o SAP real: criação e atualização de cotação,
criação e atualização de pedido, vínculo à oportunidade, espelhamento de status,
snapshot do OrcDetalhe, encerramento com cancelamento de cotação.

**Nunca exercitado no ambiente real:**

- `cancelar_e_recriar_pedido` com troca de parceiro (há teste de unidade, e a
  regra foi corrigida duas vezes nesta sessão);
- o comando `wbcpython pesos` gravando de fato (só simulação);
- o worker contínuo por vários ciclos seguidos.

---

## 11. 🟡 Pendências de dado que a virada não resolve

| # | O quê | Onde |
|---|---|---|
| 1 | 22 cotações com `CardCode` diferente do da oportunidade — mesmo número nos dois ambientes | `RETOMADA.md` 10 |
| 2 | 3.331 oportunidades encerradas com cotação aberta, fora da janela | item 3 |
| 3 | 580 oportunidades com `U_INO_PN_Correc` residual | `DECISOES.md` |
| 4 | `U_ORCIMP_NEGOCIACAO` regrediu em produção em julho/2026 (95–99% → 3%) | `DECISOES.md` |

Nenhuma impede a virada. Todas ficam mais fáceis de resolver **depois** que só um
integrador estiver escrevendo.

---

## Roteiro de virada sugerido

1. **Decidir a janela** (`MESES_DE_JANELA`) — ela define o raio de impacto:
   33, 104 ou 149 escritas. Está em aberto desde o começo (`RETOMADA.md` 4).
2. **Separar o banco de acompanhamento** de produção e rodar
   `wbcpython datas-de-abertura`.
3. **Rodar a prévia contra produção** com a trava ativa e conferir a lista:
   `uv run wbcpython pendentes --com-acao --exportar previsao.json`.
4. **Avisar o comercial** sobre as cotações que serão canceladas (item 3) e os
   pedidos que serão criados (item 4).
5. **Desligar o legado** e confirmar pela consulta do item 1.
6. **Primeiro ciclo isolado:** `wbcpython ciclo --cancelados` (só SitCode 99).
   Conferir as 27 cotações canceladas.
7. **Segundo passo, um a um:** `wbcpython ciclo --orcamento <n>` para os quatro
   pedidos.
8. **Só então** o ciclo completo, e depois o worker contínuo — observando o
   painel (aba "Log") nos primeiros ciclos.

Em qualquer passo, `wbcpython pendentes` mostra o que aconteceria sem fazer nada.
