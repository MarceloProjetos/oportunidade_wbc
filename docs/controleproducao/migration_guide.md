> **Documento histórico do pacote ControleProducao (montado em 24/09/2026 na máquina do
> Anderson, dono da aplicação).** Desde 28/09/2026 o código mora em `controleproducao/`
> do ServidorIntegracaoSAP: onde se lê `python -m app.cli …`, hoje é
> `python -m controleproducao …` na raiz do repositório; `python_app/app/` é
> `controleproducao/`; o `.env` é o do SIS (mesmos nomes); `.venv`, `scripts/00-06`,
> `config/log_config.json` e as `wheels/` não existem mais (serviço `OrcaView-ControleProducao`,
> `install_wbc_services.bat`, `deploy_update.bat`). Plano: `docs/PLANO_CONTROLE_PRODUCAO_11.md`.
> O diário (§7) vai até 24/09. Desde 28/09 as mudanças estão no plano e no `CHANGELOG.md`: SQL
> com parâmetro ligado (`core/sql_ligado`), `pymssql` no lugar de `pyodbc` (onde §6 diz
> `WbcSqlServerClient (pyodbc)`), leitores só-leitura por construção, `_update_pedido` fechado,
> Replanejar/Reprocessar só pela CLI (D8/D9), histórico das execuções no Supabase.

# Guia de Migração — ControleProducao (Addon SAP B1) → Aplicação Web Python

> **Como usar este documento**: ele é a fonte única de verdade para continuar o desenvolvimento em qualquer sessão futura (com ou sem memória das conversas anteriores). Ele documenta (1) o que o addon C# faz hoje, campo a campo e query a query, (2) o desenho da nova aplicação Python e (3) o plano de execução com o que já foi feito e o que falta. Sempre que uma decisão de arquitetura mudar, **atualize este arquivo**, não apenas o código.
>
> Local do addon legado analisado: `Fontes/ControleProducaoCongelamento/ControleProducaoCongelamento/ControleProducao/` (solução Visual Studio, .NET Framework 4.5.2/4.7.2, SDK SAP B1 DI API + UI API `SAPbouiCOM.Framework`).
> Local do novo projeto: `Fontes/ControleProducaoCongelamento/python_app/` (ver seção 6).

---

## 1. Objetivo e escopo

Transformar o addon SAP Business One "Controle de Produção — WBC" (Windows Forms via UI API, rodando no client SAP B1 sobre Windows) em uma **aplicação web em Python**, eliminando a dependência do SAP B1 Client/DI API (COM, só Windows) e substituindo-a pela **SAP Service Layer** (REST/HTTPS) para tudo que for possível, mantendo acesso complementar ao HANA e ao SQL Server externo (WBC) onde a Service Layer não for suficiente.

Decisões já tomadas com o usuário (14/09/2026):

| Decisão | Escolha |
|---|---|
| Leitura no HANA ~~(decisão original de 14/09/2026)~~ **revista em 16/09/2026** | **Híbrido, caso a caso, com o `hdbcli` na frente**: acesso direto via `hdbcli` é a **opção preferencial para leitura**; a Service Layer (`$filter`, `$expand`) entra na leitura **somente onde o `hdbcli` não for suficiente**. Inverte a prioridade da decisão original, que colocava a Service Layer primeiro e o `hdbcli` como ponte pontual. **A escrita não muda**: continua 100% pela Service Layer (ver 6.1) — o `hdbcli` é somente leitura. |
| Ordem de migração dos módulos | Seguir o fluxo de negócio: 1) Oportunidades → 2) Pedidos WBC → 3) Manutenção de OP → 4) Romaneio. **Revisto em 22/09/2026**: o módulo 1 saiu do escopo (seção 7.36); a numeração dos demais foi mantida para continuar casando com o addon legado. |
| Fidelidade vs. limpeza de regras | **Fase 1: portar 1:1** o comportamento (mesmas regras, mesmas listas fixas tipo `Explosao.txt`/`Solda.txt`, mesmos side-effects), só then refatorar com testes de regressão. |
| Como disparar as integrações | **Botões manuais na web**, espelhando o fluxo atual (usuário busca, seleciona, clica em Integrar/Processar) — sem job agendado nesta fase. |
| Interface para começar a validar (15/09/2026) | **Linha de comando primeiro.** Antes de terminar a UI web, cada módulo ganha comandos de CLI que chamam exatamente as mesmas funções de `service.py` usadas pela web — nenhuma lógica duplicada. A CLI é a forma de testar contra um ambiente real (homologação/produção) sem depender do servidor web/navegador. A web continua sendo a interface de uso do dia a dia, mas passa a ser construída depois que cada módulo estiver validado via CLI. |

---

## 2. Contexto de negócio (inferido do código)

- Empresa opera SAP Business One sobre **HANA**, schema de produção **`SBOALTAMIRAPROD`**.
- Existe um sistema externo de orçamentos chamado **WBC** (também aparece como "Valdixson" em comentários/nomes de tabela), que roda em **SQL Server** (banco `WBCCAD`, conforme `app.config`) e é o local onde vendedores montam orçamentos detalhados (estrutura de produto, preços, comissão, montagem, transporte etc.) fora do SAP.
- O addon interliga os dois mundos:
  1. Publica **Oportunidades de Venda** do SAP (`OOPR`) para o WBC (tabela `INTEGRACAO_ORCINC`), permitindo que o vendedor monte o orçamento lá.
  2. Lê de volta o orçamento fechado no WBC (tabelas `INTEGRACAO_ORC*`) e usa isso para **criar/atualizar o Pedido de Venda no SAP** (`ORDR`/`RDR1`), a **estrutura de produto (BOM)** (`ProductTrees`/`OITT`/`ITT1`) e as **Ordens de Produção** (`OWOR`/`WOR1`), inclusive explodindo recursivamente semiacabados.
  3. Permite manutenção manual das Ordens de Produção (liberar, planejar, cancelar, encerrar com baixa de insumo e entrada de produto).
  4. Um módulo de **Romaneio** permite separar/expedir parcialmente pedidos com múltiplas entregas, criando itens "compostos" dinamicamente (agrupando semiacabados disponíveis em um novo item de expedição) e lançando movimentos de estoque.
- É uma indústria de fabricação sob encomenda com estrutura de produto multinível (níveis de "nível"/`nivel`, ex.: perfil → componente → produto acabado), grupos de itens fixos (333 = acabado, 332 = semiacabado "Solda", 358 = default) e regras de nomenclatura de código de item (prefixos como `PAG`, `TPO`, `PIG` etc. definidos em `stringArray` dentro de `CriaItem`).

---

## 3. Arquitetura atual (legado)

```
┌─────────────────────────┐        ADO.NET / SqlClient        ┌───────────────────────────┐
│   WBC (SQL Server)      │ <───────────────────────────────► │  Addon C# (SAPbouiCOM.     │
│   DB: WBCCAD            │      SQLConnection.cs              │  Framework, Windows Forms  │
│   Tabelas INTEGRACAO_*  │                                    │  dentro do SAP B1 Client)  │
└─────────────────────────┘                                    │                            │
                                                                 │  - Menu.cs (registra menu) │
┌─────────────────────────┐   DI API (SAPbobsCOM, COM, só      │  - Program.cs (bootstrap)  │
│   SAP B1 / HANA          │ <  Windows) — Recordset.DoQuery,   │  - Form1 (Oportunidades)   │
│   Schema SBOALTAMIRAPROD│    Documents/Items/ProductionOrders │  - Form2 (Pedidos WBC)     │
│                          │    .Add()/.Update(), UDOs via      │  - ManutencaoOp             │
│                          │    GeneralService                  │  - Romaneio                 │
└─────────────────────────┘                                    │  - ProcessDefault.cs (regras│
                                                                 │    de negócio)              │
                                                                 │  - Querys.resx (~60 SQLs)    │
                                                                 └───────────────────────────┘
```

Pontos-chave técnicos do legado (relevantes para a portabilidade):

- **Duas fontes de dados distintas**: SAP/HANA (via DI API, `Recordset.DoQuery` com SQL em sintaxe HANA — aspas duplas em identificadores, `IFNULL`, `to_varchar`, `SUBSTR_AFTER`) e SQL Server externo WBC (via `System.Data.SqlClient`, sintaxe T-SQL — colchetes `[dbo].[Tabela]`).
- Toda escrita no SAP é feita via **DI API** (`SAPbobsCOM.Company.GetBusinessObject`), nunca via Service Layer — é o que precisa ser reescrito.
- Log de auditoria próprio: UDO **`INO_LOG`** (`ProcessDefault.preencheLog`), com campos `U_OrcNum`, `U_TipoDocumento`, `U_Status`, `U_Tipo`, `U_Mensagem`. É chamado profusamente nos `catch`.
- Numeração própria (mascarada) para o ID gerado no lado WBC: UDT **`@INO_SETUP`** (código fixo `"1"`), campos `U_Mascara` e `U_Numero`, incrementado via UDO **`INO_SETUPOBJ`**.
- Uma tabela "espelho" do orçamento do WBC é persistida dentro do SAP como UDO: **`OrcDetalhe`** (tabela pai) + tabela filha **`INO_ORC_LINHA`** — grava basicamente tudo que veio do WBC (valores, comissão, transporte, embalagem, montagem etc.) para consulta/rastreabilidade.
- Erros são majoritariamente **engolidos e logados** (`catch { preencheLog(...); }`), não interrompem o fluxo em lote — a paridade de comportamento (fase 1) deve preservar isso, mas a nova versão deveria expor esse log de forma mais visível na UI web (ver seção 8, débito técnico).
- Uso extensivo de `GC.Collect()` e `Marshal.ReleaseComObject` — eram necessários para liberar handles COM da DI API; **não existem mais em Python/Service Layer** (não portar).
- Há uma boa quantidade de **código morto/comentado** (ex.: bloco gigante comentado em `Form2.Button0_ClickAfter`, `AtualizaLinhaAntiga` todo comentado) e **código de debug esquecido** (`if (itemPai == "...") { string teste = ""; }`) — não precisa ser portado, mas está documentado aqui para não se perder contexto caso algum desses "ifs esquecidos" na verdade escondesse um caso de borda real.

---

## 4. Inventário funcional completo (módulo a módulo)

### 4.1 Bootstrap do addon (`Program.cs`, `Menu.cs`)
- Registra um menu popup "Controle de Produção - WBC" dentro do menu de Módulos do SAP B1, com 4 itens: **Integração de Oportunidades (SAP)**, **Integração de Pedidos (WBC)**, **Romaneio (HOMOLOGAÇÃO)**, **Manutenção (HOMOLOGAÇÃO)**. O item "Configurações" existe no código mas está **comentado** (não aparece no menu hoje).
- **Equivalente Python**: não existe — vira simplesmente a navegação (navbar) do app web, com uma página por módulo. Sem "SAP menu", sem "SBO_Application".

### 4.2 Tela "Configurações" (`Views/Configuracoes.b1f.cs`)
- Não está no menu ativo hoje, mas mantém a UI para editar `@INO_SETUP`: campos `U_Mascara` (máscara do número gerado, ex.: prefixo) e `U_Numero` (contador atual).
- Lógica: se a linha `Code = "1"` não existir, faz **Add**; senão faz **Update** incrementando o número.
- **Equivalente Python**: uma tela simples (`/configuracoes`) para editar esse registro via Service Layer (`GET/PATCH /INO_SETUPOBJ('1')`), útil mesmo não estando no menu do SAP.

### 4.3 Módulo 1 — Integração de Oportunidades (`Form1` / `IntegraOportunidadeSAP.b1f.cs`) — **NÃO PORTADO**

> ⚠️ **Removido do porte em 22/09/2026**, por decisão do Anderson. A tela existe no addon
> legado e continua funcionando lá; ela simplesmente não faz parte do escopo da aplicação
> nova. O código Python correspondente (`app/modules/oportunidades/`, a tela, os comandos
> de CLI e os testes) foi **apagado** — ver seção 7.36. A descrição abaixo é mantida como
> documentação do **legado**, não como especificação do que construir.

**Objetivo**: publicar Oportunidades de Venda abertas do SAP para a tabela `INTEGRACAO_ORCINC` do WBC, para que o vendedor monte o orçamento lá.

Fluxo passo a passo:
1. **Buscar** (`Button1_ClickAfter`): monta filtro opcional por nº da oportunidade OU por intervalo de datas, roda a query `buscaOportunidadesSAP` (HANA) e popula o grid.
   - Filtro fixo da query: `U_INO_StatusWBC` ainda não setado (`IFNULL(...,'0') = '0'`), `Status = 'O'` (aberta), `U_INO_IntegrouWBC` ainda `'N'`, e precisa ter endereço de cobrança cadastrado (`CRD1.Address = 'Cobrança'`).
2. Usuário marca linhas (checkbox "Selecionar") e clica em **Integrar** (`Button0_ClickAfter`):
   - Junta os `OpprId` selecionados numa lista `IN (...)`.
   - Roda `GetoportunidadesWBC` (HANA) para pegar dados completos: data, cliente, contato (telefone/e-mail), usuário responsável, tipo de venda (`U_INO_TipVndCod` resolvido via `UFD1`), representante (`OSLP.U_RepCod`), cidade/UF (prioriza endereço de entrega `CRD1`, cai para o cadastro geral `OCRD` se nulo), e o `Memo` da oportunidade.
   - Para cada oportunidade sem endereço (cidade/UF nulos) → **não integra**, só loga erro ("Oportunidade sem endereço de entrega selecionada").
   - Para as demais:
     a. Gera um **ID mascarado sequencial** consultando `GetNextMaskedNum` em `@INO_SETUP` (mas note: o incremento real do contador só acontece depois, no passo (c), dentro de `AtualizaDoc` — ver bug/risco na seção 8).
     b. Faz **INSERT direto** (`InsertPedidosWBC`) na tabela `INTEGRACAO_ORCINC` do **SQL Server WBC**, com os dados coletados.
     c. Chama `ProcessDefault.AtualizaDoc(..., Tipo="UpdateOrc")`, que:
        - Atualiza a Oportunidade no SAP (`SalesOpportunities`): `U_INO_IntegrouWBC = 'Y'`, `U_ORCNUM_WBC` e `U_ORCNUM_MASC` = ID gerado.
        - Incrementa `@INO_SETUP.U_Numero` via UDO `INO_SETUPOBJ` (`+1`).
     d. Grava log de sucesso em `INO_LOG`.

**Objetos SAP tocados**: `SalesOpportunities` (Update), UDO `INO_SETUPOBJ`, UDO `INO_LOG`.
**Tabelas WBC (SQL Server) tocadas**: `INSERT INTO INTEGRACAO_ORCINC`.

### 4.4 Módulo 2 — Integração de Pedidos WBC (`Form2` / `IntegraPedidoWBC.b1f.cs` + `ProcessDefault.cs`) — **prioridade 2, o mais complexo**

Esta tela tem **dois modos**, alternados pelo texto de `StaticText3`:

#### 4.4.1 Modo "Pedidos Novos" (busca `BuscaPedidosParaIntegrar`)
Lista pedidos de venda do SAP já vinculados a uma oportunidade integrada (`U_INO_IntegrouWBC='Y'`), ainda não processados (`U_INO_ProcessWBC='N'`), com `U_INO_Integrar='Y'`. Ao clicar em **Processar** (`Button0_ClickAfter`, ramo `"Pedidos Novos:"`), para cada linha selecionada:

1. Lê o orçamento completo do WBC (`GetOrcsWBC`, join `INTEGRACAO_ORCLST` + `INTEGRACAO_ORCITM`) → lista de `OportunidadeDoc`.
2. `ProcessDefault.preencheTabela` — grava (ou atualiza) o "espelho" do orçamento no UDO **`OrcDetalhe`**/`INO_ORC_LINHA` dentro do SAP (usa duas fontes de leitura no WBC: `GetTableValdixson`/`INTEGRACAO_ORCPRDARV` se já existir estrutura detalhada, senão `NovaTabelaQuot`/`INTEGRACAO_ORCIMP`). Retorna o `DocEntry` do UDO recém-criado (`getDocEntryTableValdixon` = `max(DocEntry)` de `@INO_ORCAM`).
3. Decide entre `UpdateTabPedido` (pedido "normal") ou `UpdateTabPedidoCong` (pedido "congelado", flag `U_INO_Congelado`) — dependendo do valor de `VerificaCong`/`VerificaTabUpdate`. Ambos setam campos de controle no `ORDR` (`U_INO_ORCAMENTO`, `U_INO_ProcessWBC`, `U_INO_Congelado`, `U_INO_VERSAOWBC`).
   - **Nota**: se `resultado == "Y"` (já tem tabela detalhe), o caminho correto seria `UpdatePedido` (que recria as linhas do pedido a partir de `GetItensSAP`+`GetPesoPedido`), mas a distinção exata entre os três métodos (`UpdateTabPedido`, `UpdateTabPedidoCong`, `UpdatePedido`) tem sobreposição de lógica no legado — **ponto a esclarecer/testar com o usuário antes de portar** (ver seção 9).
4. Busca a **estrutura de produto completa** do orçamento no WBC: `PegaEstruturaPrdWBC` sobre `INTEGRACAO_ORCPRDARV` (árvore multinível: `nivel`, `PrdCode`, `Quantidade`, `Total`, `Peso`, `idIntegracao_OrcPrdArv`).
5. Para cada item da estrutura ainda não cadastrado no SAP (`SelectCodigoItem` retorna 0):
   - Cria o item (`ProcessDefault.CriaItem`): decide grupo de item e tipo de material a partir de listas fixas (`Explosao.txt`, `Solda.txt`, `stringArray` de prefixos hardcoded no código) e do nível (item de nível abaixo do "topo" vira MRP/Make, senão Buy).
   - Se o `PrdArv` do item estiver na lista `Solda.txt`, roda `UpdateItem` (força grupo 332).
6. Agrupa os itens de nível 1 por `(GrpCode, OrcItm)` e, para cada grupo:
   - Resolve o **item "pai"** de produção (`SelectOrcItemSAP` sobre UDT `@INO_GRP_PRODUTOS`, que mapeia grupo do WBC → código de item SAP).
   - Calcula peso/valor da linha base do pedido (`BuscaMAXItemLinha`, `MaxPrecoPedido`).
   - Cria (se aplicável) um **Recurso** (`ProcessDefault.CriaResources`) representando rateio de custo de transporte/embalagem/montagem (`PegaValoresRecusos` sobre `INTEGRACAO_ORCCAB`), usado como linha de recurso na Ordem de Produção.
   - Cria a **Ordem de Produção** (`ProcessDefault.CriaOP`) do item pai, com uma linha de componente por item nível 1 da estrutura + a linha de recurso.
   - Para cada item da estrutura, chama recursivamente **`ChecaSemiAcabado`**: verifica (via query `newBuscaItem`/`verificaSemiAcabadoPeso` sobre `INTEGRACAO_ORCPRDARV`) se aquele item tem "filhos" no próximo nível; se tiver, cria uma **nova Ordem de Produção** para esse semiacabado (`CriaOPSA`) e repete recursivamente — **é aqui que a árvore de estrutura do orçamento vira uma cascata de Ordens de Produção encadeadas por nível**. É a parte de maior complexidade algorítmica de todo o addon.
   - Ao final de cada OP criada com sucesso, `ProcessDefault.AtualizaDoc(..., Tipo="OP")` grava o nº da OP no campo de usuário `U_INO_OP` da linha do pedido correspondente; e `Tipo="Sucesso"` marca `U_INO_ProcessWBC='Y'` no pedido.
   - Em caso de erro em qualquer etapa, grava em `INO_LOG` e segue para o próximo item/pedido (não aborta o lote).

#### 4.4.2 Modo "Pedidos Integrados" (busca `buscaPedidosIntegrados`, botão vira "Atualizar")
Para pedidos já processados (`U_INO_ProcessWBC='Y'`): permite **reprocessar** o vínculo pedido↔oportunidade (`ProcessDefault.AddPedidoOportunidade` — atualiza status da oportunidade para "Ganha"/`sos_Sold` e vincula o documento de pedido como "documento seguinte" da oportunidade) e **cancela as Ordens de Produção antigas ainda planejadas** (`BuscaOPS` + `ProcessDefault.CancelaOP`) antes de recriar.

**Objetos SAP tocados**: `Documents` (Orders — Update/GetByKey), `Items` (Add/Update), UDO `OrcDetalhe`/`INO_ORC_LINHA`, `ProductTrees` (BOM), `ProductionOrders` (Add/Update/Cancel), `Resource` (via `ResourcesService`), `SalesOpportunities` (Update), UDO `INO_LOG`.
**Tabelas WBC tocadas**: somente leitura (`INTEGRACAO_ORCLST`, `INTEGRACAO_ORCITM`, `INTEGRACAO_ORCPRDARV`, `INTEGRACAO_ORCIMP`, `INTEGRACAO_ORCCAB`).

### 4.5 Módulo 3 — Manutenção de OP (`Views/ManutencaoOp.b1f.cs`) — **prioridade 3**

Tela de suporte operacional para as Ordens de Produção geradas pelo módulo 2:
- **Buscar** (`OPSManutencao`): lista OPs vinculadas a um `DocNum` de pedido, com filtro opcional por intervalo de nº de OP e por intervalo de status.
- **Selecionar todos** (toggle Y/N em lote).
- **Liberar / Planejar / Cancelar** (`mudaStatus("l"/"p"/"c")`): atualiza `ProductionOrderStatus` da(s) OP(s) selecionada(s) via `updateOP`.
- **Finalizar (Encerrar)** (`Button4_ClickAfter`): para cada OP selecionada com quantidade apontada < planejada:
  1. `corrigeOP` — muda status para "Released" e força `ProductionOrderIssueType = Manual` em todas as linhas (pré-requisito para poder dar baixa manual).
  2. `SaidaEnsumo` — cria um documento de **Saída de Mercadoria por Ordem de Produção** (`oInventoryGenExit`) com `BaseEntry`/`BaseLine` apontando para os componentes pendentes (`QtdeFaltanteOP`).
  3. `EntradaProduto` — cria um documento de **Entrada de Mercadoria** (`oInventoryGenEntry`) com `BaseEntry` = OP, dando entrada do produto acabado/semiacabado.
  4. Ao final, chama `mudaStatus("f")` para fechar (`Closed`) todas as selecionadas.
- Há um método de teste esquecido em produção (`cancelapedidoteste`, cancela documentos de pedido de venda por um range fixo de `DocEntry` 13302–13309) — **não portar**, é claramente código de teste manual do desenvolvedor.

**Objetos SAP tocados**: `ProductionOrders` (Update/status), `Documents` (`oInventoryGenExit`, `oInventoryGenEntry` — Add).

### 4.6 Módulo 4 — Romaneio (`Views/Romaneio.b1f.cs`) — **prioridade 4**

O mais "manual"/interativo dos quatro: uma tela de duas grades (esquerda = disponível, direita = selecionado), ao estilo *transfer list*, para montar remessas parciais de um pedido.

1. **Buscar** (`OpsPedidoTst`): para um `DocNum` de pedido com entrega múltipla habilitada (`U_INO_EntregaMultipla != 'N'`), lista os semiacabados disponíveis por OP concluída (`CmpltQty`) descontando o que já foi usado em itens compostos anteriores (subquery sobre `OITT`/`ITT1` filtrando pelo sufixo do código do item, que carrega o nº do pedido).
2. Botões `>`, `>>`, `<`, `<<` movem linhas entre a grade "disponível" (`dt`) e a grade "selecionado" (`dt2`), com suporte a **quantidade parcial customizada** (campo `EditText1`) — valida que não exceda o disponível.
3. **Finalizar** (`Button5_ClickAfter`): agrupa os itens selecionados por `(Item de Origem, Código de Origem)` em "remessas". Para cada remessa:
   - `criacaoItem`: cria dinamicamente um **novo item SAP** "composto" (`ItemCode = <origem>_F_<linha>_<pedido>`), com sua própria estrutura de produto (`ProductTrees`) contendo os semiacabados selecionados.
   - `criaOp`: cria uma OP para esse item composto (planejada 1 un.), libera (`Released`) e retorna o `DocEntry`.
   - `SaidaEnsumo` + `EntradaMercadoria`: baixa os insumos e dá entrada do item composto (mesmos objetos do módulo 3).
   - `AtualizaPedido`: adiciona uma **nova linha** no pedido de venda (`RDR1`) para esse item composto (peso proporcional calculado por `QtdTotal`), e reduz o peso da linha de origem (`AtualizaLinhaAntiga`, hoje simplificado a não subtrair nada — o corpo antigo está comentado).
   - `FechaPedido` (não é chamado no fluxo atual, está morto): fecharia o pedido quando não sobrar OP pendente (`OpRestante`).

**Objetos SAP tocados**: `Items` (Add), `ProductTrees` (Add), `ProductionOrders` (Add/Update), `Documents` (Orders — add linha; `oInventoryGenExit`/`oInventoryGenEntry` — Add).

---

## 5. Modelo de dados

### 5.1 Tabelas/objetos padrão do SAP B1 (HANA) usados

