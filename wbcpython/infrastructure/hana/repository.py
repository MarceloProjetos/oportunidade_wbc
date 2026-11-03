"""Repository of the HANA views: direct `hdbcli` access, read-only.

Third data-access pattern of the solution, next to the Service Layer (transactional objects)
and the WBC SQL Server. The views `VW_CLIENTE_MUNICIPIO_ALTA` and `VW_EVOL_OPORTUNIDADE_ALT`
exist **only** in HANA, and only in the production schema (`docs/wbc/DECISOES.md`, "Ler o HANA
do schema de produção").

Read-only for two reasons: they are views (nothing to write) and the schema read is
production's. `assert_read_only_sql` refuses any write SQL here with no off switch;
production writes elsewhere are unlocked only on the .11, by machine IP.

`hdbcli` is in `requirements.txt` but the import is deferred into the connection opener, so
the rest of the project (tests included) works on machines without the native driver.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, Self

from wbcpython.config import HanaSettings
from wbcpython.domain.numeros import normalizar_decimal_tolerante
from wbcpython.infrastructure.hana.identificadores import citar_identificador
from wbcpython.infrastructure.hana.models import EvolucaoOportunidade, MunicipioCliente
from wbcpython.safety import assert_read_only_sql, assert_sql_allowed

logger = logging.getLogger(__name__)

FONTE = "views do SAP HANA"

VIEW_EVOLUCAO = "VW_EVOL_OPORTUNIDADE_ALT"
VIEW_MUNICIPIO = "VW_CLIENTE_MUNICIPIO_ALTA"


class ConexaoHanaIndisponivel(RuntimeError):
    """The `hdbcli` driver is not installed."""


class RepositorioViewsHana(Protocol):
    """Read contract of the HANA views. No write methods."""

    def evolucao_do_orcamento(self, n_wbc: str) -> EvolucaoOportunidade | None: ...

    def municipio(self, nome: str, uf: str = "") -> MunicipioCliente | None: ...

    def views_existem(self) -> dict[str, bool]:
        """Which of the expected views exist in the configured schema."""
        ...


def _decimal_ou_none(valor: Any) -> Decimal | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, Decimal):
        return valor
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        logger.warning("Valor numérico inesperado vindo do HANA: %r — tratado como ausente.", valor)
        return None


def _texto(valor: Any) -> str:
    return "" if valor is None else str(valor)


def _inteiro(valor: Any) -> int | None:
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _decimal_textual(valor: Any, campo: str) -> Decimal | None:
    """Normalize NVARCHAR fields that hold numbers.

    `Retorno`, `Indice` and `Negociacao` are text in the view, with a decimal separator that
    varies between records (same finding as the OrcDetalhe UDFs); parsing goes through
    `normalizar_decimal_tolerante`. Blank or null gives `None`.
    """
    if valor is None or str(valor).strip() == "":
        return None
    return normalizar_decimal_tolerante(valor, campo=campo)


def _abrir_conexao_hdbcli(settings: HanaSettings) -> Any:
    """Open the real HANA connection. The `hdbcli` import is deferred (see module docstring).

    Raises:
        ConexaoHanaIndisponivel: `hdbcli` is not installed.
    """
    try:
        from hdbcli import dbapi
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise ConexaoHanaIndisponivel(
            "O driver 'hdbcli' não está instalado. Instale com: pip install -r requirements.txt"
        ) from exc

    return dbapi.connect(
        address=settings.host,
        port=settings.port,
        user=settings.username,
        password=settings.password.get_secret_value(),
    )


class RepositorioViewsHanaSql:
    """`hdbcli` implementation. The connection factory is injectable so tests run on a fake
    connection, without the native driver or network.
    """

    def __init__(
        self,
        settings: HanaSettings,
        *,
        production_company_db: str,
        block_production_writes: bool = True,
        fabrica_de_conexao: Any = None,
    ) -> None:
        self._settings = settings
        self._production_company_db = production_company_db
        self._block_production_writes = block_production_writes
        self._fabrica = fabrica_de_conexao or (lambda: _abrir_conexao_hdbcli(settings))
        self._conexao: Any = None

        # Valida o schema já na construção: um valor inválido no .env deve
        # falhar aqui, com mensagem clara, e não no meio de uma consulta.
        self._schema_citado = citar_identificador(settings.schema_name, descricao="HANA_SCHEMA")

    @property
    def schema(self) -> str:
        return self._settings.schema_name

    # -------------------------------------------------------------- conexão

    def _obter_conexao(self) -> Any:
        if self._conexao is None:
            self._conexao = self._fabrica()
        return self._conexao

    def close(self) -> None:
        if self._conexao is not None:
            try:
                self._conexao.close()
            except Exception as exc:  # noqa: BLE001 - encerramento não pode explodir
                logger.warning("Falha ao encerrar a conexão com o HANA: %s", exc)
            finally:
                self._conexao = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------- execução

    def _executar(self, sql: str, parametros: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        """Single execution point: applies both guards before querying.

        `assert_read_only_sql` refuses any write SQL (this HANA access is read-only);
        `assert_sql_allowed` additionally applies the production-write guard (a no-op for
        reads, and redundant after the first guard).
        """
        assert_read_only_sql(sql, fonte=FONTE)
        assert_sql_allowed(
            sql,
            self._settings.schema_name,
            production_company_db=self._production_company_db,
            block_production_writes=self._block_production_writes,
        )

        cursor = self._obter_conexao().cursor()
        try:
            cursor.execute(sql, tuple(parametros))
            return list(cursor.fetchall())
        finally:
            cursor.close()

    # ------------------------------------------------------------ consultas

    #: Colunas lidas de VW_EVOL_OPORTUNIDADE_ALT, na ordem em que são mapeadas.
    COLUNAS_EVOLUCAO = (
        "N_WBC",
        "StatusWBC",
        "NumOport",
        "NumDoc",
        "TipoDoc",
        "Cotacao",
        "CodPN",
        "NomePN",
        "Representante",
        "Municipio",
        "UF",
        "Valor",
        "DataOport",
        "DataCotacao",
        "PctComissao",
        "Retorno",
        "Indice",
        "Negociacao",
    )

    def evolucao_do_orcamento(self, n_wbc: str) -> EvolucaoOportunidade | None:
        """Quote status as mirrored in HANA, or `None` when the quote is not in the view.

        The view may return several rows per quote; the first row returned is used (the query
        has no `ORDER BY`, so this relies on the view's own ordering).
        """
        colunas = ", ".join(f'"{c}"' for c in self.COLUNAS_EVOLUCAO)
        sql = (
            f"SELECT {colunas} "
            f"FROM {self._schema_citado}.{citar_identificador(VIEW_EVOLUCAO)} "
            f'WHERE "N_WBC" = ?'
        )
        linhas = self._executar(sql, (n_wbc,))
        if not linhas:
            return None

        v = linhas[0]
        return EvolucaoOportunidade(
            n_wbc=_texto(v[0]),
            status_wbc=_texto(v[1]),
            num_oportunidade=_inteiro(v[2]),
            num_documento=_inteiro(v[3]),
            tipo_documento=_texto(v[4]),
            cotacao=_inteiro(v[5]),
            codigo_pn=_texto(v[6]),
            nome_pn=_texto(v[7]),
            representante=_texto(v[8]),
            municipio=_texto(v[9]),
            uf=_texto(v[10]),
            valor=_decimal_ou_none(v[11]),
            data_oportunidade=v[12],
            data_cotacao=v[13],
            pct_comissao=_decimal_ou_none(v[14]),
            # Estes três são NVARCHAR na view e sofrem do separador decimal
            # inconsistente — por isso passam pela normalização, não por uma
            # conversão direta.
            retorno=_decimal_textual(v[15], "Retorno"),
            indice=_decimal_textual(v[16], "Indice"),
            negociacao=_decimal_textual(v[17], "Negociacao"),
        )

    def municipio(self, nome: str, uf: str = "") -> MunicipioCliente | None:
        """Resolve the municipality `AbsId` (first match), optionally narrowed by ``uf``.

        Matches on `Name_N` (unaccented, upper case), not `Name`: the municipality coming from
        the WBC is unaccented, so comparing with the accented name would miss every accented
        city, which is most of them.
        """
        sql = (
            f'SELECT "AbsId", "Name", "Name_N", "State", "Code", "IbgeCode", "Country" '
            f"FROM {self._schema_citado}.{citar_identificador(VIEW_MUNICIPIO)} "
            f'WHERE UPPER("Name_N") = UPPER(?)'
        )
        parametros: list[Any] = [nome]
        if uf:
            sql += ' AND UPPER("State") = UPPER(?)'
            parametros.append(uf)

        linhas = self._executar(sql, parametros)
        if not linhas:
            return None

        v = linhas[0]
        return MunicipioCliente(
            abs_id=int(v[0]),
            municipio=_texto(v[1]),
            municipio_normalizado=_texto(v[2]),
            uf=_texto(v[3]),
            codigo=_texto(v[4]),
            codigo_ibge=_texto(v[5]),
            pais=_texto(v[6]),
        )

    # ------------------------------------------------------------ diagnóstico

    def views_existem(self) -> dict[str, bool]:
        """Which expected views exist in the configured schema (`HANA_SCHEMA`).

        Diagnostic: queries the catalog (`SYS.VIEWS`) without touching the views. A catalog
        failure is logged and reported as all-missing instead of raising.
        """
        sql = 'SELECT "VIEW_NAME" FROM "SYS"."VIEWS" WHERE "SCHEMA_NAME" = ?'
        try:
            linhas = self._executar(sql, (self._settings.schema_name,))
        except Exception as exc:  # noqa: BLE001 - diagnóstico não pode derrubar
            logger.warning("Não foi possível consultar o catálogo do HANA: %s", exc)
            return {VIEW_EVOLUCAO: False, VIEW_MUNICIPIO: False}

        encontradas = {_texto(linha[0]).upper() for linha in linhas}
        return {
            VIEW_EVOLUCAO: VIEW_EVOLUCAO in encontradas,
            VIEW_MUNICIPIO: VIEW_MUNICIPIO in encontradas,
        }
