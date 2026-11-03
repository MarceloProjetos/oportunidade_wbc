"""Repository of the WBC: **read-only** access to the `WBCCAD` database.

Rule 5 (`wbcpython/safety.py`; see `docs/wbc/ai_spec/00_index.md`): the integration never writes to the WBC SQL
Server, in any environment. The WBC itself is the only writer.

Three layers hold that promise here:

1. **The interface offers no write.** `RepositorioOrcamentosWbc` declares only query methods.
2. **Every query goes through the lock.** `_executar()` calls `assert_read_only_sql()` before sending anything
   to the database.
3. **A read-only database user** (`db_datareader`) is recommended as an extra, server-side layer.

Processing state, including "this quote was already integrated", belongs to the solution's tracking database,
never to WBCCAD.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from itertools import batched
from typing import Any, Protocol

from sqlalchemy import Engine, bindparam, create_engine, text
from sqlalchemy.engine import URL, Row

from wbcpython.config import WbcSqlSettings
from wbcpython.infrastructure.wbc_sql import queries
from wbcpython.infrastructure.wbc_sql.models import (
    DadosImpressaoWbc,
    ItemArvoreWbc,
    ItemOrcamentoWbc,
    OrcamentoWbc,
)
from wbcpython.safety import assert_read_only_sql

logger = logging.getLogger(__name__)

#: Orçamentos por consulta ao ler situações em lote. O SQL Server tem limite de
#: parâmetros por comando, e um `IN` muito grande piora o plano de execução.
TAMANHO_DO_LOTE = 1000

FONTE = "SQL Server do WBC (WBCCAD)"


class RepositorioOrcamentosWbc(Protocol):
    """Read contract for WBC quotes.

    Deliberately has **no write methods**. If writing something to the WBC ever seems necessary, the state belongs
    in the tracking database; it does not mean a method is missing here.
    """

    def buscar_orcamento(self, orcnum: str) -> OrcamentoWbc | None: ...

    def situacao_atual(self, orcnum: str) -> tuple[int, str] | None:
        """Return `(sitcode, revisao)` without loading the items."""
        ...

    def situacoes_atuais(self, orcnums: Sequence[str]) -> dict[str, tuple[int, str]]:
        """Same as `situacao_atual`, for several quotes at once."""
        ...

    def pesos_por_item(self, orcnum: str) -> dict[int, Decimal]:
        """Weight (tree level 1) of each item, for the order's `Weight1`."""
        ...

    def orcamentos_alterados_desde(
        self, desde: datetime, *, sitcode_minimo: int = 10
    ) -> list[str]: ...


def _decimal(valor: Any) -> Decimal:
    """Convert to `Decimal`; dirty data is logged and read as 0 instead of raising an obscure exception."""
    if valor is None:
        return Decimal(0)
    if isinstance(valor, Decimal):
        return valor
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        logger.warning("Valor numérico inesperado no WBC: %r — assumido 0.", valor)
        return Decimal(0)


def _decimal_opcional(valor: Any) -> Decimal | None:
    """Like `_decimal`, but keeps null instead of assuming zero.

    Serves `ORCPRDQTD`, where "not informed" and "zero" must stay distinct: the business rule uses 1 in both cases,
    but the rule decides that, not the read. Flattening here would hide that the column is null in 100% of the WBC
    rows.
    """
    return None if valor is None else _decimal(valor)


def _texto(valor: Any) -> str:
    return "" if valor is None else str(valor)


#: Marcadores de formatação que o WBC embute nos textos longos. Não são
#: caracteres de controle — são estas sequências literais, escritas no dado.
MARCADORES_WBC = ("[CR]", "[TAB]")


def _texto_wbc(valor: Any) -> str:
    """Long WBC text with the embedded formatting markers (`MARCADORES_WBC`) replaced by a space.

    Fields such as `ORCIMP_ACABAMENTO` and `ORCPGT` carry literal `[CR]` and `[TAB]` sequences in the middle of the
    text, a leftover of how the WBC builds the printout. The legacy replaces them in the query itself; here it is
    done at read time, so it holds for any query that brings the field. Without it the finish reaches SAP as
    `"[CR][CR][CR][CR]Cinza Padrão Altamira..."`, visibly broken for whoever opens the quote.
    """
    texto = _texto(valor)
    for marcador in MARCADORES_WBC:
        texto = texto.replace(marcador, " ")
    return texto.strip()