| Tabela/Objeto | Uso |
|---|---|
| `OOPR` / `OCPR` / `OCRD` / `CRD1` / `OUSR` / `OSLP` | Oportunidade de venda, contato, parceiro de negócio, endereços, usuário, vendedor |
| `ORDR` / `RDR1` | Cabeçalho/linha do Pedido de Venda |
| `OITM` / `OITW` / `OWHS` | Cadastro de item, saldo por depósito, depósitos |
| `OITT` / `ITT1` | Estrutura de produto (BOM) — objeto `ProductTrees` |
| `OWOR` / `WOR1` | Ordem de Produção — objeto `ProductionOrders` |
| `ORSC` | Recursos — objeto `Resource`/`ResourcesService` |
| `UFD1` | Valores de campo de usuário definidos por tabela (usado p/ resolver descrição de `U_INO_TipVndCod`) |

### 5.2 Objetos de usuário (UDT/UDO) próprios do addon

| Nome | Tipo | Campos relevantes | Uso |
|---|---|---|---|
| `@INO_SETUP` / `INO_SETUPOBJ` | UDT + UDO | `Code` (fixo `"1"`), `U_Mascara`, `U_Numero` | Gera o ID sequencial mascarado publicado no WBC |
| `INO_LOG` | UDO | `U_OrcNum`, `U_TipoDocumento`, `U_Status`, `U_Tipo`, `U_Mensagem` | Log de auditoria/erro de toda integração |
| `OrcDetalhe` / `INO_ORC_LINHA` (filha) | UDO com tabela filha | `U_INO_COD`, `U_ORCVAL*`, `U_ORCIMP_*`, `U_TABELA_PRECO`, linha: `U_INO_PESO/CODIGO/Qtde/PROD/COR/PRECO/TOTAL/NIVEL/LINHA/ORCITM/ORCTXT` | Espelho do orçamento do WBC gravado dentro do SAP |
| `@INO_GRP_PRODUTOS` | UDT | `Code`, `U_INO_ItemSAP` | Mapeia grupo de produto do WBC → item SAP "pai" de produção |

### 5.3 Campos de usuário (UDF) por entidade SAP

| Entidade | Campos |
|---|---|
| `SalesOpportunities` | `U_INO_StatusWBC`, `U_INO_IntegrouWBC`, `U_ORCNUM_WBC`, `U_ORCNUM_MASC` |
| `Orders` (cabeçalho, `ORDR`) | `U_INO_ProcessWBC`, `U_INO_Congelado`, `U_INO_VERSAOWBC`, `U_INO_ORCAMENTO`, `U_INO_COTWBC`, `U_INO_TIPO_MT`, `U_INO_VL_MT`, `U_INO_COM`, `U_INO_VL_COM`, `U_INO_Integrar`, `U_INO_EntregaMultipla`, `U_INO_UpdateDetalhe` |
| `Orders` (linha, `RDR1`) | `U_INO_OP`, `U_INO_Id_IntWBC`, `U_INO_ORCITM`, `U_INO_D_Adicionais`, `U_xPed`, `U_nItem`, `U_INO_Composicao`, `U_INO_COR`, `U_INO_ORCITM_ORG` |
| `ProductionOrders` (cabeçalho/linha) | `U_INO_LinhaRef`, `U_INO_PESO`, `U_INO_DESC` |
| `Items` | `U_INO_CODARV`, `U_INO_EXPL_SOLDA` |

### 5.4 Banco externo WBC (SQL Server, DB `WBCCAD`)

Tabelas `INTEGRACAO_*` (lidas e, num único caso, escritas pelo addon):

| Tabela | Uso |
|---|---|
| `INTEGRACAO_ORCINC` | **Escrita** — publica a oportunidade para o WBC (módulo 1) |
| `INTEGRACAO_ORCCAB` | Cabeçalho do orçamento (comissão, montagem, prazo, forma de pagamento) |
| `INTEGRACAO_ORCLST` / `INTEGRACAO_ORCITM` | Lista/itens do orçamento fechado |
| `INTEGRACAO_ORCPRD` | Produtos do orçamento (nomes alternativos de código) |
| `INTEGRACAO_ORCPRDARV` | **Árvore/estrutura multinível** do orçamento (a mais importante e mais complexa) |
| `INTEGRACAO_ORCIMP` | Tabela "resumo" usada quando ainda não existe detalhamento em `ORCPRDARV` |
| `INTEGRACAO_ORCSIT` | Situação/status do orçamento (usada em query hoje comentada, `WBCStatus61`) |

> ⚠️ **Credenciais**: o `app.config` atual guarda usuário/senha do SQL Server **em texto puro** (`sap_user` / senha visível) e o IP do servidor. Na nova aplicação isso **precisa** ir para variáveis de ambiente / secrets manager — nunca versionar credenciais reais no repositório Python. Ver seção 8.

---

## 6. Arquitetura proposta em Python

### 6.1 Visão geral de camadas

```
Web (FastAPI + Jinja2, botões manuais)
        │
        ▼
Serviços de módulo (oportunidades / pedidos_wbc / manutencao_op / romaneio)
   — replicam ProcessDefault.cs 1:1 na fase 1 —
        │
        ├──► ServiceLayerClient  ──HTTPS──► SAP Service Layer ──► HANA (SBOALTAMIRAPROD)
        │        (login/sessão, CRUD, UDOs, SQLQueries/Views)
        │
        ├──► HanaDirectReader (opcional, hdbcli) ──► HANA  (só leitura, casos pontuais)
        │
        └──► WbcSqlServerClient (pyodbc) ──► SQL Server WBC (WBCCAD) — leitura + 1 INSERT
```

### 6.2 Por que FastAPI

- Assíncrono nativo → boa combinação com chamadas HTTP repetidas à Service Layer.
- Templates simples (Jinja2) resolvem bem o requisito "botões manuais espelhando o legado", sem exigir SPA.
- Tipagem com Pydantic mapeia bem os `Models` do C# (`Pedidos`, `OportunidadeDoc`, `EstruturaPrd`, `ORCCAB`, `ORCPRD`, `NewOrcPrd`, `ListagemOP`, `SeparacaoRemessa` etc.).

*(Se preferir Flask/Django, a divisão em módulos/serviços abaixo continua válida — só troca a camada de rotas.)*

### 6.3 Sessão da Service Layer

- `POST /b1s/v1/Login` com `{CompanyDB, UserName, Password}` → devolve cookies `B1SESSION` + `ROUTEID`.
- Sessão expira (padrão 30 min de inatividade) → o cliente precisa detectar HTTP 301/401 e refazer login automaticamente (retry transparente).
- Certificado: ambiente atual provavelmente usa certificado self-signed (`verify=False` já aparece no rascunho anterior) — decidir com o usuário se em produção deve validar certificado (recomendado) e importar CA.

### 6.4 Leitura híbrida do HANA — `hdbcli` primeiro (decisão revista em 16/09/2026)

Regra prática **vigente**:
- **Leitura → `hdbcli` direto, por padrão.** Vale tanto para as queries pesadas herdadas do `Querys.resx` (`GetoportunidadesWBC`, `PegaEstruturaPrdWBC`, `OpsPedidoTst`) quanto para as simples. É o caminho preferencial, não um paliativo.
- **Service Layer na leitura só quando o `hdbcli` não resolver** — tipicamente quando é preciso o objeto de negócio montado pelo B1, e não linhas de tabela (ex.: ler um `Order` com suas `DocumentLines` para depois atualizá-lo, em `_atualiza_doc`/`_update_pedido`), ou quando a informação não está exposta de forma direta nas tabelas.
- **Escrita continua exclusivamente pela Service Layer.** O `HanaDirectReader` é somente leitura, por decisão de arquitetura (seção 6.1): auditoria, campos calculados e validações do próprio B1 dependem de a escrita passar por lá. Isso não mudou e não deve mudar junto com esta decisão.

> **O que essa revisão substitui**: a regra original (14/09/2026) era o inverso — Service Layer OData para leitura simples, Calculation Views + `SQLQueries` para joins complexos, e `hdbcli` apenas como "ponte temporária" a ser eventualmente substituída por uma View. Aquele plano exigia criar e manter Views no HANA para cada query complexa; a regra atual dispensa isso.
>
> **Consequência prática**: o código já estava alinhado com a regra nova antes dela existir — todo o módulo 2 lê via `hana_reader.fetch_all(...)`, e a Service Layer aparece na leitura só nos poucos pontos em que o objeto de negócio é necessário. Então esta revisão **ratifica o que já foi construído**, em vez de exigir retrabalho. O que sai de cena é a obrigação (que estava escrita nas docstrings do `hana_reader.py`) de documentar, para cada leitura direta, qual View deveria um dia substituí-la.

### 6.5 Escrita no WBC (SQL Server)

Mantém-se **igual ao legado**: `pyodbc` (ou `pymssql`) com o driver ODBC do SQL Server, mesma string de conexão (host/porta/usuário/senha vindos de variável de ambiente), reproduzindo exatamente:
- Leitura das tabelas `INTEGRACAO_*`.
- O único `INSERT` (em `INTEGRACAO_ORCINC`, módulo 1).

### 6.6 Estrutura de diretórios do projeto Python

```
python_app/
├── README.md
├── requirements.txt
├── .env.example
├── app/
│   ├── main.py                     # cria o FastAPI app, inclui os routers
│   ├── config.py                   # Settings (pydantic-settings) lidas de .env
│   ├── core/
│   │   ├── service_layer_client.py # login/sessão/retry + helpers CRUD + UDO
│   │   ├── hana_reader.py          # leitura direta opcional via hdbcli
│   │   ├── sqlserver_client.py     # equivalente a SQLConnection.cs (pyodbc)
│   │   ├── audit_log.py            # equivalente a ProcessDefault.preencheLog (grava em INO_LOG)
│   │   └── exceptions.py
│   ├── modules/
│   │   ├── oportunidades/          # MÓDULO 1 — prioridade 1 (implementado nesta entrega, ver 7.1)
│   │   ├── pedidos_wbc/            # MÓDULO 2 — prioridade 2 (esqueleto)
│   │   ├── manutencao_op/          # MÓDULO 3 — prioridade 3 (parcial: liberar/replanejar)
│   │   └── romaneio/               # MÓDULO 4 — prioridade 4 (esqueleto)   <- único pendente
│   ├── templates/                  # Jinja2 (base.html + 1 por módulo)
│   └── static/
└── tests/
```

Cada pasta de módulo segue o mesmo padrão:
- `schemas.py` — modelos Pydantic (equivalentes às classes em `Models/`).
- `queries.py` — strings de query (equivalente ao `Querys.resx`), organizadas por HANA vs. SQL Server, com o texto original preservado como comentário para conferência.
- `service.py` — regras de negócio (equivalente a `ProcessDefault.cs` + o code-behind de cada `.b1f.cs`).
- `router.py` — rotas web (equivalente ao clique de botão).

---

## 7. Estado atual da implementação (o que já existe no repositório)

### 7.0 Interface de linha de comando (`app/cli.py`)
Adicionada em 15/09/2026, a pedido do usuário, como a forma de validar cada módulo antes/independente da UI web: `python -m app.cli <modulo> <comando>` (ex.: `python -m app.cli pedidos-wbc buscar`, `python -m app.cli conexoes testar`). Implementada com Typer; cada comando chama a mesma função de `service.py` usada pela rota web correspondente — **não existe lógica de negócio duplicada entre CLI e web**. Inclui também `conexoes testar`, que checa em sequência o login na Service Layer, uma leitura direta no HANA e uma leitura no SQL Server do WBC — é o primeiro comando a rodar em qualquer ambiente novo. Ver `python_app/README.md` para a lista completa de comandos.

### 7.1 Módulo 1 — Oportunidades: **REMOVIDO do porte (22/09/2026)**
Esteve implementado (lógica, CLI e tela web) de 14 a 22/09/2026 e **nunca chegou a ser validado contra um ambiente real** — a integração com Service Layer/SQL Server jamais foi executada. Removido a pedido do Anderson antes de qualquer validação; ver seção 7.36 para o que saiu e o que ficou.

