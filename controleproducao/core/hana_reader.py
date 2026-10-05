"""Acesso direto (somente leitura) ao HANA via `hdbcli`.

**Caminho preferencial de LEITURA** do projeto, conforme a decisão revista em 16/09/2026
(seção 6.4 do migration_guide.md): as leituras vêm por aqui por padrão — tanto as queries
pesadas herdadas do `Querys.resx` quanto as simples. A Service Layer entra na leitura só
quando este caminho não resolve, tipicamente quando é preciso o objeto de negócio montado
pelo B1 em vez de linhas de tabela (ex.: ler um `Order` com suas `DocumentLines`).

⚠️ A decisão ORIGINAL (14/09/2026) era o inverso — Service Layer primeiro, `hdbcli` só como
ponte temporária a ser substituída por Calculation Views. Se encontrar em algum lugar do
código um comentário mandando "substituir por uma View quando possível", ele é resquício
daquela regra e está obsoleto.

Nunca usar este módulo para ESCRITA — toda escrita no SAP continua passando pela Service
Layer (auditoria, triggers de campo calculado e validações de negócio do próprio B1
dependem disso). Isso NÃO mudou na revisão da decisão.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from controleproducao.config import Settings
from controleproducao.core.exceptions import WbcDatabaseError
from controleproducao.core.perf import PERFIL, forma_sql
from controleproducao.core.sql_ligado import exige_leitura, nome_de_schema

if TYPE_CHECKING:
    from hdbcli import dbapi


logger = logging.getLogger(__name__)


class HanaDirectReader:
    """Leitor do HANA que **reaproveita a conexão** entre consultas.

    ⚠️ Otimização de 16/09/2026. Antes, cada consulta abria uma conexão nova (autenticação
    completa + `SET SCHEMA`) e fechava em seguida. O perfil da execução do orçamento 00125192
    mostrou **62 conexões somando 3,7s (20% do tempo total, ~60 ms cada)** para 0,9s de
    queries de fato — ou seja, gastava-se 4x mais abrindo conexão do que consultando. Com o
    reúso, essas 62 aberturas viram 1.

    A conexão é aberta na primeira consulta (preguiçosa: instanciar o leitor não conecta) e
    fica viva até `close()` ou o fim do processo. Se ela morrer no meio (timeout do servidor,
    queda de rede), a consulta seguinte **reconecta e tenta uma vez** de forma transparente —
    sem isso, o reúso trocaria lentidão por fragilidade em execuções longas, que é justamente
    o caso de uso aqui.

    Continua sendo somente leitura: não há `commit`/`rollback` e nenhum método de escrita.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._conexao: dbapi.Connection | None = None

    # ------------------------------------------------------------------
    # Conexão
    # ------------------------------------------------------------------
    def _abre_conexao(self) -> dbapi.Connection:
        # Import tardio — ver comentário equivalente em core/sqlserver_client.py.
        from hdbcli import dbapi

        s = self._settings
        try:
            with PERFIL.medir("HANA · abrir conexão", f"{s.hana_host}:{s.hana_port}"):
                conn = dbapi.connect(
                    address=s.hana_host, port=s.hana_port, user=s.hana_username, password=s.hana_password
                )
                if s.hana_schema:
                    cursor = conn.cursor()
                    # The one name pasted into SQL text: validated, since it comes from .env.
                    cursor.execute(f'SET SCHEMA "{nome_de_schema(s.hana_schema)}"')
                    cursor.close()
            return conn
        except dbapi.Error as exc:  # pragma: no cover - depende de infra real
            raise WbcDatabaseError(f"Falha ao conectar diretamente no HANA: {exc}") from exc

    def _conexao_ativa(self) -> dbapi.Connection:
        if self._conexao is None:
            self._conexao = self._abre_conexao()
        return self._conexao

    def _descarta_conexao(self) -> None:
        """Fecha e esquece a conexão atual, para a próxima consulta reabrir."""
        if self._conexao is not None:
            try:
                self._conexao.close()
            except Exception:  # noqa: BLE001 - já estamos descartando; erro aqui é irrelevante
                pass
            self._conexao = None

    def _ainda_conectado(self) -> bool:
        """hdbcli's `isconnected()`; unknown (no connection / no method / it raises) = False,
        which keeps the old reconnect-and-retry-once path."""
        try:
            return bool(self._conexao is not None and self._conexao.isconnected())
        except Exception:  # noqa: BLE001 - a broken connection may raise here too
            return False

    def close(self) -> None:
        """Encerra a conexão reaproveitada. A CLI chama no fim de cada comando; um servidor
        web precisa chamar ao descartar o leitor, senão a conexão fica aberta à toa."""
        self._descarta_conexao()

    def __enter__(self) -> HanaDirectReader:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def _consulta(self, sql: str, params: tuple[Any, ...], como_dict: bool):
        from hdbcli import dbapi

        # hdbcli autocommits by default: the guard, not the driver, keeps this read-only.
        exige_leitura(sql)

        def _executa():
            cursor = self._conexao_ativa().cursor()
            try:
                with PERFIL.medir("HANA · executar query", forma_sql(sql)):
                    cursor.execute(sql, params)
                    linhas = cursor.fetchall()
                if not como_dict:
                    return [list(linha) for linha in linhas]
                colunas = [col[0] for col in cursor.description] if cursor.description else []
                return [dict(zip(colunas, linha)) for linha in linhas]
            finally:
                cursor.close()

        try:
            return _executa()
        except dbapi.Error as exc:
            # Reconnect only when the reused connection is actually gone. A SQL/conversion
            # error on a live connection fails the same way twice: retrying it threw away a
            # good connection, paid a new login and logged a misleading "Reconectando".
            if self._ainda_conectado():
                raise WbcDatabaseError(f"Falha ao consultar o HANA: {exc}") from exc
            # A conexão reaproveitada pode ter expirado ou caído. Descarta, reconecta e
            # tenta UMA vez; se falhar de novo, o erro é de verdade e sobe.
            logger.warning("Consulta ao HANA falhou (%s). Reconectando e tentando de novo...", exc)
            self._descarta_conexao()
            try:
                return _executa()
            except dbapi.Error as exc2:  # pragma: no cover - depende de infra real
                raise WbcDatabaseError(f"Falha ao consultar o HANA: {exc2}") from exc2

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return self._consulta(sql, params, como_dict=True)

    def fetch_all_values(self, sql: str, params: tuple[Any, ...] = ()) -> list[list[Any]]:
        """Como `fetch_all`, mas devolve cada linha como lista posicional em vez de
        `dict` — ver docstring do equivalente em `sqlserver_client.py` (mesmo problema:
        queries com colunas repetidas/sem alias, ex. `max(...)`/`max(...)`/`max(...)` em
        `BUSCA_MAX_ITEM_LINHA`, perdem valores no `dict(zip(colunas, valores))`)."""
        return self._consulta(sql, params, como_dict=False)
