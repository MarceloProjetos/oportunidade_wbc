"""Configuração da aplicação, carregada de variáveis de ambiente / .env.

Princípio (ai_spec/04_environment_constraints.md, Regra 4): nenhum nome de
company DB ou schema HANA pode ficar fixo no código — tudo vem daqui, para que
trocar de ambiente seja mudança de configuração, e para que a regra de não
escrever em produção seja verificável olhando a config, não auditando o código.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from wbcpython.safety import is_production

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
    """Conexão com o SAP Business One Service Layer (REST)."""

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
        """Valor a passar para o parâmetro `verify` do httpx."""
        if self.ca_bundle:
            return self.ca_bundle
        return self.verify_ssl


class WbcSqlSettings(BaseSettings):
    """Conexão com o SQL Server do WBC (banco WBCCAD)."""

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
    """Conexão SQL direta ao HANA via hdbcli (views de leitura)."""

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
    """Banco próprio de acompanhamento, que alimenta o dashboard."""

    model_config = SettingsConfigDict(
        env_prefix="TRACKING_", env_file=".env", extra="ignore", case_sensitive=False
    )

    db_url: SecretStr = SecretStr("sqlite:///./wbcpython_tracking.db")
    """URL do banco de tracking.

    É `SecretStr` porque, assim que o tracking sair do SQLite para um
    PostgreSQL/SQL Server, a URL passa a carregar usuário e senha embutidos
    (`postgresql://user:senha@host/db`) — e apareceria em texto puro em
    qualquer `model_dump()` ou `repr()` da configuração.
    """


class Settings(BaseSettings):
    """Configuração raiz da aplicação."""

    model_config = SettingsConfigDict(
        env_prefix="WBC_", env_file=".env", extra="ignore", case_sensitive=False
    )

    def __init__(self, **dados: Any) -> None:
        """Propaga o `_env_file` para as configurações aninhadas.

        Sem isto, `Settings(_env_file=None)` era uma promessa que o código não
        cumpria: o `Settings` de cima ignorava o arquivo, mas
        `ServiceLayerSettings`, `HanaSettings` e as demais nascem de
        `default_factory` — sem argumento nenhum — e continuavam lendo o `.env`
        do diretório corrente.

        O efeito passou despercebido enquanto todo `.env` de máquina apontava
        para homologação: o valor que vazava era igual ao esperado. Na virada
        para produção, três testes de configuração ficaram vermelhos sem que uma
        linha de código tivesse mudado — só porque a máquina passou a apontar
        para outro lugar.
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
    block_production_writes: bool = True
    production_company_db: str = "SBOALTAMIRAPROD"

    @field_validator("production_company_db")
    @classmethod
    def _producao_nao_pode_ser_vazia(cls, valor: str) -> str:
        """Um nome vazio aqui desligaria a trava de produção em silêncio.

        `is_production()` compara por igualdade: se o nome da produção for uma
        string vazia, nenhum destino jamais é reconhecido como produção — e as
        próprias ferramentas de diagnóstico passariam a reportar "homologação,
        trava ATIVA" mesmo apontadas para a produção. Melhor recusar a
        configuração no arranque do que operar com uma trava que não trava.
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
    #: A janela sob demanda (`docs/PLANO_JANELA_SOB_DEMANDA.md`) existe para
    #: vendas alcançar uma oportunidade antiga sem restart e sem TI. Este é o
    #: limite do que a tela aceita; o teto de escrita do ciclo cresce junto,
    #: por banda — ver `domain/janela.py`.
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

    worker_interval_seconds: int = Field(default=300, alias="WORKER_INTERVAL_SECONDS")

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
    worker_horario_inicio: time = Field(default=time(6, 30), alias="WORKER_HORARIO_INICIO")
    worker_horario_fim: time = Field(default=time(19, 0), alias="WORKER_HORARIO_FIM")

    def dentro_do_horario_do_worker(self, agora: time) -> bool:
        """O relógio está dentro da janela de trabalho?

        Os limites são inclusivos nas duas pontas: às 06:30 em ponto o worker
        já trabalha, e às 19:00 em ponto ainda trabalha. Um ciclo que comece
        19:00 e termine 19:00:14 é levado até o fim — a janela decide se o ciclo
        **começa**, e interromper um ciclo no meio é como se cria documento sem
        vínculo.
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
    worker_dias_de_trabalho: str = Field(default="1,2,3,4,5", alias="WORKER_DIAS_DE_TRABALHO")
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
        """Valida na partida: dia fora de 1–7 e lista vazia são recusados.

        Recusar aqui é deliberado. Um `8` ignorado em silêncio viraria um
        worker que não roda num dia e ninguém sabe por quê; uma lista vazia,
        um worker que nunca roda parecendo estar rodando.
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
        """Os dias já convertidos — validados no campo, então é só converter."""
        return frozenset(int(p) for p in self.worker_dias_de_trabalho.split(","))

    def e_dia_de_trabalho(self, dia: date) -> bool:
        """O worker trabalha neste dia da semana?"""
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
        """Para o log dizer "seg, ter, qua, qui, sex" em vez de um conjunto."""
        return ", ".join(self.DIAS_ABREVIADOS[d - 1] for d in sorted(self.dias_de_trabalho))

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    #: Folga sobre o peso líquido da árvore, para chegar ao peso de **embarque**
    #: gravado no `Weight1` da linha do pedido.
    #:
    #: Não é invenção: medido em 1.060 linhas de pedido de 2026 da produção, o
    #: `Weight1` é sistematicamente ~10% acima do peso da árvore (razão mediana
    #: 1,099; 622 delas entre 1,09 e 1,11) e inteiro em 1.056 dos 1.061 casos.
    #: Varrendo fatores de 1,000 a 1,300 de milésimo em milésimo, o que mais
    #: acerta é exatamente **1,100**.
    #:
    #: Fica configurável porque é regra de negócio (embalagem), não constante
    #: física — e porque a reprodução não é exata: ver `DECISOES.md`.
    fator_de_peso_de_embarque: Decimal = Field(
        default=Decimal("1.10"), ge=1, le=3, alias="FATOR_PESO_EMBARQUE"
    )

    #: Arquivo para onde o log também é escrito, além da tela.
    #:
    #: É o que faz a mesma execução aparecer nos dois lugares: o comando mostra
    #: as linhas enquanto roda, e o painel lê as mesmas linhas depois — inclusive
    #: as de um worker que rodou de madrugada, sem ninguém olhando o terminal.
    #: Vazio desliga o arquivo e mantém só a tela.
    log_file: str = Field(default="wbcpython.log", alias="LOG_FILE")

    @property
    def caminho_do_log(self) -> Path | None:
        """`None` quando o log em arquivo está desligado."""
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
    meses_de_janela_dirigida: int = Field(default=12, alias="MESES_DE_JANELA_DIRIGIDA")

    painel_senha: SecretStr = Field(default=SecretStr(""), alias="PAINEL_SENHA")

    #: Endereço em que o painel escuta. `127.0.0.1` só aceita a própria
    #: máquina; `0.0.0.0` aceita a rede interna.
    #:
    #: Está na configuração, e não só na linha de comando, porque o painel roda
    #: como serviço: uma escolha que vive num parâmetro de invocação se perde no
    #: primeiro reinício, e ninguém descobre até alguém reclamar que a tela não
    #: abre. O padrão continua sendo o fechado.
    painel_host: str = Field(default="127.0.0.1", alias="PAINEL_HOST")
    painel_porta: int = Field(default=8079, alias="PAINEL_PORTA")

    #: Chave de acesso do painel — a MESMA `OS_API_KEY` da API 8077 do
    #: ServidorIntegracaoSAP, de propósito: quem opera as duas telas tem uma chave
    #: só, e o `.env` é um só. Vazia, o painel fica aberto (mesmo comportamento da
    #: API, documentado no `.env.example`); o log avisa no arranque.
    api_key: SecretStr = Field(default=SecretStr(""), alias="OS_API_KEY")

    #: Para onde o botão "Sincronização SAP → Supabase" leva. Vazio = mesmo host
    #: da requisição, na porta da API (`OS_API_PORT`) — o caso da .11, onde as
    #: duas telas moram na mesma máquina.
    sis_painel_url: str = Field(default="", alias="SIS_PAINEL_URL")
    os_api_port: int = Field(default=8077, alias="OS_API_PORT")

    @property
    def painel_exige_chave(self) -> bool:
        """O painel pede a chave de acesso (há `OS_API_KEY` configurada)."""
        return bool(self.api_key.get_secret_value())

    @property
    def painel_exposto(self) -> bool:
        """O painel aceita conexão de outra máquina.

        Qualquer coisa que não seja um endereço de loopback expõe. `0.0.0.0` é
        o caso comum, mas fixar um IP da rede (`192.168.1.70`) expõe do mesmo
        jeito — e era o que um teste de `== "0.0.0.0"` deixaria passar calado.
        """
        return self.painel_host.strip() not in ("127.0.0.1", "localhost", "::1")

    @property
    def painel_pode_escrever(self) -> bool:
        """O painel só executa comando que escreve com senha **e** fora de produção.

        Em produção a execução é pelo terminal, com uma pessoa responsável na
        frente. Ver `RISCOS_PRODUCAO.md`: o primeiro ciclo lá cancelaria 27
        cotações e criaria 4 pedidos (R$ 170.973,41), tudo irreversível — isso
        não pode depender de um clique numa tela sem autenticação.
        """
        return bool(self.painel_senha.get_secret_value()) and not self.targets_production

    service_layer: ServiceLayerSettings = Field(default_factory=ServiceLayerSettings)
    wbc_sql: WbcSqlSettings = Field(default_factory=WbcSqlSettings)
    hana: HanaSettings = Field(default_factory=HanaSettings)
    tracking: TrackingSettings = Field(default_factory=TrackingSettings)

    @property
    def targets_production(self) -> bool:
        """True se o Service Layer está apontado para a company DB de produção."""
        return is_production(self.service_layer.company_db, self.production_company_db)

    def describe_environment(self) -> str:
        """Resumo legível do ambiente — para logar no arranque do worker.

        Nunca inclui credenciais.
        """
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
    """Configuração da aplicação (memoizada).

    Use `get_settings.cache_clear()` nos testes que precisarem recarregar.
    """
    return Settings()