### 7.2 Módulos 3 e 4
*(estado original desta seção: ambos eram esqueleto com queries transcritas e docstrings apontando o método/linha do C# a replicar.)*

- **Módulo 3 — Manutenção de OP: completo, validado, pendente de RETESTE** (16-21/09/2026, seções 7.17, 7.18, 7.26 a 7.32). `buscar`, `liberar`, `replanejar` e `encerrar` na CLI. Duas ressalvas, deliberadamente não arredondadas:
  - **`liberar` nunca foi executado como comando.** A função (`muda_status`) e o valor (`boposReleased`) estão provados separadamente; a combinação é inferência.
  - **O código mudou depois da execução validada** (7.30). Vieram, nessa ordem: exclusão de OP cancelada (7.31), liberação condicional ao status (7.32) e os rótulos novos da CLI. A execução bem-sucedida usou liberação **incondicional** — ou seja, o caminho "OP já liberada não recebe novo status" tem teste mas nunca rodou contra o SAP. Reteste mínimo: um `encerrar` numa OP Planejada e um numa Liberada.
- **Módulo 4 — Romaneio: ainda esqueleto.** O `service.py` mantém as docstrings apontando exatamente qual método/linha do C# cada função deve replicar, para começar direto na tradução.

### 7.3 Não incluído nesta entrega (decisão consciente)
- Autenticação/autorização de usuários da aplicação web (o legado não tem — herda do usuário logado no SAP B1 Client). **Precisa ser definida** (ver seção 9).
- Testes automatizados de integração (dependem de um HANA/WBC de homologação acessíveis).
- Deploy/infra (Docker, systemd, nginx) — pode ser adicionado depois que o core funcionar.

### 7.4 Módulo 2 — Pedidos WBC: **lógica implementada, exposta via CLI** (15/09/2026)
Portado nesta sessão (a pedido do Anderson: "vamos esquecer a parte de oportunidade de vendas por enquanto e partir para pedido de vendas") seguindo a ordem de prioridade da seção 6.1 (Oportunidades → **Pedidos WBC** → Manutenção de OP → Romaneio). É o módulo mais complexo do addon original: ~1900 linhas de C# entre `Controllers/ProcessDefault.cs` e `Views/IntegraPedidoWBC.b1f.cs`, com uma cascata recursiva de Ordens de Produção por semiacabado (ver seção 4.4.1, passo 6).

**Como foi feito**: como o código é grande demais para reler tudo manualmente com segurança, primeiro extraí (com um subagente, extração puramente verbatim, sem interpretação) os 15 métodos C# relevantes + os dois branches de `Button0_ClickAfter` para um arquivo de referência, e só depois portei função por função lendo esse material eu mesmo. As três queries que faltavam nas já transcritas (`PegaLinha`, `GetLinha`, `pegaDocNumPed`) foram extraídas direto do `Querys.resx` original (não inventadas).

**Decisões e descobertas importantes**:
- **Pergunta 1 da seção 9 (antiga) resolvida por leitura do código, não é ambígua**: a escolha entre `UpdateTabPedido`/`UpdateTabPedidoCong`/`UpdatePedido` segue exatamente esta árvore (`IntegraPedidoWBC.b1f.cs`, ~linha 258-283): primeiro checa `U_INO_Congelado` (`VerificaCong`) — se "N", checa `U_INO_UpdateDetalhe` (`VerificaTabUpdate`, chamado de `resultado` no código): `resultado != "Y"` → `UpdateTabPedido`; `resultado == "Y"` → `UpdatePedido`. Se `U_INO_Congelado != "N"` → `UpdateTabPedidoCong`. Portado em `atualiza_pedido_tabela` (`app/modules/pedidos_wbc/service.py`).
- **Bug de mapeamento de colunas evitado**: `SQLConnection.PegaEstruturaPrd` lê a query `PegaEstruturaPrdWBC` **por posição, não por nome** — a última coluna (um `PRDCOD` recalculado via `INTEGRACAO_ORCPRD`) vira `PrdCode`, a coluna 4 (`PRDCOD` original de `INTEGRACAO_ORCPRDARV`) vira `PrdArv`, e a coluna `num_row` (`ROW_NUMBER()`) vira `linhaOrc`. Fácil de inverter por engano lendo só os nomes das colunas — replicado com mapeamento posicional explícito em `busca_estrutura_produto`.
- **`CriaItem` faz duas checagens de "solda" diferentes**: internamente, ao decidir grupo 332 vs. 358 na criação, compara `ItemCode` (= `PrdCode`) contra `Solda.txt`; já o loop externo em `Button0_ClickAfter`, que decide se chama `UpdateItem` para forçar grupo 332, compara `PrdArv` (não `PrdCode`) contra a mesma lista. Duas comparações diferentes contra o mesmo arquivo — ambas replicadas separadamente (`_cria_item` vs. `_cria_ou_atualiza_item` em service.py).
- **`preencheTabela` tem uma particularidade no caminho de fallback** (quando o orçamento não tem estrutura detalhada em `INTEGRACAO_ORCPRDARV`): o `foreach` original tem um `return` dentro do loop, então só processa o **primeiro** registro de `NovaTabelaQuot`, nunca os demais. Replicado de propósito (fidelidade fase 1).
- **`CriaOP` não cria a OP quando `entregaMultipla == "Y"`** (deixa `retorno = 0` sem chamar `Add()`) — parece uma condição invertida (o nome sugere que "entrega múltipla" deveria ser tratada, não pulada), mas é o que o C# original faz. Preservado; ver pergunta nova abaixo.
- **Gap real, não inventado**: `Resources/Solda.txt` (lista de códigos completos usada por `CriaItem`/`UpdateItem` para decidir grupo 332) não veio no projeto enviado. `app/modules/pedidos_wbc/listas_fixas.carregar_solda()` tenta ler `app/modules/pedidos_wbc/resources/Solda.txt`; até o Anderson enviar o conteúdo, a lista fica vazia (comportamento seguro — nenhum item cai em 332 por engano). `Explosao.txt` (180 códigos, usado só para uma flag informativa `U_INO_EXPL_SOLDA`) veio completo e já está embutido no código.
- **Nomes de entidade/campo da Service Layer não confirmados contra o ambiente real**: `Resources` (Recurso — `CriaResources`) e a coleção de linhas de `SalesOpportunities` (`AddPedidoOportunidade`) usam os nomes mais prováveis segundo a documentação SAP, mas — diferente de `ProductionOrders`/`Items`/`Orders`, mais padronizados — precisam ser confirmados contra o `$metadata` real da Service Layer do Anderson antes do primeiro teste (marcado com `TODO` no código exato).
- Validado estruturalmente (sem ambiente real disponível aqui): rodei os dois fluxos completos (`processar_pedidos_novos`/`reprocessar_pedidos_integrados`) com HANA/WBC/Service Layer simulados (mocks) para garantir que a cadeia inteira de chamadas — incluindo a recursão de semiacabado — executa sem erro de referência/atributo. Isso NÃO substitui o teste real (seção 10, passo 4): comparar registro a registro com o addon legado rodando em paralelo continua obrigatório antes de produção, e é ainda mais crítico aqui por ser o módulo mais arriscado.

### 7.5 `.env` real e descoberta do projeto "WBCPython" (15/09/2026)
O Anderson copiou para dentro de `python_app/` um `.env` de um **outro projeto/pasta dele, mais
maduro, chamado "WBCPython"** (tem `ai_spec/02_data_model.md` próprio, CLI `wbcpython ciclo
--ambiente ...`, painel Streamlit, worker agendado e banco de tracking — nada disso existe
ainda neste `python_app`). Ele confirmou que esse projeto já existe em outra pasta do
computador dele e decidiu, por ora, **não reconciliar os dois** — só ajustar este `python_app`
para ler corretamente as credenciais desse `.env`.

O que foi feito nesta sessão (escopo explicitamente limitado pelo Anderson a "adaptar a leitura
das credenciais"):
- `app/config.py` reescrito para usar exatamente os nomes de variável do `.env` real: `WBC_SQL_HOST/PORT/DATABASE/USERNAME/PASSWORD/DRIVER` (antes `WBC_SQLSERVER_*`), `HANA_USERNAME` (antes `HANA_USER`), mais `HANA_SCHEMA`, `SL_CA_BUNDLE`, `SL_TIMEOUT_SECONDS`, `WBC_ENVIRONMENT`, `WBC_BLOCK_PRODUCTION_WRITES`, `WBC_PRODUCTION_COMPANY_DB`. O caminho do `.env` agora é resolvido de forma absoluta (`Path(__file__).resolve().parent.parent / ".env"`), então funciona independente de onde o `python -m app.cli`/`uvicorn` for chamado.
- `app/core/sqlserver_client.py` e `app/core/hana_reader.py` atualizados para os novos nomes de campo (estavam quebrados — `AttributeError` — logo após a reescrita do `config.py`; corrigido e testado).
- `HanaDirectReader._connect()` agora executa `SET SCHEMA` quando `HANA_SCHEMA` está preenchido (o `.env` real observa que, no protótipo original do WBCPython, as views eram lidas no schema de produção mesmo com a escrita em homologação — ver pergunta nova abaixo, item 7).
- Campos do `.env` que pertencem só ao WBCPython (`TRACKING_DB_URL`, `WORKER_*`, `PAINEL_*`, `MESES_DE_JANELA*`, `WBC_HOMOLOG_*`, `WBC_PROD_*`) foram incluídos em `Settings` só para não perder o valor caso o `.env` seja reaproveitado depois, mas **nenhum código deste projeto os lê ainda**.
- `Settings.is_production` foi adicionado (compara `SL_COMPANY_DB` com `WBC_PRODUCTION_COMPANY_DB`), mas **ainda não é usado por nenhuma trava de escrita real** — isso ficou fora do escopo desta sessão (ver pergunta nova abaixo, item 8). Não existe ainda uma exceção `ProductionWriteBlocked`.
- `.env.example` reescrito para refletir a estrutura real (sem segredos).
- `pytest tests/ -q` seguiu passando (5 testes) após as mudanças; validado manualmente que `Settings()` carrega os valores certos e que `WbcSqlServerClient._connection_string()`/`HanaDirectReader` constroem sem erro a partir do `.env` real.

### 7.6 Módulo 2 — verificação de reprocessamento / OP duplicada (15/09/2026, a pedido do Anderson)

O Anderson perguntou como o sistema se comporta ao rodar `pedidos-wbc processar-novos` duas vezes para o mesmo pedido. Resposta: **o C# original não tem nenhuma proteção em código contra isso** — a única proteção era a tela `Form2`, cuja grade "Pedidos Novos" listava só pedidos com `U_INO_ProcessWBC='N'` (`BuscaPedidosParaIntegrar`), então um pedido já processado simplesmente sumia da lista. Como a CLI recebe o `opp_id` direto (sem passar pela grade), essa proteção não existia mais nesta reescrita — um gap real, identificado nesta sessão, não presente no addon legado nem antes desta data. O Anderson pediu para implementar a verificação; feito nesta sessão.

**O que foi adicionado** (`app/modules/pedidos_wbc/service.py` + `queries.py`, sem equivalente no C# original):
- `_verifica_process_wbc(hana_reader, doc_entry)` — lê `ORDR."U_INO_ProcessWBC"`. Em `processar_pedidos_novos`, se vier `"Y"` e `force=False` (padrão), o pedido é **pulado** (entra em `com_erro` com uma mensagem explicando o motivo) em vez de ser reprocessado.
- `_op_ja_existe_para_item(hana_reader, doc_num_ped, item_code)` — busca em `OWOR` uma Ordem de Produção **não cancelada** (`"Status" <> 'C'`) com `OriginNum = doc_num_ped` e `ItemCode = item_code`. Em `_processa_grupo_producao`, se encontrar uma e `force=False`, **pula a criação da nova OP** para aquele item (grava um log "Aviso" via `preenche_log` e não chama `cria_ordem_producao`), evitando duplicar Ordens de Produção.
- Flag `force: bool = False` propagada por `processar_pedidos_novos` → `_processa_grupo_producao`, e exposta na CLI como `--force` (`python -m app.cli pedidos-wbc processar-novos 12345 --force`) para quando o Anderson quiser reprocessar de propósito (ex.: depois de corrigir algo manualmente, ou combinado com `reprocessar-integrados`, que cancela as OPs antigas antes).
- Validado com um smoke test mockado (HANA/Service Layer simulados): confirmado que (a) `force=False` + pedido já processado gera o skip esperado em `com_erro`; (b) `force=True` ignora essa checagem; (c) o item com OP não cancelada já existente é pulado sem chamar `cria_ordem_producao`, registrando o log de aviso.
- `pytest tests/ -q` seguiu passando (5 testes) após as mudanças.

Como sempre neste guia, essa checagem está claramente marcada nos docstrings do código como adicionada em 15/09/2026 e ausente do C# original — não é uma "correção silenciosa" do comportamento legado, é uma feature nova pedida explicitamente pelo Anderson.

### 7.7 Primeiros testes reais contra homologação (15/09/2026)

O Anderson começou a validar o módulo 2 contra o ambiente real de homologação. Dois problemas de ambiente (não de lógica de negócio) apareceram e foram corrigidos:

- **Faltava aplicar o `{filtro}` da query `BUSCA_PEDIDOS_PARA_INTEGRAR`** em `buscar_pedidos_para_integrar` (nova função/comando `pedidos-wbc buscar`, adicionados nesta sessão para listar pedidos elegíveis a testar) — a string ia para o HANA com o literal `{filtro}` sem `.format()`, causando erro de sintaxe. Corrigido chamando `.format(filtro="")` (mesmo padrão já usado em `oportunidades/service.py`).
- **Conexão com o SQL Server do WBC falhava com `certificate verify failed: self-signed certificate`**: o ODBC Driver 18 passou a exigir TLS com validação de certificado por padrão (diferente do Driver 17 e do `SqlClient` .NET do addon legado, que não validavam), e o servidor WBC usa certificado autoassinado. Corrigido adicionando `TrustServerCertificate=yes` na connection string (`WbcSqlServerClient._connection_string`), controlável por uma nova opção `Settings.wbc_sql_trust_server_certificate` (padrão `True` — não precisa nada novo no `.env` real do Anderson para funcionar).
- `pytest tests/ -q` seguiu passando (5 testes) após as duas correções.

Também notado en passant (não corrigido, é só rótulo de comentário): em `queries.py`, `BUSCA_PEDIDOS_PARA_INTEGRAR` e `BUSCA_PEDIDOS_INTEGRADOS` estão fisicamente na seção comentada como `--- MSSQL (WBC) ---`, mas são queries **HANA** de verdade (identificadores entre aspas duplas, `IFNULL`, tabelas `ORDR`/`OPR1`/`OOPR`) — e são executadas via `hana_reader`, corretamente. É só uma mislabel de organização do arquivo, sem efeito funcional; não fizemos a reorganização ainda para não misturar com as mudanças acima.

### 7.8 Bugs reais de porte encontrados testando `processar-novos` contra homologação (15/09/2026)

Continuando os testes do Anderson, dois bugs genuínos da reescrita Python (não comportamento herdado do C#) apareceram e foram corrigidos:

**1. Colunas sem alias colidindo em `dict` (`IndexError: list index out of range`)**

Várias queries deste módulo foram herdadas do C# original já pensadas para leitura **posicional** (`SqlDataReader.GetValue(i)`/`ExecuteSelectNew`, sem nomear cada coluna) — ex.: `NOVA_TABELA_QUOT` tem 35 `ISNULL(campo,0)` seguidos sem `AS`, `NOVA_TABELA_QUOT_LINHA` tem várias colunas `''` literais e `ISNULL(...)` sem alias, `VERIFICA_SEMI_ACABADO_PESO` tem 3 `REPLACE(CONVERT(...))`/`CONVERT(...)` sem alias, e `BUSCA_MAX_ITEM_LINHA` (HANA) tem 3 `max(...)` sem alias. `WbcSqlServerClient.fetch_all`/`HanaDirectReader.fetch_all` montam um `dict` fazendo `zip(nomes_de_coluna, valores)` — para essas queries, o driver ODBC devolve nome vazio/repetido para cada coluna sem alias, e colunas com o mesmo nome **colidem no dict**, perdendo valores silenciosamente. O `ExecuteSelectNew` do C# original não tinha esse problema porque lia direto por índice, sem nunca passar por um dicionário nomeado.
- **Correção**: adicionado `fetch_all_values()` em ambos os clientes (`sqlserver_client.py`/`hana_reader.py`), que devolve cada linha como lista posicional (`list(row)`) em vez de `dict`. As quatro funções afetadas (`_busca_header_nova_tabela_quot`, `_busca_nova_tabela_quot_com_linhas`, o trecho de `checa_semi_acabado` que lê `VERIFICA_SEMI_ACABADO_PESO`, e o trecho de `_processa_grupo_producao` que lê `BUSCA_MAX_ITEM_LINHA`) passaram a usar `fetch_all_values` em vez de `fetch_all` + `list(row.values())`.
- Outras queries com acesso posicional que **não** tinham esse problema (colunas de fato distintas, sem colisão de nome — ex. `PEGA_ESTRUTURA_PRD_WBC` via `tst.*`, `PEGA_VALORES_RECURSOS` via `ORCBAS1/2/3`) foram deixadas como estavam.

**2. `Decimal` não serializável em JSON ao criar a Ordem de Produção**

`hdbcli`/`pyodbc` devolvem `Decimal` para colunas numéricas do HANA/SQL Server; esse valor chegava sem conversão até o corpo da requisição `POST /ProductionOrders` (campo `PlannedQuantity`), e o `httpx`/`json.dumps` padrão não sabe serializar `Decimal` (`TypeError: Object of type Decimal is not JSON serializable`). A DI API do C# original não tinha esse problema porque aceita `decimal`/`DateTime` nativamente nas propriedades COM, sem passar por serialização JSON.
- **Correção**: adicionada uma função `_json_safe()` em `service_layer_client.py` que converte `Decimal` → `float` e `date`/`datetime` → ISO string recursivamente (dicts e listas aninhados), aplicada em `ServiceLayerClient.post`/`.patch` antes de qualquer requisição — corrige de forma geral para qualquer entidade criada/atualizada pela Service Layer, não só `ProductionOrders`.

`pytest tests/ -q` seguiu passando (5 testes) após as duas correções.

### 7.9 Nomes de propriedade da Service Layer e divergência no `U_INO_LinhaRef` (15/09/2026)

Com as correções anteriores, a verificação de reprocessamento da seção 7.6 **funcionou na prática** (o pedido 84301 já estava com `U_INO_ProcessWBC='Y'` e foi corretamente bloqueado, com a mensagem orientando o `--force`). Rodando com `--force`, o fluxo avançou até as escritas reais na Service Layer e expôs mais três pontos — desta vez resolvidos **relendo o C# original** (`ProcessDefault.cs`, trazido de volta do computador do Anderson), não por tentativa e erro:

**1. `IssueType` → `ProductionOrderIssueType` (erro bloqueante, corrigido)**

`POST /ProductionOrders` falhava com `Property 'IssueType' of 'ProductionOrderLine' is invalid`. O nome correto da propriedade é `ProductionOrderIssueType` — confirmado no C# original (`OrdemPrducao.Lines.ProductionOrderIssueType = BoIssueMethod.im_Backflush`, ProcessDefault.cs linhas 546/554/704/717). O **valor** que eu já usava (`im_Backflush`) está certo: embora o C# atribua `im_Manual` primeiro em alguns pontos (linhas 546, 564), ele **sobrescreve com `im_Backflush` logo antes do `Add()`** (linhas 554, 704, 717), então o valor efetivo é sempre `im_Backflush`. Corrigido nos três pontos (`cria_ordem_producao`, a linha de recurso, e `cria_ordem_producao_semi_acabado`).

**2. Divergência de fidelidade no `U_INO_LinhaRef` do cabeçalho da OP (corrigida)**

Ao reler o `CriaOP` original, notei algo que meu porte tinha achatado: o C# reatribui `OrdemPrducao.UserFields.Fields.Item("U_INO_LinhaRef").Value = item.linha` **dentro do `foreach` das linhas** (ProcessDefault.cs ~683). Como esse é um campo de usuário do *cabeçalho*, o valor que efetivamente vai para a OP é o do **último item processado** — e não o `linhaBase` atribuído antes do loop (~637), que só prevalece quando nenhum item é processado. Meu porte gravava sempre `linha_base` (que chega como `-1`), o que produziria um valor diferente do legado em toda OP criada. Replicado fielmente agora, com comentário explicando a mecânica.

**3. Nome da coleção filha do UDO `OrcDetalhe` (a confirmar)**

`POST /OrcDetalhe` retorna `Internal server error` — a Service Layer não diz qual campo está errado. Duas mudanças prováveis foram aplicadas: (a) `U_INO_DATA` passou a usar `%Y-%m-%d` em vez de `isoformat()` com microssegundos (o mesmo formato `%Y-%m-%d` já foi aceito pelo `POST /ProductionOrders`, que passou da validação de datas); e (b) a coleção filha passou de `INO_ORC_LINHA` (nome da tabela filha usado pela DI API em `oGeneralData.Child("INO_ORC_LINHA")`, ProcessDefault.cs ~1587/1698) para **`INO_ORC_LINHACollection`**, que é a convenção da Service Layer para tabelas filhas de UDO — isolado na constante `_UDO_ORC_DETALHE_CHILD`.

Como (b) é convenção e não verificação, foi adicionado um comando de diagnóstico para confirmar contra o ambiente real em vez de seguir tentando:

```bash
python -m app.cli diag entidade OrcDetalhe --campos
```

Ele lê **um registro já existente** da entidade (o addon legado criou muitos) e imprime os nomes reais de campo e da coleção filha. É a forma confiável de fechar também as perguntas 9/10 da seção 9 (nomes de `Resources` e das linhas de `SalesOpportunities`), que até agora estavam marcadas como "mais prováveis segundo a documentação".

**Observação de método**: o arquivo de referência com o C# extraído se perdeu entre sessões; o `ProcessDefault.cs` original foi trazido de volta do computador do Anderson para checar esses pontos. Vale repetir isso sempre que aparecer dúvida de nome/valor — o C# é a fonte de verdade e está a um comando de distância.

### 7.10 Tipagem dos campos de UDO na Service Layer (15/09/2026) — **descoberta importante**

O `diag entidade OrcDetalhe --campos` resolveu a dúvida da seção 7.9 e revelou algo mais amplo, que provavelmente vale para **todos** os UDOs deste projeto:

- ✅ **`INO_ORC_LINHACollection` confirmado** — a convenção `<TabelaFilha>Collection` estava certa.
- ✅ **`U_INO_DATA` como `"AAAA-MM-DD"` confirmado** (o registro real traz `"2024-12-01"`).
- ⚠️ **Os campos numéricos são `float`/`int` de verdade na Service Layer** — e é aí que estava o `Internal server error`. O C# original passava **tudo como string** (`oGeneralData.SetProperty("U_ORCVALVND", item.ORCVALVND.ToString())`), porque a DI API converte para o tipo do UDF automaticamente. A Service Layer é tipada em JSON e **rejeita string onde o campo é numérico**, devolvendo um `Internal server error` genérico, sem dizer qual campo — o que torna esse erro particularmente difícil de diagnosticar às cegas.

Tipos confirmados no `OrcDetalhe` (registro real, DocEntry 263790), agora aplicados em `_monta_body_orc_detalhe`:
- `float`: `U_ORCVALVND`, `U_ORCVALLST`, `U_ORCVALINV`, `U_ORCVALLUC`, `U_ORCVALEXP`, `U_ORCVALCOM`, `U_ORCPERCOM`, `U_ORCVALTRP`, `U_ORCVALMON`, `U_ORCBAS1/2/3`
- `int`: `U_CLICOD`, `U_CLICONCOD`, `U_PRZENT`
- `str`: todos os demais — **incluindo `U_ORCVALEMB`**, que apesar do nome ("valor embalagem") é um UDF de **texto** no SAP (vem como `"0.0000"` no registro real). Esse é exatamente o tipo de detalhe que só o dado real revela: manter `str()` aqui e trocar por `float` nos vizinhos parece inconsistente no código, mas é o que o campo é.

**Regra geral para o resto da migração**: onde o C# faz `SetProperty(..., x.ToString())`, **não** replicar o `.ToString()` na Service Layer — conferir o tipo real com `diag entidade <Entidade> --campos` (o comando também lista os campos e tipos das coleções filhas) e mandar o tipo certo. Isso vale para os UDOs ainda não exercitados (`INO_LOG`, `INO_SETUPOBJ`) e para os módulos 3 e 4 quando forem portados.

### 7.11 Atualização de linha de documento: nunca reescrever a coleção inteira (15/09/2026)

Com as correções da 7.10, `POST /OrcDetalhe` passou e **a Ordem de Produção foi criada com sucesso** — o fluxo avançou até o `AtualizaDoc(Tipo="OP")`, que falhou com:

```
PATCH /Orders(19466): '' is not a valid value for property 'U_B1SYS_RevenueInd2'
```

**Causa**: `_atualiza_doc` fazia um *read-modify-write* da coleção inteira — `GET /Orders(x)`, alterava uma linha em memória e devolvia **todo** o `DocumentLines` no PATCH. Isso reenvia todos os campos de todas as linhas, incluindo UDFs de **outros add-ons** (aqui `U_B1SYS_RevenueInd2`, da localização brasileira) que voltam vazios (`""`) no GET mas são campos de lista validada na escrita — o SAP recusa `""`. Não é um problema do pedido nem do dado: é o padrão de atualização que estava errado.

**Correção**: atualização **parcial** — mandar só a linha alvo com só o campo que muda:

```json
PATCH /Orders(19466)  {"DocumentLines": [{"LineNum": 4, "U_INO_OP": 555}]}
```

Isso replica exatamente a semântica da DI API no C# (`Doc.Lines.SetCurrentLine(linha); Doc.Lines.UserFields...Item("U_INO_OP").Value = numOP; Doc.Update();` — ProcessDefault.cs ~235), que também só envia o campo alterado.

**Regra geral**: nunca reler e reenviar uma coleção de linhas inteira só para mudar um campo — além de frágil (qualquer UDF validado de qualquer add-on instalado quebra a escrita), é desnecessário. Vale para os módulos 3 e 4, que também atualizam linhas de documento.

**`Querys.Linha` — lacuna fechada, com uma diferença deliberada**

O porte assumia que o parâmetro `linha` já era o número da linha do pedido, com um comentário admitindo que faltava extrair `Querys.Linha` do `Querys.resx`. A query real é:

```sql
SELECT T0."VisOrder" FROM RDR1 T0 WHERE T0."DocEntry" = {0} and T0."U_INO_ORCITM" = {1}
```

Ou seja: o parâmetro é o **`U_INO_ORCITM`** (item do orçamento), e a query resolve qual linha do pedido corresponde a ele. Transcrita como `LINHA_PEDIDO`, com **uma diferença consciente**: selecionamos também o `LineNum` e é ele que usamos. O C# usa `VisOrder` porque a DI API endereça linha por **posição** (`SetCurrentLine(i)`); a Service Layer endereça por **`LineNum`**, a chave da linha em `DocumentLines`. Nos casos normais os dois coincidem — mas se as linhas do pedido tiverem sido reordenadas, usar `VisOrder` como se fosse `LineNum` gravaria o `U_INO_OP` na **linha errada**, silenciosamente. Quando nenhuma linha corresponde, a função agora loga um aviso e não escreve nada (antes, o `for` simplesmente não achava nada e mandava o PATCH assim mesmo).

⚠️ **A confirmar em homologação**: o comportamento da Service Layer ao receber um `DocumentLines` parcial. A documentação da SAP indica que, informando a chave da linha (`LineNum`), apenas as linhas listadas são atualizadas e as demais ficam intactas — mas **isso precisa ser verificado no primeiro teste**: depois de rodar, conferir no pedido se todas as linhas originais continuam lá e se o `U_INO_OP` foi para a linha certa. Não usar em produção antes dessa conferência.

### 7.12 `pedidos-wbc comparar-ops` — auditoria contra as OPs do legado (15/09/2026)

O Anderson pediu para comparar as OPs geradas por este porte com as que o addon legado gerou em produção para o mesmo orçamento: se o **número** e o **conteúdo** batem, e, se faltou alguma, qual e por quê. Como isso vai ser necessário a cada rodada de validação (é o passo 4 da seção 10, "comparar registro a registro"), virou comando:

```bash
python -m app.cli pedidos-wbc comparar-ops 00125391 [--schema-legado SBOALTAMIRAPROD] [--detalhes]
```

Só leitura, nunca escreve. **Sempre schema a schema** — o Anderson esclareceu em 16/09/2026 que os dois ambientes são schemas HANA distintos e permanentes: `SBOALTAMIRAPROD` é onde o **addon legado** roda, `SBOALTAMIRAHOMOLOG` é onde **este porte** roda. Os defaults vêm do `.env` (`HANA_SCHEMA_LEGADO` e `HANA_SCHEMA`), então na prática basta `comparar-ops <orcamento>`. Exige que o usuário HANA tenha SELECT no schema de produção (se faltar, o comando diz isso em vez de estourar um traceback).

A primeira versão deste comando tinha um segundo modo, "mesmo schema separado por data de criação", partindo da suposição errada de que a homologação seria uma cópia da produção e conteria as OPs do legado. **Removido**: além de nunca ter sido o caso, era uma armadilha — na virada do dia as OPs da véspera caíam no lado errado e o relatório aparecia invertido. Sobrou apenas `--desde AAAA-MM-DD`, com outro propósito: filtrar o lado novo para isolar a última execução quando a homologação acumulou OPs de reprocessamentos anteriores (o relatório avisa sozinho quando detecta OPs de datas diferentes desse lado).

**Ordenação**: as duas tabelas saem ordenadas pelo mesmo critério de **conteúdo** — item produzido → quantidade planejada → linha de referência —, nunca pelo número da OP, já que cada ambiente tem sua própria sequência (produção nos 7000, homologação nos 151000). Assim a mesma OP cai na mesma posição das duas tabelas e a conferência é linha a linha. A quantidade vem antes da linha porque um mesmo item pode ter várias OPs no mesmo pedido (`PAR000PADRA000000000` aparece 4× no orçamento 00120634, com quantidades diferentes), e é pela quantidade que se distinguem.

**OPs canceladas ficam FORA da comparação por padrão** (decidido pelo Anderson em 16/09/2026; a primeira versão as incluía). Em produção várias foram canceladas manualmente depois da integração — é ruído operacional, não resultado do que a integração gerou. `--com-canceladas` volta a incluí-las.

Descartar canceladas tem um efeito colateral que o relatório trata explicitamente: um item cuja OP de produção foi cancelada some do lado legado e passaria a aparecer como **"só no novo (a mais)"**, dando a impressão de que o porte criou algo indevido — quando na verdade os dois criaram e um humano cancelou depois. Por isso o comando (a) informa quantas OPs foram descartadas de cada lado e (b) marca esses itens como **"no legado só cancelada — equivalente"** em vez de "a mais", distinguindo-os dos itens que realmente só existem do lado novo.

O comando também **diagnostica as faltantes**, checando as causas conhecidas — todas olhando o **lado novo** (o schema do porte), porque é o que o porte enxergava ao decidir criar ou não a OP. Olhar o lado do legado dava diagnóstico errado (bug corrigido em 16/09/2026 junto com a mudança para schema a schema): a verificação de reprocessamento consulta as OPs do próprio schema do porte, e o `U_INO_EntregaMultipla` que importa é o do pedido de lá. As causas:
1. `U_INO_EntregaMultipla = 'Y'` no pedido → `CriaOP` não cria a OP (quirk do C#, pergunta 9 da seção 9);
2. grupo do WBC sem linha em `@INO_GRP_PRODUTOS` → grupo inteiro pulado com log "Item correspondente ... não cadastrado";
3. OP não cancelada já existente + execução sem `--force` → pulada pela verificação da seção 7.6;
4. nenhuma das anteriores → aponta para o `INO_LOG` e para a hipótese de execução abortada.

⚠️ **A causa (4) é importante e não é hipotética**: qualquer exceção dentro do loop de grupos em `processar_pedidos_novos` é capturada pelo `except` do pedido, o que **interrompe os grupos seguintes e a cascata de semiacabados**. Foi exatamente o que aconteceu na execução que expôs o bug da seção 7.11: a OP do primeiro grupo foi criada, o `_atualiza_doc` estourou, e o pedido inteiro foi abortado. Ou seja: **uma comparação feita logo após uma execução que deu erro mede o erro, não o porte** — rodar a comparação só depois de uma execução que terminou limpa. (Esse comportamento de "engolir o erro e seguir para o próximo pedido" é fiel ao legado, item 3 da seção 8 — mas no legado cada grupo era resiliente de outra forma; vale reavaliar na fase 2 se um erro em um grupo deveria mesmo abortar os demais.)

### 7.13 Chave de entidade na URL: percent-encoding obrigatório (15/09/2026)

Testando um segundo pedido (orçamento 00120634), apareceu:

```
PATCH /Items('ESCESP00000SUP000000#1205#210#0'): Unrecognized resource path.
```

**Causa**: o código do item contém `#`, e numa URL o `#` inicia o **fragmento** — tudo depois dele nem chega ao servidor. Verificado no `httpx`: a URL montada vira literalmente `.../Items('ESCESP00000SUP000000`, que é de fato um "resource path" inválido. O erro não tem nada a ver com o item nem com permissão. Esse problema **não existia no addon C#**, que endereçava o objeto pela DI API (`oItems.GetByKey(codigo)`), sem nunca montar URL — é uma classe de bug que só nasce ao trocar DI API por REST.

**Correção**: a formatação da chave saiu dos chamadores e virou responsabilidade do cliente (`_formata_chave` em `service_layer_client.py`), aplicada por `get_by_key`/`update_entity`:
- strings recebem percent-encoding (`#` → `%23`, e também `/`, `?`, `%`, `&`, espaço);
- aspa simples dentro do literal é escapada duplicando (regra do OData);
- `int`/string de dígitos viram chave numérica sem aspas (`Orders(19466)`).

**Um detalhe que quase virou regressão**: chaves de texto que *parecem* número. O `Code` do UDO `@INO_SETUP` é o **texto** `"1"` — a heurística "só dígitos → chave numérica" produziria `INO_SETUPOBJ(1)`, que a Service Layer recusa; e um `ItemCode` composto só de dígitos teria o mesmo destino. Por isso `get_by_key`/`update_entity` aceitam `chave_texto=True`, usado em `Items` e nos UDOs com `Code`. Regra: **toda entidade de chave string passa `chave_texto=True`**; só `DocEntry`/`OpprId` e afins usam o padrão numérico.

**Testes de regressão** (`tests/test_service_layer_client.py`, novo): cobrem os dois bugs desta rodada — a formatação de chave (com `#`, com aspa simples, chave numérica, chave de texto que parece número, e chamador que já passou aspas) e a conversão de `Decimal`/datas da seção 7.8. Inclui um teste que monta a requisição com `httpx` e confere que o `%23` chega intacto (sem virar `%2523` por duplo-encoding). A suíte passou de 5 para 19 testes.

Vale notar o padrão: as três últimas falhas (7.10 tipos, 7.11 coleção de linhas, 7.13 chave na URL) são todas da mesma família — **coisas que a DI API resolvia implicitamente e que a REST exige explícitas**. É o principal risco residual deste porte, e o motivo de rodar cada módulo contra homologação antes de confiar nele.

### 7.14 Diagnóstico de lentidão e otimizações (16/09/2026)

O Anderson relatou lentidão na criação das OPs. Em vez de otimizar por palpite, o primeiro passo foi instrumentar: `app/core/perf.py` mede o tempo separando **abrir conexão**, **executar query** e **Service Layer**, e a flag `--perfil` em `pedidos-wbc processar-novos` mostra o progresso durante a execução e imprime o relatório no fim.

**A medição desmentiu a hipótese inicial.** Eu apostava nas conexões (cada consulta abria uma nova, com autenticação e, no SQL Server, handshake TLS). Os 18,9s do orçamento 00125192 se dividiram assim:

| Categoria | Chamadas | Tempo | % |
|---|---|---|---|
| Service Layer | 16 | 9,9s | 52% |
| HANA · abrir conexão | 62 | 3,7s | 20% |
| WBC · executar query | 49 | 1,6s | 9% |
| HANA · executar query | 62 | 0,9s | 5% |
| WBC · abrir conexão | 49 | 0,5s | 2% |

As conexões eram a segunda causa, não a primeira. **E o relatório apontou errado**: a conclusão automática comparava só "abrir conexão" contra "executar query" e ignorava a Service Layer, anunciando como gargalo a causa de 20% enquanto a de 52% estava na primeira linha da própria tabela. Corrigido para concluir a partir da categoria líder — um relatório de diagnóstico que induz à conclusão errada é pior do que não ter relatório.

**Bug encontrado junto, mais grave que a lentidão**: o log mostrava `OP criada: DocEntry=0` nas sete OPs. Elas eram criadas (201 Created), mas a chave não era capturada, porque o código lia `DocEntry` e a entidade `ProductionOrders` da Service Layer expõe **`AbsoluteEntry`** (`DocEntry` é o nome da coluna em `OWOR`). Esse zero seguia para `_atualiza_doc`, que gravava `U_INO_OP = 0` nas linhas do pedido — as OPs nasciam certas e o vínculo com o pedido ficava zerado, silenciosamente. Corrigido em `_chave_do_documento_criado`, que tenta as duas grafias e avisa se não achar nenhuma; e `marca_op_nas_linhas` agora se recusa a gravar vínculo zerado. É a mesma família das seções 7.10/7.11/7.13: coisas que a DI API resolvia implicitamente e a REST exige explícitas.

**Otimizações aplicadas** (as duas aprovadas pelo Anderson):
1. **`HanaDirectReader` reaproveita a conexão** entre consultas — 62 aberturas viram 1, economizando ~3,6s. A conexão abre na primeira consulta e, se cair no meio (timeout do servidor numa execução longa), a consulta seguinte reconecta e tenta uma vez de forma transparente: sem isso, o reúso trocaria lentidão por fragilidade. Tem `close()` e funciona como context manager — a web precisa fechar explicitamente.
2. **`marca_op_nas_linhas` grava `U_INO_OP` em lote**: era 1 consulta + 1 `PATCH /Orders` por item do grupo (6 PATCHes / 4,1s no perfil); agora é 1 consulta resolvendo todas as linhas (`LINHAS_PEDIDO_POR_ORCITM`) + 1 PATCH com todas no mesmo corpo. O estado final é idêntico. O C# também fazia um `Update()` por item, mas isso era característica da DI API, não regra de negócio — a única diferença real é sob falha: antes as marcações já feitas ficavam gravadas, agora é tudo ou nada (e como uma exceção aborta o pedido inteiro, o estado parcial não era coerente nos dois casos).

Não mexemos no reúso de conexão do **SQL Server do WBC**: são só 0,5s (2%), e a mesma mudança ali adicionaria superfície de risco por um ganho marginal. Fica registrado como opção se o perfil de um orçamento maior mostrar outra proporção.

`tests/test_otimizacoes.py` (novo) cobre as duas otimizações e o bug da chave: reúso de conexão, reconexão automática após queda, `close()`, gravação em lote numa única chamada, deduplicação de linhas, recusa de gravar vínculo zerado, e as grafias de chave. A suíte foi de 19 para 32 testes.

**Resultado medido** (mesmo orçamento 00125192, logo depois): **18,9s → 7,0s**. As conexões HANA sumiram do relatório (62 → 1) e os `PATCH /Orders` caíram de 6 para 3. E o `DocEntry` das OPs passou a vir preenchido (157832…157838) em vez de `0`, com o `U_INO_OP` sendo gravado de verdade — confirmando a correção da chave.

⚠️ **Nem todo esse ganho é das otimizações.** Na segunda execução o `PATCH /Items` levou 0,4s contra 2,6s da primeira (mesma chamada, 6x mais rápida) e o `POST /OrcDetalhe` caiu de 1,1s para 0,1s — isso é variação do servidor, não código. Atribuíveis às mudanças são ~5,7s dos 11,9s; o resto foi o ambiente estar mais leve. **Antes de otimizar mais, rodar duas ou três vezes para ter uma linha de base confiável** — medir contra base ruidosa leva a perseguir ganho que não existe.

### 7.15 Otimizações analisadas e ADIADAS (16/09/2026) — decisão consciente

Com 7,0s, a Service Layer ainda é 59% do tempo (13 chamadas, 4,2s): `PATCH /Orders` 3 (1,9s), `POST /ProductionOrders` 7 (1,7s), `PATCH /Items` 1 (0,4s), `POST /OrcDetalhe` 1 (0,1s), `POST /INO_LOG` 1, Login 1. O Anderson pediu a análise do impacto **antes** de aplicar qualquer coisa, e decidiu **registrar e não aplicar por enquanto**. Fica aqui o estudo pronto para quando for a hora.

**Opção A — consolidar o "Sucesso" no mesmo PATCH das linhas (risco baixo, ganho pequeno)**

`marca_op_nas_linhas` e `_atualiza_doc(..., "Sucesso")` são dois `PATCH /Orders` seguidos, na mesma entidade e no mesmo instante (service.py, fim de `_processa_grupo_producao`) — cabem num só. Há um agravante que este pedido não revela: o "Sucesso" está **dentro do loop de grupos**, então um pedido com N grupos dispara N PATCHes idênticos gravando `U_INO_ProcessWBC='Y'`. O orçamento 00120634, com 2 grupos, já grava duas vezes a mesma coisa.
- Ganho: 1 chamada por grupo — ~0,6s aqui, ~1,3s no 00120634.
- Risco: baixo. Mesma entidade, mesmo momento, estado final idêntico.
- Duas formas: fundir o "Sucesso" no PATCH das linhas (economiza N chamadas), ou apenas tirá-lo do loop para rodar uma vez por pedido (economiza N-1, e é mais simples).

**Opção B — agrupar os `POST /ProductionOrders` em `$batch` (risco alto, ganho grande em pedidos grandes)**

Viabilidade **verificada**: o retorno de `cria_ordem_producao_semi_acabado` é descartado e a recursão de `checa_semi_acabado` não depende da chave da OP criada (decide a partir de dados do WBC). As OPs são independentes entre si, então poderiam ser montadas e enviadas numa requisição só.
- Ganho: ~1,4s aqui (7 chamadas → 1); **~6,5s no 00120634** (29 OPs). Escala com o tamanho do pedido, que é onde a lentidão realmente incomoda.
- Riscos: (1) exige reestruturar a cascata recursiva de semiacabados — o código mais complexo do porte e **o menos validado**, já que a comparação contra produção ainda não foi feita; (2) muda a semântica de falha (changeset é tudo-ou-nada; hoje, se a 5ª OP falha, as 4 anteriores existem); (3) perde o progresso ao vivo no log, que se provou útil — foi por ele que o `DocEntry=0` apareceu; (4) `$batch` (multipart, Content-ID) é mais uma superfície da REST não exercitada, e as seções 7.10 a 7.13 mostram que cada uma delas trouxe surpresa.

**Opção C — o resto é inerente**

`POST /ProductionOrders` sem batch é irredutível (uma OP = uma chamada). Os 1,5s do WBC são duas queries pesadas de estrutura rodando 1x cada. E um ponto de atenção para o futuro: `PATCH /Items` é **1 chamada só porque `Solda.txt` está vazio** (gap da seção 7.4) — com a lista real, vira uma chamada por item de solda e pode crescer bastante. Reavaliar quando o arquivo chegar.

**Recomendação registrada**: aplicar A quando conveniente; deixar B para **depois** da validação das OPs contra produção (`comparar-ops --schema-legado SBOALTAMIRAPROD`). O argumento não é de performance, é de ordem de trabalho: 7s para 7 OPs não é problema operacional, e B mexe justamente no código que ainda precisa ser provado correto. Otimizar antes de validar é otimizar algo que pode ter de ser reescrito.

### 7.16 `pedidos-wbc cancelar-ops` — limpeza de um pedido de vendas (16/09/2026)

Pedido pelo Anderson como primeiro passo de um "processo de limpeza de pedido de vendas": cancelar todas as OPs de um pedido, mas **só se o pedido estiver em estado seguro para isso**.

```bash
python -m app.cli pedidos-wbc cancelar-ops 84245                    # por DocNum do pedido
python -m app.cli pedidos-wbc cancelar-ops 00125192 --orcamento
python -m app.cli pedidos-wbc cancelar-ops 84245 --manter-vinculos  # só cancela, não mexe no pedido
```

**A regra é lista branca**: todas as OPs do pedido precisam estar **Planejadas (`P`)** ou **Canceladas (`C`)**. Qualquer outra aborta o processo sem alterar nada, mostrando quais bloquearam e por quê. O Anderson citou explicitamente "nenhuma liberada", mas a regra que ele definiu exclui também **Encerrada (`L`)** — e é bom que exclua: uma OP encerrada já teve apontamento de produção, baixa de insumo e entrada de produto acabado; cancelá-la seria pior do que cancelar uma liberada. Status desconhecido também bloqueia, pelo mesmo princípio.

**Desenho**: a verificação (`levanta_ops_para_cancelamento`) é uma função **separada** da execução (`cancela_ops_do_pedido`), e só lê. Isso permite à CLI mostrar a tabela completa de OPs com seus status, barrar quando houver impedimento e só então pedir confirmação — em vez de descobrir o problema no meio da escrita. OPs já canceladas são contadas no relatório e ignoradas na execução (não se recancela o que já está cancelado).

Um erro ao cancelar uma OP específica **não interrompe as demais**: o resultado separa `canceladas` de `com_erro` e a CLI lista as que falharam com o motivo. Cancelar 8 de 10 e dizer exatamente quais faltaram é mais útil do que parar na terceira e deixar o pedido num estado que ninguém sabe qual é.

**Limpeza dos vínculos da integração** (incluída no mesmo comando logo em seguida, a pedido do Anderson): depois de cancelar, o pedido volta ao estado de "não processado" — `U_INO_ProcessWBC = 'N'` no cabeçalho e `U_INO_OP = 0` nas linhas que tinham OP, **numa única chamada** à Service Layer (aplicando a lição da 7.14). Sem isso a limpeza ficava pela metade: as OPs canceladas, mas o pedido ainda marcado como processado e com as linhas apontando para OPs mortas.

Os dois valores não foram escolhidos, são os que o próprio sistema já usa: `'N'` é o que `_atualiza_doc(..., "Erro")` grava e o que `BUSCA_PEDIDOS_PARA_INTEGRAR` filtra para considerar um pedido pendente; `0` é como o **addon legado** zerava esse campo (`DocCot.Lines.SetCurrentLine(i); ...U_INO_OP = 0`, portado em `_update_tab_pedido`) e como o resto do código lê "sem OP" (`IFNULL(U_INO_OP,0) = 0`). Efeito: o pedido reaparece em `pedidos-wbc buscar` e pode ser reprocessado **sem** `--force`. `--manter-vinculos` só cancela as OPs, sem tocar no pedido.

⚠️ **A limpeza é pulada se qualquer cancelamento falhar.** Marcar o pedido como "não processado" enquanto existe OP viva apontando para ele é pior que o estado anterior — some da vista sem ter sumido do banco. Nesse caso a CLI avisa explicitamente e devolve código de erro. Pela mesma lógica, a limpeza roda mesmo quando não há OP a cancelar (todas já canceladas): o pedido pode estar travado por um `U_INO_ProcessWBC='Y'` de execução anterior, e é exatamente esse estado que impede o reprocessamento limpo.

`tests/test_cancelar_ops.py` (novo, 16 testes): o mais importante é `test_nada_e_cancelado_quando_ha_op_liberada`, que verifica pela lista de escritas na Service Layer que **nenhuma** OP foi tocada — inclusive as que estariam aptas. Cobre também `L` e status desconhecido bloqueando, o cancelamento só das planejadas, a exigência de confirmação, a resiliência a erro numa OP isolada, a limpeza de cabeçalho e linhas num único PATCH, o `--manter-vinculos`, e `test_limpeza_e_pulada_se_algum_cancelamento_falhar`. Suíte: 32 → 48 testes.

> Nota de método: o teste da confirmação falhou na primeira execução acusando "cancelou sem confirmar" — o defeito estava no próprio teste (`argumentos or ["--sim"]` trata lista vazia como ausência e injetava o `--sim` justamente no caso que queria testar sem ele), não no comando. Corrigido com sentinela `None` e registrado no arquivo, porque é uma armadilha fácil de reintroduzir.

### 7.17 Módulo 3 — Liberar / Replanejar OP (16/09/2026)

Primeira parte do módulo 3 a sair do esqueleto. Porte de `ManutencaoOp.mudaStatus` + `updateOP` (ManutencaoOp.b1f.cs, linhas ~241 e ~278), lidos do original.

```bash
python -m app.cli manutencao-op liberar 9001 9002        # por número de OP
python -m app.cli manutencao-op liberar --pedido 84245   # todas as OPs do pedido
python -m app.cli manutencao-op replanejar 9002
python -m app.cli manutencao-op replanejar --pedido 84245
```

O mapa de transições veio do `updateOP` verbatim: `p`=`boposPlanned`, `c`=`boposCancelled`, `l`=`boposReleased`, `f`=`boposClosed` **com `ClosingDate = hoje`** (o original faz `OrdemPrducao.ClosingDate = DateTime.Now` nesse ramo). Os quatro estão no mapa, mas **a CLI expõe só liberar e replanejar**: o `"f"` é o último passo do encerramento (`Button4_ClickAfter`), que antes disso faz `corrigeOP` + `SaidaEnsumo` + `EntradaProduto` — fechar a OP sem a movimentação de estoque deixaria o apontamento inconsistente. Está documentado na docstring de `muda_status` para não ser usado avulso.

**Ergonomia herdada da tela**: no legado o usuário via os status na grade antes de clicar. A CLI imprime a mesma informação (status atual + ação por OP) antes de pedir confirmação. **OPs já no status de destino são puladas** em vez de gastarem uma chamada — a Service Layer é o recurso mais caro da execução (seção 7.14).

**Diferença consciente em relação ao C#**: lá os erros de cada OP iam só para o `INO_LOG`/status bar e o usuário não sabia quais tinham dado certo. Aqui o resultado separa `alteradas` de `com_erro`, e um erro numa OP não interrompe as demais — mesmo critério do `cancelar-ops`.

**Onde isso resolve um problema prático**: o `pedidos-wbc cancelar-ops` barra quando há OP liberada, e até agora a única saída era a tela do SAP. Agora é `manutencao-op replanejar --pedido <n>` e em seguida o `cancelar-ops`.

`tests/test_manutencao_op.py` (novo, 13 testes): alteração só das que não estão no destino, por número e por pedido, confirmação obrigatória, recusa de argumentos conflitantes, resiliência a erro isolado, e a conferência do mapa de transições contra o `updateOP` original (incluindo o `ClosingDate` do `"f"`). Suíte: 48 → 61 testes.

**Continua pendente no módulo 3**: a busca com filtros por faixa de nº de OP e de status (`OPSManutencao`), e o encerramento com movimentação de estoque — concluídos em 17/09/2026, ver 7.18.

---

### 7.18 Módulo 3 concluído — Buscar e Encerrar OP (17/09/2026)

Fecha o módulo 3. Porte de `ManutencaoOp.buscar()` (linha ~99) e de `Button4_ClickAfter` (linha ~399) com os três métodos que ele orquestra: `corrigeOP` (~364), `SaidaEnsumo` (~472) e `EntradaProduto` (~547), todos lidos do original antes de escrever qualquer linha.

```bash
python -m app.cli manutencao-op buscar 84245
python -m app.cli manutencao-op buscar 84245 --status-de P
python -m app.cli manutencao-op buscar 84245 --op-de 9001 --op-ate 9010 --json

python -m app.cli manutencao-op encerrar 9001 9002      # IRREVERSÍVEL
python -m app.cli manutencao-op encerrar --pedido 84245
```

#### `buscar` — a montagem do `WhereQuery`

A regra do original foi preservada tal como está: informar só o limite inferior vira `=`, informar os dois vira `between`. O que mudou:

- **Limite superior sozinho agora é erro.** No C# o `if` externo testava só o limite inferior, então `NO2`/`Status` preenchidos sem `NO1`/`Status1` eram silenciosamente ignorados. Na tela dava para perceber olhando a grade; numa CLI passaria despercebido.
- **O status é validado contra `P`/`R`/`L`/`C`** em vez de ir direto do campo de texto para dentro do literal SQL (débito 2 da seção 8, corrigido no ponto onde o porte toca).
- A coluna `"Selecionar"` (a caixa de seleção da grade) sai do resultado: na CLI a seleção é o próprio argumento do comando.
- Leitura por `hdbcli`, não pela Service Layer — três joins e aliases de exibição, exatamente o caso previsto na decisão de 14/09 (seção 6.4). A assinatura antiga do esqueleto recebia um `ServiceLayerClient`; foi trocada.

#### `encerrar` — a única operação irreversível do módulo

Para cada OP, na ordem do original: `corrige_op` → `saida_insumo` → `entrada_produto` → status Encerrada.

`corrige_op` libera a OP e força `ProductionOrderIssueType = im_Manual` em **todas** as linhas — sem isso o SAP tenta backflush e recusa a saída manual. Usa PATCH parcial das linhas (só `LineNumber` + o campo alterado), pelo motivo da seção 7.11.

A saída (`InventoryGenExits`) e a entrada (`InventoryGenEntries`) **não informam item, quantidade nem depósito**: apontam para a OP por `BaseType`/`BaseEntry`/`BaseLine` e deixam o SAP copiar do documento base. É o que o original faz — as atribuições de `ItemCode`/`Quantity` estão lá, comentadas. A novidade em REST é o `BaseType = 202` (`oProductionOrders`), que a DI API inferia do objeto e a Service Layer exige explícito: mesma classe de divergência das seções 7.9 e 7.10.

**Três divergências conscientes em relação ao C#**, todas registradas nas docstrings:

1. **`BaseLine` usa o `LineNum` real da linha da OP.** O original usava o *índice da linha no recordset* (`novoDoc.Lines.BaseLine = i`) devolvido por `QtdeFaltanteOP` — uma query que nem `ORDER BY` tem. Funciona enquanto a ordem do banco coincidir com a numeração das linhas; não é garantido. Query nova `LINHAS_FALTANTES_OP`, que traz o `LineNum`.
2. **Linhas já totalmente baixadas (`PlannedQty - IssuedQty <= 0`) são puladas.** O original as incluía e deixava o SAP recusar ou lançar zero.
3. **Uma OP só é encerrada se a sua própria cadeia tiver ido até o fim.** O legado chamava `mudaStatus("f")` uma vez no final, sobre **todas** as linhas marcadas, inclusive aquelas cujo `corrigeOP`/`SaidaEnsumo` havia falhado — fechando OPs sem a movimentação de estoque correspondente. Esse é o bug mais sério encontrado no módulo 3.

A precondição do legado foi mantida: só entram OPs com `apontada < planejada` (`if (qtdAD < qtdPD)`). Também desaparece a ida ao banco por `OPDE` a cada linha — o `DocEntry` já vem do levantamento.

**Guardas da CLI**: a tabela impressa antes da confirmação mostra, por OP, o que vai acontecer (`saída + entrada + encerrar`, `já encerrada`, `ignorada`); o aviso de irreversibilidade é explícito; e `--sim` é obrigatório para pular a confirmação. Mesmo padrão do `cancelar-ops` (seção 7.16).

`tests/test_manutencao_op_encerrar.py` (novo, 17 testes): montagem do filtro nos quatro casos, recusa do limite superior sozinho e do status inválido, obrigatoriedade do pedido, ordenação e tradução de status; `im_Manual` em todas as linhas; `BaseLine` pelo `LineNum` e o pulo das linhas sem saldo; a ordem exata das quatro etapas; e as três guardas que importam — não encerrar quando a movimentação falha, não movimentar OP já apontada, exigir confirmação. Suíte: 61 → 78 testes.

~~**Ainda não validado em ambiente real.**~~ — **validado em 21/09/2026** (seção 7.30). Os nomes `InventoryGenExits`/`InventoryGenEntries`, o `BaseType = 202` e o `BaseLine` foram todos confirmados por execução e conferência. O que faltava e não estava previsto aqui: `BPL_IDAssignedToInvoice` e `Series`, que a DI API preenchia sozinha (seções 7.27 a 7.29).

---

### 7.19 Trava de escrita em produção — `--producao` (21/09/2026)

Até 20/09, o que separava homologação de produção era apenas o valor de `SL_COMPANY_DB` no `.env`: qualquer comando de escrita rodava contra o que estivesse configurado, sem aviso. `Settings.is_production` existia desde 15/09 mas **não era lido por ninguém** — conferido por busca no código antes de implementar.

**Modelo de duas chaves**, as duas obrigatórias para escrever em produção:

1. `WBC_BLOCK_PRODUCTION_WRITES=false` no `.env` — chave de ambiente, deliberada, que não se gira sem querer no meio de um comando.
2. `--producao` na linha de comando — reconhecimento por execução, de quem está rodando naquele momento.

Nenhuma libera sozinha. A flag existe para que apontar o `.env` para produção não baste; o `.env` existe para que a flag não baste. Com o bloqueio do `.env` ligado, nem `--producao` passa — a mensagem diz explicitamente que aquela é a chave de ambiente.

**`--sim` deixa de pular a confirmação em produção.** Lá é preciso digitar o nome da company DB. Esse é o cenário que a trava existe para cobrir: um comando com `--sim` dentro de um script, apontado para o ambiente errado, rodaria sozinho até o fim.

**Onde a regra mora**: `app/core/guardas.py`, não na CLI. Quando a camada web entrar, chama a mesma função — se a regra morasse na CLI, a web nasceria sem trava. A CLI só traduz a exceção em mensagem e código de saída.

**Comandos cobertos** (todos os que gravam): `oportunidades integrar`, `pedidos-wbc processar-novos`, `reprocessar-integrados`, `cancelar-ops`, `manutencao-op liberar`, `replanejar`, `encerrar`. Os de leitura (`buscar`, `comparar-ops`) **não** ganharam a flag de propósito: exigi-la onde não há escrita ensinaria o usuário a digitá-la sem pensar, que é o oposto do objetivo.

Em `cancelar-ops` e `encerrar` a trava é aplicada **antes** do levantamento das OPs — recusar depois de ler o pedido inteiro só faria esperar para receber o mesmo "não".

`--producao` em homologação não é erro, mas avisa que não teve efeito: quem passa a flag por hábito precisa saber que o alvo não é produção, senão conclui que está protegido quando não está.

**⚠️ Limite conhecido, registrado como pendência e não como resolvido**: a decisão se baseia na company DB da Service Layer (`SL_COMPANY_DB == WBC_PRODUCTION_COMPANY_DB`). As escritas no SQL Server do WBC (`INTEGRACAO_ORCINC`, módulo 1) ficam cobertas por tabela — acompanham a escrita no SAP nos comandos que fazem as duas coisas —, mas um `.env` com SAP de homologação e WBC de produção passaria pela trava.

`tests/test_trava_producao.py` (novo, 15 testes): as quatro combinações das duas chaves, o aviso da flag inócua em homologação, ausência de credenciais nas mensagens, `--sim` não dispensando a confirmação em produção, a digitação certa e a errada da company DB, homologação seguindo sem fricção — e dois testes de cobertura: **todo** comando de escrita expõe `--producao` e nenhum comando de leitura expõe. O primeiro é o que impede a trava de envelhecer: um comando de escrita novo sem a flag quebra a suíte. Suíte: 78 → 93 testes.

Nos testes existentes foi preciso fixar `cfg.return_value.is_production = False`: um `MagicMock` devolve verdadeiro para qualquer atributo, então os comandos passavam a ser tratados como produção e a trava barrava tudo. O `guardas` é testado com `SimpleNamespace` justamente para não repetir essa armadilha.

---

### 7.20 `Resources/Solda.txt` recebido — a lista sai de vazia (21/09/2026)

O arquivo estava em `ControleProducao/Resources/Solda.txt` no projeto original e foi copiado sem alteração para `app/modules/pedidos_wbc/resources/Solda.txt`. São 5.229 linhas / **5.015 códigos distintos** (há repetições no original, absorvidas pelo `frozenset` sem mudar comportamento).

**O que isso muda**: até 20/09 o porte rodava com a lista vazia, o que era a escolha segura na ausência do arquivo, mas **não** era equivalente ao legado. Com a lista vazia, todo item que o addon mandaria para `ItemsGroupCode = 332` ia para `358`. Ou seja: **qualquer item criado pelo `processar-novos` antes de hoje pode estar no grupo errado**, e isso não aparece como erro em lugar nenhum — o item é criado com sucesso, só no grupo 358. Vale conferir os itens criados nos testes de homologação antes de usá-los como base de comparação.

**Dois pontos do C#, com campos diferentes**, ambos conferidos na fonte antes de considerar o porte fiel:

| Onde | Fonte | Campo comparado |
|---|---|---|
| `CriaItem` → grupo 332 vs 358 | `ProcessDefault.cs` ~941 | `ItemCode` (`PrdCode`) |
| `UpdateItem` → força 332 em item existente | `IntegraPedidoWBC.b1f.cs` ~368 | `PrdArv` |

A assimetria parece erro de digitação do autor original, mas está nas duas fontes e foi preservada. Registrada aqui para que ninguém a "conserte" sem decidir antes se é para consertar.

**Precedência preservada**: no `CriaItem` o teste dos prefixos de 333 vem primeiro e vence. **1.477 dos 5.015 códigos da Solda contêm algum prefixo de 333** e portanto nunca chegam a ser avaliados para 332. É assim no legado; se essa ordem se inverter, esses 1.477 mudam de grupo em massa. Há teste cobrindo.

**Duas mudanças de implementação, sem efeito no resultado**: a lista virou `frozenset` (o C# varre o array inteiro com `foreach` por item; com 5.015 entradas isso é desperdício gratuito e o resultado da comparação é idêntico) e `carregar_solda()` ganhou `lru_cache` — sem ele o arquivo de 115 KB seria lido do disco **uma vez por item da estrutura**, porque `_cria_item` chama a função a cada item. O C# também relê o arquivo a cada `CriaItem`; não é comportamento a preservar, é custo a evitar.

`tests/test_listas_fixas.py` (novo, 5 testes): carga do arquivo real, cache, comparação exata (não por prefixo), precedência do 333, e independência da Explosao. Suíte: 93 → 98 testes.

Com isso, **a pergunta 8 da seção 9 está resolvida** e sai da lista de pendências.

---

### 7.21 Como verificar o PATCH parcial de `DocumentLines` — `diag patch-parcial` (21/09/2026)

A pendência da seção 7.11 estava aberta desde 16/09 sem um jeito prático de fechá-la. Agora tem comando:

```bash
python -m app.cli diag patch-parcial 84263              # só leitura, por DocNum
python -m app.cli diag patch-parcial --docentry 19466   # por DocEntry
python -m app.cli diag patch-parcial 84263 --aplicar    # experimento controlado
```

**Parte 1 — forense, sem escrever nada.** Lê `ADOC`/`ADO1`, o histórico que o B1 guarda a cada atualização do documento, e mostra quantas linhas o pedido tinha em cada versão. Se a contagem cair de uma versão para a seguinte, alguma atualização comeu linhas — e o relatório diz em qual. Serve para olhar retroativamente o 84263/19466, que já passou pelo PATCH parcial, sem precisar de uma fotografia "antes" que ninguém tirou na época.

Duas ressalvas honestas sobre essa parte: o histórico só existe se o log de alterações estiver ativo para o objeto (se vier vazio, não prova nada em nenhuma direção, e o comando diz isso em vez de dar sinal verde); e uma perda detectada não aponta o culpado sozinho, porque outros add-ons e o próprio usuário também atualizam pedidos.

**Parte 2 — experimento controlado (`--aplicar`).** Fotografa as linhas em `RDR1`, reenvia **uma** linha com o valor de `U_INO_OP` que ela já tem, fotografa de novo e compara. O corpo enviado é o mesmo formato de `marca_op_nas_linhas` (`DocumentLines` com só `LineNum` + um campo), e o valor é igual ao atual de propósito: assim tudo o que aparecer na comparação é efeito colateral do mecanismo, não da alteração pedida. O comando detecta os dois modos de falha — linha que sumiu, e campo que voltou zerado sem ter sido enviado (foi assim que o `U_B1SYS_RevenueInd2` apareceu).

Passa pela trava de produção como qualquer escrita, e a comparação normaliza tipos (`Decimal('2')` vs `2` não vira "alteração") para o relatório não encher de ruído.

**Onde rodar**: em homologação, num pedido com várias linhas.

> ⚠️ **Correção (21/09/2026)**: este guia vinha registrando o incidente como "pedido 84263/19466", como se fossem o mesmo documento. **Não são.** O DocNum 84263 tem DocEntry 19253; o `Orders(19466)` do log de 16/09 é outro documento. O par estava errado desde a seção 7.11, e auditar por "84263" olha o documento errado. Por isso o comando ganhou `--docentry`: `DocNum` e `DocEntry` são numerações distintas do B1 e não se deduzem uma da outra.

### 7.22 Resultado da verificação (21/09/2026)

Rodado contra homologação (o `--aplicar` passou sem `--producao`, então `SL_COMPANY_DB` não era a de produção).

**Parte 1, no DocNum 84263 (DocEntry 19253)**: 99 versões em `ADO1`, todas com 2 linhas, e 2 linhas hoje em `RDR1`. Nenhuma versão perdeu linha.

**Parte 2, no mesmo pedido**: reenviada só a linha 0 (`I000006`, `U_INO_OP` 153247). Depois: 2 linhas, nenhum campo alterado. **Passou.**

**O que isso prova, e o que não prova.** Prova que o mecanismo preservou a linha não enviada neste caso. Não é o fechamento completo da pendência, por três motivos que ficam registrados em vez de arredondados:

1. **O documento do incidente não foi auditado.** A parte 1 rodou no 19253; o PATCH que falhou em 16/09 foi no `Orders(19466)`. Falta rodar `diag patch-parcial --docentry 19466`.
2. **Duas linhas é o caso mínimo.** Uma linha não enviada sobreviveu — é evidência real, mas fraca. Um pedido com 10+ linhas, enviando uma só, é o teste que convence.
3. **O histórico começa na versão 10.** As versões 1–9 não estão em `ADO1`, então as primeiras atualizações do documento não foram examinadas.

Nada disso desfaz o resultado: a hipótese de que o PATCH parcial destrói a coleção **não se sustentou** no teste direto. Mas a pendência só fecha depois de (1) e (2).

`tests/test_patch_parcial.py` (novo, 7 testes) cobre a ferramenta, não o mecanismo: detecção de linha sumida, de campo alterado, ausência de falso positivo, normalização de tipo, e a conferência de que o corpo do experimento é idêntico ao do código de produção — se não fosse, o teste não provaria nada sobre o código real. Suíte: 98 → 105 testes.

---

### 7.23 Como validar o porte contra o legado — procedimento do `comparar-ops` (21/09/2026)

O `comparar-ops` existe desde 15/09 (seção 7.12) e nunca rodou com baseline real. Este é o procedimento.

#### A premissa

O comando compara **o mesmo orçamento WBC nos dois schemas**: `SBOALTAMIRAPROD` (onde o addon legado roda) e `SBOALTAMIRAHOMOLOG` (onde este porte roda). Para o resultado significar alguma coisa, o mesmo orçamento precisa ter sido processado **pelos dois**. Não é um teste que se roda uma vez: é um ciclo — processar, comparar, corrigir, reverter, repetir.

#### Pré-requisitos

1. **Usuário HANA com SELECT nos dois schemas.** Sem isso o comando falha na leitura do lado legado.
2. **`.env` coerente e apontado para homologação:** `SL_COMPANY_DB` e `HANA_SCHEMA` na base de homologação, `HANA_SCHEMA_LEGADO=SBOALTAMIRAPROD`. Os dois primeiros têm que casar — se divergirem, o porte lê de uma base e grava na outra (aconteceu em 21/09, ver 7.19).
3. **Homologação precisa conter o pedido.** Se a cópia for anterior ao pedido escolhido, não há o que processar. O WBC (SQL Server) é o mesmo para os dois lados e só é lido.

#### Escolher o candidato

Precisa ser um orçamento que **o legado já processou em produção** e cujo pedido **ainda não foi processado em homologação**. Esta query lista os candidatos com quantas OPs o legado gerou para cada um (ajuste os nomes de schema):

```sql
SELECT H."U_ORCNUM_WBC" AS "orcamento",
       HO."DocNum"      AS "pedido",
       (SELECT COUNT(*)
          FROM "SBOALTAMIRAPROD"."OWOR" W
          INNER JOIN "SBOALTAMIRAPROD"."ORDR" PO ON W."OriginAbs" = PO."DocEntry"
         WHERE PO."DocNum" = HO."DocNum" AND W."Status" <> 'C') AS "ops_no_legado"
FROM "SBOALTAMIRAHOMOLOG"."OOPR" H
  INNER JOIN "SBOALTAMIRAHOMOLOG"."OPR1" L  ON H."OpprId" = L."OpprId" AND L."ObjType" = 17
  INNER JOIN "SBOALTAMIRAHOMOLOG"."ORDR" HO ON L."DocId"  = HO."DocEntry"
WHERE IFNULL(H."U_INO_IntegrouWBC",'N') = 'Y'
  AND IFNULL(HO."U_INO_ProcessWBC",'N') = 'N'
  AND HO."DocStatus" = 'O'
  AND IFNULL(H."U_ORCNUM_WBC",'') <> ''
ORDER BY 3;
```

**Comece pelo menor** que tenha pelo menos um semiacabado — a cascata recursiva é a parte mais complexa e a que mais erra, mas depurar 60 OPs na primeira rodada não ajuda ninguém. Um candidato com 5 a 15 OPs no legado é o ponto de partida.

#### O ciclo

```bash
# 1. Baseline: antes de processar. O lado legado deve vir populado e o novo, vazio.
#    Se o lado legado vier vazio, pare: ou faltou permissão no schema, ou o orçamento
#    escolhido não foi processado em produção. Um relatório com os dois lados vazios
#    não é aprovação.
python -m app.cli pedidos-wbc comparar-ops 00120634

# 2. Processar em homologação (anote a data, serve no passo 4).
python -m app.cli pedidos-wbc processar-novos <opp_id>

# 3. A comparação de verdade.
python -m app.cli pedidos-wbc comparar-ops 00120634 --detalhes

# 4. Se a homologação já acumulou OPs de execuções anteriores, isole a rodada atual.
python -m app.cli pedidos-wbc comparar-ops 00120634 --desde 2026-09-21

# 5. Achou divergência, corrigiu o código: zere e repita.
python -m app.cli pedidos-wbc cancelar-ops 00120634 --orcamento
python -m app.cli pedidos-wbc processar-novos <opp_id>
```

O passo 5 é o motivo de `cancelar-ops` existir: ele devolve o pedido a "não processado" e permite reprocessar limpo, sem `--force` e sem OP duplicada. Se `cancelar-ops` for barrado por OP liberada, `manutencao-op replanejar --pedido <n>` destrava.

#### O que olhar, além da contagem

`--detalhes` traz os componentes de cada OP. A contagem bater e os componentes não é o erro mais provável e o mais fácil de deixar passar — a cascata pode criar o número certo de OPs com a estrutura errada. Confira também o item produzido de cada OP e as quantidades planejadas.

#### Quatro coisas que distorcem o resultado

1. **Itens em grupo errado.** Itens criados pelo porte em homologação **antes de 21/09** podem estar no `ItemsGroupCode` 358 em vez de 332, efeito da `Solda.txt` vazia (seção 7.20). Valide num orçamento cujos itens ainda não existam em homologação, ou confira o grupo dos que já existem.
2. **Itens que já existem.** Se a cópia de homologação veio depois de o legado rodar, os itens que o addon criou já estão lá — e o caminho de criação de item do porte não é exercitado. Isso não invalida a comparação das OPs, mas deixa uma parte do módulo 2 sem teste.
3. **Cancelamentos manuais em produção.** São ruído operacional; por isso as canceladas ficam fora por padrão. O relatório avisa quando o único registro de um item no legado está cancelado.
4. **Múltiplas gerações.** Pedidos reprocessados várias vezes acumulam gerações de OPs (o 84371 tem três). Use `--desde` para isolar.

#### Quando considerar validado

Quando um orçamento com semiacabados fechar: mesmo número de OPs, mesmos itens produzidos, mesmas quantidades e mesmos componentes — e o `U_INO_OP` das linhas do pedido apontando para as OPs certas. Depois disso, repetir num orçamento grande. Só então a decisão sobre produção tem base.

---

### 7.24 Primeira validação contra produção — resultado e dois defeitos (21/09/2026)

Primeira execução real do `comparar-ops` com baseline de produção. Orçamento **00120634**, pedido 84263.

**O resultado principal é bom**: 29 OPs no legado contra 29 no porte, mesmos itens produzidos, mesmas quantidades planejadas, mesmo `U_INO_LinhaRef`, e componentes **idênticos em 24 dos 26 itens**. A cascata recursiva de semiacabados — a parte mais complexa e a de maior risco — reproduziu a estrutura do legado. Diferenças que **não** são defeito: o porte cria as OPs Planejadas e as do legado estão Liberadas (o legado liberou depois); e 35 itens aparecem só do lado novo porque a OP correspondente no legado foi cancelada manualmente.

Duas divergências. A primeira era bug e foi corrigida; a segunda, investigada até a origem do dado, **não é bug do porte**.

#### Defeito 1 — recurso de rateio faltando (CORRIGIDO)

O item `I000005` saiu sem a linha `GGF_00120634I0000050`: 12 componentes no legado, 11 no porte. O `INO_LOG` deu a causa direta:

```
Erro ao preencher recurso: unsupported operand type(s) for /: 'float' and 'decimal.Decimal'
```

**Causa imediata**: `hdbcli` devolve `decimal.Decimal` para colunas numéricas, e `Decimal` não opera com `float`. A conta do rateio (`(qtd * custo) / valor_total * valor_linha`) misturava os dois e levantava `TypeError`. O `except` de `_processa_grupo_producao` — réplica fiel do `catch` do C# — engoliu a exceção, registrou no `INO_LOG` e **a OP foi criada sem a linha de rateio**. Mesma classe do `Decimal is not JSON serializable` da seção 7.8: tipo de banco escapando para dentro da lógica.

**Causa secundária, independente e também real**: o código do Recurso é montado com o `valor_linha` colado no fim, sem separador decimal. O legado usa `double.ToString()` em pt-BR, onde um valor inteiro sai **sem parte decimal** (`0` → `"0"`, `3854.14` → `"3854,14"` → `385414`). O porte usava `str(valor)`, que acrescenta o `.0` (`str(0.0) == "0.0"` → `"00"`; `str(Decimal("0.00"))` → `"000"`). Para valores não inteiros os dois coincidem — por isso o `I000006` funcionou —, mas para o `I000005`, com valor 0, o porte procurava `GGF_00120634I00000500` e **nunca reconhecia** o `GGF_00120634I0000050` que o legado já havia criado. É o débito nº 8 (cultura de parsing decimal) no lugar mais traiçoeiro: não num cálculo, mas na formação de um identificador.

**O que foi feito:**

- `_num()` converte na fronteira, uma vez, em vez de espalhar `float(...)` por cada operação — nas leituras de `BUSCA_MAX_ITEM_LINHA`/`MAX_PRECO_PEDIDO` e defensivamente dentro de `cria_recurso_rateio`.
- `_digitos_do_valor_como_no_legado()` replica o `double.ToString()` do C#.
- `valor_total == 0` passa a devolver `""` com aviso, em vez de `ZeroDivisionError` — que o mesmo `except` engoliria do mesmo jeito.
- O `except` ganhou `logger.exception`. O `catch` silencioso do C# foi preservado (abortar o pedido por causa do rateio seria pior), mas o erro passa a aparecer no log da aplicação. **Esse é o aprendizado que vale mais que a correção**: um `TypeError` nosso ficou invisível no console e só apareceu numa comparação com produção, dias depois. Réplica fiel do tratamento de erro do legado não pode significar réplica fiel da invisibilidade dele.

`tests/test_recurso_rateio.py` (novo, 23 testes), incluindo um que prova a premissa (`2.0 / Decimal("10")` levanta `TypeError`) e um que confere que o nome do recurso do `I000005` fecha igual ao do legado. Suíte: 107 → 130 testes.

#### Divergência 2 — `TPO00000000000000000`: 0,01 no porte, 2 no legado (RESOLVIDA — não é bug do porte)

Investigada até o dado em 21/09/2026. **A conclusão inverteu a suspeita inicial**: o valor do porte é rastreável até a origem; o do legado não é.

**O que o dado mostra.** As 18 linhas de `TPO00000000000000000` do orçamento 00120634 em `INTEGRACAO_ORCPRDARV`:

- **`ORCPES` é igual a `ORCQTD` em todas as 18 linhas.** Peso e quantidade são o mesmo número nessa tabela para esse item — o que torna a hipótese "colunas trocadas na leitura" irrelevante aqui: trocar as duas não mudaria nada.
- **Nenhuma linha tem `ORCQTD = 2`**, e nenhum valor arredonda para 2.
- **Exatamente uma linha arredonda para 0,01**: a de `0.005` (id 3529976), que está em `ORCPRDARV_NIVEL = 3` — o nível esperado para um filho de um pai de nível 2, que é o que o `newBuscaItem` seleciona (`NIVEL(pai)+1`).

**O que os dois lados gravaram:**

| Lado | `PlannedQty` | `U_INO_PESO` |
|---|---|---|
| `SBOALTAMIRAPROD` (legado) | 2 | **0** |
| `SBOALTAMIRAHOMOLOG` (porte) | 0,01 | 0,01 |

O porte está coerente com a origem: `peso = qtd = 0.005`, e `round(0.005, 2) = 0.01` nos dois campos — exatamente o que o código faz, e o que o C# também faz (`Math.Round(item.Quantidade, 2)` e `Math.Round(item.peso, 2)`, `ProcessDefault.cs` ~542/551).

O lado do legado **não fecha nem com o próprio código dele**: o `CriaOPSA` grava `U_INO_PESO = Math.Round(peso, 2)` da mesma fonte, e não existe linha de TPO com peso 0 no orçamento. Uma OP criada por aquele código, com esses dados, não poderia ter saído com peso 0 e quantidade 2.

**Conclusão**: ou os dados do WBC mudaram desde agosto, ou aquela linha da OP de produção foi **ajustada manualmente** depois de criada — plausível, já que as OPs de produção estão Liberadas e passaram por apontamento. Nenhuma alteração de código foi feita, porque não há o que corrigir.

Para confirmar a edição manual:

```sql
SELECT "DocNum", "CreateDate", "UpdateDate", "Status"
FROM "SBOALTAMIRAPROD"."OWOR" WHERE "DocNum" = 151537;
```

`UpdateDate` maior que `CreateDate` indica alteração após a criação.

**A lição metodológica, que vale mais que o caso**: o baseline de produção **não é automaticamente a verdade**. Ele é o resultado do legado *mais* tudo que aconteceu com aquele documento depois — liberação, apontamento, ajuste manual. Numa comparação como esta, "o porte divergiu do legado" é uma hipótese a testar contra a origem do dado, não um veredito. Aqui, ir até a `INTEGRACAO_ORCPRDARV` foi o que separou um bug real (o rateio, divergência 1) de uma diferença que não é bug.

#### Resíduo

Este orçamento foi processado em homologação **antes** da `Solda.txt` ser instalada (seção 7.20), então itens criados nesta rodada podem estar no `ItemsGroupCode` 358 em vez de 332. A comparação de OPs não olha grupo de item, então isso não aparece no relatório acima.

---

### 7.25 Segunda validação — orçamento 00124945, e a decisão sobre arredondamento (21/09/2026)

Segundo orçamento validado, agora um grande: pedido 84348, **68 OPs no legado contra 68 no porte**, e **componentes idênticos em 43 dos 47 itens**. As OPs de 23, 10 e 8 componentes (os `I000003`) bateram linha a linha. Com o 00120634 (29 OPs), são dois orçamentos fechando.

O ciclo do procedimento da seção 7.23 funcionou exatamente como desenhado: `cancelar-ops` foi barrado por 68 OPs Liberadas → `manutencao-op replanejar --pedido 84348` destravou → `cancelar-ops` zerou o pedido → `processar-novos` recriou → `comparar-ops` comparou. Quatro comandos, sem intervenção manual no SAP.

#### As quatro divergências restantes: arredondamento, não lógica

Todas de **um centavo**, e com direção inconsistente — três vezes o porte para cima, uma para baixo:

| Item | Componente | Legado | Porte |
|---|---|---|---|
| `PPLSUP00000075000000` | `TPO00000000000000000` | 4,4 | 4,41 |
| `PPLSUPLIDIR200000000` | `TPO00000000000000000` | 3,16 | 3,17 |
| `PPLSUPLIESQ200000000` | `TPO00000000000000000` | 3,16 | 3,17 |
| `PPLSUPSOLDAVID000000` | `ALPRFQ24-KG18000F066` | 105,62 | 105,61 |

**Causa, confirmada numericamente.** Os valores chegam exatamente em `.xx5` (a `verificaSemiAcabadoPeso` converte com `CONVERT(varchar, ...)`), e os dois lados arredondam para 2 casas com regras diferentes:

- **C#** `Math.Round(double, int)` escala por 100, arredonda e divide. A multiplicação carrega erro de ponto flutuante que pode atravessar o empate.
- **Python** `round()` inspeciona o valor decimal verdadeiro do `double`.

Um modelo do algoritmo do C# reproduz **os três padrões observados**, o que confirma a causa:

| valor | decimal real do double | C# | Python | observado |
|---|---|---|---|---|
| 105,615 | 105,614999999999994884… | 105,62 | 105,61 | legado 105,62 / porte 105,61 |
| 4,405 | 4,405000000000000248689… | 4,40 | 4,41 | legado 4,4 / porte 4,41 |
| 3,165 | 3,165000000000000035527… | 3,16 | 3,17 | legado 3,16 / porte 3,17 |

**O Python está mais correto.** Em 105,615 o `double` vale de fato 105,61499…, que arredonda para 105,61; o 105,62 do legado vem do erro introduzido pela multiplicação.

#### Decisão do Anderson (21/09/2026): manter o Python

**Nenhuma alteração de código.** As OPs do porte terão a quantidade matematicamente correta, e o legado seguirá com a dele. Consequência operacional, que é o preço da decisão:

> **Essas divergências de ±0,01 em quantidade de componente vão aparecer no `comparar-ops` para sempre.** Não são bug e não devem ser investigadas de novo. Em cada auditoria futura, uma divergência de exatamente um centavo numa quantidade cujo valor de origem termina em `5` é esperada — qualquer outra magnitude, ou um centavo num valor que não é empate, é coisa nova e merece investigação.

As alternativas consideradas e recusadas: replicar o algoritmo do C# (daria paridade bit-a-bit, ao custo de reproduzir o defeito numérico de propósito) e tolerar 0,01 no comparador (limparia o relatório, mas esconderia uma divergência real de um centavo que tivesse outra causa).

#### Balanço da validação do módulo 2

| Orçamento | OPs legado | OPs porte | Estruturas idênticas | Pendências |
|---|---|---|---|---|
| 00120634 | 29 | 29 | 26/26 (após a correção do rateio) | — |
| 00124945 | 68 | 68 | 43/47 | 4 × arredondamento (decidido: esperado) |

Dois bugs reais encontrados e corrigidos em toda a validação: o `Decimal`/`float` no rateio (7.24) e o nome do Recurso (7.24). Nenhum na cascata recursiva de semiacabados — a parte que dava mais medo.

---

### 7.26 Faixa de status é alfabética, não do ciclo de vida (21/09/2026)

Primeiro uso real do `manutencao-op buscar`. A query rodou sem erro, mas devolveu vazio para `--status-de P --status-ate C`.

**A causa não é o pedido, é o filtro.** O `between` do original compara os códigos de status como **texto**, então a faixa é alfabética — `C < L < P < R` — e não tem relação nenhuma com o ciclo de vida da OP (que seria P → R → L, com C à parte). Pedir "de Planejada até Cancelada" é um intervalo invertido: zero linhas, sempre.

O problema não é a regra, que é do legado e foi preservada. É que o resultado é **vazio silencioso**, indistinguível de "este pedido não tem OP". Quem escreveu o comando errado nesta sessão fui eu, ao sugerir a linha de teste — o que é a melhor demonstração possível de que a armadilha pega quem conhece o código.

**Correção**: faixa invertida agora é erro explícito, com a ordem real e a saída na mensagem:

```
Faixa de status invertida: 'P' vem depois de 'C' na ordem alfabética usada pelo
filtro (C < L < P < R), então o `between` devolveria zero linhas.
Para essa faixa, inverta: --status-de C --status-ate P. Para todos os status,
omita os dois filtros.
```

Mesma guarda aplicada à faixa numérica de OP (`--op-de 9010 --op-ate 9001`). O `--help` do comando passou a avisar que a faixa é alfabética. É o terceiro caso do mesmo padrão nesta seção — junto com o `--op-ate` sozinho (7.18) e o status inválido: **regra do legado preservada, silêncio do legado não.**

`_ORDEM_STATUS` é derivada de `STATUS_OP` e tem um teste travando o valor `("C", "L", "P", "R")`, porque "consertar" isso para a ordem do ciclo de vida faria o filtro divergir do legado sem avisar ninguém.

Suíte: 130 → 135 testes.

---

### 7.27 Primeiro `encerrar` real — a filial (`BPLId`) que a DI API preenchia sozinha (21/09/2026)

Primeira execução do `manutencao-op encerrar` em ambiente real. OP 156209 do pedido 84348 em homologação, escolhida por ser a de menor alcance: quantidade planejada 1, dois componentes, estoque conferido antes (9.106 e 127.740 contra 10,09 e 0,55 necessários).

**Falhou na etapa 2, e a falha valeu mais que um sucesso:**

```
OP 156209 falhou em 'saída de insumo': POST /InventoryGenExits:
Specify an active branch  [OIGE.BPLId]
```

**O que isso confirmou de graça**: a entidade `InventoryGenExits` **existe e o nome está certo** — o erro é validação de campo, não rota inexistente. Era uma das duas suposições que eu tinha derivado do C# sem confirmar (seção 7.18). A outra, `BaseType`/`BaseLine`, segue sem prova, porque a execução não chegou lá.

**A lacuna**: a empresa usa filiais, e a Service Layer exige `BPLId` explícito na saída e na entrada de mercadoria. O C# não informa esse campo em lugar nenhum — a DI API o preenchia a partir da filial padrão do usuário logado. Não havia o que portar: é comportamento a **inventar**, não a traduzir. É o quinto caso do mesmo padrão (`ProductionOrderIssueType`, tipos dos campos do UDO, `#` na URL, `BaseType`, agora `BPLId`), e mantém a divergência DI API → REST como o risco residual principal do porte.

**Estado deixado pela falha**: a etapa 1 (`corrigeOP`) já havia rodado, então a OP ficou **Liberada** com baixa manual nas linhas. Nenhum lançamento de estoque aconteceu. Foi o comportamento não-transacional documentado, e parou no ponto mais seguro da cadeia; desfeito com `manutencao-op replanejar 156209`.

#### A correção: derivar do dado, não configurar

`_filial_do_movimento()` resolve o `BPLId` por ordem de preferência:

| # | Origem | Por quê nessa posição |
|---|---|---|
| 1 | Filial do **depósito da OP** (`OWHS."BPLid"`) | Um lançamento de estoque acontece na filial do depósito, e o B1 exige essa coerência |
| 2 | Filial dos **depósitos dos componentes** (`WOR1` → `OWHS`) | Cobre a OP cujo depósito de produto não tem filial |
| 3 | Filial do **pedido de origem** (`ORDR."BPLId"`) | Vínculo mais fraco: uma OP pode atender pedido de outra filial |
| 4 | `SL_BUSINESS_PLACE_ID` do `.env` | Último recurso, de propósito — um número fixo quebra em silêncio quando a segunda filial nascer |

Cada candidato é conferido contra as filiais **ativas** (`OBPL."Disabled" <> 'Y'`), não só contra as existentes. Não é preciosismo: no ambiente da Altamira existem duas filiais e a de código 2 está desabilitada. Derivar uma filial desabilitada daria a mesma recusa do SAP, só mais tarde e com pior diagnóstico.

Curiosidade de nomenclatura que custa tempo se não estiver escrita: em `OWHS` a coluna é `"BPLid"` (d minúsculo) e em `ORDR` é `"BPLId"` (D maiúsculo). Inconsistência do próprio B1.

#### Duas decisões de ordem que vieram do incidente

**A filial é resolvida ANTES de qualquer escrita.** Se não houver filial ativa determinável, a OP não é nem liberada. Foi exatamente a classe de meio-caminho que este teste produziu, e não há razão para mexer no documento antes de saber que a cadeia pode terminar.

**Resolvida uma única vez por OP**, e o mesmo valor vai para a saída e para a entrada — saída e entrada em filiais diferentes deixariam o estoque inconsistente entre elas. Há teste travando isso.

`tests/test_manutencao_op_encerrar.py` ganhou 9 testes: as quatro origens na ordem, filial desabilitada ignorada, cadastro sem filial ativa, erro legível quando nada serve, nenhuma escrita quando a filial não resolve, e saída/entrada na mesma filial. Os testes existentes do `finalizar_ops` passaram a despachar o HANA simulado **por conteúdo da query** em vez de por ordem de chamada — a resolução de filial acrescentou uma leitura e quebrou os mocks posicionais, que é o defeito clássico desse estilo de mock. Suíte: 135 → 144 testes.

**Ainda em aberto**: o `encerrar` continua sem execução bem-sucedida. A OP 156209 está pronta para a segunda tentativa, com estoque conferido — e é um bom caso para o `BaseLine`, porque os dois componentes têm quantidades bem distintas (10,09 e 0,55), então uma troca de linha apareceria na hora.

---

### 7.28 A outra metade: a série de numeração (`Series`) — 21/09/2026

Depois de derivar o `BPLId` (7.27), o SAP continuou respondendo exatamente a mesma coisa: `Specify an active branch [OIGE.BPLId]`. A filial era resolvida corretamente (`filial 1`, origem: depósito da OP) e o documento seguia recusado.

**Faltava a `Series`.** A mensagem do SAP fala em filial, mas o que ele não consegue é resolver a numeração — e sem numeração não há documento. Informar a filial sem a série não basta.

Os dados do ambiente deram a resposta pronta:

| Tabela | O que mostra |
|---|---|
| `NNM1` | ObjectCode 60 → Series 20 "Primário", `BPLId` **nulo**; ObjectCode 59 → Series 19 "Primário", `BPLId` **nulo** |
| `OIGE` (documentos existentes) | `Series = 20` **com** `BPLId = 1` |

Ou seja: nesta empresa as séries **não são por filial** — uma série "Primário" serve todas —, e os documentos que o legado criou carregam a série e a filial juntas. A DI API escolhia a série padrão do usuário logado; em REST, de novo, é explícito.

É o **sexto** caso do padrão DI API → REST, e o mais instrutivo deles: os outros cinco eram um campo faltando, este é um campo faltando **cuja ausência o SAP reporta com o nome de outro campo**. A mensagem aponta para `OIGE.BPLId` e o que falta é `Series`. Seguir a mensagem de erro literalmente levaria a mexer no campo errado indefinidamente — o que aconteceu na primeira tentativa de correção.

#### `_serie_do_documento()`

Precedência, cobrindo os dois arranjos possíveis porque uma empresa pode passar a ter séries por filial sem avisar:

1. Série cadastrada **para aquela filial**;
2. Série **sem filial**, que serve todas.

Nunca uma série bloqueada (`Locked = 'Y'`). Empate resolvido pelo menor número, para ser determinístico — série diferente a cada execução espalharia a numeração dos documentos sem que ninguém entendesse por quê.

`ObjectCode`: `'60'` = Saída de Mercadoria (`OIGE`/`InventoryGenExits`), `'59'` = Entrada de Mercadoria (`OIGN`/`InventoryGenEntries`).

#### A guarda que isso acrescenta

**As duas séries são resolvidas antes de qualquer escrita**, junto com a filial. O motivo é concreto: descobrir que falta a série da **entrada** depois de a saída já estar lançada deixaria baixa de insumo sem a entrada correspondente — o pior estado da cadeia, que não se desfaz por comando e exige cancelar o documento no SAP à mão. Tem teste travando isso, com o cenário exato (série da saída existe, da entrada não).

#### Um detalhe de log que custou clareza

O log imprimia `OP 157974` quando o comando recebeu `156209`. Não era bug de lógica — 157974 é o `DocEntry` e 156209 o `DocNum` —, mas em toda a CLI "OP \<n\>" significa DocNum, e a mensagem parecia falar de outra OP. Passou a imprimir `OP DocEntry=157974`. Mensagem ambígua durante a investigação de um erro custa mais do que parece.

`tests/test_manutencao_op_encerrar.py` ganhou 5 testes: precedência da série da filial, exclusão de bloqueadas e de outras filiais, determinismo do `ORDER BY`, erro legível sem série, saída e entrada com séries **diferentes** (20 e 19), e nenhuma escrita quando uma das duas não resolve. Suíte: 144 → 149 testes.

**Placar das três tentativas de `encerrar`**: nenhum lançamento de estoque indevido, nenhum documento órfão, nenhuma OP encerrada sem movimentação. A cadeia falhou sempre no mesmo ponto seguro — que é o comportamento que as guardas foram desenhadas para produzir.

---

### 7.29 O campo da filial não se chama `BPLId` — e uma heurística minha que não se sustenta (21/09/2026)

Terceira tentativa do `encerrar`, com `BPLId` **e** `Series` no corpo, e a mesma recusa: `Specify an active branch [OIGE.BPLId]`.

A resposta veio de ler um documento real, com o comando que existe exatamente para isso:

```bash
python -m app.cli diag entidade InventoryGenExits --campos
```

Uma saída de mercadoria criada pelo legado tem:

| Campo | Valor |
|---|---|
| `Series` | 20 |
| **`BPL_IDAssignedToInvoice`** | **1** |
| `BPLName` | Altamira Indústria Metalúrgica Ltda. |

**Não existe `BPLId` na entidade.** O campo da filial em `Documents` é `BPL_IDAssignedToInvoice`. O nome é ruim — fala de *invoice* num documento de estoque —, mas é o que a Service Layer expõe.

E a `IGE1` não tem coluna de filial (o SQL de linha falhou com `invalid column name: L.BPLId`), o que descartou de uma vez a hipótese de filial no nível da linha.

#### A heurística que me custou duas rodadas

Depois da segunda falha eu argumentei o seguinte, e registrei como conclusão:

> "A Service Layer não reclamou `Property 'BPLId' is invalid` nem para `Series`. Ela rejeita propriedade inexistente com essa mensagem. Como não rejeitou, **os dois nomes estão certos**."

**Isso não se sustenta.** A Service Layer **ignora em silêncio** propriedade desconhecida no POST — pelo menos nesta versão e neste endpoint. A ausência de "invalid property" não prova nada sobre o nome do campo. Era uma inferência apresentada como fato, e ela me fez procurar o problema em todo lugar menos no nome.

Pior: a mensagem de erro do SAP cita `OIGE.BPLId`, que é o nome da **coluna da tabela**, não o da propriedade da API. Quem segue a mensagem literalmente escreve `BPLId` no payload — exatamente o que eu fiz, duas vezes.

**A regra prática que fica**: contra a Service Layer, nome de campo se descobre **lendo um registro real**, nunca deduzindo do nome da coluna, da mensagem de erro ou da ausência de erro. O `diag entidade` foi construído em 15/09 para isso (seção 7.10) e eu deixei de usá-lo nas duas primeiras tentativas. Custou três execuções.

#### Estado

`BPL_IDAssignedToInvoice` + `Series` nos dois documentos, com um teste que também afirma que `BPLId` **não** vai no corpo — enviar um campo que a API ignora dá a impressão de estar preenchido quando não está.

O log do corpo enviado, acrescentado como diagnóstico, **fica permanente**: é um lançamento de estoque irreversível, e registrar o que foi mandado é o mínimo para auditar depois.

**Quatro tentativas, nenhum lançamento indevido, nenhum documento órfão.** O `BaseType`/`BaseLine` continua sem prova — nunca chegou a ser avaliado.

---

### 7.30 Módulo 3 validado — `encerrar` executado e conferido (21/09/2026)

Quinta tentativa, sucesso. OP 156209 (pedido 84348, homologação): saída `DocEntry 65026`, entrada `DocEntry 57815`, OP encerrada.

#### A conferência

| O que | Esperado | Obtido |
|---|---|---|
| OP: status | `L` (Encerrada) | `L` ✓ |
| OP: apontado | 1 | 1 ✓ |
| OP linha 0: `IssuedQty` | 10,09 | 10,09 ✓ |
| OP linha 1: `IssuedQty` | 0,55 | 0,55 ✓ |
| Saída: `ALPCFF01010060000000` | 10,09, `BaseLine` 0 | ✓ |
| Saída: `TPO00000000000000000` | 0,55, `BaseLine` 1 | ✓ |
| Entrada: item produzido | qtd 1, depósito 01 | ✓ |
| `BaseType` nos três | 202 | ✓ |

**As duas suposições derivadas do C# estão confirmadas.** `BaseType = 202` é aceito, e o `BaseLine` casa cada componente com a sua própria linha da OP — cada item saiu com a quantidade certa, nada cruzado. A OP foi escolhida justamente porque as duas quantidades são bem distintas (10,09 e 0,55): um `BaseLine` trocado apareceria na hora. Era a última dúvida aberta do módulo (seção 7.18), e fecha aqui.

**As séries também se provaram sozinhas**: a `NNM1` tinha `NextNumber` 63.715 para a série 20 e 57.064 para a 19, e os documentos saíram com exatamente esses `DocNum`. Confirmação independente de que a derivação da seção 7.28 escolheu as séries certas.

#### Uma imprecisão de log corrigida na mesma passada

O log fechava com `Planejada -> Encerrada`, mas o `corrigeOP` já havia liberado a OP antes disso — o status vinha do levantamento e estava velho. Passou a refletir o estado real (`Liberada -> Encerrada`), com teste. Não é cosmético: esconder a passagem por Liberada esconde exatamente o estado que sobra quando a cadeia falha depois desse ponto, que foi o que aconteceu quatro vezes hoje.

#### Estado do módulo 3

| Comando | Evidência |
|---|---|
| `buscar` | Executado em homologação, 68 OPs, status traduzido e ordenação conferidos |
| `replanejar` | Executado, 68 OPs de Liberada para Planejada, e várias vezes em OP única |
| `encerrar` | **Executado e conferido documento a documento** |
| `liberar` | **Nunca executado como comando.** A função (`muda_status`) e o valor (`boposReleased`, via `corrige_op`) estão provados separadamente, então o risco residual é o da combinação — mas isso é inferência, não evidência |

**Cinco tentativas de `encerrar`, quatro falhas, nenhum lançamento indevido, nenhum documento órfão, nenhuma OP encerrada sem movimentação.** As guardas foram desenhadas para que a falha parasse sempre no ponto seguro, e foi o que aconteceu — inclusive nas falhas causadas pelos meus próprios erros de diagnóstico.

#### O que as cinco tentativas custaram, e por quê

Três das quatro falhas vieram de eu inferir em vez de observar: `BPLId` deduzido da mensagem de erro (que cita o nome da **coluna**, não o da propriedade), `Series` deduzida por analogia, e a conclusão de que "sem erro de propriedade inválida, o nome está certo" — que é falsa, porque a Service Layer ignora propriedade desconhecida em silêncio. O `diag entidade` resolveu em um comando o que eu tentei adivinhar em três rodadas. **Contra a Service Layer, campo se descobre lendo um registro real** — está na seção 7.29, e é a lição mais reutilizável do dia.

---

### 7.31 Que status permite encerrar — e uma OP cancelada que era oferecida (21/09/2026)

Pergunta do Anderson: *por que a OP precisa estar Planejada para ser encerrada?*

**Ela não precisa.** O `encerrar` aceita **Planejada e Liberada**, e faz sentido: `corrigeOP` é o primeiro passo e libera a OP de todo jeito, então uma Liberada já está no estado que ele quer. O legado também não checava status — só `qtdApontada < qtdPlanejada`.

Eu havia afirmado o contrário durante os testes ("você precisa de outro pedido com OP Planejada"), e por causa disso pedi um `replanejar` entre cada tentativa. **Aqueles quatro `replanejar` eram desnecessários.**

#### A lacuna que a pergunta expôs

Do jeito que estava, uma OP **Cancelada** era oferecida para encerramento — e para liberar e replanejar também. O SAP recusaria: cancelada é estado final, não se libera. A cadeia falharia no primeiro passo, com uma mensagem obscura.

É outra vez o padrão central deste porte: **a proteção do legado morava na grade, não no código.** `OPS_MANUTENCAO` filtra `Status != 'C'`, então a grade da tela nunca mostrava canceladas e o código nunca precisou verificar. O `OPS_POR_PEDIDO` herdou esse filtro; o `OPS_POR_DOCNUM` — usado quando o usuário informa os números das OPs — não, porque ali não havia grade nenhuma para filtrar.

Mesma família do gap de reprocessamento da seção 7.6, e o terceiro caso: sempre que a CLI aceita um identificador direto, uma verificação que a tela fazia por filtro desaparece.

**Correção**: OP cancelada aparece na tabela com a ação `cancelada — não pode ser encerrada` (ou `não pode mudar de status`), e fica fora da execução. Deliberadamente **não** foi filtrada na query: sumir com um número que o usuário digitou é pior do que explicar por que ele não entra — mesmo critério do vazio silencioso das seções 7.18 e 7.26.

Uma cancelada no meio de um lote não impede as demais.

`tests/test_manutencao_op.py` +3 e `test_manutencao_op_encerrar.py` +2, incluindo um que trava que **Liberada é aceita** — para a suposição errada não voltar em forma de "guarda de segurança". Suíte: 150 → 155 testes.

Também foi preciso consertar o helper de teste da CLI: ele devolvia `[]` para toda consulta ao HANA, então a cadeia parava em "determinar filial e séries" e o teste media outra coisa. Segundo mock que a resolução de filial quebrou — o primeiro foi na seção 7.27.

---

### 7.32 Regra de negócio: uma OP só é apontada estando liberada (21/09/2026)

Regra informada pelo Anderson. Encerrar uma OP envolve **apontar produção** — saída de insumo e entrada de produto —, então uma OP Planejada precisa ser **liberada** antes.

**O código já fazia isso**, dentro do `corrigeOP`. O que faltava era a regra estar visível: o guia registrava apenas a consequência técnica ("sem `im_Manual` o SAP tenta backflush e recusa a saída manual"), sem dizer a regra de negócio que a motiva, e a CLI escondia o passo — a tabela anunciava `saída + entrada + encerrar` e a liberação acontecia sem aparecer.

Isso não é cosmético. **A liberação é exatamente o estado que sobra quando a cadeia falha depois dela** — aconteceu quatro vezes nos testes de 21/09, e em cada uma a OP ficou Liberada sem que o comando tivesse anunciado que ia liberá-la.

#### O que mudou

`corrige_op` passou a receber o status atual e a devolver se liberou:

- **Planejada** → o PATCH inclui `ProductionOrderStatus = boposReleased`, com log explicando o motivo;
- **Já liberada** → o status **não** é enviado. Reenviar o status que a OP já tem é escrita sem efeito, e a Service Layer é o recurso mais caro da execução (52% do tempo, seção 7.14);
- **Em qualquer caso**, o `im_Manual` vai para todas as linhas — ele não depende do status, é o que permite a baixa manual dos insumos.

Na CLI:

| Status da OP | Ação anunciada na tabela |
|---|---|
| Planejada | `liberar + saída + entrada + encerrar` |
| Liberada | `saída + entrada + encerrar` |
| Encerrada | `já encerrada` |
| Cancelada | `cancelada — não pode ser encerrada` |

Antes da confirmação, quando houver Planejadas: *"N está Planejada e será LIBERADA antes — uma OP só pode ser apontada estando liberada."* E o relatório final marca `(liberada antes)` nas que passaram por isso, para o registro dizer o que aconteceu em vez de o leitor supor.

`tests/test_manutencao_op_encerrar.py` +6: liberação da Planejada, ausência de status na já liberada, `im_Manual` em ambos os casos, o `foi_liberada` no resultado, e os dois avisos da CLI. Suíte: 155 → 161 testes.

Um detalhe de teste que vale anotar: procurar a frase `"liberar + saída + entrada + encerrar"` na saída da CLI **falha** — o Rich quebra a célula da tabela entre as bordas. O teste normaliza os espaços e procura o pedaço que cabe numa linha. Asserção sobre saída formatada precisa disso, senão quebra por formatação e não por conteúdo.

---

### 7.33 Reverter a liberação quando nada foi lançado (21/09/2026)

Sexta execução do `encerrar`, OP 156207 (Planejada). **O caminho novo funcionou**: a liberação condicional aconteceu (`liberando antes do apontamento`), o PATCH passou e a cadeia chegou até a saída. O ramo "Planejada → libera antes" da seção 7.32 está validado contra o SAP.

A falha foi ambiental: `10001287 - Item cost not found for one or more items`. Um dos componentes não tem custo apurado no depósito, e o SAP não lança saída de mercadoria sem custo. Não é defeito do porte — é a mesma família do problema de estoque previsto antes do primeiro teste. **Nenhuma verificação prévia foi acrescentada**: apurar custo depende do método de valoração por item e por depósito, e replicar essa regra seria duplicar lógica do SAP para reproduzir uma mensagem que ele já dá com clareza.

#### O que mudou: a OP volta sozinha para Planejada

Essa foi a **quinta** falha do dia a deixar a OP Liberada, exigindo um `replanejar` manual. O padrão ficou claro o suficiente para virar código.

Quando a cadeia falha, `finalizar_ops` agora decide entre três estados, e a diferença entre eles é o que importa:

| Situação | O que acontece | Por quê |
|---|---|---|
| Liberada por este comando, **saída não lançada** | Devolvida para Planejada | Nada foi movimentado; reverter é limpo |
| Liberada por este comando, **saída já lançada** | **Fica Liberada** | Reverter deixaria estoque movimentado numa OP Planejada — pior que o estado atual |
| Já chegou Liberada | Fica Liberada | Só se desfaz o que este comando fez; rebaixá-la seria alterar estado que não era nosso |

No segundo caso o relatório é enfático, porque é o único estado da cadeia que exige intervenção manual:

> ATENÇÃO: a saída de insumo JÁ foi lançada e a OP continua Liberada. Não use `replanejar` — a saída precisa ser cancelada no SAP primeiro, senão fica estoque movimentado numa OP planejada.

A reversão é **melhor esforço**: se ela também falhar, o relatório diz isso e **o erro original continua sendo o principal**. Esconder a causa atrás de um problema de limpeza seria trocar um diagnóstico bom por um ruim.

`tests/test_manutencao_op_encerrar.py` +4: reversão na falha da saída, ausência de reversão na falha da entrada, OP que já chegou liberada intocada, e a falha da reversão não mascarando o erro original. Suíte: 161 → 165 testes.

#### Para o próximo teste de `encerrar`

Escolher uma OP cujos componentes tenham **custo apurado**, não só saldo:

```sql
SELECT L."ItemCode", L."PlannedQty", I."EvalSystem", IFNULL(W."OnHand",0) "saldo",
       IFNULL(W."AvgPrice",0) "custo_medio"
FROM "SBOALTAMIRAHOMOLOG"."OWOR" O
  INNER JOIN "SBOALTAMIRAHOMOLOG"."WOR1" L ON O."DocEntry" = L."DocEntry"
  LEFT JOIN "SBOALTAMIRAHOMOLOG"."OITM" I ON I."ItemCode" = L."ItemCode"
  LEFT JOIN "SBOALTAMIRAHOMOLOG"."OITW" W
         ON W."ItemCode" = L."ItemCode" AND W."WhsCode" = L."wareHouse"
WHERE O."DocNum" = <a OP candidata>;
```

Componente com `custo_medio` zerado é o que produz o `10001287`.

---

### 7.34 `encerrar --pedido`: encerrar o pedido inteiro, de baixo para cima (21/09/2026)

Pedido do Anderson: encerrar todas as OPs de um pedido, atuando recursivamente.

```bash
python -m app.cli manutencao-op encerrar --pedido 84348
```

#### Por que a ordem não é detalhe

**A razão é física, não estética.** A saída de insumo de uma OP pai consome o item que uma OP filha produz. Encerrar de cima para baixo falharia por falta de estoque — ou, pior, consumiria saldo de outra origem e o erro só apareceria na conferência de custo, semanas depois. Então: **filha antes da mãe**.

#### Como a hierarquia é descoberta

**Pelo item, porque o B1 não tem campo de "OP pai" para esta cascata.** Se o item produzido pela OP B (`OWOR."ItemCode"`) aparece como componente da OP A (`WOR1."ItemCode"`), B precede A. Ordenação topológica (Kahn), com desempate pelo `DocNum` para a ordem ser **reproduzível** — ordem variando entre execuções tornaria um problema impossível de investigar.

Dois casos que o desenho trata de propósito:

- **Item produzido por várias OPs.** O `PAR000PADRA000000000` tem 13 OPs no pedido 84348. A relação é item→OPs, então todas as produtoras precedem todas as consumidoras, sem tratamento especial.
- **OP que consome o que ela mesma produz** (reprocesso, refugo): a aresta para si mesma é descartada, senão viraria um ciclo falso e a OP seria recusada sem motivo.

#### Ciclo de dependência: recusa, não chute

Se houver ciclo, o comando **não encerra nada** e lista as OPs envolvidas. Numa operação irreversível de estoque, encerrar em ordem arbitrária é pior que recusar: uma delas consumiria o produto da outra antes de ele existir. Um ciclo não deveria existir numa árvore de estrutura, mas dado ruim existe — e o custo de assumir que não existe é alto demais.

#### Falha no meio da cascata

Aqui o projeto **abre uma exceção consciente** ao seu próprio precedente. A regra estabelecida em vários pontos (7.16, 7.17, 7.24) é "uma unidade com erro não impede as demais". Ela vale para unidades **independentes**; numa cascata elas não são.

Quando uma OP falha, todas as que dependem dela — direta ou transitivamente — são **puladas**, não tentadas. Tentá-las produziria uma fila de "sem estoque" que esconde a causa real, que está lá embaixo. **OPs independentes seguem normalmente**, então a exceção é cirúrgica: só a sub-árvore afetada para.

O relatório separa quatro desfechos: `finalizadas`, `ignoradas` (apontada = planejada), `com_erro` e `puladas`, esta última dizendo de qual OP cada uma dependia.

#### Escala

O pedido 84348 tem 68 OPs. Encerrar todas são ~4 chamadas à Service Layer por OP (liberar, saída, entrada, encerrar), na casa de 270 chamadas. A coluna `#` na tabela de confirmação mostra a ordem inteira antes de começar — numa operação irreversível dessa dimensão, ver a ordem antes é parte da decisão.

`tests/test_manutencao_op_encerrar.py` +9: filha antes da mãe, cascata de três níveis, item com várias produtoras, determinismo da ordem, ciclo recusado, auto-consumo que não é ciclo, dependentes transitivos alcançando a cadeia inteira, e as duas metades do comportamento de falha (pula quem depende, não pula quem é independente). Suíte: 165 → 174 testes.

#### Uma nota sobre testar saída formatada

Terceira vez no dia que uma asserção sobre conteúdo de célula de tabela quebrou por formatação: acrescentar a coluna `#` estreitou a coluna `Ação` e o Rich passou a quebrar `liberar + saída + entrada + encerrar` em pedaços diferentes. Os testes de CLI passaram a afirmar só sobre a **prosa fora da tabela** e palavras isoladas. Asserção sobre layout renderizado mede formatação, não comportamento.

---

### 7.35 A camada web dos módulos 2 e 3 (22/09/2026)

Até aqui o painel tinha uma tela viva (Oportunidades, desde removida — seção 7.36) e três "em construção". As decisões do Anderson para a web foram três: **sem autenticação por enquanto**, **operação irreversível exige confirmação**, **operação longa roda em segundo plano com acompanhamento**. As três viraram peças em `app/core/`, compartilhadas — não código repetido em cada módulo.

#### As peças

| Arquivo | Papel |
|---|---|
| `core/templates.py` | Instância única do Jinja com o ambiente como global — a faixa de ambiente está em toda página por construção (7.34/banner). |
| `core/tarefas.py` | Registro de execuções em segundo plano, **uma por módulo**. |
| `core/tarefas_router.py` | `/tarefas`, `/tarefas/{id}`, `/tarefas/{id}/estado` (JSON de acompanhamento), `/tarefas/{id}/cancelar`. |
| `core/confirmacao.py` | Plano + token de uso único, validade de 10 min. |
| `core/web.py` | Tradução da trava de produção (`guardas.py`) para HTTP. |

#### Por que o clique não grava direto

Toda operação irreversível é de duas etapas: uma rota `/conferir` que **lê** e monta o plano, e uma rota `/executar` que só aceita o token daquele plano. O ganho não é cerimônia: o usuário confirma sobre o que foi **calculado e mostrado**, não sobre o que imagina que vai acontecer, e um F5 na tela de resultado não reexecuta nada — o token já foi consumido.

Detalhe deliberado: errar o texto de confirmação de produção **não** consome o token. Obrigar a refazer a conferência inteira por causa de uma digitação empurra o usuário a clicar mais rápido da próxima vez, que é o oposto do objetivo.

#### O tema recorrente aparece de novo

Em `/pedidos-wbc/processar/conferir` a lista de pedidos é **relida do banco** e o que o formulário mandou é conferido contra ela; um número que saiu da lista elegível é recusado com o motivo. É a terceira encarnação da mesma lição da seção 8: no legado a proteção morava no filtro da grade, e toda camada que aceita um identificador direto perde a checagem que a tela fazia por filtragem. Aqui a "grade" é a query, e ela continua mandando.

#### O teste que pega a rota esquecida

`tests/test_web_modulos.py::test_toda_rota_post_que_grava_passa_pela_trava` varre as rotas POST dos módulos e falha se alguma gravar sem passar por `exige_autorizacao`. Uma rota de leitura se declara na lista `ROTAS_QUE_NAO_GRAVAM`, com o motivo. **O teste já pegou uma na estreia**: `/oportunidades/buscar` não estava declarada. Era leitura, mas o ponto é que ninguém precisou lembrar de conferir.

É o análogo web do teste que verifica que todo comando de escrita da CLI expõe `--producao`, e existe pelo mesmo motivo: o painel usa o **mesmo `.env`** da CLI, então uma rota sem trava grava em produção sem barreira enquanto o comando equivalente no terminal seria recusado. Duas camadas com regras diferentes sobre a mesma escrita é o tipo de divergência que só se descobre depois.

#### O que a tela de encerramento mostra

A ordem (filha antes da mãe) é **calculada e exibida** na coluna `#` do plano, e é essa ordem que a execução segue — reler o banco no `/executar` recalcularia uma ordem que o usuário não conferiu. Ciclo de dependência recusa a operação inteira, como na CLI. A coluna `Ação` diz, por OP, se ela será liberada antes; e a decisão de processar viaja no plano como `_processar`, não como releitura do texto daquela coluna: frase escrita para humano ler não é estrutura de dados.

#### Limites assumidos

Tarefas e planos vivem **em memória, no processo**. Reiniciar a aplicação apaga o histórico de execuções e invalida os tokens pendentes. O segundo efeito é desejado (nenhum token sobrevive para ser executado depois, contra um estado que já mudou); o primeiro é custo aceito enquanto o uso for interno e manual. O caminho quando incomodar é persistir (`TRACKING_DB_URL` existe para isso), não trocar de arquitetura.

> **29/09/2026 — o primeiro efeito deixou de valer para o histórico.** Na .11, cada execução terminada vai também para o Supabase (`controle_producao_execucoes`, as 30 mais recentes; `core/historico.py`), e a tela Execuções lê de lá: reiniciar não apaga mais a lista. Foi Supabase e não o SQLite do `TRACKING_DB_URL` por decisão do Marcelo (o mesmo banco dos outros logs do SIS, legível fora da .11). A execução em si continua só em memória — reiniciar no meio dela perde o acompanhamento, e é por isso que o `deploy_update.bat` recusa parar com `/health/ocupado` = 1. Tokens pendentes continuam morrendo no restart, de propósito.

Cancelar uma tarefa interrompe **entre passos** e **não desfaz** o que já foi gravado no SAP. A tela diz isso ao lado do botão.

#### Ainda não feito na web

Módulo 4 (Romaneio) e tela de Configurações continuam "em construção". O resultado das tarefas aparece como JSON na tela de acompanhamento — suficiente para operar, ainda não uma leitura confortável para o `comparar-ops`.

---

### 7.36 Módulo 1 (Oportunidades) removido do porte (22/09/2026)

Decisão do Anderson, ao ver a tela: **"remova esta opção de todo o projeto"**. A Integração de Oportunidades existe no addon legado e continua lá; ela não faz parte do fluxo que a aplicação nova precisa cobrir.

#### O que saiu

| Removido | |
|---|---|
| `app/modules/oportunidades/` | service, router, queries, schemas — o módulo inteiro |
| `app/templates/oportunidades.html` | a tela |
| CLI | `oportunidades buscar` e `oportunidades integrar`, e o grupo `oportunidades` |
| Navegação | o item do menu superior e a linha da home |
| Testes | os que exercitavam a tela e os comandos |

Nada mais dependia dele: `preenche_log` mora em `core/audit_log.py` e `OportunidadeDoc` em `pedidos_wbc/schemas.py`, ambos fora do módulo. A verificação foi feita antes de apagar, não depois.

#### A numeração não foi refeita

Os módulos passam a ser 2, 3 e 4 — começando no 2. Renumerar para 1, 2, 3 quebraria a correspondência com o menu do addon legado, com as seções 4.3 a 4.6 deste guia e com todo o histórico da seção 7. A home diz isso em uma linha, para que a ausência do 1 seja lida como intencional e não como bug.

#### O que a remoção deixa órfão

`WbcSqlServerClient.execute` — a **única via de escrita no WBC** do porte — era usada só pelo `INSERT INTO INTEGRACAO_ORCINC` do módulo 1. Ficou sem chamador. Mantido, com a observação no código: o módulo 4 (Romaneio) ainda não foi portado e pode precisar escrever; se não precisar, o método sai junto.

A seção 4.3 permanece no guia como documentação **do legado**, marcada como não portada. Apagá-la esconderia o que o addon faz de quem for mexer nele; mantê-la sem marcação faria alguém tentar portá-la de novo.

#### Uma correção aproveitada

A ajuda da CLI dizia que `processar-novos` recebe "Números de Oportunidade". Recebe o **nº do orçamento WBC** (`OOPR.U_ORCNUM_MASC`, ex. `00125431`), não a chave da Oportunidade no SAP (`OPR1.OpprId`, ex. `15024`) — e foi exatamente esse texto que me levou ao erro de 22/09 na tela (seção 7.35). Corrigido nos três pontos: o argumento de `processar-novos`, o de `reprocessar-integrados` e o `buscar`, que agora diz qual das duas colunas usar e o que acontece ao trocá-las.

---

### 7.37 Tema visual aplicado a partir do painel de Usuários (22/09/2026)

O Anderson mandou a tela de Usuários de outro sistema da casa como referência: **"aplique as fontes, cores, ícones etc"**. O painel passou a ter a mesma linguagem visual — escuro, cartões de contagem com barra colorida à esquerda, pílulas por categoria, tabela em painel arredondado com cabeçalho em versalete.

#### O que é regra, e não enfeite

**Tudo é token em `:root`.** Cor, raio, espaçamento e pilha de fontes vivem num lugar só. A referência usa uma cor por grupo de usuário; aqui a mesma paleta serve a status de OP, situação de tarefa e tipo de operação. É o que impede o vermelho do botão de encerrar divergir do vermelho da faixa de produção quando alguém ajustar um deles.

**Tema claro é a mesma folha com os tokens redefinidos**, não um segundo tema escrito à mão. Nenhum componente sabe que existe mais de um tema — então componente novo nasce funcionando nos dois. A preferência fica no navegador e é aplicada **antes da primeira pintura**: lê-la depois faria a página piscar em branco a cada navegação para quem usa o escuro.

**Cor por status de OP, por significado.** Amarelo = Planejada (ainda não pode ser apontada), azul = Liberada, verde = Encerrada, vermelho = Cancelada. É a mesma leitura que o operador já faz na grade do SAP, não uma escolha estética.

#### Sem nada vindo de fora

Ícones são **SVG inline** (`app/templates/_icones.html`, macro `icone()`), com `currentColor` para herdarem a cor do contexto. Fontes são as do sistema, com Inter na frente da pilha para quem a tiver instalada — **sem `@import` de CDN**.

O motivo é o mesmo nos dois casos: esta aplicação roda na rede interna, às vezes sem saída para a internet. Uma página que só fica legível quando o Google responde é pior do que uma página com a fonte do sistema, e um ícone que só aparece quando o CDN responde é pior que ícone nenhum.

#### Duas correções que a mudança trouxe junto

- **A coluna "Status (descrição)" saiu da tabela de OPs.** Mostrar `P` e `Planejada` em duas colunas é a mesma informação duas vezes, e era ela que empurrava as datas para fora da tela. Agora o status é uma pílula colorida com a descrição dentro, na posição onde o olho já ia.
- **Os cartões de contagem usam flex com largura mínima, não grid `1fr`.** Com dois cartões, o grid os esticava até a largura da tela e o número ficava boiando num painel enorme.

#### Um teste intermitente corrigido de passagem

A limpeza entre testes da camada web não cancelava os `asyncio.Task` pendentes. Como o registro aceita **uma execução por módulo**, uma tarefa deixada rodando por um teste anterior fazia o seguinte receber "já existe execução em andamento" em vez do redirecionamento — falha que aparecia e sumia conforme a ordem e a velocidade da suíte. Teste intermitente é o pior tipo de teste: ensina a ignorar vermelho.

---

### 7.38 Guia de estilo OrçaView aplicado ao painel (22/09/2026)

O Anderson enviou o `GUIA_ESTILO_ORCAVIEW.md` — a ficha das três telas do OrçaView (`status`, `Usuarios`, `Pedidos`) com o alicerce comum, a ponderação entre padrão e licença, e um kit pronto para equipe de fora. O painel foi refeito sobre a **seção 5** desse guia, que é exatamente o caso deste projeto: uma tela irmã das três, sem copiar nenhuma delas.

#### O que veio do guia

| Do guia | Onde |
|---|---|
| Paleta `--ov-*`, escuro default, claro por `data-theme` | `style.css`, três linhas de definição |
| Escala `--fs-*`, espaços `--space-*`, raios, `--easing-out` | idem (§1.3 e §1.4 transcritas) |
| Anti-FOUC: tema escrito no `<html>` antes de qualquer `<link>` | `base.html`, primeiro `<script>` |
| Acento único coral; estado em cor **semântica** | pílulas `.ov-pill.is-*` |
| KPI: `grid` + `auto-fit`, borda-esquerda de 4px | `.ov-kpis` / `.ov-kpi` |
| Botão de relevo: gradiente de 3 paradas, sem borda, glow da própria cor | `.ov-btn` |
| Título de seção: caps `.08em` + quadradinho do acento | `.ov-card__title`, `h2` |
| Tabela: cabeçalho caps apagado, zebra, hover `--ov-a08`, `tabular-nums` | `.ov-table` |
| `zoom: .9` na **casca**, nunca no `body` | `.ov-shell` |
| `translateY` de hover só em `(hover:hover) and (pointer:fine)` | `.ov-btn:hover` |
| Foco `outline: 2px` + `offset: 2px` | `:focus-visible` global |

As regras de build do §6 foram verificadas por script: **zero** `font-size` literal, **zero** hex fora das três linhas de definição da paleta (que levam o comentário `css-token-ok` sancionado). Os `rgba()` restantes do relevo viraram `rgb(… / …)`, deixando explícito que são opacidade de preto/branco puro, não cor da paleta escrita à mão.

#### Três divergências conscientes, por falta dos assets

1. **Ícones em SVG inline**, não Bootstrap Icons — a fonte de ícones não está vendorizada aqui. Respeita a regra que importa (§1.1): nada de CDN, porque o app roda em LAN.
2. **Botão de tema na barra de navegação**, não `position:fixed` no canto. A posição fixa do guia pressupõe o drawer do `menu.js`, que este app não tem — aqui a própria barra é o menu, e um botão fixo cairia em cima dela.
3. **Sem Bootstrap**: as classes `.ov-*` do kit são autossuficientes.

A chave de storage é a da casa (`orcaview-theme`) e o atributo é `data-theme`. Este app serve de outra porta, então o navegador não compartilha o storage com o OrçaView — seguir a convenção não custa nada e evita duas grafias para a mesma ideia se um dia forem servidos juntos.

#### Duas coisas que o guia corrigiu no que eu já tinha feito

- **A faixa de KPI voltou a ser `grid` + `auto-fit`.** Eu havia trocado para `flex` com largura mínima porque com dois cartões o grid os esticava. O guia é explícito (§5.3): *"faixa de KPI é `grid` com `auto-fit`, nunca `flex` + `min-width` — o flex deixa buraco à direita"*. O esticamento é o comportamento aceito pela casa; o buraco não é.
- **A tabela larga rola dentro do cartão** (`width: max-content; min-width: 100%`), em vez de quebrar a descrição do produto em seis linhas. É o §7: *"rolagem horizontal de tabela é interna ao cartão"*.

#### Um teste amarrado à marcação

Três asserções de paginação quebraram ao trocar `página 2 de 3` por `página <b>2</b> de <b>3</b>`. Foram reescritas para afirmar sobre o **texto que o usuário lê**, não sobre o HTML: `_texto()` tira as tags antes de comparar. Teste que quebra com mudança de estilo mede marcação, não comportamento — é o mesmo erro da nota sobre saída formatada do Rich (7.29).

---

### 7.39 Trava de escrita em produção removida (22/09/2026)

Pedido do Anderson: *"remover a trava que limita a escrita em produção e que precisa escrever a palavra SBOALTAMIRAPROD. Deixe igual a homologação, sem travas. Apenas aviso."*

A trava viveu um dia — criada em 21/09 (seção 7.19), removida em 22/09. O contexto mudou no meio: ela nasceu durante a validação, quando cada execução era um teste e um engano de ambiente custaria refazer trabalho; sai agora que os módulos 2 e 3 estão validados e a operação passou a ser do dia a dia. Fricção de validação cobrada de quem opera todo dia deixa de proteger e passa a atrapalhar. **A decisão é do dono do ambiente.**

#### O que saiu

| Removido | Onde estava |
|---|---|
| `WBC_BLOCK_PRODUCTION_WRITES` | `config.py` — o campo sumiu; `extra="ignore"` descarta a variável se o `.env` ainda a tiver |
| `--producao` | todos os comandos da CLI |
| Digitação da company DB | `_confirma` (CLI) e o formulário de confirmação (web) |
| `EscritaBloqueada`, `autoriza_escrita`, `texto_de_confirmacao` | `core/guardas.py` |
| `exige_texto` do plano | `core/confirmacao.py` |

`guardas.py` virou um rótulo: `ambiente_descrito` e `aviso_de_escrita`, que descrevem e não recusam. `core/web.py` perdeu o `HTTPException(403)`.

#### O que ficou, e por quê

- **A faixa vermelha no topo de toda página** em produção. Ela é global do Jinja (`core/templates.py`), então tela nova não esquece.
- **O aviso em log a cada escrita em produção**, com o alvo e a operação. É o rastro que sobra para auditoria depois — e é por isso que `avisa_escrita` continua sendo chamada por toda rota que grava, e que o teste de cobertura continua exigindo a chamada.
- **A conferência em duas etapas de operação irreversível** (plano calculado, mostrado, token de uso único). **Nunca foi** a trava de produção: vale igual em homologação, porque o motivo dela é a operação não ter volta, não o ambiente ser arriscado. Foi preciso dizer isso em três docstrings para que a próxima leitura não a confunda com resto da trava e a remova junto.
- **A ênfase na pergunta da CLI**: em produção ela sai em vermelho, dizendo o alvo.

#### O que se perdeu, dito com todas as letras

O cenário que motivou a trava era um comando com `--sim` dentro de um script, apontado para o ambiente errado, rodando sozinho de madrugada. Contra isso, hoje, **só o cuidado com o `SL_COMPANY_DB` do `.env`**. Está escrito no topo de `guardas.py`, onde quem for mexer vai ler.

#### Consequências nos testes

`test_trava_producao.py` foi substituído por `test_aviso_producao.py`, que garante o inverso do que o anterior garantia: produção é **anunciada** (em log e em tela, com o alvo e a operação), homologação **não** ganha ruído, e **nada é recusado** por causa do ambiente. Um teste novo afirma que nenhum comando expõe mais `--producao` — deixá-lo em um só seria pior que em nenhum.

Três testes de `test_tarefas.py` que exercitavam a digitação viraram um só, afirmando que o plano **não** tem mais `exige_texto`.

> ⚠️ Pendência mecânica: `tests/test_trava_producao.py` foi apagado no repositório de trabalho, mas o arquivo continua na máquina do Anderson (não tenho como apagar arquivos lá). Ele importa `EscritaBloqueada`, que não existe mais, e quebra a coleta do pytest inteiro até ser removido à mão.

---

### 7.40 Revisão de legibilidade e portabilidade (23/09/2026)

Revisão pedida pelo Anderson: *"veja se pode melhorar o código tornando portável e de simples leitura"*. Análise estática apenas — nenhum ambiente foi tocado. 233 testes passando ao fim.

#### O bug de portabilidade

**A aplicação web só subia se o `uvicorn` fosse chamado de dentro de `python_app/`.** `StaticFiles(directory="app/static")` e `Jinja2Templates(directory="app/templates")` são caminhos relativos ao diretório de trabalho; de qualquer outro lugar a app morria com `Directory 'app/static' does not exist` **antes de servir a primeira página**. Reproduzido rodando de `/tmp` e de `/`.

O incômodo é maior do que parece: um serviço systemd sem `WorkingDirectory` explícito é o caso mais comum de deploy, e é exatamente onde isso falha. `config.py` (o `.env`) e `listas_fixas.py` (o `Solda.txt`) já derivavam o caminho de `__file__` — esses dois pontos é que ficaram para trás, o que torna a inconsistência ainda mais fácil de não notar.

Corrigido nos dois, com teste que afirma que os caminhos são absolutos e existem.

#### Dois achados que NÃO foram corrigidos, de propósito

**1. `_get_doc_entry_table_valdixon` é um esqueleto que devolve `0` — e isso não é neutro.**

O valor sobe como `tb_valdixson` e decide dois comportamentos:

- **`U_INO_ORCAMENTO` nunca é gravado no pedido.** Os três `Update*` do C# fazem `if (tbValdixson != 0) ...Item("U_INO_ORCAMENTO").Value = tbValdixson` (ProcessDefault.cs 1090, 1168, 1251). Com 0 o `if` é sempre falso aqui e sempre verdadeiro lá — **divergência silenciosa em todo pedido processado**, não detalhe de implementação.
- **O ramo `tb_valdixson != 0` de `_update_pedido` é código morto.** Só o `else` roda, e com ele some a consulta de peso (`GET_PESO_PEDIDO`, que o C# faz com `tbValdixson`) e portanto o `Weight1` das linhas.

Não corrigi porque a correção exige uma decisão: ler o `DocEntry` da resposta do `create_entity` (mais correto, e evita a corrida do `max()`) ou executar a query do legado (fiel, mas pode devolver o registro de outro processo). A query foi **restaurada** em `queries.py` — eu a tinha removido na varredura de código morto, e estava errado: ela não está sem uso por ser lixo, está sem uso porque a função que a usaria é um esqueleto.

**2. `GetByKey("SalesOpportunities", doc_num)` — o legado passa o número errado.**

Em `_update_pedido`, ramo `else`, a chave de `SalesOpportunities` recebe o nº do PEDIDO. É réplica fiel: a chamada viva do C# passa `oRs.Fields.Item(2)` = DocNum (IntegraPedidoWBC.b1f.cs 276 e 831), enquanto a linha **comentada** logo acima (801) passa `"Num Oportunidade"`, que é o `OpprId` de verdade. O autor do addon sabia o valor certo e a versão que ficou no ar usa o errado.

É o mesmo erro de numeração que custou o bug de 22/09 (seção 7.35). Deixei documentado no ponto exato, sem corrigir: merece decisão explícita, e hoje o ramo é inalcançável por causa do achado 1.

#### Legibilidade — o que mudou

**Nomes que mentiam.** `opp_id` recebia o **nº do orçamento WBC**, não a chave da Oportunidade — 60 ocorrências, e foi essa confusão que gerou o bug de 22/09. Renomeado para `orc_num` em todo o módulo 2, incluindo a chave do resultado e o argumento da CLI (a ajuda dizia "Nº do ORÇAMENTO WBC" enquanto o metavar dizia `{opp_ids}`). `atualiza_pedido_tabela` tinha `oppr_id` e `oppr_id_orig`, dois nomes quase iguais para o orçamento e para o pedido: viraram `orc_num` e `doc_num`.

**Constantes de negócio nomeadas.** `332`/`333`/`358` (grupos de item), `"08"`/`"01"` (depósitos), o prefixo `"I"` e o prazo de 20 dias estavam como literais em até quatro pontos cada. Nomear não muda a execução; muda quem consegue ler sem abrir o C# ao lado, e é o que permite achar todos os usos de uma regra quando ela mudar. A regra do depósito virou `_deposito_do_item()`, que devolve dicionário porque o campo precisa ficar **ausente** quando não se aplica — `Warehouse: None` não é a mesma coisa para a Service Layer.

**Duplicação byte a byte extraída.** Os dois ramos de `_update_pedido` montavam as mesmas nove chaves da linha do pedido, diferindo só por `Weight1` → `_monta_linha_pedido()`. Na CLI, o bloco "Puladas" (doze linhas, incluindo a explicação em `[dim]`) estava copiado em três comandos, e o "Falharam" em dois → `_imprime_ops_puladas()` e `_imprime_ops_com_erro()`.

**`list(rows[0].values())[0]`** aparecia nove vezes — idioma necessário (as queries transcritas não têm alias), mas que some no meio da linha. Virou `_primeiro_valor(rows, padrao)`, que também unifica o `if rows` que faltava em alguns pontos.

**Código morto removido:** o import `WbcDatabaseError`, a constante `_UDO_LOG`, o parâmetro `exc` de `_falha_nao_implementado`, o import `ambiente_descrito` na CLI e seis queries transcritas que nenhum caminho executava. Query transcrita e não usada é pior que ausente: quem lê presume que algum caminho a executa e vai procurar onde.

#### O que ficou de fora, e por quê

- **Dividir `app/cli.py` (1593 linhas) em pacote** — ganho real de leitura, mas é movimentação em massa e merece ser uma mudança isolada, revisável sozinha.
- **Quebrar as funções longas** (`_processa_grupo_producao` 125 linhas, `_diagnostica_ops_faltantes` 113, `_update_pedido` 111, `preenche_tabela` 107) — cada corte é uma decisão sobre onde está a fronteira real, no código mais validado do projeto.
- **Parametrizar as queries HANA** (hoje 100% por `.format()`) — é o débito nº 2 da seção 8, e mexer em SQL validado contra produção não é assunto de uma revisão de legibilidade.

---


### 7.41 Grupos que não geram OP precisam gritar (23/09/2026)

**Sintoma.** O pedido 84426 tinha 4 itens de orçamento no WBC; a execução terminou "concluída",
sem erro na tela, e saíram OPs de apenas 2 deles. O log de cancelamento posterior confirma a
conta: 18 OPs planejadas, com exatamente **dois** pais de nível 1 (`I000003` — Porta-Paletes e
`I000002` — Estantes) e 16 componentes deles. Os outros dois grupos não produziram nada e
ninguém foi avisado.

**O que o log de cancelamento prova — e o que ele não prova.** Ele prova que só 2 dos 4 grupos
foram processados. Ele **não** consegue dizer por quê, e a razão disso é o próprio defeito:
`_processa_grupo_producao` tinha três saídas sem OP e **nenhuma delas emitia `logger.*`** —
gravavam só no `@INO_LOG` via `preenche_log`. Como o acompanhamento em tela é uma ponte para os
loggers do módulo (`acompanha_log`, seção 7.35), essas três saídas eram invisíveis: não apareciam
na tela, não entravam em `com_erro`, não constavam do resultado da tarefa. O operador via
"concluída" em verde.

As três saídas:

1. **`@INO_GRP_PRODUTOS` sem linha para o `GrpCode`** — `SELECT_ORC_ITEM_SAP` volta vazio e não há
   item SAP de produção para criar a OP. (Eu apontei esta como a causa mais provável do 84426;
   **estava errado** — o `@INO_LOG` mostrou que foi a 2, e por um defeito do porte: ver 7.42.)
2. **Já existe OP não cancelada para o pedido+item** — a verificação de reprocessamento
   (seção 7.6) pula a criação sem `--force`.
3. **A Service Layer recusou a OP** (`status != 0`) — grava "Erro" no `@INO_LOG` e marca
   `U_INO_ProcessWBC='N'`, mas seguia mudo na tela.

**Correção.** Cada saída passou a (a) emitir `logger.error`/`logger.warning` prefixado com
`SEM OP:` e (b) **devolver o motivo** em vez de `return` seco. `processar_pedidos_novos` acumula
tudo em `sem_op`, troca o "Pedido integrado com Sucesso." do `@INO_LOG` por uma linha que diz
quantos grupos de quantos ficaram sem OP, e devolve `sem_op` no resultado. O router mostra
`ATENÇÃO — N grupo(s) sem OP: <motivos>` em vez de "concluído", e a CLI imprime o bloco
"Grupos que NÃO geraram OP".

**A lição, pela quinta vez neste porte.** Uma condição silenciosa só aparece depois do estrago:
reprocessamento (15/09), OP cancelada no encerrar (21/09), pedido fora da lista elegível (22/09),
OP em status terminal (22/09) e agora grupo sem OP. O padrão é sempre o mesmo — o legado
protegia pela grade, o porte precisa proteger em código *e dizer que protegeu*. `preenche_log`
sozinho não é aviso: é registro em tabela que ninguém abre. Aviso é o que chega à tela de quem
está olhando.

**Como confirmar qual das três causou o 84426** (consultas só de leitura, em `diag84426.sql`):
o `@INO_LOG` do orçamento `00125540` traz a mensagem exata; o cruzamento entre os `GrpCode` de
nível 1 da estrutura do orçamento no WBC e as linhas de `@INO_GRP_PRODUTOS` fecha a causa 1.



### 7.42 A causa real do 84426: grupos do mesmo item se bloqueavam (23/09/2026)

O `@INO_LOG` do orçamento `00125540`, enviado pelo Anderson, fechou a questão. Execução do porte
(usuário `financeiro04`, 15:12–15:13):

| # | Registro | Grupo |
|---|---|---|
| 9009 | Erro ao preencher recurso — `POST /Resources: Data DailyCapacities not found` | 1 |
| 9010 | idem | 2 |
| 9011 | Aviso: já existe OP não cancelada (DocEntry=158588) para o pedido 84426, item I000003 | 2 — pulado |
| 9012 | idem rateio | 3 |
| 9013 | Aviso: já existe OP (DocEntry=**158588**), item I000003 | 3 — pulado |
| 9014 | idem rateio | 4 |
| 9015 | Pedido integrado com Sucesso. | — |

**Defeito 1 — a checagem de reprocessamento do porte (seção 7.6) bloqueava grupos irmãos.** Três
dos quatro itens do orçamento resolvem, via `@INO_GRP_PRODUTOS`, para o mesmo item SAP `I000003`
(Porta-Paletes). O grupo 1 criou a OP 158588; os grupos 2 e 3 perguntaram "existe OP não cancelada
para pedido 84426 + I000003?", encontraram **a OP que o grupo 1 tinha acabado de criar na mesma
execução**, e pularam como se fosse reprocessamento. O `DocEntry` repetido nas duas mensagens é a
prova. O legado não tem essa checagem e cria uma OP por grupo — por isso o reprocessamento feito
depois (registros 9017–9020, usuário `projeto06`, `DataSource='O'`, **quatro** "Pedido integrado
com Sucesso." — o C# grava essa linha dentro do `foreach` dos grupos, `IntegraPedidoWBC.b1f.cs`
~723) gerou as quatro.

Essa é uma regressão que **eu** introduzi em 15/09 ao acrescentar uma proteção que o legado não
tinha. Não apareceu na validação porque os orçamentos testados tinham um grupo por item SAP.

**Correção.** A pergunta mudou de "existe?" para "quantas?": `_ops_existentes_para_item` devolve
todas as OPs prévias do item, e o N-ésimo grupo de um item só é reprocessamento se o item já tinha
pelo menos N OPs **antes** desta execução (`_op_previa_do_grupo`). A foto é tirada no primeiro
grupo de cada item, antes de qualquer criação, e guardada num `contexto` compartilhado pelos
grupos do mesmo pedido. Resultado: pedido novo cria todas; pedido completo re-executado pula
todas; pedido que parou no meio completa só o que faltou. `_op_ja_existe_para_item` saiu.
Teste ponta a ponta com três grupos → `I000003` cria três OPs e consulta uma vez só; ele falha
contra a lógica antiga (verificado por mutação).

**Defeito 2 — o recurso de rateio nunca foi criado pelo porte.** `POST /Resources` falha em todos
os grupos com `Data DailyCapacities not found`. É a pergunta 10 da seção 9, que ficou aberta: o
corpo usa `ResourceCode`/`DailyCapacities` "segundo a documentação", nunca confirmados. Na
validação não apareceu porque os recursos `GGF_` já existiam (criados pelo legado) e o porte só
os reencontrava (7.24). Em produção, **todo pedido processado pelo porte cujo `GGF_` ainda não
existia teve as OPs criadas sem a linha de rateio** (custo de transporte/embalagem/montagem). O
C# usa `Resource.VisCode = NomeRecurso` e devolve `ret.Code` — outro indício de que o nome do
campo no corpo REST está errado.

**Atualização (23/09/2026, mesmo dia) — corrigido.** O Anderson rodou `diag entidade Resources`
(só leitura) e os nomes reais apareceram: `VisCode` (não `ResourceCode`), `Name` (não
`ResourceName`), `ResourceWarehouses[].Warehouse` (não `Warehouses[].WarehouseCode`) e
`ResourceDailyCapacities[]` (não `DailyCapacities`). São os nomes da DI API, com prefixo
`Resource` nas coleções. O corpo foi corrigido; como no C#, só `VisCode` é enviado e o que se
devolve é o `Code` retornado. Isso fecha a pergunta 10 da seção 9 para `Resources`. Conferido
pelo Anderson no mesmo dia: nenhum `GGF_` em produção tem `ResCode <> VisResCode`, então o
`Code` devolvido na criação e o `VisResCode` usado no reuso são o mesmo valor — a
inconsistência herdada do C# não tem efeito prático.

*Texto original da análise, mantido como histórico:* sem os nomes reais, seria mais um chute. O caminho é o
mesmo da 7.10 — ler um recurso existente (só leitura, em homologação):
`python -m app.cli diag entidade Resources`. Enquanto isso, a falha do rateio passou a constar do
resultado (`sem_rateio`), da linha de acompanhamento ("ATENÇÃO — N OP(s) sem a linha de rateio")
e do relatório da CLI.

**Para levantar os pedidos já afetados** (só leitura):
```sql
SELECT DISTINCT "U_OrcNum", "CreateDate", "Creator"
  FROM "SBOALTAMIRAPROD"."@INO_LOG"
 WHERE "U_Mensagem" LIKE 'Erro ao preencher recurso%'
 ORDER BY "CreateDate";
```


### 7.43 O DocNum do pedido vinha da Oportunidade — e às vezes era o cancelado (23/09/2026)

**Como apareceu.** O Anderson perguntou por que o 84425, com duas linhas de `I000003`, gerou as
duas OPs, se no 84426 a mesma situação fez a checagem pular os grupos. Não foi "forçar" (ele não
marcou) nem o addon antigo (`DataSource='S'`). As duas OPs (158567 e 158572) estão corretamente
vinculadas ao 84425 (`OriginNum=84425`, `OriginAbs=20099`) — e mesmo assim a checagem não viu a
primeira quando processou o segundo grupo.

**Causa.** `_processa_grupo_producao` resolvia o pedido **duas vezes, por caminhos diferentes**:

- `DocEntry` por `PEGA_DOC_ENTRY_PED` — filtra `CANCELED='N'` e ordena `DocEntry DESC`;
- `DocNum` por `PEGA_DOC_NUM_PED` (= `pegaDocNumPed` do C#, portado fielmente) — **sem filtro de
  cancelado e sem `ORDER BY`**, usando a primeira linha.

A Oportunidade do 84425 tem dois pedidos vinculados: o **84424 (cancelado, `Line` 4)** e o 84425
(`Line` 5). A busca do DocNum devolveu o 84424. O DocEntry (20099) estava certo — por isso as OPs
nasceram vinculadas ao pedido certo —, mas a checagem de OP existente procurou OPs do **84424**,
não achou nenhuma e deixou criar. No 84426 a Oportunidade só tem o próprio pedido, a checagem
olhou o lugar certo e disparou (7.42). **Os dois comportamentos opostos têm a mesma raiz.**

**O que mais usa esse DocNum** — e por isso o defeito é maior que a checagem:
- `BUSCA_MAX_ITEM_LINHA` → `quantidade_linha_base` (**a quantidade planejada da OP**) e
  `valor_linha` (base do rateio e parte do nome do recurso `GGF_`);
- `MAX_PRECO_PEDIDO` → `valor_total` (divisor do rateio);
- `GET_VERSAO_PEDIDO` (lido e descartado).

Ou seja: quando a Oportunidade tem um pedido cancelado listado antes do vigente, **a quantidade da
OP é lida do pedido cancelado**. Se os dois pedidos têm as mesmas quantidades (caso típico de
pedido refeito), o erro não aparece; se diferem, a OP sai com a quantidade errada. É um defeito
**do próprio addon legado** — a query é idêntica —, portanto já afetava produção antes do porte.

**Correção.** O DocNum passa a sair do DocEntry já resolvido
(`DOC_NUM_POR_DOC_ENTRY`: `SELECT "DocNum" FROM ORDR WHERE "DocEntry" = …`). São o mesmo pedido
por construção. `PEGA_DOC_NUM_PED` saiu. Divergência deliberada do legado, para corrigir um erro
dele. Teste novo garante que o DocNum nunca mais vem de consulta à Oportunidade (`OPR1`).

**Para conferir o estrago já feito** (só leitura): OPs principais cuja quantidade planejada difere
da linha do pedido a que estão vinculadas —
```sql
SELECT W."DocEntry", W."DocNum", W."OriginNum", W."ItemCode",
       W."PlannedQty", L."Quantity" AS "Qtd na linha", L."LineNum"
  FROM "SBOALTAMIRAPROD".OWOR W
  JOIN "SBOALTAMIRAPROD".RDR1 L ON L."DocEntry" = W."OriginAbs" AND L."U_INO_OP" = W."DocEntry"
 WHERE W."Status" <> 'C' AND W."PlannedQty" <> L."Quantity"
 ORDER BY W."DocEntry" DESC;
```


### 7.44 A quantidade da OP vinha da linha errada do mesmo pedido (24/09/2026)

**Regra de negócio (informada pelo Anderson).** Até pouco tempo, toda linha de pedido vinha com
quantidade **1**, representando um conjunto. Como isso dificultava o controle de estoque, a regra
mudou: a linha passou a trazer a **quantidade de módulos**. Pedidos antigos têm praticamente só
linhas com 1. Isso explica por que quase todas as OPs históricas têm `PlannedQty = 1`, e o defeito
abaixo ficou escondido durante anos.

**Como apareceu.** A consulta que compara a quantidade da OP com a da linha vinculada devolveu, além
das OPs com 1, pedidos em que **várias OPs têm o mesmo número**, que não bate com a linha delas:
84274 (três OPs com 255, em linhas de 8, 1 e 18), 84286 (215), 83091 (13 e 234), 83932 (102). Eu
tinha suposto que esses números vinham de um pedido cancelado da mesma oportunidade (o defeito da
7.43). **A consulta seguinte desmentiu:** nenhum desses pedidos tem outro pedido na oportunidade, e
cada número é a quantidade de **outra linha do mesmo pedido**. No 84274, 255 é a linha 6 e 9 é a
linha 5. No 84286, 215 é a linha 2.

**Causa.** `BuscaMAXItemLinha`, do C# e portado igual, filtra só por pedido + item + "sem OP" e pega a
primeira linha do `ORDER BY LineNum DESC`. Ou seja, pega **a linha sem OP de maior número daquele
item**, não a linha do grupo em processamento. Enquanto a linha 6 do 84274 não recebeu sua OP, todo
grupo de I000003 leu 255. Depois que ela foi vinculada, os seguintes leram 9 (linha 5). Com todas as
linhas em 1 o erro não tinha efeito. Com quantidade por módulos, **todo pedido com mais de uma linha
do mesmo item sai com quantidades trocadas**. O mesmo `valor_linha` entra no rateio e no nome do
recurso `GGF_`.

**Correção.** A busca passou a se restringir às linhas do grupo (`U_INO_ORCITM IN (…)`), a mesma
correspondência que `marca_op_nas_linhas` usa para gravar `U_INO_OP`, e a identificar o pedido pelo
`DocEntry`. Quando não acha a linha, mantém o 1 do legado, mas avisa na tela. Teste novo reproduz o
84274 (três grupos → 8, 1, 255). Divergência deliberada do legado.

**Levantamento feito em 24/09/2026** (OPs planejadas criadas desde 01/09/2026, data da regra
nova): só **2 casos**, as OPs 156794 e 156798 do pedido 84425, ambas com 1 em vez de 6. As duas são
efeito do pedido cancelado da oportunidade (7.43), não da linha trocada. O Anderson corrigiu
manualmente no SAP.


### 7.45 Romaneio retirado do menu (24/09/2026)

A pedido do Anderson, o item "Romaneio" saiu do menu superior (`base.html`) e da página inicial
(`home.html`). **Só a navegação mudou**: o módulo 4 continua no código como esqueleto (serviço, rota
`/romaneio` com a página "em construção", comando `romaneio` da CLI), sem link nenhum para ele. Teste
novo em `test_smoke.py` garante que nenhuma página volte a oferecer o link.


### 7.46 Pacote de implantação para servidor Windows (24/09/2026)

**Pedido do Anderson:** pacote para mover a aplicação para outra máquina, onde o Claude vai
montá-la. Destino: **servidor Windows** que vai rodar a web para os usuários. Documentação
histórica incluída; o C# do addon antigo, não.

**Onde está:** `pacote_windows/` na pasta do projeto, e o `.zip` ao lado. O ponto de entrada é o
`CLAUDE.md` do pacote (roteiro em 8 fases, regras de segredo e de produção, lista do que o Claude
pode rodar sozinho). A pessoa começa pelo `LEIA-ME.md`.

**Origem do código:** a cópia da máquina do Anderson, conferida arquivo a arquivo (conteúdo
idêntico à cópia de trabalho). **Ficaram de fora:** `.env`, `.venv`, `__pycache__`, os restos
do módulo 1 (`app/modules/oportunidades/`, `templates/oportunidades.html`), que o Anderson
ainda tem na máquina porque eu não consigo apagar arquivos lá, e `tests/test_trava_producao.py`,
que quebra a coleta do pytest. Os dois continuam lá; apagar à mão.

**Dependências:** versões **exatas** do `.venv` do Anderson (Python 3.14) em
`requirements-windows.txt`, com duas adaptações para Windows: `uvicorn[standard]` desdobrado
nos extras (o `uvloop` do extra não existe para Windows) e `colorama` explícito (o click exige no
Windows). As 38 rodas `win_amd64`/`cp314` foram baixadas e vão no pacote, para instalar sem
internet. Existirem todas para Windows + 3.14 foi verificado no download. O conjunto exato foi
instalado do zero num Python **3.13** (Linux): `pip check` limpo, 244 testes passando e a web
subindo pelo mesmo comando do serviço. Em 3.14 não consegui testar aqui (só havia um 3.14
*release candidate*, com o qual o pydantic 2.13 é incompatível); a garantia para 3.14 é ser
exatamente o conjunto que roda hoje na máquina do Anderson.

**Correções no código feitas durante a revisão para Windows** (também gravadas na pasta do
Anderson):
1. `SL_CA_BUNDLE` e `SL_TIMEOUT_SECONDS` estavam documentados no `.env.example` mas eram
   ignorados pelo cliente da Service Layer (verify só por `SL_VERIFY_SSL`, timeout fixo em 60 s).
   No Windows importa: o Python não usa o repositório de certificados do sistema, e com CA
   interna a única saída era desligar a verificação. Agora `_verificacao_tls` respeita o bundle.
2. `.env` lido como `utf-8-sig`: o Bloco de Notas grava UTF-8 com BOM, e o BOM grudava no nome
   da primeira variável, que era ignorada sem aviso. Teste novo falha contra a versão antiga.
3. Quatro testes abriam arquivos por caminho relativo ao diretório atual; passaram a usar o
   caminho do próprio arquivo de teste. A suíte agora passa chamada de qualquer pasta.

4. A dica impressa pelo `pedidos-wbc buscar` mandava usar `<Num Oportunidade>` (a chave
   `OpprId`), mas os comandos recebem o nº do orçamento ("Nº Oportunidade"); e, para os já
   integrados, sugeria `processar-novos --force`, que recria OPs por cima das existentes. Agora
   aponta para `reprocessar-integrados <Nº Oportunidade>`.

**Revisão independente** (subagente, antes de fechar o pacote) encontrou 26 pontos; os que
importavam foram corrigidos: um argumento com aspas duplas que o PowerShell 5.1 corta ao passar
para o Python (o `02_instalar.ps1` pararia na primeira execução, com diagnóstico enganoso); a pasta
do pacote gravável por usuários comuns com o código rodando como SYSTEM (o 02 agora restringe);
firewall possivelmente desligado por GPO sem aviso (o 05 agora confere); rodar o 05 de novo
voltava o firewall ao padrão (parâmetros agora ficam em `config/servico.json`); senha da conta de
serviço na linha de comando (agora `Get-Credential`); scripts parando o servidor no meio de uma
execução (agora consultam `/tarefas` e recusam sem `-Forcar`); "Liberar"/"Replanejar" gravam sem
conferência e o CLAUDE.md dizia só "não clicar em Executar"; risco de CSRF registrado; `.env.example`
em CRLF+BOM, com orientação de aspas para senhas com `#`/`$`.

**Decisões de implantação:**
- **Um worker só** (`--workers 1`), escrito em todos os scripts: planos de confirmação e
  execuções vivem na memória do processo.
- **Log em arquivo com rotação** (`config/log_config.json` → `logs/app.log`, 10 × 10 MB). Até
  aqui a web não configurava log nenhum: o `logger.info` dos serviços só aparecia na tela
  "Execuções" e se perdia com ela.
- **`PYTHONUTF8=1`** em tudo que o pacote inicia: sem isso, mensagem de log com caractere fora do
  cp1252 some com `UnicodeEncodeError` quando a saída vai para arquivo.
- **Serviço:** NSSM (recomendado, o `nssm.exe` não vai no pacote: executável de terceiro vem da
  fonte oficial) ou Agendador de Tarefas (nativo; `.cmd` com laço de reinício, espera com `ping`
  porque `timeout` não funciona sem console).
- **Firewall** restrito a `LocalSubnet` por padrão.

**Riscos registrados no pacote (`docs/SEGURANCA.md`), não resolvidos:** a aplicação **não tem
login** (e `/docs` do FastAPI está ligado). Recomendado implementar autenticação antes de ampliar
o acesso. Continua pendente a checagem de coerência `HANA_SCHEMA` × `SL_COMPANY_DB`: o script 01
avisa quando diferem, mas a aplicação não impede.

**Não testado de verdade:** os scripts PowerShell passaram na análise de sintaxe (PowerShell 7) e
as funções comuns foram exercitadas, mas os cmdlets exclusivos de Windows (serviço, tarefa,
firewall, ODBC, `icacls`) só vão rodar pela primeira vez no servidor.

## 8. Débitos técnicos e riscos identificados no legado (para decidir o que herdar)

1. **Credenciais em texto puro** no `app.config` (usuário/senha do SQL Server WBC, IP do servidor). → Nova app: variáveis de ambiente/secret manager, nunca no repo.
2. **SQL montado por concatenação de string** em vários pontos (`String.Format` direto em valores vindos de grid/usuário, ex. `EditText0.Value` entrando direto no `WHERE`) → risco de SQL injection. Recomenda-se, na reescrita, usar parâmetros bindados (`pyodbc`/`hdbcli` com placeholders) mesmo mantendo o texto da query.
3. **Erros engolidos silenciosamente** (`catch { log; continue; }`) em quase todo lugar — bom para não travar lote, ruim para diagnóstico. Fase 1 replica o comportamento; fase 2 poderia expor um painel de "erros pendentes" na web lendo o próprio `INO_LOG`.
4. **Regras de negócio hardcoded em listas fixas**: `Resources/Explosao.txt` (~85 códigos de item que forçam uma flag `U_INO_EXPL_SOLDA`), `Resources/Solda.txt` (lista grande de códigos que forçam grupo de item 332) e o `stringArray` de prefixos (`ARG`, `BAG`, `PAG`, `TPO`...) dentro de `CriaItem`. Fase 1: portar como estão (arquivos de config/lista); fase 2: candidatos a virar tabela configurável.
5. **Lógica ambígua entre `UpdateTabPedido`, `UpdateTabPedidoCong` e `UpdatePedido`** (seção 4.4.1, passo 3) — os três têm código muito parecido com pequenas diferenças na condição de disparo; recomenda-se validar com o usuário o critério de negócio real antes de portar (pergunta aberta, seção 9).
6. **Bug potencial na geração do ID mascarado** (módulo 1): `GetNextMaskedNum` é chamado passando sempre `{num}=0` para *cada* pedido do loop, e o incremento do contador só acontece depois dentro de `AtualizaDoc`. Se duas oportunidades forem processadas na mesma leitura, ambas podem calcular o **mesmo número** antes que o contador seja persistido (não há transação/lock). Precisa decisão: reproduzir o bug (fase 1, para não gerar divergência de numeração vs. o legado enquanto os dois rodarem em paralelo) ou já corrigir com um lock otimista.
7. **Código morto/de teste em produção**: `ManutencaoOp.cancelapedidoteste()` (cancela um range fixo de pedidos), blocos gigantes comentados em `Form2`, `AtualizaLinhaAntiga` sem corpo. Não portar.
8. **Cultura de parsing decimal (`pt-BR`)**: algumas conversões usam `CultureInfo.GetCultureInfo("pt-BR")` (vírgula decimal) enquanto outras assumem ponto — atenção ao portar para Python (usar `Decimal` com parsing explícito, nunca depender de locale do SO).
9. **Timeouts de comando aumentados manualmente** (`command.CommandTimeout = 500`/`300`) sinalizam que certas queries (`ExecuteSelectNew`, `ExecuteSelect2`) são pesadas — bons candidatos a virar Views/índices no HANA em vez de SQL ad-hoc repetido.

---

## 9. Perguntas em aberto

~~1. Critério exato para escolher entre `UpdateTabPedido`, `UpdateTabPedidoCong` e `UpdatePedido`~~ — **resolvida em 15/09/2026 lendo o C# original**, não era ambígua (ver seção 7.4). Fica registrada aqui só para histórico: a árvore de decisão real é `U_INO_Congelado` → (se "N") `U_INO_UpdateDetalhe`.

1. O **bug de concorrência na numeração mascarada** (item 6, seção 8): interessa manter compatível bit-a-bit com o legado enquanto os dois sistemas convivem, ou já corrigir?
2. **Autenticação da aplicação web**: usar os mesmos usuários/senhas do SAP B1 (via Service Layer, delegando o login), ou um login próprio da aplicação (mais simples, mas duplica gestão de usuário)?
3. **Ambiente de homologação**: existe uma company DB de teste na Service Layer e um banco WBC de teste para validar os módulos 1 e 2 sem tocar produção?
4. ~~**Permissão para criar Calculation Views / Views no HANA**~~ — **resolvida por decisão em 16/09/2026**: a revisão da seção 6.4 colocou o `hdbcli` como caminho preferencial de leitura, então não é mais necessário criar Views para as queries complexas e a pergunta perde o objeto. Em contrapartida, aparece um requisito no lugar: o usuário HANA do `.env` precisa de **SELECT** nas tabelas lidas — e, para a auditoria `comparar-ops` (seção 7.12), também no schema de produção.
5. As listas fixas (`Explosao.txt`, `Solda.txt`, prefixos hardcoded) mudam com alguma frequência? Se sim, vale a pena já nascerem como tabela editável em vez de arquivo — mesmo mantendo a fase 1 "1:1", dá para já persistir o conteúdo delas numa tabela de configuração lida pela app, sem mudar o comportamento.
6. **Schema do HANA para leituras diretas**: o `.env` do projeto "WBCPython" observa que, no protótipo original, as views (`VW_CLIENTE_MUNICIPIO_ALTA`, `VW_EVOL_OPORTUNIDADE_ALT`) eram consultadas no schema de **produção** mesmo quando a escrita apontava para homologação. Isso vale também para este `python_app`? Precisa ser confirmado antes de qualquer leitura via `HanaDirectReader` em ambiente de homologação.
7. **Reconciliação com o projeto "WBCPython"** (descoberto em 15/09/2026, ver seção 7.5): esse projeto já implementa CLI com `--ambiente`, painel Streamlit, worker agendado, banco de tracking e a trava `WBC_BLOCK_PRODUCTION_WRITES`/`ProductionWriteBlocked`. Quando o Anderson quiser, decidir: (a) este `python_app` absorve essas peças do WBCPython, (b) os dois projetos continuam separados com propósitos diferentes, ou (c) este projeto é descontinuado em favor do WBCPython. Enquanto isso não for decidido, `Settings.is_production` fica pronto mas sem nenhuma trava de escrita usando-o.
8. ~~**Conteúdo real de `Resources/Solda.txt`**~~ — **resolvida em 21/09/2026**: o arquivo estava em `ControleProducao/Resources/` e foi instalado (ver seção 7.20). Fica o resíduo: itens criados antes dessa data podem estar no grupo 358 em vez de 332.
9. **`CriaOP` "pula" a criação da OP quando `entregaMultipla == "Y"`** (seção 7.4) — parece uma condição invertida no C# original (o nome sugere o oposto do que o código faz). Confirmar com o Anderson se isso é intencional antes de validar o módulo 2 em homologação.
10. **Nomes exatos de entidade/campo na Service Layer para `Resources` (Recurso) e para a coleção de linhas de `SalesOpportunities`** (módulo 2, `cria_recurso_rateio`/`_add_pedido_oportunidade`) — precisam ser confirmados contra o `$metadata` real do ambiente antes do primeiro teste; a DI API usava Business Services (`ResourcesService`) que podem mapear diferente na Service Layer REST.

---

## 10. Próximos passos imediatos

*(Atualizado em 22/09/2026 — a lista original, de 14/09, foi integralmente cumprida: os quatro itens de infraestrutura e a validação do módulo 1 estão em 7.x.)*

**Retestar o módulo 3.** O código mudou depois da execução validada de 21/09: exclusão de OP cancelada, liberação condicional, reversão da liberação em caso de falha, ordenação por dependência. O caminho "já Liberada → não envia status" tem teste mas nunca tocou o SAP, `liberar` nunca foi executado como comando, e `encerrar --pedido` nunca rodou contra ambiente real.

**Exercitar a web em homologação.** As telas dos módulos 2 e 3 existem e têm teste, mas nenhum clique real gravou nada ainda. O caminho a percorrer é o mesmo da CLI: buscar → conferir → confirmar → acompanhar a tarefa.

**Guarda de coerência `HANA_SCHEMA` × `SL_COMPANY_DB`.** Oferecida em 21/09 e ainda não implementada. Os dois podem divergir, e foi exatamente isso que fez parecer que `cancelar-ops` não cancelava: lê-se de um banco e grava-se em outro, sem nada avisando.

**Módulo 4 (Romaneio) e tela de Configurações.** Ambos intocados — o 4 tem só o esqueleto de service e as rotas de navegação. Com o módulo 1 fora do escopo (7.36), o Romaneio é o único que resta portar.

**Resultado legível para `comparar-ops` na web.** Hoje sai como JSON na tela de acompanhamento.

---

*Documento gerado a partir da leitura integral do código-fonte em `ControleProducao/` (Controllers, Models, Views, Querys.resx, app.config) em 14/09/2026. Substitui a versão anterior deste arquivo, que cobria apenas o módulo de Oportunidades — módulo que, em 22/09/2026, saiu do escopo do porte (seção 7.36).*
