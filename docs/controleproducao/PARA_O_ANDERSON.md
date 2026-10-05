# Para o Anderson — o ControleProducao virou parte do ServidorIntegracaoSAP

> 28/09/2026. Decisão do Marcelo: o seu pacote (montado em 24/09) passa a viver dentro do
> repositório do ServidorIntegracaoSAP (o SIS, `github.com/MarceloProjetos/oportunidade_wbc`),
> como `controleproducao/`, do mesmo jeito que o WBCPython virou `wbcpython/` em 08/09 — um
> Python, um `.env`, um login, um deploy, um serviço a mais na .11. Este documento diz **o que
> mudou no seu código e por quê, como trabalhar daqui em diante, e o que só você pode decidir**.
> Desde 28/09 13:26 o serviço `OrcaView-ControleProducao` está no ar na .11 (F3 do plano;
> a F6 abre a tela para a rede). Nada foi gravado no SAP por ele lá ainda: o piloto (F5)
> depende das decisões abaixo.
>
> **Atualização 30/09/2026 — o parágrafo acima é o retrato de 28/09 de manhã.** Hoje: a tela
> está na rede desde 28/09 ~14:45 (F6) e **já gravou em produção** — 1º pedido real 84433 às
> 15:36 de 28/09, processado **também pelo addon** (as OPs do addon foram canceladas; é o risco
> de §4). O módulo 3 está na tela desde 28/09 (Liberar/Encerrar; Replanejar só CLI, D9); a F4
> ficou coberta por decisão (D7: a semana de uso no seu notebook), **sem reteste** — a §5 deixou
> de ser pré-requisito. Em 29–30/09: SQL dos módulos 2 e 3 com parâmetro ligado
> (`core/sql_ligado`), WBC em `pymssql`, `_update_pedido` fechado com erro até você validar,
> Reprocessar só CLI (D8 — **revertida em 30/09**: voltou à tela, com o aviso de que não
> recria), leitores só-leitura por construção, histórico das execuções no
> Supabase e log em arquivo dos comandos da CLI que gravam. O plano e o `CHANGELOG.md` têm o
> detalhe.
>
> **Mexidas no seu fluxo em 30/09 (só leitura, para você revisar pelo diff):**
> `WbcSqlServerClient` reaproveita **uma** conexão por execução (antes, um login no SQL Server
> por consulta — por item da estrutura, por código de semiacabado, por grupo), com rollback ao
> fim de cada consulta e reconexão única se ela cair; em `_processa_grupo_producao`, DocEntry,
> DocNum, entrega múltipla, item SAP do GrpCode, custos do WBC e preço máximo passam a ser lidos
> **uma vez por pedido** (no `contexto` que você já usava), `doc_entry_final` reaproveita o
> `doc_entry_ped` (entre os dois só há leituras) e o `GET_VERSAO_PEDIDO` saiu (era lido e
> descartado, como no C#); no `finalizar_ops`, filiais ativas e séries uma vez por encerramento.
> A quantidade e o valor da linha do grupo (`BUSCA_MAX_ITEM_LINHA`) continuam por grupo. A pasta
> `IntegracaoPedido_CriacaoOP/` foi apagada do notebook do Marcelo (D2) — o original é o seu zip.

## 1. O que mudou no seu código

Tudo que está listado aqui está num único commit do SIS, para você revisar pelo diff. A regra
de negócio dos módulos **não mudou**: `service.py`, `queries.py` e os templates fazem o mesmo.

| Onde | Mudança | Por quê |
| --- | --- | --- |
| `python_app/app/**` → `controleproducao/**` (achatado) | O pacote se chama `controleproducao`; 124 `from app.…` e 53 alvos de `patch("app.…")` nos testes renomeados; os literais de logger em `acompanha_log` viraram `service.__name__`; textos de ajuda dizem `python -m controleproducao …` | `app` colide com o `api.py:app` do SIS e é genérico demais na raiz. Os literais de logger renomeados à mão porque o acompanhamento na tela ficaria mudo sem erro |
| `controleproducao/__main__.py` (novo) | `python -m controleproducao …` | Padrão do SIS (`python -m wbcpython …`) |
| `config.py` | Lê o `.env` **da raiz do SIS** (o caminho por `__file__` já fazia isso ao achatar). Ganhou os fallbacks `SL_*`→`OP_SL_*`, `WBC_SQL_*`→`SQL_*`/`SQLSERVER_*`, `HANA_*`→`SAP_*` (`AliasChoices` + `env_ignore_empty`). O bloco "reservado" (`PAINEL_PORTA=8501`, `WORKER_*`, `TRACKING_DB_URL`…) saiu. `hana_schema` virou **propriedade = `sl_company_db`**. Campos novos: `CP_HOST`, `CP_PORTA`, `CP_LOG_FILE`, `OS_API_KEY`, `PAINEL_PORTA`, `WBC_PAINEL_URL` | Um `.env` só, com os nomes que o worker já usa. Os campos reservados colidiam com variáveis vivas do painel (`PAINEL_PORTA=8079`). `HANA_SCHEMA` no `.env` do SIS é o schema de **leitura do worker** e pode diferir da company de escrita — ler ORDR/OWOR num schema e gravar noutro é o bug de 21/09; derivar de `SL_COMPANY_DB` mata a classe inteira |
| `core/service_layer_client.py` | `_assegura_escrita(metodo)` no início de `_request` e `_via_batch`: POST/PATCH na company de produção só da máquina com o IP da .11 (`wbcpython.safety`); GET livre. `aclose()` faz `POST /Logout` quando logou (falha vira warning, nunca exceção) | A trava que você tirou em 22/09 era uma chave no `.env`; no SIS a regra é "função de produção liga pela máquina, não por chave" (uma chave some numa reescrita do `.env` e a função morre calada). Sessões vazadas contam no teto do Service Layer, que o worker divide com o pacote |
| `core/guardas.py` · `core/web.py` · `cli.py` (`_avisa_ambiente`) | `aviso_de_escrita` levanta `ProductionWriteBlocked` fora da .11 (produção); a web devolve **503** antes de criar a tarefa, a CLI sai com **2** antes de perguntar. `avisa_escrita` também devolve 503 quando não há `OS_API_KEY` | Sem a camada de cima, a recusa do cliente viraria "concluída com falhas" silenciosa (`audit_log` engole exceções) |
| `core/acesso.py` (novo) · `main.py` · `templates/entrar.html` (novo) · `templates/base.html` | Middleware de login com o **mesmo cookie do painel WBC** (`wbcpython/dashboard/acesso.py`: HMAC da `OS_API_KEY`); `/entrar` e `/sair` próprios; `docs_url=None`; POST por cookie exige `Origin`/`Referer` do próprio host; rotas abertas: `/entrar`, `/sair`, `/health`, `/health/ocupado`, `/painel-wbc`, `/static/*`. `base.html` ganhou "⇄ Painel WBC" e "Sair" | A tela vai para a rede da fábrica e grava em produção; sem login, qualquer máquina que alcance a porta cria/encerra OP. O cookie é o do painel porque o navegador não separa cookie por porta: uma entrada vale para as duas telas |
| `cli.py` (comando `web`) | `python -m controleproducao web` sobe o uvicorn com `CP_HOST`/`CP_PORTA` e log em `logs/controleproducao.log` (5 MB × 3, formato do SIS); `uvicorn.run(log_config=None)` | Substitui `python -m uvicorn --app-dir python_app app.main:app --log-config config\log_config.json` dos seus scripts |
| `core/templates.py` | global Jinja `exige_chave()` | Botão "Sair" só quando há chave |
| Vários `.py` | Reformatação pelo `ruff` do SIS: `Optional[X]` → `X \| None`, `typing.List` → `list`, ordem de imports, 3 imports mortos, 1 variável morta; 14 linhas > 120 colunas quebradas à mão (textos de ajuda do `cli.py`, `pedidos_wbc/service.py`); `E501` liberado só para o SQL de `queries.py` | `ruff check .` = 0 é gate do pre-commit do SIS |
| `tests/` → `tests/controleproducao/` | `conftest.py` novo (isola `.env` e ambiente; fixture `como_a_11`); `test_web_modulos.py`: 2 testes instáveis corrigidos (o `GET /estado` entrou para dentro do `with patch`, com polling de `terminada`) e a fixture `cliente` agora entra com chave + cookie + `Origin`; testes novos: `test_config.py`, `test_acesso.py`, `test_web_comando.py`, trava/logout em `test_service_layer_client.py`; `test_aviso_producao.py` reescrito para a regra do IP | A suíte do SIS roda inteira no pre-commit com `-x`: um teste intermitente barraria todo commit de todo mundo. Os 2 falhavam 1–2 vezes a cada rodada (corrida entre a tarefa em background e o `patch.__exit__`) |
| `requirements.txt` do SIS | `typer` e `rich` entraram; `hdbcli` **fica 2.29.23** (você empacotou 2.30.24) | Um Python só na .11, com os pins do SIS |

Ficou de fora, de propósito: `wheels/`, `scripts/00–06.ps1`, `config/log_config.json`,
`MANIFESTO`, `requirements-windows.txt`, `.venv`, o `CLAUDE.md`/`LEIA-ME` do pacote e as docs
de instalação isolada. O SIS já tem deploy (`deploy_update.bat`), serviço (NSSM), log,
monitoração (`/status`) e login. `migration_guide.md`, `decisoes.md` e `GUIA_ESTILO_ORCAVIEW.md`
vieram para `docs/controleproducao/` com uma nota de contexto no topo.

Duas coisas que o seu código ainda tem e que ficaram **anotadas, não corrigidas** (são suas):
o `basicConfig(force=True)` + `getLogger("httpx").setLevel(WARNING)` da CLI vaza para o
processo (contido no `conftest` da suíte); e o SQL do módulo 3 monta `doc_num`/`op_docnums` por
`str.format` — o SIS usa `sql_seguro` (t-string) para valor vindo de fora; fica para uma etapa
combinada. *(30/09: o SQL já foi resolvido — F7, módulos 2 e 3 com parâmetro ligado; resta
só o `basicConfig(force=True)`.)*

## 2. Como trabalhar daqui em diante

1. Clone do SIS (é público): `git clone https://github.com/MarceloProjetos/oportunidade_wbc.git`
   e, dentro dele, `git config core.hooksPath .githooks` — o pre-commit roda `ruff check .` e a
   suíte inteira (~50 s) e barra o commit se algo falhar. Não use `--no-verify`.
2. `pip install -r requirements.txt -r requirements-dev.txt` no Python 3.14 (o SIS não usa venv
   na .11; no seu notebook pode usar, o `run_*.bat` prefere `venv\` se existir).
3. `.env` na raiz a partir do `.env.example` (bloco WBC + bloco "Controle de Produção"). Para
   homologação: `SL_COMPANY_DB=SBOALTAMIRAHOMOLOG`. **Do seu notebook nada vai para produção**:
   a trava pelo IP recusa antes do login.
4. Rodar: `python -m controleproducao web` / `python -m controleproducao pedidos-wbc buscar`.
   Testar: `python -m pytest tests/controleproducao -q`. Lint: `python -m ruff check .`.
5. Entregar: branch por entrega, commit, push, e o Marcelo faz o deploy na .11
   (`deploy_update.bat`). Versão = commit; não há mais `MANIFESTO`/zip.
6. O `CLAUDE.md` do SIS (raiz) é o guia dos agentes; `docs/controleproducao/README.md` é o
   guia do pacote. O `CLAUDE.md` do seu pacote não vale mais.

Regras do repo que alcançam o seu código: comentários e docstrings **novos** em inglês
técnico (o que já está em português fica); `ruff` em 0; nenhum teste pode alcançar SAP/HANA/WBC.

## 3. O que só você pode decidir

Numeração igual à do plano (`docs/PLANO_CONTROLE_PRODUCAO_11.md`, §4).

**D12 — Onde o código mora.** Confirma que passa a desenvolver no clone do SIS (item 2)? A
alternativa — continuar no seu pacote e migrar cada zip — repete o rename, o `ruff` e a trava a
cada versão.

**D13 — Quem manda em cada campo do pedido.** O worker WBC do SIS e o seu pacote gravam o
mesmo `ORDR`: o worker cria o pedido, refaz as **linhas** a cada revisão do orçamento (salvo
`U_INO_Congelado='Y'`) e **cancela-e-recria** o pedido na troca de PN, sem olhar `OWOR`; o
pacote grava `U_INO_Congelado='Y'` ao processar (e `cancelar-ops` não reverte), `U_INO_OP` nas
linhas e as OPs. Hoje produção tem 2.581 pedidos com `Congelado='Y'`. Perguntas: (a) o worker
deve ignorar pedido com `U_INO_ProcessWBC='Y'`? (b) `cancelar-ops` deve restaurar
`Congelado` ao valor lido antes (`VERIFICA_CONG`)? (c) `U_INO_ProcessWBC='Y'` pode passar a ser
gravado **depois** da primeira OP (hoje é antes — uma queda no meio deixa o pedido "processado"
sem OP, e a retomada exige `--force`, que duplica)? Minha sugestão: sim às três.

**D14 — Addon legado × tela nova.** Em 23/09 o addon C# reprocessou o 84426 depois do porte e as
OPs saíram em dobro (o addon não tem a guarda de OP duplicada). Enquanto os dois existirem:
quais orçamentos são "da web" (lista nominal no piloto), quem avisa o PCP, e quando o addon
"Integração de Pedidos (WBC)" é desligado para pedidos novos? Sugestão: 3 orçamentos no piloto,
comunicado seu ao PCP antes do primeiro `processar-novos`, addon desligado na saída do piloto.

**D15 — Três regras que só você conhece.** (a) `U_INO_EntregaMultipla='Y'` pular a OP
principal (mas criar as de semiacabado) sem constar em `sem_op` é intencional? (b)
`U_INO_ORCAMENTO` nunca é gravado pelo porte (`tb_valdixson=0`, `Weight1` morto) — deixar,
já que o worker grava, e registrar em `decisoes.md`? **→ Decidido pelo Marcelo em 05/10/2026:
grava, e foi corrigido** (o `DocEntry` vem da resposta do `POST`, nunca do `max()`); o
`Weight1` (`GET_PESO_PEDIDO`) segue só em `_update_pedido`, fechado pela F7. Achado
para você: o `max("DocEntry")` do addon fez 5 pedidos abertos apontarem para o Detalhe do
orçamento 00124945 em 01/10 (84327, 84353, 84371, 84375, 84391). (c) As linhas ficam com `im_Manual` depois
de replanejar — importa para quem aponta pelo cliente SAP?

**D16 — Quem grava `ORDR.U_INO_Integrar='Y'`.** É pré-requisito da lista "Pedidos Novos";
pedidos criados pelo worker nascem sem ele. Ninguém achou quem preenche (pessoa no cliente
SAP? o addon?). Sem isso a lista da .11 vem vazia.

**Escopo da 1ª entrega (D8, é do Marcelo, mas depende de você):** só Buscar + `processar-novos`
+ `cancelar-ops`; `reprocessar-integrados` fica fora (cancela **toda** OP planejada do pedido,
inclusive as do addon, **não recria**, e reescreve a Oportunidade que o worker governa);
módulo 3 só depois do reteste em homologação (`liberar` nunca rodou como comando, a web nunca
gravou, `encerrar --pedido` nunca rodou; 4 correções depois da única execução validada).

## 4. Limpeza do que ficou em produção (22–23/09) — antes do piloto

Só leitura, no cliente SAP ou HANA Studio, em `SBOALTAMIRAPROD`. A guarda de OP duplicada
conta OPs prévias por item: sujeira muda a decisão dela.

Pedidos cujo rateio falhou (OPs sem a linha `GGF_`):

```sql
SELECT DISTINCT "U_OrcNum", "CreateDate", "Creator"
  FROM "SBOALTAMIRAPROD"."@INO_LOG"
 WHERE "U_Mensagem" LIKE 'Erro ao preencher recurso%'
 ORDER BY "CreateDate";
```

OPs principais cuja quantidade planejada difere da linha do pedido a que estão vinculadas:

```sql
SELECT W."DocEntry", W."DocNum", W."OriginNum", W."ItemCode",
       W."PlannedQty", L."Quantity" AS "Qtd na linha", L."LineNum"
  FROM "SBOALTAMIRAPROD".OWOR W
  JOIN "SBOALTAMIRAPROD".RDR1 L ON L."DocEntry" = W."OriginAbs" AND L."U_INO_OP" = W."DocEntry"
 WHERE W."Status" <> 'C' AND W."PlannedQty" <> L."Quantity"
 ORDER BY W."DocEntry" DESC;
```

**Resultado em 28/09 (rodado do notebook, só leitura, PROD):** a 1ª consulta devolve **4
orçamentos**, todos de execuções do porte (`financeiro04`): 00124709 (pedido 84420, 22/09),
00125644 (84422, 23/09), 00125551 (84425, 23/09) e 00125540 (84426, 23/09) — os quatro
pedidos estão com OPs Planejadas vivas de 22–24/09 (84420: 30; 84422: 12; 84425: 20;
84426: 67). A 2ª consulta devolve **504 linhas**, quase todas anteriores ao porte e do
padrão `I000002`/`I000003` com `PlannedQty=1` × quantidade da linha — do jeito que está,
ela não serve de gate "0 = limpo"; se a regra for outra para o item-conjunto, diga qual.
Nenhuma OP órfã (OWOR viva com pedido `CANCELED='Y'`). Roteiro completo em
`maintenance/pre_voo_controleproducao.py`.

E, pelo que o diário registra: as OPs em dobro do 84426 (addon + porte), os OrcDetalhe órfãos
(um novo a cada execução) e itens que podem ter nascido no grupo 358 em vez de 332 antes do
`Solda.txt` entrar (21/09). O que corrigir à mão é decisão sua; o resultado entra no plano.

## 5. Reteste em homologação (F4 do plano) — o que precisamos de você

> *30/09/2026: não é mais pré-requisito — F4 coberta por decisão (D7). Fica como roteiro, se
> você quiser provar o rollback do `encerrar` antes de usá-lo em volume.*

Do seu notebook (ou do do Marcelo), contra `SBOALTAMIRAHOMOLOG` restaurada de produção:

- Módulo 2: um orçamento com **≥ 2 grupos do mesmo item** e `GGF_` ainda inexistente →
  `processar-novos` → `comparar-ops <orc> --detalhes` → conferir ORSC (rateio), `U_INO_OP`,
  quantidade da linha do grupo → `cancelar-ops` e conferir o que **não** volta (itens, recursos,
  `Congelado`).
- Módulo 3: `liberar` 1 OP; `encerrar` 1 Planejada e 1 Liberada com custo apurado; `encerrar
  --pedido` num pedido pequeno; 1 clique de cada ação pela tela.
- Rollback provado: cancelar no cliente SAP a entrada e a saída de um `encerrar` de teste
  (OIGN → OIGE), e decidir o caminho para "saída lançada, entrada falhou" (rodar `encerrar` de
  novo na mesma OP, ou cancelar a saída).

O que precisamos de volta: os DocEntry de tudo que foi criado/encerrado, e 0 divergências.

## 6. Checklist do que devolver

- [ ] D12: fluxo de entrega no repo do SIS — sim/não.
- [ ] Revisão do diff do commit de integração (tabela do item 1).
- [ ] D13 (a)(b)(c), D14, D15 (a)(b)(c), D16.
- [ ] Resultado das duas consultas do item 4 e o que foi corrigido à mão.
- [ ] Orçamentos/OPs escolhidos para o reteste (item 5) e uma janela de ~2 h com o Marcelo.
