"""Lines of integration orders with their change log, for `domain.peso_reescalado` — read only.

Only open lines of open orders that the integration created (version 1 saved by its Service
Layer user — or no version 1 left, the SAP keeps 99, so the rule can warn instead of the order
vanishing) and that a person saved since ``desde``. Two queries per call. Same schema rule as
`oportunidades`: the company that receives the writes (`SL_COMPANY_DB`), never `HANA_SCHEMA`.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from typing import Any

from wbcpython.config import HanaSettings, Settings
from wbcpython.domain.peso_reescalado import LinhaAtual, Versao, versao_da_linha
from wbcpython.infrastructure.hana.identificadores import citar_identificador
from wbcpython.infrastructure.hana.repository import _abrir_conexao_hdbcli

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LinhaDoPedido:
    doc_entry: int
    doc_num: int
    orcamento: str
    line_num: int
    item: str
    atual: LinhaAtual
    versoes: tuple[Versao, ...]
    #: The line's first known version is the order's oldest surviving one, and that is not
    #: version 1: who created it is lost — see `decidir`.
    historico_cortado: bool = False


class RepositorioPesosReescaladosHana:
    def __init__(
        self,
        settings: HanaSettings,
        *,
        company_db: str,
        usuarios_da_integracao: Collection[str],
        fabrica_de_conexao: Any = None,
    ) -> None:
        self._schema = citar_identificador(company_db, descricao="SL_COMPANY_DB")
        self._usuarios = sorted({u.strip().lower() for u in usuarios_da_integracao if u.strip()})
        if not self._usuarios:
            # Without it there is no telling the integration's weight from a typed one.
            raise ValueError("SL_USERNAME vazio: sem ele não há como saber o que a integração gravou.")
        self._fabrica = fabrica_de_conexao or (lambda: _abrir_conexao_hdbcli(settings))
        self._conexao: Any = None

    @classmethod
    def de_settings(cls, settings: Settings) -> RepositorioPesosReescaladosHana:
        """The worker's and the CLI's reader: the company that receives the writes, its SL user."""
        return cls(
            settings.hana,
            company_db=settings.service_layer.company_db,
            usuarios_da_integracao={settings.service_layer.username},
        )

    def close(self) -> None:
        if self._conexao is not None:
            try:
                self._conexao.close()
            except Exception as exc:  # noqa: BLE001 - closing must not raise
                logger.warning("Falha ao encerrar a conexão com o HANA: %s", exc)
            finally:
                self._conexao = None

    def _consultar(self, sql: str, params: tuple) -> list[dict[str, Any]]:
        if self._conexao is None:
            self._conexao = self._fabrica()
        cursor = self._conexao.cursor()
        try:
            cursor.execute(sql, params)
            colunas = [d[0] for d in cursor.description]
            return [dict(zip(colunas, linha, strict=False)) for linha in cursor.fetchall()]
        finally:
            cursor.close()

    def linhas(self, *, desde: date | None, pedido: int | None = None) -> list[LinhaDoPedido]:
        """``desde`` = the oldest date a person's save counts; ``None`` with ``pedido`` = any date."""
        s, usuarios = self._schema, tuple(self._usuarios)
        integracao = (
            f'SELECT U."USERID" FROM {s}."OUSR" U WHERE LOWER(U."USER_CODE") IN '
            f'({", ".join("?" for _ in usuarios)})'
        )
        autor = 'COALESCE(A."UserSign2", A."UserSign")'
        pedidos = self._consultar(
            f"""
SELECT R."DocEntry", R."DocNum", R."U_INO_COTWBC",
       (SELECT MIN(A."LogInstanc") FROM {s}."ADOC" A
        WHERE A."ObjType" = '17' AND A."DocEntry" = R."DocEntry") AS "PrimeiraVersao"
FROM {s}."ORDR" R
WHERE R."CANCELED" = 'N' AND R."DocStatus" = 'O' AND LENGTH(R."U_INO_COTWBC") > 0
  AND (? IS NULL OR R."DocNum" = ?)
  AND (EXISTS (SELECT 1 FROM {s}."ADOC" A WHERE A."ObjType" = '17' AND A."DocEntry" = R."DocEntry"
               AND A."LogInstanc" = 1 AND {autor} IN ({integracao}))
       OR NOT EXISTS (SELECT 1 FROM {s}."ADOC" A WHERE A."ObjType" = '17'
                      AND A."DocEntry" = R."DocEntry" AND A."LogInstanc" = 1))
  AND EXISTS (SELECT 1 FROM {s}."ADOC" A WHERE A."ObjType" = '17' AND A."DocEntry" = R."DocEntry"
              AND (? IS NULL OR A."UpdateDate" >= ?) AND {autor} NOT IN ({integracao}))
""",
            (pedido, pedido, *usuarios, desde, desde, *usuarios),
        )
        if not pedidos:
            return []
        cabecalho = {int(p["DocEntry"]): p for p in pedidos}
        entradas = tuple(cabecalho)
        por_linha: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
        # Every version of every open line, with the line as it is now (RDR1) on each row.
        for linha in self._consultar(
            f"""
SELECT L."DocEntry", L."LineNum", L."LogInstanc", L."Quantity", L."Weight1", L."LineTotal",
       A."UpdateDate", A."UpdateTS", U."USER_CODE", U."U_NAME", C."ItemCode",
       C."Quantity" AS "QtdAtual", C."Weight1" AS "PesoAtual", C."LineTotal" AS "TotalAtual"
FROM {s}."ADO1" L
INNER JOIN {s}."RDR1" C ON C."DocEntry" = L."DocEntry" AND C."LineNum" = L."LineNum"
                       AND C."LineStatus" = 'O'
INNER JOIN {s}."ADOC" A ON A."ObjType" = L."ObjType" AND A."DocEntry" = L."DocEntry"
                        AND A."LogInstanc" = L."LogInstanc"
LEFT JOIN {s}."OUSR" U ON U."USERID" = {autor}
WHERE L."ObjType" = '17' AND L."DocEntry" IN ({", ".join("?" for _ in entradas)})
ORDER BY L."DocEntry", L."LineNum", L."LogInstanc"
""",
            entradas,
        ):
            por_linha[(int(linha["DocEntry"]), int(linha["LineNum"]))].append(linha)

        resultado = []
        for (doc_entry, line_num), linhas in por_linha.items():
            versoes = tuple(versao_da_linha(v, self._usuarios) for v in linhas)
            primeira = int(cabecalho[doc_entry]["PrimeiraVersao"] or 1)
            agora = linhas[-1]
            resultado.append(
                LinhaDoPedido(
                    doc_entry=doc_entry,
                    doc_num=int(cabecalho[doc_entry]["DocNum"]),
                    orcamento=str(cabecalho[doc_entry]["U_INO_COTWBC"] or "").strip(),
                    line_num=line_num,
                    item=str(agora["ItemCode"] or ""),
                    atual=LinhaAtual(
                        float(agora["QtdAtual"] or 0),
                        float(agora["PesoAtual"] or 0),
                        float(agora["TotalAtual"] or 0),
                    ),
                    versoes=versoes,
                    historico_cortado=primeira > 1 and versoes[0].instancia == primeira,
                )
            )
        return resultado
