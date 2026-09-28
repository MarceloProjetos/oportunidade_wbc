"""Acesso ao SQL Server externo do sistema WBC (banco `WBCCAD`).

Equivalente direto de `Controllers/SQLConnection.cs` no addon original: mesma base de
dados, mesmas tabelas `INTEGRACAO_*`, mesmo padrão de "monta string SQL e executa".

Diferença deliberada em relação ao legado (ver "Débitos técnicos", item 2, no
migration_guide.md): aqui usamos **parâmetros bindados** (`?` do pyodbc) em vez de
concatenar valores vindos de tela diretamente na string SQL, para eliminar o risco de
SQL injection que existia no C# original — sem mudar o texto/lógica das queries em si.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from controleproducao.config import Settings
from controleproducao.core.exceptions import WbcDatabaseError
from controleproducao.core.perf import PERFIL, forma_sql

if TYPE_CHECKING:
    import pyodbc

logger = logging.getLogger(__name__)


class WbcSqlServerClient:
    """Espelha os métodos de `SQLConnection.cs`:

    - `ExecuteQuery`      -> `execute_non_query` / `fetch_all` conforme o caso
    - `ExecuteSelect`     -> `fetch_scalar_list`
    - `ExecuteSelectMultip` / `ExecuteSelectMultipString` -> `fetch_row`
    - `ExecuteSelectNew`, `ExecuteSelect2`, `selectORCCAB`, `PegaEstruturaPrd`,
      `PegaOrcamentosWBC` -> ficam nos módulos de negócio (`pedidos_wbc/queries.py`
      e `service.py`), pois cada um monta um objeto de domínio específico
      (`NewOrcPrd`, `ORCPRD`, `ORCCAB`, `EstruturaPrd`, `OportunidadeDoc`)
      — este cliente só executa e devolve linhas cruas (`list[dict]`).
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    def _connection_string(self) -> str:
        s = self._settings
        trust_cert = "yes" if s.wbc_sql_trust_server_certificate else "no"
        return (
            f"DRIVER={{{s.wbc_sql_driver}}};"
            f"SERVER={s.wbc_sql_host},{s.wbc_sql_port};"
            f"DATABASE={s.wbc_sql_database};"
            f"UID={s.wbc_sql_username};"
            f"PWD={s.wbc_sql_password};"
            # Necessário com o ODBC Driver 18 contra o servidor WBC (certificado
            # autoassinado) — ver comentário em controleproducao/config.py, campo
            # wbc_sql_trust_server_certificate. Driver 17 e o SqlClient .NET do addon
            # legado não exigiam isso.
            f"TrustServerCertificate={trust_cert};"
        )

    @contextmanager
    def _connect(self) -> Iterator[pyodbc.Connection]:
        # Import tardio (não em nível de módulo): pyodbc exige a lib de sistema
        # `unixodbc` instalada, que nem sempre está presente no ambiente de
        # desenvolvimento/CI. Assim o resto da aplicação sobe normalmente mesmo sem
        # ela, e o erro só aparece quando este cliente é de fato usado.
        import pyodbc

        try:
            # ⚠️ Conexão NOVA por chamada — e com o Driver 18 isso inclui handshake TLS.
            # Medido à parte da query: é o principal suspeito da lentidão relatada em
            # 16/09/2026 (ver controleproducao/core/perf.py).
            with PERFIL.medir("WBC (SQL Server) · abrir conexão", self._settings.wbc_sql_host):
                conn = pyodbc.connect(self._connection_string())
        except pyodbc.Error as exc:  # pragma: no cover - depende de infra real
            raise WbcDatabaseError(f"Falha ao conectar no SQL Server WBC: {exc}") from exc
        try:
            yield conn
        finally:
            conn.close()

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Equivalente a rodar `ExecuteQuery`/`ExecuteReader` e iterar todas as linhas."""
        with self._connect() as conn:
            cursor = conn.cursor()
            with PERFIL.medir("WBC (SQL Server) · executar query", forma_sql(sql)):
                cursor.execute(sql, params)
                linhas = cursor.fetchall()
            columns = [col[0] for col in cursor.description] if cursor.description else []
            return [dict(zip(columns, row)) for row in linhas]

    def fetch_all_values(self, sql: str, params: tuple[Any, ...] = ()) -> list[list[Any]]:
        """Como `fetch_all`, mas devolve cada linha como lista posicional (`pyodbc.Row`
        convertido para `list`) em vez de `dict`.

        ⚠️ Adicionado em 15/09/2026 ao testar `pedidos-wbc processar-novos` contra a
        homologação real: várias queries deste módulo (herdadas do C# original, que lia
        por índice via `SqlDataReader.GetValue(i)`) têm **colunas repetidas sem alias**
        (ex.: `NOVA_TABELA_QUOT`, com 35 `ISNULL(...)` seguidos sem `AS`). O driver ODBC
        devolve nomes de coluna vazios/duplicados para essas expressões, e `fetch_all`
        monta um `dict` via `zip(colunas, valores)` — colunas com nome repetido colidem
        e o `dict` resultante perde valores (`IndexError: list index out of range` ao
        tentar acessar `valores[2]`, etc.). Use este método (em vez de `fetch_all` +
        `list(row.values())`) em qualquer query que já dependia de acesso posicional."""
        with self._connect() as conn:
            cursor = conn.cursor()
            with PERFIL.medir("WBC (SQL Server) · executar query", forma_sql(sql)):
                cursor.execute(sql, params)
                return [list(row) for row in cursor.fetchall()]

    def execute_non_query(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        """Equivalente ao caminho de `INSERT`/`UPDATE` em `SQLConnection.ExecuteQuery`
        ⚠️ Sem uso desde 22/09/2026: o único `INSERT` do porte (`INTEGRACAO_ORCINC`)
        pertencia ao módulo 1, removido. Mantido porque é a única via de escrita no WBC e
        o módulo 4 (Romaneio) ainda não foi portado — se ele não precisar escrever, este
        método pode sair junto.
        """
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            conn.commit()
