"""Configuração centralizada da aplicação.

Substitui o `app.config` do addon C# (que guardava usuário/senha em texto puro —
ver "Débitos técnicos", item 1, em docs/controleproducao/migration_guide.md). Todas as
credenciais vêm de variáveis de ambiente (arquivo `.env`, nunca commitado).

Since 2026-09-28 this package lives beside `wbcpython/` in the ServidorIntegracaoSAP and
shares its single `.env`: every credential reads the WBC name first and falls back to the
SIS name (`OP_SL_*`, `SQL_*`/`SQLSERVER_*`, `SAP_*`), exactly like `wbcpython/config.py`.
"""
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from wbcpython import safety

# Absolute path so `python -m controleproducao` and the service work from any cwd. The package
# sits at `<SIS>/controleproducao/`, so `parent.parent` is the SIS root: this is the same
# `.env` the API, the scheduler and `wbcpython` read — on purpose, one env file per machine.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    # "utf-8-sig" (24/09/2026, implantação em Windows): lê o .env com ou sem BOM. O Bloco de
    # Notas do Windows grava UTF-8 COM BOM; com "utf-8" puro, o BOM grudava na primeira linha.
    #
    # populate_by_name: fields with `validation_alias` stay constructible by attribute name
    # (`Settings(sl_username=...)`). env_ignore_empty: an empty `SL_USERNAME=` line in the
    # shared .env must fall through to the SIS name (`OP_SL_USERNAME`), not win as "".
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8-sig",
        extra="ignore",
        populate_by_name=True,
        env_ignore_empty=True,
    )

    # -- Ambiente lógico (homolog/prod) ---------------------------------------
    # `wbc_block_production_writes` foi removido em 22/09/2026 junto com a trava de
    # escrita (ver `core/guardas.py`). O `.env` pode continuar trazendo a variável: o
    # `extra="ignore"` acima a descarta em silêncio — mas ela não faz mais nada, e é
    # melhor apagá-la do `.env` do que deixar alguém achando que ainda protege.
    # WBC_ENVIRONMENT is the worker's (wbcpython/config.py); this package never read it.
    wbc_production_company_db: str = "SBOALTAMIRAPROD"

    # -- SAP Business One — Service Layer --------------------------------------
    sl_base_url: str = "https://localhost:50000/b1s/v1"
    sl_company_db: str = "SBOALTAMIRAHOMOLOG"
    sl_username: str = Field(default="", validation_alias=AliasChoices("SL_USERNAME", "OP_SL_USERNAME"))
    sl_password: str = Field(default="", validation_alias=AliasChoices("SL_PASSWORD", "OP_SL_PASSWORD"))
    sl_verify_ssl: bool = True
    sl_ca_bundle: str | None = None
    sl_timeout_seconds: int = 60

    # -- WBC — SQL Server (banco WBCCAD) --------------------------------------
    wbc_sql_host: str = Field(default="", validation_alias=AliasChoices("WBC_SQL_HOST", "SQL_HOST", "SQLSERVER_HOST"))
    wbc_sql_port: int = Field(default=1433, validation_alias=AliasChoices("WBC_SQL_PORT", "SQL_PORT", "SQLSERVER_PORT"))
    wbc_sql_database: str = Field(
        default="WBCCAD", validation_alias=AliasChoices("WBC_SQL_DATABASE", "SQL_DATABASE", "SQLSERVER_DATABASE")
    )
    wbc_sql_username: str = Field(
        default="", validation_alias=AliasChoices("WBC_SQL_USERNAME", "SQL_USER", "SQLSERVER_USER")
    )
    wbc_sql_password: str = Field(
        default="", validation_alias=AliasChoices("WBC_SQL_PASSWORD", "SQL_PASSWORD", "SQLSERVER_PASSWORD")
    )
    # NO EFFECT since 29/09/2026 (F7): core/sqlserver_client.py talks to the WBC through
    # pymssql, like the worker, and needs no ODBC driver. Kept (with the TrustServerCertificate
    # flag below) only so an existing WBC_SQL_DRIVER line in a .env keeps parsing.
    wbc_sql_driver: str = "ODBC Driver 18 for SQL Server"
    # O ODBC Driver 18 passou a exigir TLS + validação de certificado por padrão
    # (diferente do Driver 17 e do SqlClient .NET usado no addon legado). O servidor
    # WBC usa certificado autoassinado — sem isso, a conexão falha com
    # "certificate verify failed: self-signed certificate". Adicionado em 15/09/2026
    # ao testar contra a homologação real (não existia essa preocupação no C# original).
    wbc_sql_trust_server_certificate: bool = True

    # Filial (BPLId) usada nos lançamentos de estoque do módulo 3 quando não for possível
    # derivá-la do depósito nem do pedido de origem. 0 = não configurada. Último recurso:
    # o normal é derivar do dado (ver `manutencao_op._filial_do_movimento`), porque um
    # número fixo aqui quebra em silêncio no dia em que uma segunda filial for criada.
    sl_business_place_id: int = 0

    # -- SAP HANA — acesso direto via hdbcli (views) ---------------------------
    hana_host: str = Field(default="", validation_alias=AliasChoices("HANA_HOST", "SAP_HOST"))
    hana_port: int = Field(default=30015, validation_alias=AliasChoices("HANA_PORT", "SAP_PORT"))
    hana_username: str = Field(default="", validation_alias=AliasChoices("HANA_USERNAME", "SAP_USER"))
    hana_password: str = Field(default="", validation_alias=AliasChoices("HANA_PASSWORD", "SAP_PASSWORD"))
    # Schema onde o ADDON LEGADO roda (produção). Usado só pela auditoria
    # `pedidos-wbc comparar-ops`, para confrontar as OPs que o legado gerou lá com as que
    # este porte gera no schema de `hana_schema` (homologação). Nunca é escrito.
    hana_schema_legado: str = "SBOALTAMIRAPROD"

    # -- Serviço web do Controle de Produção (uvicorn) -------------------------
    cp_host: str = "127.0.0.1"
    cp_porta: int = 8080
    cp_log_file: str = "logs/controleproducao.log"
    # Progress of the CLI's write commands (cli._grava_tambem_em_arquivo), relative to the cwd.
    cp_cli_log_file: str = "logs/controleproducao_cli.log"
    # Shared with the API 8077 and the WBC panel (see the SIS CLAUDE.md, "OS_API_KEY").
    os_api_key: SecretStr = Field(default=SecretStr(""), validation_alias=AliasChoices("OS_API_KEY"))
    # Only for the link back to the WBC panel (`python -m wbcpython dashboard`); the panel
    # itself reads PAINEL_PORTA from `wbcpython/config.py`.
    painel_porta: int = 8079
    wbc_painel_url: str = ""
    # The way back to the OrçaView home (the "OrçaView" link in the top bar): this screen is
    # opened in a new tab from the OrçaView card "Integração de Pedidos WBC" (web V118.404).
    # The .90 address is the default; ORCAVIEW_URL only for a dev copy.
    orcaview_url: str = "http://192.168.0.90:8000/"

    # -- Supabase: history of the Execuções screen (core/historico.py) -----------------
    # The SIS names, read as they are (the API and the ETLs use the same two lines). Only the
    # service role key works: the table has RLS on and no policy. Used on the .11 only.
    supabase_url: str = ""
    supabase_service_role_key: SecretStr = SecretStr("")

    log_level: str = "INFO"

    @property
    def hana_schema(self) -> str:
        """Schema of the direct HANA reads (`SET SCHEMA` in `core/hana_reader.py`).

        Derived, not configured: in the single `.env`, `HANA_SCHEMA` is the READ schema of the
        WBC worker and may differ from the company the Service Layer writes to. This package
        reads ORDR/OWOR to decide what to write, so the reads must hit the same company as
        the writes — reading one schema and writing another was the bug of 21/09/2026.
        """
        return self.sl_company_db

    @property
    def is_production(self) -> bool:
        """True se a company DB configurada para a Service Layer for a de produção.

        É o que decide a faixa vermelha no topo de toda página e o aviso em log a cada
        escrita (`controleproducao.core.guardas.aviso_de_escrita`). Entre 21 e 22/09/2026 foi também a
        base da trava de escrita, removida a pedido do Anderson — hoje o efeito é
        informar, não impedir.
        """
        # Same comparison as the hard gate (strip + casefold): with an exact compare, a
        # differently-cased name showed "homologação" while the SL client refused as production.
        return safety.is_production(self.sl_company_db, self.wbc_production_company_db)


@lru_cache
def get_settings() -> Settings:
    return Settings()