def _revisao(valor: Any) -> str:
    """Normalize the revision: stripped and **always uppercase**.

    The revision letter (`A`, `B`, `C`...) identifies the quote version, and the case matters in two places:

    * it goes to the document's `U_INO_VERSAOWBC` UDF in SAP, read by people and compared by queries: `"a"` and
      `"A"` must not coexist;
    * it feeds `ordem_revisao()`, which also uppercases on its own (ASCII - 64); keeping the value uppercase here
      guarantees that an old revision never looks newer and triggers a cancel and recreate of the document.

    In today's data the column is null or already uppercase, so this is a safeguard.
    """
    return _texto(valor).strip().upper()


class RepositorioOrcamentosWbcSql:
    """`RepositorioOrcamentosWbc` over SQL Server (or any database supported by SQLAlchemy).

    Takes a ready `Engine`, so it can be tested against in-memory SQLite with no SQL Server involved.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------ construção

    @classmethod
    def a_partir_de(cls, settings: WbcSqlSettings) -> RepositorioOrcamentosWbcSql:
        """Build the repository from the settings (`mssql+pymssql`).

        Read-only is enforced by `_executar`, not by the connection (see `montar_url`).
        """
        return cls(create_engine(cls.montar_url(settings), pool_pre_ping=True))

    @staticmethod
    def montar_url(settings: WbcSqlSettings) -> URL:
        """Build the connection URL.

        Uses `URL.create()` instead of formatting the string by hand: with direct interpolation a password containing
        `@` turned the following part into the **hostname** (connecting to the wrong server and leaking part of the
        password in any log of the "masked" URL), and a `%` was read as a URL escape, silently authenticating with a
        different password. `URL.create()` treats the password as data, and its `repr()` masks it.

        **`ApplicationIntent=ReadOnly` is not used.** It was tried as defense in depth, but `pymssql` does not
        accept it: SQLAlchemy forwards it to the driver, which fails with
        `TypeError: connect() got an unexpected keyword argument`. It works with `pyodbc`, not with this driver. Found
        when connecting in the real environment; leaving it would break every WBC read.

        Read-only does not depend on it: it is in the code (`assert_read_only_sql` at the single execution point) and in
        the interface, which exposes no write method. The extra layer belongs on the **server**: a database user with
        read-only permission (`db_datareader`), more effective than any connection flag.
        """
        return URL.create(
            "mssql+pymssql",
            username=settings.username,
            password=settings.password.get_secret_value(),
            host=settings.host,
            port=settings.port,
            database=settings.database,
        )

    # -------------------------------------------------------------- execução

    def _executar(
        self, sql: str, *, expandir: tuple[str, ...] = (), **parametros: Any
    ) -> Sequence[Row[Any]]:
        """Single execution point; this is where the read-only lock is applied.

        `expandir` names the parameters that receive a list and become an `IN` with one marker per item. SQLAlchemy
        builds the list: the query text stays fixed, and the lock inspects it before any expansion.
        """
        assert_read_only_sql(sql, fonte=FONTE)
        consulta = text(sql)
        if expandir:
            consulta = consulta.bindparams(*(bindparam(nome, expanding=True) for nome in expandir))
        with self._engine.connect() as conexao:
            return conexao.execute(consulta, parametros).fetchall()

    # -------------------------------------------------------------- consultas

    def buscar_orcamento(self, orcnum: str) -> OrcamentoWbc | None:
        linhas = self._executar(queries.ORCAMENTO_POR_NUMERO, orcnum=orcnum)
        if not linhas:
            return None
        orcamento = self._montar_orcamento(linhas)
        return orcamento.model_copy(update={"arvore": tuple(self.buscar_linhas_arvore(orcnum))})

    def buscar_linhas_arvore(self, orcnum: str) -> list[ItemArvoreWbc]:
        """Product tree of the quote; empty when there is no breakdown.

        A separate query on purpose: the tree has its own cardinality (a quote with 20 tree rows for 2 items is common)
        and joining it to the header query would multiply the items.
        """
        linhas = self._executar(queries.LINHAS_DA_ARVORE, orcnum=orcnum)
        return [
            ItemArvoreWbc(
                orcitm=int(d["orcitm"] or 0),
                grupo=int(d["grupo"] or 0),
                subgrupo=int(d["subgrupo"] or 0),
                produto=_texto(d["produto"]).strip(),
                nivel=int(d["nivel"] or 0),
                cor=_texto(d["cor"]).strip(),
                descricao=_texto(d["descricao"]).strip(),
                quantidade=_decimal(d["quantidade"]),
                total=_decimal(d["total"]),
                peso=_decimal(d["peso"]),
                id_integracao=int(d["id_integracao"] or 0),
            )
            for d in (linha._mapping for linha in linhas)
        ]

    def pesos_por_item(self, orcnum: str) -> dict[int, Decimal]:
        """Weight of each quote item, for the order's `Weight1`.

        Tree level 1 only; see `queries.PESOS_NIVEL_1_POR_ITEM` for why (summing the levels counts the same mass two or
        three times).

        An item without a tree simply **does not appear** in the dict. That is the difference between "weight unknown"
        and "weight is zero", and whoever builds the line needs it: with no known weight the field is not sent, and SAP
        keeps the one from the item master.
        """
        linhas = self._executar(queries.PESOS_NIVEL_1_POR_ITEM, orcnum=orcnum)
        pesos: dict[int, Decimal] = {}
        for linha in linhas:
            dados = linha._mapping
            peso = _decimal(dados["peso"])
            if peso > 0:
                pesos[int(dados["orcitm"] or 0)] = peso
        return pesos

    def situacao_atual(self, orcnum: str) -> tuple[int, str] | None:
        linhas = self._executar(queries.SITUACAO_POR_NUMERO, orcnum=orcnum)
        if not linhas:
            return None
        linha = linhas[0]._mapping
        return int(linha["sitcode"] or 0), _revisao(linha["revisao"])

    def situacoes_atuais(self, orcnums: Sequence[str]) -> dict[str, tuple[int, str]]:
        """`{orcnum: (sitcode, revisao)}` for several quotes at once.

        Quotes go in batches of `TAMANHO_DO_LOTE` because SQL Server refuses commands with thousands of parameters, and
        a huge `IN` degrades the execution plan. A thousand at a time covers the current window in two round trips.

        Absent from the result means "does not exist in the WBC": the caller treats that as a quote error, not as
        situation zero. Confusing the two would make a nonexistent quote look "below the minimum" and silently vanish.
        """
        situacoes: dict[str, tuple[int, str]] = {}
        for lote in batched(orcnums, TAMANHO_DO_LOTE):
            linhas = self._executar(
                queries.SITUACOES_EM_LOTE, expandir=("orcnums",), orcnums=list(lote)
            )
            for linha in linhas:
                dados = linha._mapping
                situacoes[_texto(dados["orcnum"])] = (
                    int(dados["sitcode"] or 0),
                    _revisao(dados["revisao"]),
                )
        return situacoes

    def orcamentos_alterados_desde(self, desde: datetime, *, sitcode_minimo: int = 10) -> list[str]:
        linhas = self._executar(
            queries.ORCAMENTOS_ALTERADOS_DESDE,
            desde=desde,
            sitcode_minimo=sitcode_minimo,
        )
        return [_texto(linha._mapping["orcnum"]) for linha in linhas]

    # ------------------------------------------------------------- montagem

    @staticmethod
    def _montar_orcamento(linhas: Iterable[Row[Any]]) -> OrcamentoWbc:
        """Rebuild header + items from the flattened result of the join.

        The `LEFT JOIN` makes a quote without items come as a single row with null item fields, hence rows whose
        `id_integracao` and `orcitm` are both zero are discarded.
        """
        linhas = list(linhas)
        cabecalho = linhas[0]._mapping

        itens: list[ItemOrcamentoWbc] = []
        ja_vistos: set[tuple[int, int]] = set()

        for linha in linhas:
            dados = linha._mapping
            sem_item = not dados["id_integracao"] and not dados["orcitm"]
            if sem_item:
                continue

            # Deduplicação obrigatória: o orçamento se junta a ORCIMP e ORCCAB
            # por ORCNUM, e nada garante uma única linha nessas tabelas. Havendo
            # duas, o produto cartesiano do join repete **cada item**, dobrando
            # silenciosamente a quantidade que alimenta a cotação e o pedido no
            # SAP. Era exatamente isso que o GROUP BY do legado escondia.
            chave = (int(dados["id_integracao"] or 0), int(dados["orcitm"] or 0))
            if chave in ja_vistos:
                continue
            ja_vistos.add(chave)

            itens.append(
                ItemOrcamentoWbc(
                    orcitm=int(dados["orcitm"] or 0),
                    grupo=int(dados["grupo"] or 0),
                    subgrupo=int(dados["subgrupo"] or 0),
                    produto=_texto(dados["produto"]),
                    quantidade=_decimal_opcional(dados["quantidade"]),
                    texto=_texto(dados["texto"]),
                    valor=_decimal(dados["valor"]),
                    ipi=_decimal(dados["ipi"]),
                    icms=_decimal(dados["icms"]),
                    id_integracao=int(dados["id_integracao"] or 0),
                )
            )

        return OrcamentoWbc(
            orcnum=_texto(cabecalho["orcnum"]),
            revisao=_revisao(cabecalho["revisao"]),
            sitcode=int(cabecalho["sitcode"] or 0),
            cliente_nome=_texto(cabecalho["cliente_nome"]),
            representante=_texto(cabecalho["representante"]),
            municipio=_texto(cabecalho["municipio"]),
            uf=_texto(cabecalho["uf"]),
            data_orcamento=cabecalho["data_orcamento"],
            data_alteracao=cabecalho["data_alteracao"],
            percentual_comissao=_decimal(cabecalho["percentual_comissao"]),
            valor_comissao=_decimal(cabecalho["valor_comissao"]),
            base2=_decimal(cabecalho["base2"]),
            data_ultima_alteracao_impressao=cabecalho["data_ultima_alteracao_impressao"],
            retorno=_decimal(cabecalho["retorno"]),
            negociacao=_decimal(cabecalho["negociacao"]),
            indice_vendas=_decimal(cabecalho["indice_vendas"]),
            impressao=RepositorioOrcamentosWbcSql._montar_impressao(cabecalho),
            itens=tuple(itens),
        )

    @staticmethod
    def _montar_impressao(cabecalho: Any) -> DadosImpressaoWbc:
        """Gather the print fields that feed the OrcDetalhe snapshot."""
        return DadosImpressaoWbc(
            valor_venda=_decimal(cabecalho["imp_valor_venda"]),
            valor_lista=_decimal(cabecalho["imp_valor_lista"]),
            valor_investimento=_decimal(cabecalho["imp_valor_investimento"]),
            valor_lucro=_decimal(cabecalho["imp_valor_lucro"]),
            valor_expedicao=_decimal(cabecalho["imp_valor_expedicao"]),
            valor_comissao=_decimal(cabecalho["imp_valor_comissao"]),
            percentual_comissao=_decimal(cabecalho["imp_percentual_comissao"]),
            valor_transporte=_decimal(cabecalho["imp_valor_transporte"]),
            valor_embalagem=_decimal(cabecalho["imp_valor_embalagem"]),
            valor_montagem=_decimal(cabecalho["imp_valor_montagem"]),
            base1=_decimal(cabecalho["imp_base1"]),
            base2=_decimal(cabecalho["imp_base2"]),
            base3=_decimal(cabecalho["imp_base3"]),
            cliente_codigo=int(cabecalho["imp_cliente_codigo"] or 0),
            contato_codigo=int(cabecalho["imp_contato_codigo"] or 0),
            contato=_texto(cabecalho["imp_contato"]).strip(),
            pagamento_codigo=_texto_wbc(cabecalho["imp_pagamento_codigo"]),
            pagamento_texto=_texto_wbc(cabecalho["imp_pagamento_texto"]),
            montagem_tipo=_texto_wbc(cabecalho["imp_montagem_tipo"]),
            prazo_entrega=int(cabecalho["imp_prazo_entrega"] or 0),
            revisao=_revisao(cabecalho["imp_revisao"]),
            email=_texto(cabecalho["imp_email"]).strip(),
            fone=_texto(cabecalho["imp_fone"]).strip(),
            cidade=_texto(cabecalho["imp_cidade"]).strip(),
            uf=_texto(cabecalho["imp_uf"]).strip(),
            tipo_venda=_texto(cabecalho["imp_tipo_venda"]).strip(),
            transporte=_texto_wbc(cabecalho["imp_transporte"]),
            acabamento=_texto_wbc(cabecalho["imp_acabamento"]),
            montagem=_texto_wbc(cabecalho["imp_montagem"]),
            tabela_preco=_texto(cabecalho["imp_tabela_preco"]).strip(),
        )
