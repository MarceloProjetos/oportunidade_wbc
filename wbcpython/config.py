"""Application settings, loaded from environment variables / .env.

Principle (ai_spec Rule 4, index at ``docs/wbc/ai_spec/00_index.md``): no company DB or
HANA schema name may be hardcoded; everything comes from here, so switching environment
is a config change. Production writes are not gated by config: since 2026-09-28 they are
unlocked only by machine identity (``safety.is_production_machine()``).
"""

from __future__ import annotations

from datetime import date, time
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from wbcpython import padroes
from wbcpython.safety import is_production, is_production_machine

# Uma senha por sistema (decisão do Marcelo, 09/09/2026). O `.env` do ServidorIntegracaoSAP
# já descreve o mesmo HANA (`SAP_*`), o mesmo SQL Server do WBC (`SQL_*`/`SQLSERVER_*`) e o
# mesmo usuário do Service Layer (`OP_SL_*`) que este pacote usa. Cada credencial abaixo
# lê primeiro o nome do WBC e, se ele não existir, o do SIS — assim uma rotação de senha é
# uma linha por sistema, e o bloco WBC do `.env` fica só com o que é exclusivo dele.
#
# O que NÃO cai no SIS, de propósito: `SL_COMPANY_DB`, `WBC_ENVIRONMENT` e a trava. A
# company de escrita do WBC não pode "herdar" a de produção de outro módulo em silêncio —
# apontar para produção continua sendo um ato explícito neste bloco.


class ServiceLayerSettings(BaseSettings):
    """SAP Business One Service Layer (REST) connection."""

    model_config = SettingsConfigDict(
        env_prefix="SL_", env_file=".env", extra="ignore", case_sensitive=False,
        populate_by_name=True,
    )

    base_url: str = "https://sapbusinessonehana-vm:50000/b1s/v1"
    company_db: str = "SBOALTAMIRAHOMOLOG"
    username: str = Field(default="", validation_alias=AliasChoices("SL_USERNAME", "OP_SL_USERNAME"))
    password: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("SL_PASSWORD", "OP_SL_PASSWORD")
    )
    verify_ssl: bool = False
    ca_bundle: str | None = None
    timeout_seconds: float = 60.0

    @property
    def verify(self) -> bool | str:
        """Value for the `verify` parameter of httpx (CA bundle path wins over the bool)."""
        if self.ca_bundle:
            return self.ca_bundle
        return self.verify_ssl


class WbcSqlSettings(BaseSettings):
    """WBC SQL Server (WBCCAD database) connection. Read-only by Rule 5."""

    model_config = SettingsConfigDict(
        env_prefix="WBC_SQL_", env_file=".env", extra="ignore", case_sensitive=False,
        populate_by_name=True,
    )

    host: str = Field(default="", validation_alias=AliasChoices("WBC_SQL_HOST", "SQL_HOST", "SQLSERVER_HOST"))
    port: int = Field(default=1433, validation_alias=AliasChoices("WBC_SQL_PORT", "SQL_PORT", "SQLSERVER_PORT"))
    database: str = Field(
        default="WBCCAD", validation_alias=AliasChoices("WBC_SQL_DATABASE", "SQL_DATABASE", "SQLSERVER_DATABASE")
    )
    username: str = Field(default="", validation_alias=AliasChoices("WBC_SQL_USERNAME", "SQL_USER", "SQLSERVER_USER"))
    password: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("WBC_SQL_PASSWORD", "SQL_PASSWORD", "SQLSERVER_PASSWORD"),
    )


class HanaSettings(BaseSettings):
    """Direct HANA SQL connection via hdbcli (read views)."""

    model_config = SettingsConfigDict(
        env_prefix="HANA_", env_file=".env", extra="ignore", case_sensitive=False,
        populate_by_name=True,
    )

    host: str = Field(default="", validation_alias=AliasChoices("HANA_HOST", "SAP_HOST"))
    port: int = Field(default=30015, validation_alias=AliasChoices("HANA_PORT", "SAP_PORT"))
    username: str = Field(default="", validation_alias=AliasChoices("HANA_USERNAME", "SAP_USER"))
    password: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("HANA_PASSWORD", "SAP_PASSWORD")
    )
    schema_name: str = Field(
        default="SBOALTAMIRAHOMOLOG", validation_alias=AliasChoices("HANA_SCHEMA", "SAP_SCHEMA")
    )


