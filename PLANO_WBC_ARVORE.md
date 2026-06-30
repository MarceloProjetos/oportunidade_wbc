# Plano — Sincronização da Árvore de Produto WBC (`INTEGRACAO_ORCPRDARV`)

> **Status:** implementado (pendente de validação contra o SAP/SQL reais e de rodar o SQL no Supabase).

## Objetivo

Quando um pedido é sincronizado pelo painel e a **OS está OK**, espelhar também a **árvore de
produto WBC** daquele orçamento no Supabase, numa tabela nova consumida **read-only** por outro
programa.

## Vínculo de dados

- O **código WBC** de um pedido é o `ORCNUM` (WBC), que no SAP aparece como **`NºOrçament`**
  na view de OS `VW_EXPORT_ORDENS_SERVICO_1`. Ex.: pedido **83913** → `NºOrçament` **00123822**.
- A árvore está em `WBCCAD.dbo.INTEGRACAO_ORCPRDARV`, filtrada por `ORCNUM` (`nvarchar(8)`,
  com zero à esquerda).

## Fluxo (gatilho = o botão "Sincronizar" que já existe)

Em `api.py` → `_sync_one(nped)` (sem mudança para o usuário):

1. `diagnosticar_nped` (OWOR): **só segue se a OS existe e não está cancelada** ("OS OK").
2. `sync_os(nped)` — a sincronização da OS de hoje.
3. **Se a OS sincronizou OK → dispara `extract_wbc_arvore.main(nped)`** (best-effort: falha aqui
   é logada e **não** quebra a resposta da OS; vem no campo `wbc` do resultado).

`extract_wbc_arvore.main(nped)`:
- `resolver_orcnum(nped)` → lê `NºOrçament` no SAP e normaliza para 8 dígitos;
- `extract_arvore_to_dataframe(orcnum)` → `SELECT * FROM INTEGRACAO_ORCPRDARV WHERE ORCNUM = ?`
  (parametrizado);
- grava em `wbc_arvore_produto` com **replace por ORCNUM** (carrega-depois-poda escopado: re-sync
  troca só aquela árvore, nunca esvazia a tabela se algo falhar);
- registra em `sincronizacao_log_wbc_arvore`.

## Supabase

- **`sql/wbc_arvore.sql`** — DDL de `wbc_arvore_produto` (espelho das 13 colunas, case idêntico
  ao SQL Server, entre aspas) + `sincronizacao_log_wbc_arvore`; **RLS forçado, sem policy**
  (só `service_role` escreve/lê).
- **`sql/wbc_arvore_read_policy.sql`** — policy de **SELECT para `anon`** → o programa consumidor
  lê com a **anon key** (read-only por construção, pois não há policy de escrita). Mesmo padrão da
  tabela de OS.

## Configuração (`.env`, todas opcionais)

| Var | Default |
|-----|---------|
| `WBC_ARVORE_VIEW` | `WBCCAD.dbo.INTEGRACAO_ORCPRDARV` |
| `WBC_ARVORE_TABLE` | `wbc_arvore_produto` |
| `WBC_ARVORE_SYNC_LOG_TABLE` | `sincronizacao_log_wbc_arvore` |
| `WBC_ARVORE_INSERT_BATCH_SIZE` | `500` |

## Deploy

1. Rodar no SQL Editor do Supabase: `sql/wbc_arvore.sql`, depois `sql/wbc_arvore_read_policy.sql`.
2. Copiar para o servidor: `extract_wbc_arvore.py`, `api.py`, `config.py`, `db_utils.py`.
3. `nssm restart OrcaView-OS-API`.
4. Sincronizar um pedido com OS OK e conferir `wbc_arvore_produto` + `sincronizacao_log_wbc_arvore`.

## Pontos a validar no ambiente real

- **`NºOrçament` ↔ `ORCNUM`**: confirmar que o valor casa (a normalização faz `zfill(8)` quando
  numérico). Se o `NºOrçament` vier em outro formato, ajustar `_normaliza_orcnum`.
- **Case das colunas** (pyodbc): a DDL assume os nomes exatos da tabela
  (`ORCNUM`, `idIntegracao_OrcPrdArv`, `orcprdarv_dth`, …). Se o pyodbc devolver case diferente,
  alinhar a DDL.
- **"OS OK"**: hoje = *existe e não cancelada*. Se "OK" tiver um status específico (ex.: liberada
  `R`), é só apertar a condição em `_sync_one`.
