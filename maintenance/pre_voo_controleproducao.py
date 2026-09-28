"""Pre-flight of the Controle de Produção pilot (F5 of docs/PLANO_CONTROLE_PRODUCAO_11.md).

Read-only, against the company the package resolves (``SL_COMPANY_DB`` → ``hana_schema``).
Every statement is a SELECT with the schema spelled out; the only inputs are the WBC quote
number / order DocNum given on the command line, and they go in as bound parameters.

What it answers, in the order the pilot needs it:

- A. which open orders are waiting for OPs (the same SELECT as the "Buscar" button);
- B. for ONE quote: the two ways of locating its order agree on the DocEntry, the INO flags
  (``Integrar``, ``ProcessWBC``, ``UpdateDetalhe``, ``EntregaMultipla``), lines and groups
  of the same item (the rateio case), an existing ``GGF_`` resource, ``@INO_LOG`` history
  and live OPs — anything unexpected here is a reason not to process it today;
- C. who created OPs in the last days (user × day) — tells whether the legacy addon is still
  running, i.e. whether the PCP must be warned before the pilot (D14);
- D. baseline audits: live OPs whose sales order is cancelled (orphans) and the rateio
  failures logged by the package.

Run from the repository root. From the notebook the local ``.env`` resolves HOMOLOG; to look
at production pass the company for this process only::

    SL_COMPANY_DB=SBOALTAMIRAPROD python maintenance/pre_voo_controleproducao.py 00125460
    python maintenance/pre_voo_controleproducao.py --pedido 84435 --dias 10

On the .11 the ``.env`` already points at production. Writes are impossible here by
construction (no Service Layer client is created), and the IP gate would refuse them anyway
off the .11.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controleproducao.config import get_settings  # noqa: E402
from controleproducao.core.hana_reader import HanaDirectReader  # noqa: E402
from wbcpython.safety import assert_read_only_sql  # noqa: E402

_NOME_DE_SCHEMA = re.compile(r"^[A-Z0-9_]+$")
_LARGURA = 30  # rows printed per section before "... +N"

Consulta = tuple[str, str, tuple[Any, ...]]


def _schema_entre_aspas(nome: str) -> str:
    """The schema comes from settings, never from the command line — still, it is spliced
    into SQL, so it must look like an identifier."""
    if not _NOME_DE_SCHEMA.match(nome):
        raise SystemExit(f"schema inesperado: {nome!r}")
    return f'"{nome}"'


def _consultas_do_pedido(s: str, orc: str | None, pedido: int | None) -> list[Consulta]:
    """Section B: one quote (or one DocNum) under the microscope. ``s`` is the quoted schema."""
    if orc is not None:
        where_r, params = 'R."U_INO_COTWBC" = ?', (orc,)
    else:
        where_r, params = 'R."DocNum" = ?', (pedido,)
    where_x = where_r.replace('R."', 'X."')
    # The quote number of the order found by B1, for the lookups that key on it (B2, B5, B6).
    orc_do_pedido = f'(SELECT MAX(X."U_INO_COTWBC") FROM {s}.ORDR X WHERE {where_x} AND X."CANCELED" = \'N\')'
    return [
        (
            "B1. Cabeçalho e flags INO (CANCELED='N')",
            f'''SELECT R."DocEntry", R."DocNum", R."DocStatus", R."DocDate", R."BPLId", R."DocTotal",
                       R."U_INO_COTWBC", R."U_INO_Integrar", R."U_INO_ProcessWBC", R."U_INO_UpdateDetalhe",
                       R."U_INO_EntregaMultipla", R."U_INO_Congelado", R."UpdateDate"
                  FROM {s}.ORDR R WHERE {where_r} AND R."CANCELED" = 'N' ''',
            params,
        ),
        (
            "B2. Localização 2 — OOPR.U_ORCNUM_WBC via OPR1 (deve devolver o MESMO DocEntry de B1)",
            f'''SELECT R."DocEntry", R."DocNum", O."OpprId", O."U_ORCNUM_WBC", O."U_ORCNUM_MASC",
                       O."U_INO_IntegrouWBC"
                  FROM {s}.OOPR O
                  JOIN {s}.OPR1 L ON L."OpprId" = O."OpprId" AND L."ObjType" = 17
                  JOIN {s}.ORDR R ON R."DocEntry" = L."DocId"
                 WHERE R."CANCELED" = 'N' AND O."U_ORCNUM_WBC" = {orc_do_pedido}''',
            params,
        ),
        (
            "B3. Linhas: total, com U_INO_OP, itens distintos, abertas",
            f'''SELECT COUNT(*) AS "Linhas",
                       SUM(CASE WHEN IFNULL(L."U_INO_OP", 0) <> 0 THEN 1 ELSE 0 END) AS "LinhasComOP",
                       COUNT(DISTINCT L."ItemCode") AS "ItensDistintos",
                       SUM(CASE WHEN L."LineStatus" = 'O' THEN 1 ELSE 0 END) AS "LinhasAbertas"
                  FROM {s}.RDR1 L JOIN {s}.ORDR R ON R."DocEntry" = L."DocEntry"
                 WHERE {where_r} AND R."CANCELED" = 'N' ''',
            params,
        ),
        (
            "B4. Itens em mais de uma linha (>= 2 grupos = o caso do rateio GGF_)",
            f'''SELECT L."ItemCode", COUNT(*) AS "Linhas", SUM(L."Quantity") AS "Qtd"
                  FROM {s}.RDR1 L JOIN {s}.ORDR R ON R."DocEntry" = L."DocEntry"
                 WHERE {where_r} AND R."CANCELED" = 'N'
                 GROUP BY L."ItemCode" HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC''',
            params,
        ),
        (
            "B5. Recurso GGF_ do orçamento já existe? (esperado antes do 1º processar: não)",
            f'''SELECT "ResCode", "ResName" FROM {s}.ORSC
                 WHERE "ResCode" LIKE '%' || {orc_do_pedido} || '%' ''',
            params,
        ),
        (
            "B6. @INO_LOG do orçamento (já passou por processamento?)",
            f'''SELECT G."CreateDate", G."Creator", LEFT(G."U_Mensagem", 120) AS "Msg"
                  FROM {s}."@INO_LOG" G
                 WHERE G."U_OrcNum" = {orc_do_pedido}
                 ORDER BY G."CreateDate" DESC''',
            params,
        ),
        (
            "B7. OPs vivas do pedido, por autor e dia",
            f'''SELECT W."CreateDate", U."USER_CODE", W."Status", COUNT(*) AS "OPs",
                       SUM(W."PlannedQty") AS "QtdPlanejada"
                  FROM {s}.OWOR W
                  JOIN {s}.ORDR R ON R."DocEntry" = W."OriginAbs"
                  LEFT JOIN {s}.OUSR U ON U."USERID" = W."UserSign"
                 WHERE {where_r} AND R."CANCELED" = 'N' AND W."Status" <> 'C'
                 GROUP BY W."CreateDate", U."USER_CODE", W."Status" ORDER BY W."CreateDate"''',
            params,
        ),
    ]


def _consultas(s: str, orc: str | None, pedido: int | None, desde: date) -> list[Consulta]:
    """(title, sql, params) in display order. ``s`` is the quoted schema."""
    consultas: list[Consulta] = [
        (
            "A. Pedidos abertos com Integrar='Y' ainda sem ProcessWBC='Y' (o 'Buscar' da tela)",
            f'''SELECT R."DocNum", R."DocEntry", R."DocDate", R."CardCode", R."DocTotal", O."U_ORCNUM_MASC",
                       (SELECT COUNT(*) FROM {s}.OWOR W
                         WHERE W."OriginAbs" = R."DocEntry" AND W."Status" <> 'C') AS "OPsVivas"
                  FROM {s}.ORDR R
                  JOIN {s}.OPR1 L ON L."DocId" = R."DocEntry" AND L."ObjType" = 17
                  JOIN {s}.OOPR O ON O."OpprId" = L."OpprId"
                 WHERE IFNULL(O."U_INO_IntegrouWBC", 'N') = 'Y' AND IFNULL(R."U_INO_ProcessWBC", 'N') = 'N'
                   AND R."DocStatus" = 'O' AND R."CANCELED" = 'N' AND R."U_INO_Integrar" = 'Y'
                   AND O."U_ORCNUM_MASC" <> ''
                 ORDER BY R."DocNum" DESC''',
            (),
        ),
    ]
    if orc is not None or pedido is not None:
        consultas += _consultas_do_pedido(s, orc, pedido)
    consultas += [
        (
            f"C. Quem criou OP desde {desde:%d/%m} (usuário × dia) — addon vivo?",
            f'''SELECT W."CreateDate", U."USER_CODE", COUNT(*) AS "OPs", COUNT(DISTINCT W."OriginAbs") AS "Pedidos",
                       MIN(W."OriginNum") AS "PedidoMin", MAX(W."OriginNum") AS "PedidoMax"
                  FROM {s}.OWOR W LEFT JOIN {s}.OUSR U ON U."USERID" = W."UserSign"
                 WHERE W."CreateDate" >= ?
                 GROUP BY W."CreateDate", U."USER_CODE" ORDER BY W."CreateDate", U."USER_CODE"''',
            (desde.isoformat(),),
        ),
        (
            "D1. OPs órfãs — OWOR viva cujo pedido de origem está CANCELED='Y' (esperado: 0)",
            f'''SELECT W."DocEntry", W."DocNum", W."OriginNum", W."ItemCode", W."Status", W."PlannedQty", W."CreateDate"
                  FROM {s}.OWOR W JOIN {s}.ORDR R ON R."DocEntry" = W."OriginAbs"
                 WHERE W."OriginType" = '17' AND W."Status" <> 'C' AND R."CANCELED" = 'Y'
                 ORDER BY W."DocEntry" DESC''',
            (),
        ),
        (
            "D2. @INO_LOG — rateio que falhou (OPs sem a linha GGF_)",
            f'''SELECT DISTINCT "U_OrcNum", "CreateDate", "Creator"
                  FROM {s}."@INO_LOG"
                 WHERE "U_Mensagem" LIKE 'Erro ao preencher recurso%'
                 ORDER BY "CreateDate"''',
            (),
        ),
    ]
    return consultas


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pré-voo do piloto do Controle de Produção (só leitura).")
    ap.add_argument("orcamento", nargs="?", help="nº do orçamento WBC como está em ORDR.U_INO_COTWBC (ex. 00125460)")
    ap.add_argument("--pedido", type=int, help="DocNum do pedido, em vez do orçamento")
    ap.add_argument("--dias", type=int, default=8, help="janela da seção C (quem criou OP); padrão 8")
    args = ap.parse_args(argv)
    if args.orcamento and args.pedido:
        ap.error("informe o orçamento OU --pedido, não os dois")
    orc = args.orcamento.strip() if args.orcamento else None
    if orc is not None and not orc.isdigit():
        ap.error("o orçamento é só dígitos (ex. 00125460)")

    settings = get_settings()
    schema = _schema_entre_aspas(settings.hana_schema)
    desde = date.today() - timedelta(days=args.dias)
    print(f"alvo: {settings.hana_schema} (is_production={settings.is_production}) · só leitura")

    reader = HanaDirectReader(settings)
    try:
        for titulo, sql, params in _consultas(schema, orc, args.pedido, desde):
            assert_read_only_sql(sql, fonte="HANA")
            print(f"\n=== {titulo}")
            try:
                linhas = reader.fetch_all(sql, params)
            except Exception as exc:  # noqa: BLE001 — one failing query must not hide the others
                print(f"  ERRO: {type(exc).__name__}: {str(exc)[:300]}")
                continue
            print(f"  {len(linhas)} linha(s)")
            for linha in linhas[:_LARGURA]:
                print("  " + " | ".join(f"{k}={v}" for k, v in linha.items()))
            if len(linhas) > _LARGURA:
                print(f"  ... +{len(linhas) - _LARGURA}")
    finally:
        reader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