class TrackingSettings(BaseSettings):
    """Own tracking DB that feeds the dashboard."""

    model_config = SettingsConfigDict(
        env_prefix="TRACKING_", env_file=".env", extra="ignore", case_sensitive=False
    )

    db_url: SecretStr = SecretStr(padroes.TRACKING_DB_URL)
    """URL do banco de tracking.

    O default é o MESMO de `config.WBC_TRACKING_DB_URL_DEFAULT` (a raiz), que o check
    `wbc_worker` do `/status` lê: com defaults diferentes, um `.env` sem `TRACKING_DB_URL`
    punha o worker gravando num arquivo e o `/status` lendo outro. Teste de paridade:
    `tests/test_config_paridade_wbc.py`.

    É `SecretStr` porque, assim que o tracking sair do SQLite para um
    PostgreSQL/SQL Server, a URL passa a carregar usuário e senha embutidos
    (`postgresql://user:senha@host/db`) — e apareceria em texto puro em
    qualquer `model_dump()` ou `repr()` da configuração.
    """


class Settings(BaseSettings):
    """Root application settings."""

    model_config = SettingsConfigDict(
        env_prefix="WBC_", env_file=".env", extra="ignore", case_sensitive=False
    )

    def __init__(self, **dados: Any) -> None:
        """Propagate ``_env_file=None`` to the nested settings.

        The nested classes are built by ``default_factory`` with no arguments, so without
        this ``Settings(_env_file=None)`` still read the cwd's `.env` for them. It went
        unnoticed while every machine's `.env` pointed at homologation (the leaked value
        equalled the expected one); at the production cut-over three config tests went
        red with no code change, only because the machine now pointed elsewhere.
        """
        if dados.get("_env_file", ...) is None:
            for campo, classe in (
                ("service_layer", ServiceLayerSettings),
                ("wbc_sql", WbcSqlSettings),
                ("hana", HanaSettings),
                ("tracking", TrackingSettings),
            ):
                dados.setdefault(campo, classe(_env_file=None))
        super().__init__(**dados)

    environment: Literal["homolog", "prod"] = "homolog"
    production_company_db: str = "SBOALTAMIRAPROD"

    @property
    def block_production_writes(self) -> bool:
        """Production writes are unlocked only on ``safety.PRODUCTION_MACHINE_IP``.

        Was ``WBC_BLOCK_PRODUCTION_WRITES`` until 2026-09-28; that key is now ignored.
        """
        return not is_production_machine()

    @field_validator("production_company_db")
    @classmethod
    def _producao_nao_pode_ser_vazia(cls, valor: str) -> str:
        """Reject an empty production name: it would silently disable production detection.

        `is_production()` compares by equality, so with an empty name no target is ever
        recognized as production: `targets_production` stays False (the painel's write
        gate opens) and diagnostics report "homologação" even when pointed at production.
        Better to refuse the config at startup than to run with a gate that gates nothing.
        """
        if not valor or not valor.strip():
            raise ValueError(
                "WBC_PRODUCTION_COMPANY_DB não pode ser vazio: é ele que a trava de "
                "segurança usa para reconhecer a company DB de produção."
            )
        return valor.strip()

    #: Janela de busca de oportunidades, em meses. O corte é o primeiro dia do
    #: mês, `meses` meses atrás, comparado com `OOPR.OpenDate`.
    #:
    #: É configurável porque o valor **não é conhecido**. O legado montava esta
    #: data misturando -6 meses no ano com -9 no mês, o que dava um corte
    #: imprevisível — não dá para afirmar qual janela ele usava de fato. Seis é
    #: a hipótese mais provável, não uma certeza, e a pergunta está aberta com
    #: o negócio (`RETOMADA.md`, item 4). Até haver resposta, ajustar aqui é
    #: melhor do que recompilar uma constante.
    #:
    #: Atenção ao que o campo significa: `OpenDate` é a data de **abertura da
    #: oportunidade**, não a da última alteração do orçamento. Encurtar a janela
    #: exclui oportunidades antigas mesmo que o orçamento delas tenha mudado
    #: ontem no WBC.
    meses_de_janela: int = Field(default=6, ge=1, le=120, alias="MESES_DE_JANELA")

    #: Teto de **escritas** por ciclo — documentos criados, atualizados ou
    #: cancelados, e oportunidades alteradas.
    #:
    #: Não limita a leitura: avaliar a janela inteira custa centésimos de
    #: segundo desde que a leitura passou a vir do HANA numa consulta só. O que
    #: precisa de freio é a escrita: em agosto de 2026 havia 1.785 oportunidades
    #: na janela, e liberar tudo de uma vez criaria todos os documentos
    #: represados numa única execução.
    limite_de_escrita_por_ciclo: int = Field(default=200, alias="LIMITE_DE_ESCRITA_POR_CICLO")

    #: Maior janela que alguém pode pedir pela tela do painel, em meses.
    #:
    #: A janela sob demanda (`PLANO_JANELA_SOB_DEMANDA.md (removed 2026-10-06)`) existe para
    #: vendas alcançar uma oportunidade antiga sem restart e sem TI. Este é o
    #: limite do que a tela aceita; o teto de escrita do ciclo cresce junto,
    #: por banda — ver `domain/janela.py`. 24 é a escolha do Marcelo (11/09/2026): cobre
    #: dois anos de oportunidades antigas e mantém o pior caso de escrita pequeno.
    janela_maxima: int = Field(
        default=24, ge=1, le=120, alias="JANELA_MAXIMA"
    )

    #: Teto de escrita que nenhuma banda ultrapassa.
    #:
    #: Separado do escalonamento de propósito: é a rede embaixo dele, para o dia
    #: em que alguém aumentar `JANELA_MAXIMA` sem refazer a conta do pior caso.
    teto_absoluto_de_escrita: int = Field(
        default=2000, ge=1, alias="TETO_ABSOLUTO_DE_ESCRITA"
    )

    #: Quanto tempo a pergunta "quer rodar outro ciclo?" fica de pé, em minutos.
    #:
    #: Passado o prazo, a janela volta ao padrão e o log registra onde o ciclo
    #: estendido parou. Quinze minutos é a escolha do Marcelo (11/09/2026).
    janela_espera_minutos: int = Field(
        default=15, ge=1, alias="JANELA_ESPERA_MINUTOS",
    )

    #: Retenção dos eventos de **decisão** do acompanhamento, em dias. O worker
    #: apaga os mais velhos uma vez por dia (`wbcpython faxina` faz o mesmo à
    #: mão). Ação, erro e reprocessamento nunca são apagados. `0` desliga.
    #:
    #: Seis dias foi a escolha do Marcelo em 08/09/2026: cobre a semana de
    #: trabalho, e o que importa de um orçamento (as ações) não passa por aqui.
    eventos_retencao_dias: int = Field(default=6, ge=0, alias="EVENTOS_RETENCAO_DIAS")

    worker_interval_seconds: int = Field(default=padroes.WORKER_INTERVAL_S, alias="WORKER_INTERVAL_SECONDS")

    #: Horário em que o worker trabalha. Fora dele, o ciclo agendado não roda.
    #:
    #: Vale **só para o worker**, e não para `wbcpython ciclo`: o comando manual
    #: é alguém pedindo, e recusar um pedido explícito por causa do relógio
    #: seria obstrução, não proteção.
    #:
    #: `fim` antes de `inicio` é lido como janela que atravessa a meia-noite
    #: (ex.: 19:00 → 06:30). Não é o caso de hoje, mas o contrário — recusar
    #: calado, ou pior, nunca rodar — seria uma armadilha silenciosa para quem
    #: configurasse assim um dia.
    worker_horario_inicio: time = Field(
        default=padroes.WORKER_HORARIO_INICIO, alias="WORKER_HORARIO_INICIO"
    )
    worker_horario_fim: time = Field(default=padroes.WORKER_HORARIO_FIM, alias="WORKER_HORARIO_FIM")

    def dentro_do_horario_do_worker(self, agora: time) -> bool:
        """Whether ``agora`` is inside the worker's working window.

        Both ends are inclusive (06:30 and 19:00 sharp are in). The window only decides
        whether a cycle **starts**: a cycle begun at 19:00 and ending at 19:00:14 runs to
        the end, since interrupting one midway is how documents without a link get created.
        """
        inicio, fim = self.worker_horario_inicio, self.worker_horario_fim
        if inicio <= fim:
            return inicio <= agora <= fim
        return agora >= inicio or agora <= fim

    #: Dias da semana em que o worker trabalha, no padrão ISO: 1 = segunda,
    #: 7 = domingo. O padrão é de segunda a sexta.
    #:
    #: É uma lista, e não um `apenas_dias_uteis`, porque feriado e escala de
    #: sábado são a mesma pergunta com resposta diferente — e uma bandeira
    #: booleana obrigaria a mexer em código no dia em que a resposta mudar.
    #: O campo é **texto**, e a conversão para conjunto é a propriedade
    #: `dias_de_trabalho`. Não é preferência de estilo: tipado como
    #: `frozenset[int]`, o `pydantic-settings` classifica o campo como complexo
    #: e tenta `json.loads("1,2,3,4,5")` **antes** de qualquer validador —
    #: `SettingsError` na partida, e só ao ler o `.env` de verdade. Os testes
    #: não pegaram porque construíam `Settings(...)` com o valor já pronto, sem
    #: passar pela fonte do `.env`; quem pegou foi rodar no ambiente real.
    worker_dias_de_trabalho: str = Field(default=padroes.WORKER_DIAS, alias="WORKER_DIAS_DE_TRABALHO")
    #: Arquivo cuja existência pede ao worker que termine o que está fazendo e
    #: saia. O `deploy_update.bat` o grava antes do `nssm stop`; o worker o
    #: remove na partida. Relativo ao cwd (a raiz do projeto), como o resto de
    #: `state/`. Ver `host/parada.py`.
    worker_arquivo_de_parada: Path = Field(
        default=Path("state/wbc_worker.stop"), alias="WORKER_ARQUIVO_DE_PARADA"
    )

    @field_validator("worker_dias_de_trabalho", mode="before")
    @classmethod
    def _dias_da_semana(cls, valor: Any) -> Any:
        """Startup validation: a day outside 1-7 or an empty list is refused.

        Deliberate: a silently ignored `8` would be a worker that skips a day for no
        visible reason, and an empty list a worker that never runs while looking alive.
        """
        if not isinstance(valor, str):
            valor = ",".join(str(int(d)) for d in sorted(valor))
        partes = [p.strip() for p in str(valor).split(",") if p.strip()]
        if not partes:
            raise ValueError(
                "WORKER_DIAS_DE_TRABALHO vazio: o worker não rodaria nunca. "
                "Use, por exemplo, 1,2,3,4,5 (segunda a sexta)."
            )
        try:
            dias = {int(p) for p in partes}
        except ValueError as exc:
            raise ValueError(
                f"WORKER_DIAS_DE_TRABALHO: {valor!r} não é uma lista de números "
                f"separados por vírgula (1 = segunda ... 7 = domingo)."
            ) from exc
        fora = sorted(d for d in dias if not 1 <= d <= 7)
        if fora:
            raise ValueError(
                f"WORKER_DIAS_DE_TRABALHO: {fora} fora de 1–7 (1 = segunda, 7 = domingo)."
            )
        return ",".join(str(d) for d in sorted(dias))

    @property
    def dias_de_trabalho(self) -> frozenset[int]:
        """The working days as ints (already validated by the field, so just convert)."""
        return frozenset(int(p) for p in self.worker_dias_de_trabalho.split(","))

    def e_dia_de_trabalho(self, dia: date) -> bool:
        """Whether the worker works on this weekday."""
        return dia.isoweekday() in self.dias_de_trabalho

    #: Nomes dos dias, escritos aqui em vez de vir do `strftime`.
    #:
    #: `%A` depende do locale do processo: numa máquina sem `pt_BR` instalado o
    #: log da integração sairia com "Saturday" no meio do português. O worker
    #: roda como serviço, onde o locale é o que o sistema deu.
    DIAS_DA_SEMANA: ClassVar[tuple[str, ...]] = (
        "segunda",
        "terça",
        "quarta",
        "quinta",
        "sexta",
        "sábado",
        "domingo",
    )
    DIAS_ABREVIADOS: ClassVar[tuple[str, ...]] = (
        "seg",
        "ter",
        "qua",
        "qui",
        "sex",
        "sáb",
        "dom",
    )

    def nome_do_dia(self, dia: date) -> str:
        return self.DIAS_DA_SEMANA[dia.isoweekday() - 1]

    @property
    def dias_de_trabalho_por_extenso(self) -> str:
        """For the log: "seg, ter, qua, qui, sex" instead of a set."""
        return ", ".join(self.DIAS_ABREVIADOS[d - 1] for d in sorted(self.dias_de_trabalho))

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")


    #: Arquivo para onde o log também é escrito, além da tela.
    #:
    #: É o que faz a mesma execução aparecer nos dois lugares: o comando mostra
    #: as linhas enquanto roda, e o painel lê as mesmas linhas depois — inclusive
    #: as de um worker que rodou de madrugada, sem ninguém olhando o terminal.
    #: Vazio desliga o arquivo e mantém só a tela.
    log_file: str = Field(default="wbcpython.log", alias="LOG_FILE")

    @property
    def caminho_do_log(self) -> Path | None:
        """Log file path, or `None` when file logging is off."""
        return Path(self.log_file) if self.log_file.strip() else None

    #: Senha exigida pelo painel para executar qualquer comando que escreva.
    #:
    #: O painel não tem autenticação de usuário, e a aba "Executar" dispara
    #: comandos que criam e cancelam documentos no SAP. Esta senha é o degrau
    #: entre "alcançou a porta" e "pode escrever".
    #:
    #: **Falha fechada**: vazia desabilita os comandos de escrita, em vez de
    #: liberá-los. É a mesma escolha de `safety.py` — configuração incompleta
    #: bloqueia, nunca libera —, e aqui importa mais ainda: o padrão de um campo
    #: esquecido não pode ser "qualquer um na rede cria pedido no SAP".
    #:
    #: Senha não identifica **quem** executou, só que alguém a conhecia. Por
    #: isso o painel exige, junto, o nome de quem está executando, que vai para
    #: o histórico. Uma coisa autoriza; a outra audita.
    #: Janela usada quando o ciclo mira **um** orçamento (`--orcamento`).
    #:
    #: Maior que a janela do ciclo inteiro de propósito. A janela existe para
    #: limitar o que é varrido sem ninguém pedindo; pedir um orçamento pelo
    #: número é o contrário disso — alguém sabe qual quer e está esperando que
    #: aconteça. Com a janela normal, um orçamento mais antigo simplesmente não
    #: era encontrado, e o comando terminava com "0 avaliado(s)" e código 0:
    #: um nada silencioso que parece sucesso.
    # 24 since 01/10/2026 (Marcelo): a single quote run from the painel must reach any
    # quote the window-arming card can reach (max 24), so the operator never needs both.
    meses_de_janela_dirigida: int = Field(default=24, alias="MESES_DE_JANELA_DIRIGIDA")

    painel_senha: SecretStr = Field(default=SecretStr(""), alias="PAINEL_SENHA")

    #: Endereço em que o painel escuta. `127.0.0.1` só aceita a própria
    #: máquina; `0.0.0.0` aceita a rede interna.
    #:
    #: Está na configuração, e não só na linha de comando, porque o painel roda
    #: como serviço: uma escolha que vive num parâmetro de invocação se perde no
    #: primeiro reinício, e ninguém descobre até alguém reclamar que a tela não
    #: abre. O padrão continua sendo o fechado.
    painel_host: str = Field(default="127.0.0.1", alias="PAINEL_HOST")
    painel_porta: int = Field(default=padroes.PAINEL_PORTA, alias="PAINEL_PORTA")

    #: Chave de acesso do painel — a MESMA `OS_API_KEY` da API 8077 do
    #: ServidorIntegracaoSAP, de propósito: quem opera as duas telas tem uma chave
    #: só, e o `.env` é um só. Vazia, o painel fica aberto (mesmo comportamento da
    #: API, documentado no `.env.example`); o log avisa no arranque.
    api_key: SecretStr = Field(default=SecretStr(""), alias="OS_API_KEY")

    #: Para onde o botão "Sincronização SAP → Supabase" leva. Vazio = mesmo host
    #: da requisição, na porta da API (`OS_API_PORT`) — o caso da .11, onde as
    #: duas telas moram na mesma máquina.
    sis_painel_url: str = Field(default="", alias="SIS_PAINEL_URL")
    os_api_port: int = Field(default=padroes.OS_API_PORT, alias="OS_API_PORT")

    #: Where the "Controle de Produção" link goes: ``CP_URL`` verbatim, or the same host on
    #: ``CP_PORTA`` (the .11 case). The default port lives in three configs (root,
    #: here and ``controleproducao``) — ``tests/test_config_paridade_wbc.py`` keeps them equal.
    cp_url: str = Field(default="", alias="CP_URL")
    cp_porta: int = Field(default=padroes.CP_PORTA, alias="CP_PORTA")

    #: The "← OrçaView" link of the shared top bar (casa/, 01/10/2026): the .90 home. Same
    #: default as ``controleproducao`` (parity test); ``ORCAVIEW_URL`` only for a dev copy.
    orcaview_url: str = Field(default=padroes.ORCAVIEW_URL, alias="ORCAVIEW_URL")

    @property
    def painel_exige_chave(self) -> bool:
        """Whether the painel asks for the access key (an `OS_API_KEY` is configured)."""
        return bool(self.api_key.get_secret_value())

    @property
    def painel_exposto(self) -> bool:
        """Whether the painel accepts connections from other machines.

        Anything but a loopback address exposes it: `0.0.0.0` is the common case, but a
        fixed LAN IP exposes it just the same, which an `== "0.0.0.0"` test would miss.
        """
        return self.painel_host.strip() not in ("127.0.0.1", "localhost", "::1")

    @property
    def painel_pode_escrever(self) -> bool:
        """The painel runs writing commands only with a password **and** off production.

        In production execution goes through the terminal, with a responsible person at
        the keyboard. See `RISCOS_PRODUCAO.md`: the first cycle there would cancel 27
        quotes and create 4 orders (R$ 170,973.41), all irreversible, which must not hinge
        on a click in a screen without user authentication.
        """
        return bool(self.painel_senha.get_secret_value()) and not self.targets_production

    @property
    def painel_pode_armar_janela(self) -> bool:
        """Arming the window is the deliberate exception to the production block, and only it.

        The guard above stops a click from **triggering** a write cycle in production.
        Arming triggers nothing: the worker already runs by itself there every
        `WORKER_INTERVAL_SECONDS`, writing to SAP, request or not. The request only changes
        **how far back** that automatic cycle looks.

        The password still applies. It was dropped for a day (2026-09-14) and restored the
        same day: without the production block in front, it is the **only** guard on an
        action that causes hundreds of irreversible writes. Hence it is checked as a
        precondition, not just compared: with an empty `PAINEL_SENHA`,
        `compare_digest("", "")` would let through whoever typed nothing.
        """
        return bool(self.painel_senha.get_secret_value())

    service_layer: ServiceLayerSettings = Field(default_factory=ServiceLayerSettings)
    wbc_sql: WbcSqlSettings = Field(default_factory=WbcSqlSettings)
    hana: HanaSettings = Field(default_factory=HanaSettings)
    tracking: TrackingSettings = Field(default_factory=TrackingSettings)

    @property
    def targets_production(self) -> bool:
        """True if the Service Layer points at the production company DB."""
        return is_production(self.service_layer.company_db, self.production_company_db)

    def describe_environment(self) -> str:
        """Human-readable environment summary, logged at worker startup. Never includes credentials."""
        trava = "ATIVA" if self.block_production_writes else "DESATIVADA"
        alvo = "PRODUÇÃO" if self.targets_production else "homologação"
        return (
            f"ambiente={self.environment} | "
            f"company_db={self.service_layer.company_db} ({alvo}) | "
            f"schema_hana={self.hana.schema_name} | "
            f"trava_de_escrita_em_producao={trava}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Application settings (memoized); tests that need a reload call `get_settings.cache_clear()`."""
    return Settings()
