"""Command-line entry point of WBCPython.

Available commands:

    wbcpython env       Show which environment the application points at.
    wbcpython doctor    Diagnose the installation: configuration, optional
                        dependencies and readiness for each project phase.
    wbcpython check-sap Test the real connection to the Service Layer (uses the network).
    wbcpython check-hana Test HANA and say in which schema the views exist.
    wbcpython pendentes Show what a cycle would do, without executing anything.
                        (--com-acao lists only those that would result in a write;
                        --exportar writes the snapshot the painel shows.)
    wbcpython worker    Start the integration worker (scheduled, continuous).
    wbcpython ciclo     Run ONE integration cycle and exit.
    wbcpython dashboard Start the tracking painel (HTML + HTMX).
    wbcpython pesos     Recompute the weight (Weight1) of the lines of an already
                        created order, from the WBC product tree.
    wbcpython pesos-reescalados
                        Show the weights the SAP multiplied when a person changed
                        the line quantity (read only).
    wbcpython datas-de-abertura
                        Fill in the opening date on old tracking rows, so the painel
                        can apply the window.
    wbcpython faxina    Delete decision events older than the retention (actions and
                        errors stay).
    wbcpython janela    Show, arm or clear the extended-window request of the next cycle.

`env` and `doctor` are local checks, safe to run at any time on any machine. Of the checks,
only `check-sap` and `check-hana` use the network — and only to read.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import ssl
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from wbcpython import __version__
from wbcpython.config import Settings, get_settings

OK = "  [ok] "
AVISO = "  [!]  "
ERRO = "  [X]  "
PENDENTE = "  [ ]  "


def _cmd_env(settings: Settings) -> int:
    """Show the current environment, without exposing credentials.

    The write lock shown here is derived from the machine's IP (`Settings.block_production_writes`).
    """
    print(f"WBCPython {__version__}")
    print(settings.describe_environment())

    if settings.targets_production:
        print()
        print(f"{AVISO}A aplicação está apontada para a company DB de PRODUÇÃO.")
        if settings.block_production_writes:
            print(f"{OK}A trava de escrita está ATIVA: leituras são permitidas, escritas serão")
            print("       bloqueadas com ProductionWriteBlocked.")
        else:
            # Até a virada, esta combinação era proibida e o comando saía com
            # erro. Agora ela é o estado normal — sair com erro faria o `doctor`
            # e a aba "Executar" do painel marcarem falha em toda execução, e um
            # alarme que toca sempre deixa de ser lido.
            print(f"{AVISO}A trava de escrita está DESATIVADA: este ambiente ESCREVE em")
            print(f"       {settings.service_layer.company_db}. Cotação cancelada e pedido")
            print("       criado não se desfazem.")
    return 0


def _tem_modulo(nome: str) -> bool:
    return importlib.util.find_spec(nome) is not None


def _cmd_doctor(settings: Settings) -> int:
    """Local diagnosis of the installation. Does not touch the network."""
    problemas = 0

    print(f"WBCPython {__version__} — diagnóstico")
    print(f"Python {sys.version.split()[0]}")
    print()

    # --- Ambiente e segurança -------------------------------------------------
    print("Ambiente e segurança")
    print(f"       {settings.describe_environment()}")
    if settings.targets_production and not settings.block_production_writes:
        # Não conta como problema desde a virada: é o estado de operação normal.
        # Contá-lo faria o `doctor` sair com erro em toda execução, e um
        # diagnóstico que nunca fica verde para de ser diagnóstico.
        print(f"{AVISO}Apontado para produção com a trava DESATIVADA: este ambiente")
        print(f"       ESCREVE em {settings.service_layer.company_db}.")
    elif settings.targets_production:
        print(f"{AVISO}Apontado para produção (somente leitura permitida pela trava).")
    else:
        print(f"{OK}Apontado para ambiente de não-produção, com a trava ativa.")
    print()

    # --- Configuração ---------------------------------------------------------
    print("Configuração (.env)")
    obrigatorios = [
        ("SL_BASE_URL", settings.service_layer.base_url),
        ("SL_COMPANY_DB", settings.service_layer.company_db),
        ("SL_USERNAME", settings.service_layer.username),
        ("SL_PASSWORD", settings.service_layer.password.get_secret_value()),
    ]
    faltando = [nome for nome, valor in obrigatorios if not valor]
    if faltando:
        print(f"{ERRO}Faltam variáveis do Service Layer: {', '.join(faltando)}")
        print("       Copie .env.example para .env e preencha com os dados de homologação.")
        problemas += 1
    else:
        print(f"{OK}Service Layer configurado (usuário e senha presentes).")

    if settings.wbc_sql.host and settings.wbc_sql.username:
        print(f"{OK}SQL Server do WBC configurado ({settings.wbc_sql.database}).")
    else:
        print(f"{PENDENTE}SQL Server do WBC ainda não configurado (necessário na Fase 3).")

    if settings.hana.host and settings.hana.username:
        print(f"{OK}HANA configurado (schema {settings.hana.schema_name}).")
    else:
        print(f"{PENDENTE}HANA ainda não configurado (necessário na Fase 4).")

    if settings.service_layer.ca_bundle:
        if Path(settings.service_layer.ca_bundle).is_file():
            print(f"{OK}Certificado da CA configurado e encontrado.")
        else:
            print(
                f"{ERRO}SL_CA_BUNDLE aponta para um arquivo inexistente: "
                f"{settings.service_layer.ca_bundle}"
            )
            problemas += 1
    elif not settings.service_layer.verify_ssl:
        print(f"{AVISO}Validação de certificado TLS desativada (SL_VERIFY_SSL=false).")
        print("       Aceitável em desenvolvimento; precisa ser resolvido antes de produção.")
    print()

    # --- Dependências opcionais ----------------------------------------------
    print("Dependências opcionais")
    for modulo, extra, fase in (
        ("pymssql", "mssql", "Fase 3 — repositório do WBC"),
        ("hdbcli", "hana", "Fase 4 — views do HANA"),
        ("fastapi", "dashboard", "Fase 9 — painel"),
    ):
        if _tem_modulo(modulo):
            print(f"{OK}{modulo} instalado.")
        else:
            print(f"{PENDENTE}{modulo} ausente — instale com: pip install -r requirements.txt  ({fase})")
    print()

    # --- Resultado ------------------------------------------------------------
    if problemas:
        print(f"Diagnóstico concluído com {problemas} problema(s) que impedem o uso.")
        return 1
    print("Diagnóstico concluído: nenhum problema impeditivo encontrado.")
    return 0


def _cmd_check_sap(settings: Settings) -> int:
    """Test the real connection to the Service Layer. Read only."""
    # Importado aqui para que `env`/`doctor` não dependam do cliente HTTP.
    from wbcpython.infrastructure.service_layer import ServiceLayerClient, ServiceLayerError

    print(f"Conectando em {settings.service_layer.base_url}")
    print(f"Company DB: {settings.service_layer.company_db}")
    if settings.targets_production:
        print(f"{AVISO}Apontado para PRODUÇÃO. Este teste faz apenas leitura.")
    print()

    def _dicas() -> None:
        print()
        print("       Verifique, nesta ordem:")
        print("       1. está na rede interna da Altamira?")
        print("       2. SL_BASE_URL, SL_USERNAME, SL_PASSWORD e SL_COMPANY_DB no .env;")
        print("       3. certificado TLS (SL_VERIFY_SSL / SL_CA_BUNDLE).")

    # A construção fica DENTRO do try: httpx valida o arquivo de certificado já
    # no construtor, e um SL_CA_BUNDLE inexistente levanta FileNotFoundError
    # antes de qualquer requisição — que escapava do tratamento quando o cliente
    # era criado aqui fora, e virava traceback justamente no caso que a dica 3
    # existe para explicar.
    cliente: ServiceLayerClient | None = None
    try:
        cliente = ServiceLayerClient(
            settings.service_layer,
            production_company_db=settings.production_company_db,
            block_production_writes=settings.block_production_writes,
        )
        print(f"{OK}{cliente.testar_conexao()}")
        return 0
    except ServiceLayerError as exc:
        print(f"{ERRO}{exc}")
        _dicas()
        return 1
    except httpx.HTTPError as exc:
        # Falha de rede/DNS/TLS antes mesmo de haver resposta HTTP.
        # (httpx.ConnectError NÃO é OSError — precisa ser capturado assim.)
        print(f"{ERRO}Não foi possível alcançar o servidor: {exc}")
        print("       Confirme o endereço em SL_BASE_URL e o acesso à rede interna")
        print("       (VPN, se estiver fora da Altamira).")
        return 1
    except (OSError, ssl.SSLError) as exc:
        # Certificado inexistente/ilegível em SL_CA_BUNDLE, entre outros.
        print(f"{ERRO}Falha ao preparar a conexão: {exc}")
        _dicas()
        return 1
    finally:
        if cliente is not None:
            cliente.close()


def _cmd_check_hana(settings: Settings) -> int:
    """Test the HANA connection and diagnose the schema. Read only."""
    from wbcpython.infrastructure.hana import (
        IdentificadorInvalido,
        RepositorioViewsHanaSql,
    )

    if not settings.hana.host or not settings.hana.username:
        print(f"{ERRO}HANA não configurado. Preencha HANA_HOST e HANA_USERNAME no .env.")
        return 1

    print(f"Conectando em {settings.hana.host}:{settings.hana.port}")
    print(f"Schema: {settings.hana.schema_name}")
    print()

    try:
        repo = RepositorioViewsHanaSql(
            settings.hana,
            production_company_db=settings.production_company_db,
            block_production_writes=settings.block_production_writes,
        )
    except IdentificadorInvalido as exc:
        print(f"{ERRO}{exc}")
        return 1

    try:
        resultado = repo.views_existem()
    except Exception as exc:  # noqa: BLE001 - diagnóstico deve sempre orientar
        print(f"{ERRO}Falha ao consultar o HANA: {exc}")
        print()
        print("       Verifique: rede interna, HANA_HOST/HANA_PORT (30015),")
        print("       credenciais, e se o driver está instalado (pip install -r requirements.txt).")
        return 1
    finally:
        repo.close()

    for view, existe in sorted(resultado.items()):
        print(f"{OK if existe else PENDENTE}{view}")

    print()
    if all(resultado.values()):
        print(f"As duas views existem em '{settings.hana.schema_name}'.")
        return 0
    if any(resultado.values()):
        print(f"{AVISO}Apenas parte das views existe em '{settings.hana.schema_name}'.")
        return 1
    print(f"{AVISO}Nenhuma das views existe em '{settings.hana.schema_name}'.")
    print("       Se elas só existirem no schema de produção, aponte HANA_SCHEMA para lá:")
    print("       a leitura é permitida pela regra do projeto — apenas a escrita não é.")
    return 1


#: SitCode do WBC para orçamento cancelado. `SITCODES_ENCERRAMENTO` também tem
#: 70 e 90, que igualmente encerram a oportunidade; `--cancelados` é
#: deliberadamente só o 99, porque a leva que se quer isolar é a dos
#: cancelamentos.
SITCODE_CANCELADO = 99


def _cmd_worker(
    settings: Settings,
    *,
    um_ciclo: bool,
    orcamento: str | None,
    apenas_sitcode: int | None = None,
    somente_leitura: bool = False,
) -> int:
    """Start the worker (continuous) or run a single cycle.

    With `somente_leitura` (`ciclo --simular`) the cycle decides and fills in the tracking, and
    writes nothing to the SAP.
    """
    from wbcpython.host.worker import WorkerIntegracao

    # Mesmo log da prévia: tela e arquivo, e o arquivo é o que o painel mostra.
    # Para o worker isso é o essencial da coisa — ele roda sem ninguém olhando o
    # terminal, e sem arquivo a execução de madrugada não deixa rastro nenhum.
    # Only the continuous worker rotates the file (one rotating process; see logs.configurar).
    arquivo = _preparar_log(settings, rotacionar=not um_ciclo)
    if arquivo:
        print(f"Log desta execução: {arquivo} (visível também no painel, aba 'Log').")

    if somente_leitura:
        # O aviso de produção abaixo não vale para o ensaio, e repeti-lo aqui
        # ensinaria a ignorá-lo: quem vê "escreve de verdade" no início de uma
        # execução que não escreve nada para de acreditar no aviso quando ele
        # importa.
        aviso = (
            f"SIMULAÇÃO ({settings.service_layer.company_db}): o ciclo decide e "
            "preenche o acompanhamento, e não escreve nada no SAP."
        )
        print(f"{OK}{aviso}")
        logging.getLogger("wbcpython").warning(aviso)
    elif settings.targets_production and not settings.block_production_writes:
        # A recusa que existia aqui foi removida na virada para produção, a
        # pedido do usuário — era a segunda defesa, e existia para proteger
        # contra desligar a trava e esquecer ligada. Agora a trava está
        # desligada de propósito, e ela deixaria a integração sem poder rodar.
        #
        # O que fica no lugar é visibilidade: nenhum ciclo em produção começa
        # sem esta linha no log e na tela. Quem abrir o log de uma execução de
        # madrugada precisa saber, na primeira linha, que aquilo escreveu em
        # documento de verdade.
        aviso = (
            f"ESCRITA EM PRODUÇÃO HABILITADA ({settings.service_layer.company_db}). "
            "Cotação cancelada e pedido criado não se desfazem."
        )
        print(f"{AVISO}{aviso}")
        logging.getLogger("wbcpython").warning(aviso)

    worker = WorkerIntegracao(settings)
    try:
        if um_ciclo:
            resultado = worker.executar_ciclo(
                orcamento=orcamento,
                apenas_sitcode=apenas_sitcode,
                somente_leitura=somente_leitura,
            )
            if resultado.ignorado:
                # Exit non-zero so the painel shows the run as failed, not "ok".
                print(f"{AVISO}{resultado.resumo} Nada foi lido nem gravado; rode de novo.")
                return 1
            print(f"{OK}{resultado.resumo}")
            if somente_leitura:
                print(
                    f"{AVISO}Foi simulação: nenhum documento foi criado, alterado ou "
                    f"cancelado. Rode sem --simular para executar de verdade."
                )
            return 1 if resultado.erros else 0
        worker.rodar_continuamente()
        return 0
    except KeyboardInterrupt:
        print("\nEncerrado pelo usuário.")
        return 0


def _cmd_dashboard(settings: Settings, *, host: str | None, porta: int | None) -> int:
    """Start the tracking painel (FastAPI + HTMX).

    Listens on `127.0.0.1` by default. Without `OS_API_KEY` the painel has **no authentication**:
    anyone who reaches it can register a reprocessing request in anyone's name, besides triggering
    the read commands and the cycle simulation; with `OS_API_KEY` set it asks for the access key
    (`Settings.painel_exige_chave`). Exposing it on the network is a conscious decision —
    `PAINEL_HOST` in the `.env` or `--host` — not something that happens by oversight.

    The command line wins over the configuration: `--host` serves a one-off test without editing
    the `.env`.
    """
    host = host or settings.painel_host
    porta = porta or settings.painel_porta
    if not _tem_modulo("fastapi") or not _tem_modulo("uvicorn"):
        print(f"{ERRO}FastAPI/Uvicorn não instalados.")
        print("       Instale com: pip install -r requirements.txt")
        return 1

    import uvicorn

    from wbcpython.dashboard.web import criar_app

    _preparar_log(settings)
    print(f"Painel em http://{'localhost' if host == '127.0.0.1' else host}:{porta}")
    if host.strip() not in ("127.0.0.1", "localhost", "::1"):
        if settings.painel_exige_chave:
            print(f"{OK}Escutando em {host}: o painel pede a chave de acesso (OS_API_KEY).")
            logging.getLogger("wbcpython").info(
                "Painel exposto na rede (host=%s, porta=%s), com chave de acesso.", host, porta
            )
        else:
            print(
                f"{AVISO}Escutando em {host}: qualquer máquina que alcance esta porta abre "
                f"o painel. Ele não tem autenticação — defina OS_API_KEY no .env."
            )
            logging.getLogger("wbcpython").warning(
                "Painel exposto na rede (host=%s, porta=%s), sem autenticação.", host, porta
            )
    print("Ctrl+C para encerrar.")
    try:
        uvicorn.run(criar_app(settings=settings), host=host, port=porta, log_level="warning")
    except KeyboardInterrupt:
        pass
    return 0


def _cmd_pendentes(
    settings: Settings,
    *,
    orcamento: str | None,
    apenas_com_acao: bool = False,
    exportar: str | None = None,
    meses_pedidos: int | None = None,
) -> int:
    """Show what a cycle would do — **without executing anything**.

    The safety net that was missing to test in homologation: before running `ciclo`, you can see,
    quote by quote, which rule the domain would apply and which actions would come out of it. Uses
    only reads (GET on the Service Layer, SELECT on HANA and on the WBC), does not open a tracking
    transaction and does not call the processor — the one that writes.

    The simulation lives here, in the command, and not in a shared layer: it is a command-line tool,
    and whoever reads it expects to find it in the command that runs it. What travels outside is the
    **snapshot** (`application.previsao` defines only the format), not the decision path.

    The report goes through `logging`, not `print`: the same run shows on the screen of whoever ran
    it and in the file the painel reads. See `wbcpython.logs`.

    `apenas_com_acao` hides the rows without action. In a window of ~950 opportunities, those with
    action are a few dozen: the full listing exceeds 2,900 lines and buries exactly what you want to
    check before running the cycle. The footer summary still counts **all** the evaluated rows, so
    filtering the display never gives the impression of a smaller window than it is.

    `exportar` writes the snapshot as JSON — what the painel's "Próximo ciclo" tab reads. The painel
    queries neither SAP nor WBC, by architecture decision (see `dashboard/__init__`).

    `meses_pedidos` rehearses another window without arming anything.
    """
    import json
    from datetime import UTC, datetime

    from wbcpython.application.previsao import (
        ACOES_DE_DOCUMENTO,
        ACOES_QUE_ESCREVEM,
        STATUS_OPORTUNIDADE,
        LinhaDePrevisao,
        Previsao,
        para_json,
    )
    from wbcpython.application.processar import (
        decidir_pela_situacao,
        decidir_pelo_orcamento,
        precisa_do_orcamento,
        total_do_payload,
    )
    from wbcpython.domain import janela as jn
    from wbcpython.domain.cotacao import linhas as linhas_da_cotacao
    from wbcpython.domain.pedido import linhas as linhas_do_pedido
    from wbcpython.host.worker import janela_padrao
    from wbcpython.infrastructure.hana.oportunidades import RepositorioOportunidadesHana
    from wbcpython.infrastructure.service_layer.client import ServiceLayerClient
    from wbcpython.infrastructure.service_layer.documentos import (
        RepositorioDocumentosVendaServiceLayer,
    )
    from wbcpython.infrastructure.service_layer.grupo_produtos import (
        RepositorioGrupoProdutosServiceLayer,
    )
    from wbcpython.infrastructure.service_layer.oportunidades import CAMPO_CHAVE
    from wbcpython.infrastructure.wbc_sql.repository import RepositorioOrcamentosWbcSql

    arquivo = _preparar_log(settings)
    relatar = logging.getLogger("wbcpython.pendentes").info
    alertar = logging.getLogger("wbcpython.pendentes").warning

    relatar(settings.describe_environment())
    relatar("")
    relatar("Prévia somente leitura: nada será criado, alterado ou cancelado.")
    if arquivo:
        relatar(f"Log desta execução: {arquivo} (visível também no painel, aba 'Log').")
    relatar("")

    # A prévia usa a **mesma** janela que o ciclo usaria com os mesmos
    # argumentos — inclusive a dirigida do `--orcamento`. Uma prévia que olha
    # uma janela menor diria "nada a fazer" sobre um orçamento que o ciclo
    # seguinte vai atualizar, e é justamente a prévia que autoriza rodá-lo.
    #
    # `meses` existe para a pergunta que a janela sob demanda criou: "o que
    # aconteceria se eu pedisse 13 meses?". Sem ele, a única forma de descobrir
    # era **armar** o pedido — e aí o ciclo automático já poderia disparar antes
    # de alguém ler a resposta, escrevendo em produção o que ninguém conferiu.
    # Aqui a janela entra por parâmetro e nada é gravado: a prévia continua
    # sendo prévia.
    if meses_pedidos is not None:
        meses = jn.validar_meses(
            meses_pedidos, padrao=settings.meses_de_janela, maximo=settings.janela_maxima
        )
    elif orcamento:
        meses = settings.meses_de_janela_dirigida
    else:
        meses = settings.meses_de_janela
    corte = janela_padrao(meses=meses)
    # O corte do PEDIDO é o da janela padrão, não o da janela ensaiada: é o que
    # o ciclo vai aplicar, e uma prévia que prometesse pedidos fora dele mentiria
    # justamente onde ela existe para não mentir.
    corte_de_pedido = janela_padrao(meses=settings.meses_de_janela)
    if meses_pedidos is not None:
        relatar(
            f"Janela de ENSAIO: {meses} meses (desde {corte.isoformat()}). "
            f"Nada foi armado — o ciclo segue na janela em vigor."
        )
        relatar("")
    coletadas: list[LinhaDePrevisao] = []
    com_acao = 0
    total = 0
    grupos_no_de_para = 0
    try:
        with ServiceLayerClient(
            settings.service_layer,
            production_company_db=settings.production_company_db,
            block_production_writes=settings.block_production_writes,
        ) as cliente:
            documentos = RepositorioDocumentosVendaServiceLayer(cliente)
            grupos = RepositorioGrupoProdutosServiceLayer(cliente)
            wbc = RepositorioOrcamentosWbcSql.a_partir_de(settings.wbc_sql)
            de_para = grupos.carregar()
            grupos_no_de_para = len(de_para)
            relatar(f"De-para de grupos: {grupos_no_de_para} grupo(s) cadastrado(s).")

            # Mesma fonte do ciclo: a prévia tem de mostrar o que o ciclo
            # faria, e não uma amostra colhida de outro jeito.
            repositorio_hana = RepositorioOportunidadesHana(
                settings.hana, company_db=settings.service_layer.company_db
            )
            try:
                pendentes = repositorio_hana.pendentes_de_integracao(
                    desde=corte, orcamento=orcamento
                )
            finally:
                repositorio_hana.close()
            # The months actually read, and why — it used to print the standard window's
            # months even when the rehearsal or the directed window had read more.
            if meses_pedidos is not None:
                origem = "ensaio"
            elif orcamento:
                origem = f"dirigida, só o orçamento {orcamento}"
            else:
                origem = "padrão"
            relatar(
                f"{len(pendentes)} oportunidade(s) na janela "
                f"(OpenDate >= {corte.isoformat()}, {meses} meses — {origem}; "
                f"teto de escrita por ciclo: {settings.limite_de_escrita_por_ciclo})."
            )
            # Situações em lote, como no ciclo: sem isso a prévia levaria
            # minutos consultando o WBC um orçamento por vez.
            situacoes = wbc.situacoes_atuais([p["U_ORCNUM_WBC"] for p in pendentes])
            relatar("")

            for oportunidade in pendentes:
                total += 1
                orcnum = str(oportunidade.get("U_ORCNUM_WBC") or "").strip()
                chave = oportunidade.get(CAMPO_CHAVE)
                cliente_nome = str(oportunidade.get("CardName") or "")
                # Os dois casos abaixo não geram ação, mas continuam visíveis
                # com o filtro: são problemas de dado, e esconder problema é o
                # oposto do que a prévia serve para fazer.
                if not orcnum:
                    problema = "sem U_ORCNUM_WBC — seria ignorada."
                    alertar(f"{ERRO}Oportunidade {chave}: {problema}")
                    coletadas.append(
                        LinhaDePrevisao(oportunidade=_inteiro(chave), problema=problema)
                    )
                    continue

                situacao = situacoes.get(orcnum)
                if situacao is None:
                    problema = "não encontrado no WBC — viraria erro no ciclo."
                    alertar(f"{ERRO}Orçamento {orcnum}: {problema}")
                    coletadas.append(
                        LinhaDePrevisao(
                            orcnum=orcnum,
                            oportunidade=_inteiro(chave),
                            cliente=cliente_nome,
                            parceiro=str(oportunidade.get("CardCode") or ""),
                            problema=problema,
                        )
                    )
                    continue

                # Os MESMOS dois passos e a MESMA porta do ciclo
                # (`processar.decidir_pela_situacao` / `precisa_do_orcamento` /
                # `decidir_pelo_orcamento`): só carrega o orçamento inteiro (279 ms)
                # quando há ação.
                estado, decisao = decidir_pela_situacao(
                    orcnum, situacao, oportunidade, documentos, corte_de_pedido=corte_de_pedido
                )
                acoes = tuple(a.value for a in decisao.acoes)

                valor = None
                resumo_das_linhas: tuple[str, ...] = ()
                avisos: tuple[str, ...] = ()
                if precisa_do_orcamento(decisao):
                    orc = wbc.buscar_orcamento(orcnum)
                    if orc is None:
                        problema = "não encontrado no WBC — viraria erro no ciclo."
                        alertar(f"{ERRO}Orçamento {orcnum}: {problema}")
                        coletadas.append(
                            LinhaDePrevisao(
                                orcnum=orcnum,
                                oportunidade=_inteiro(chave),
                                cliente=cliente_nome,
                                problema=problema,
                            )
                        )
                        continue
                    estado, decisao = decidir_pelo_orcamento(
                        orc, oportunidade, documentos, corte_de_pedido=corte_de_pedido
                    )
                    acoes = tuple(a.value for a in decisao.acoes)
                    if any(a in ACOES_DE_DOCUMENTO for a in acoes):
                        # A prévia mostra as linhas do documento que a decisão
                        # vai tocar: os dois têm os mesmos item/quantidade/preço,
                        # mas exibir o certo evita a impressão de que são
                        # intercambiáveis.
                        resolver = (
                            linhas_do_pedido
                            if any("pedido" in acao for acao in acoes)
                            else linhas_da_cotacao
                        )
                        linhas = resolver(orc, de_para)
                        resumo_das_linhas = tuple(
                            f"{linha['ItemCode']} x{linha['Quantity']:g} "
                            f"= {linha['LineTotal']:.2f}"
                            for linha in linhas.linhas
                        )
                        avisos = tuple(linhas.avisos)
                        # A prévia mostra o que o ciclo faria, e o ciclo não
                        # envia documento sem valor — o SAP recusa com -5002.
                        # Reusa o mesmo cálculo da produção, para as duas não
                        # divergirem. `total` é o contador do laço — nome
                        # distinto de propósito, porque reusá-lo aqui zerou o
                        # resumo uma vez.
                        valor = total_do_payload({"DocumentLines": list(linhas.linhas)})

                # "Resultaria em escrita" pela decisão FINAL — a do orçamento inteiro
                # pode perder ações (orçamento sem item), e contar pela prévia
                # anunciava escrita que o ciclo não faria.
                escreve = any(a in ACOES_QUE_ESCREVEM for a in acoes)
                if escreve:
                    com_acao += 1

                documentos_no_sap = oportunidade.get("documentos") or {}
                registro = LinhaDePrevisao(
                    orcnum=orcnum,
                    oportunidade=_inteiro(chave),
                    cliente=cliente_nome,
                    parceiro=estado.parceiro_atual,
                    sitcode_wbc=estado.sitcode_wbc,
                    sitcode_sap=estado.sitcode_sap,
                    revisao_wbc=estado.revisao_wbc,
                    revisao_cotacao=estado.revisao_cotacao_sap,
                    revisao_pedido=estado.revisao_pedido_sap,
                    status_oportunidade=STATUS_OPORTUNIDADE.get(
                        estado.status_oportunidade.strip().upper(),
                        estado.status_oportunidade,
                    ),
                    cotacao_docnum=_docnum(documentos_no_sap.get("cotacao")),
                    pedido_docnum=_docnum(documentos_no_sap.get("pedido")),
                    parceiro_corrigido=estado.parceiro_novo,
                    parceiro_do_pedido_no_sap=estado.parceiro_pedido_sap,
                    troca_de_parceiro=estado.troca_de_parceiro_pendente,
                    regra=decisao.regra,
                    motivo=" ".join(decisao.motivos),
                    acoes=acoes,
                    valor_do_documento=valor,
                    linhas_do_documento=resumo_das_linhas,
                    avisos=avisos,
                )
                coletadas.append(registro)

                if not escreve and apenas_com_acao:
                    continue

                marca = AVISO if escreve else OK
                relatar(
                    f"{marca}{orcnum}  sit WBC={estado.sitcode_wbc} "
                    f"sit SAP='{estado.sitcode_sap}' rev='{estado.revisao_wbc}' "
                    f"cotacao={'S' if estado.tem_cotacao else 'N'} "
                    f"pedido={'S' if estado.tem_pedido else 'N'}"
                )
                relatar(f"       regra: {decisao.regra}")
                relatar(f"       ações: {', '.join(acoes) if acoes else '(nenhuma)'}")

                if estado.orcamento_sem_itens:
                    # A decisão já saiu sem as ações de documento (ver
                    # `sitcode._sem_itens`), então não há linha para mostrar —
                    # mas o operador precisa ler POR QUE este orçamento não vai
                    # virar nada, senão ele some da prévia sem explicação.
                    alertar(
                        f"{AVISO}       documento NÃO seria criado: o orçamento não tem "
                        f"item no WBC — o SAP recusa documento sem valor."
                    )
                elif resumo_das_linhas or registro.toca_documento:
                    relatar(f"       linhas: {', '.join(resumo_das_linhas) or '(nenhuma)'}")
                    if registro.sem_valor:
                        alertar(
                            f"{AVISO}       documento NÃO seria criado: total "
                            f"{valor} — o SAP recusa documento sem valor."
                        )
                for aviso in avisos:
                    alertar(f"{AVISO}       {aviso}")
    except Exception as exc:  # noqa: BLE001 — a prévia relata, não derruba
        logging.getLogger("wbcpython.pendentes").error(f"{ERRO}{type(exc).__name__}: {exc}")
        return 1

    if exportar:
        previsao = Previsao(
            gerado_em=datetime.now(UTC),
            company_db=settings.service_layer.company_db,
            corte=corte.isoformat(),
            meses_de_janela=settings.meses_de_janela,
            limite_de_escrita=settings.limite_de_escrita_por_ciclo,
            linhas=tuple(coletadas),
            grupos_no_de_para=grupos_no_de_para,
        )
        destino = Path(exportar)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(para_json(previsao), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        relatar("")
        relatar(f"{OK}Retrato salvo em {destino} ({previsao.total} linha(s)).")
        relatar("       Abra no painel: `python -m wbcpython dashboard`, aba 'Próximo ciclo'.")

    relatar("")
    if orcamento and total == 0:
        # Terminar em "0 avaliada(s)" com código 0 fazia um nada silencioso
        # parecer sucesso: quem pediu um orçamento pelo número está esperando
        # uma resposta sobre **ele**, não um resumo vazio.
        alertar(
            f"{AVISO}Orçamento {orcamento} não encontrado na janela de {meses} meses "
            f"(desde {corte.isoformat()}). Ou não existe oportunidade para ele no "
            f"SAP, ou é mais antigo que a janela — ajuste MESES_DE_JANELA_DIRIGIDA."
        )
    relatar(f"Resumo: {total} avaliada(s); {com_acao} resultaria(m) em escrita no SAP.")
    if apenas_com_acao:
        relatar("(--com-acao: as demais foram avaliadas, apenas não foram listadas.)")
    if com_acao and settings.targets_production:
        # O aviso tem de dizer a verdade sobre a trava, e não repetir o texto de
        # quando ela existia: um alerta que promete uma proteção desligada é pior
        # do que alerta nenhum — quem lê conclui que o ciclo é ensaio.
        if settings.block_production_writes:
            alertar(
                f"{AVISO}O alvo é PRODUÇÃO ({settings.service_layer.company_db}), mas a "
                f"trava de escrita está ATIVA: o ciclo não gravaria nada."
            )
        else:
            alertar(
                f"{AVISO}O alvo é PRODUÇÃO ({settings.service_layer.company_db}) e a trava "
                f"de escrita está DESATIVADA: um ciclo agora faria estas "
                f"{com_acao} escrita(s) de verdade."
            )
            alertar(f"{AVISO}Cancelamento de cotação e criação de pedido não se desfazem.")
    return 0


def _cmd_datas_de_abertura(settings: Settings) -> int:
    """Fill in `data_abertura` on the old tracking rows.

    Without this, the painel's window cut has no effect on the history: rows saved before the column
    existed have a null date, and a null date passes the filter on purpose (disappearing for lack of
    data that was never recorded would be worse than showing up). The ones that already left the
    window will never be checked by the cycle again, so they would stay null forever.

    Reads `OpenDate` from HANA — with no window filter, because the target is exactly whoever is
    outside it — and writes **only** to the tracking database. Nothing is touched in the SAP or the
    WBC. Running it twice makes no difference.
    """
    from wbcpython.infrastructure.hana.oportunidades import RepositorioOportunidadesHana
    from wbcpython.tracking import RepositorioTracking

    _preparar_log(settings)
    relatar = logging.getLogger("wbcpython.acompanhamento").info

    relatar(settings.describe_environment())
    relatar("")
    relatar("Preenche datas no acompanhamento. O SAP e o WBC são apenas lidos.")

    tracking = RepositorioTracking.a_partir_da_url(settings.tracking.db_url.get_secret_value())
    sem_data = [r.orcnum for r in tracking.listar(limite=1_000_000) if r.data_abertura is None]
    if not sem_data:
        relatar(f"{OK}Nada a preencher: todas as linhas já têm data de abertura.")
        return 0

    relatar(f"{len(sem_data)} linha(s) sem data de abertura.")
    repositorio = RepositorioOportunidadesHana(
        settings.hana, company_db=settings.service_layer.company_db
    )
    try:
        datas = repositorio.datas_de_abertura(sem_data)
    except Exception as exc:  # noqa: BLE001 — relata, não derruba
        logging.getLogger("wbcpython.acompanhamento").error(f"{ERRO}{type(exc).__name__}: {exc}")
        return 1
    finally:
        repositorio.close()

    preenchidas = tracking.preencher_datas_de_abertura(datas)
    faltando = len(sem_data) - preenchidas
    relatar(f"{OK}{preenchidas} linha(s) preenchida(s).")
    if faltando:
        # Orçamento que existe no acompanhamento e não na company atual: veio de
        # outro ambiente, ou a oportunidade foi removida. Fica sem data, e
        # continua visível — não é um erro, é uma informação que não existe.
        relatar(
            f"       {faltando} sem correspondência em {settings.service_layer.company_db} "
            "— seguem sem data e continuam visíveis no painel."
        )
    return 0


def _cmd_faxina(settings: Settings, *, dias: int | None) -> int:
    """Delete the decision events older than the retention. Actions and errors stay.

    The worker does this by itself once a day; the command exists to run by hand (after importing an
    old database, for example) and for whoever turned the retention off in the `.env` and wants to
    delete at a chosen moment.
    """
    from wbcpython.tracking import RepositorioTracking

    _preparar_log(settings)
    relatar = logging.getLogger("wbcpython.acompanhamento").info
    prazo = dias if dias is not None else settings.eventos_retencao_dias
    if prazo <= 0:
        relatar(f"{AVISO}Retenção desligada (EVENTOS_RETENCAO_DIAS=0): nada apagado.")
        return 0
    tracking = RepositorioTracking.a_partir_da_url(settings.tracking.db_url.get_secret_value())
    apagados = tracking.faxina_de_eventos(dias=prazo)
    relatar(
        f"{OK}{apagados} evento(s) de decisão com mais de {prazo} dia(s) apagado(s). "
        f"Ações, erros e reprocessamentos ficam."
    )
    return 0


def _cmd_janela(
    settings: Settings,
    *,
    armar: int | None,
    limpar: bool,
    por: str,
) -> int:
    """Show, arm or clear the extended-window request.

    The painel is the normal way — this command exists to operate the .11 when the screen is down
    (during a deploy, for example) and to check the state without opening the browser.
    """
    from wbcpython.domain import janela as jn
    from wbcpython.tracking import RepositorioTracking

    _preparar_log(settings)
    relatar = logging.getLogger("wbcpython.acompanhamento").info
    alertar = logging.getLogger("wbcpython.acompanhamento").warning
    tracking = RepositorioTracking.a_partir_da_url(settings.tracking.db_url.get_secret_value())

    if limpar:
        tracking.limpar_janela(motivo="Limpada pela linha de comando.")
        relatar(f"{OK}Janela de volta ao padrão de {settings.meses_de_janela} meses.")
        return 0

    if armar is not None:
        try:
            meses = jn.validar_meses(
                armar, padrao=settings.meses_de_janela, maximo=settings.janela_maxima
            )
        except ValueError as exc:
            alertar(f"{AVISO}{exc}")
            return 2
        teto = jn.teto_de_escrita(
            meses,
            padrao=settings.meses_de_janela,
            base=settings.limite_de_escrita_por_ciclo,
            absoluto=settings.teto_absoluto_de_escrita,
        )
        tracking.armar_janela(meses, por=por)
        relatar(
            f"{OK}Janela de {meses} meses armada para a próxima passada do ciclo "
            f"(teto de {teto} escrita(s)). Depois dela, volta a "
            f"{settings.meses_de_janela} meses sozinha."
        )
        return 0

    pedido = tracking.janela_pedida()
    if pedido.estado is jn.EstadoDaJanela.OCIOSO:
        relatar(f"Janela no padrão: {settings.meses_de_janela} meses.")
        if pedido.detalhe:
            relatar(f"Último pedido: {pedido.detalhe}")
        return 0
    if pedido.estado is jn.EstadoDaJanela.ARMADO:
        relatar(
            f"Janela de {pedido.meses} meses ARMADA para a próxima passada "
            f"(pedida por {pedido.pedido_por})."
        )
        return 0
    relatar(
        f"Janela de {pedido.meses} meses AGUARDANDO resposta: {pedido.faltaram} "
        f"oportunidade(s) ficaram de fora. {pedido.detalhe}"
    )
    relatar(
        "Os ciclos automáticos seguem no padrão. Responda no painel, ou rearme "
        "com `--armar`; sem resposta, o pedido vence sozinho."
    )
    return 0


def _cmd_pesos(
    settings: Settings, *, pedido: int | None, orcamento: str | None, simular: bool
) -> int:
    """Recompute the `Weight1` of the lines of an already created order.

    Serves the orders that were born before the weight rule existed, and the case where the WBC tree
    was corrected after the order was created.

    It does not redo the order: it writes **only** the weight, line by line, matching by the
    `U_INO_ORCITM` the integration already recorded on each one. Price, item, text and warehouse stay
    exactly as they were — see `atualizar_pesos`.

    `--simular` shows what would change without writing anything, and is the recommended way to look
    before applying.
    """
    from wbcpython.infrastructure.service_layer.client import ServiceLayerClient
    from wbcpython.infrastructure.service_layer.documentos import (
        UDF_ORCAMENTO,
        RepositorioDocumentosVendaServiceLayer,
        TipoDocumento,
    )
    from wbcpython.infrastructure.wbc_sql.repository import RepositorioOrcamentosWbcSql

    _preparar_log(settings)
    log = logging.getLogger("wbcpython.pesos")
    relatar, alertar = log.info, log.warning

    relatar(settings.describe_environment())
    relatar("")
    relatar(
        "Peso da linha = nível 1 da árvore do WBC + 10%, com 2 casas "
        "(sem truncar — regra de 29/09/2026)."
    )
    if simular:
        relatar("")
        relatar("Simulação: nada será gravado no SAP.")
    relatar("")

    try:
        with ServiceLayerClient(
            settings.service_layer,
            production_company_db=settings.production_company_db,
            block_production_writes=settings.block_production_writes,
        ) as cliente:
            documentos = RepositorioDocumentosVendaServiceLayer(cliente)
            wbc = RepositorioOrcamentosWbcSql.a_partir_de(settings.wbc_sql)

            if pedido is not None:
                doc = documentos.buscar_por_docnum(TipoDocumento.PEDIDO, pedido)
                alvo = f"pedido {pedido}"
            else:
                doc = documentos.buscar(TipoDocumento.PEDIDO, str(orcamento))
                alvo = f"orçamento {orcamento}"

            if doc is None:
                alertar(f"{ERRO}Nenhum pedido encontrado para o {alvo}.")
                return 1

            orcnum = str(doc.get(UDF_ORCAMENTO) or "").strip()
            doc_entry = int(doc["DocEntry"])
            relatar(
                f"Pedido DocNum={doc.get('DocNum')} DocEntry={doc_entry} "
                f"orçamento={orcnum or '(sem vínculo)'} "
                f"cancelado={'S' if doc.get('Cancelled') == 'tYES' else 'N'}"
            )
            if not orcnum:
                # Sem o vínculo não há como saber de qual orçamento vem o peso.
                alertar(f"{ERRO}O pedido não tem {UDF_ORCAMENTO} — nada a fazer.")
                return 1

            pesos = wbc.pesos_por_item(orcnum)
            if not pesos:
                alertar(
                    f"{AVISO}O orçamento {orcnum} não tem árvore de produtos (nível 1) "
                    "no WBC — não há peso a gravar."
                )
                return 0

            mudancas, iguais, sem_peso = _pesos_das_linhas(
                doc, pesos, relatar, alertar
            )

            if not mudancas:
                relatar("")
                relatar(f"{OK}Nada a alterar: {iguais} linha(s) já com o peso correto.")
                return 0

            relatar("")
            if simular:
                relatar(
                    f"{AVISO}{len(mudancas)} linha(s) mudariam. Rode sem --simular para gravar."
                )
                return 0

            documentos.atualizar_pesos(TipoDocumento.PEDIDO, doc_entry, mudancas)
            relatar(f"{OK}{len(mudancas)} linha(s) atualizada(s) no pedido {doc.get('DocNum')}.")
            if sem_peso:
                relatar(f"       {sem_peso} linha(s) sem peso na árvore ficaram como estavam.")
            return 0
    except Exception as exc:  # noqa: BLE001 — o comando relata, não derruba
        log.error(f"{ERRO}{type(exc).__name__}: {exc}")
        return 1


def _pesos_das_linhas(
    doc: dict[str, Any],
    pesos: dict[int, Decimal],
    relatar: Any,
    alertar: Any,
) -> tuple[dict[int, float], int, int]:
    """Compare the weight of each SAP line with what the rule would produce.

    Returns `(changes by LineNum, lines already correct, lines without weight)`.

    Matching is by the `U_INO_ORCITM` recorded on the line — not by order. One line more or less in
    the document must not shift the weight of all the others, which is what comparing by position
    would do.

    The calculation is the creation one (`domain.linhas.peso_da_linha`, the same function) and cannot
    diverge from it: if it did, running this command right after creating the order would report a
    change on every line.
    """
    from wbcpython.domain.linhas import peso_da_linha

    mudancas: dict[int, float] = {}
    iguais = sem_peso = 0

    for linha in doc.get("DocumentLines") or ():
        line_num = int(linha.get("LineNum") or 0)
        orcitm = _inteiro(linha.get("U_INO_ORCITM"))
        atual = float(linha.get("Weight1") or 0)
        peso = pesos.get(orcitm) if orcitm is not None else None

        if peso is None:
            sem_peso += 1
            alertar(
                f"{AVISO}Linha {line_num} (ORCITM {orcitm}): sem peso na árvore — "
                f"mantido {atual:g}."
            )
            continue

        # Mesma conta da criação: árvore + 10% da linha inteira, sem dividir pela
        # quantidade.
        quantidade = Decimal(str(linha.get("Quantity") or 1))
        calculado = peso_da_linha(peso)
        novo = float(calculado) if calculado is not None else 0.0
        if novo <= 0:
            sem_peso += 1
            alertar(
                f"{AVISO}Linha {line_num} (ORCITM {orcitm}): peso zero na árvore — "
                f"mantido {atual:g}."
            )
            continue
        if abs(novo - atual) < 0.0005:
            iguais += 1
            continue

        mudancas[line_num] = novo
        relatar(
            f"{AVISO}Linha {line_num} (ORCITM {orcitm}, qtd {quantidade:g}): "
            f"{atual:g} -> {novo:g} kg"
        )

    return mudancas, iguais, sem_peso


def _cmd_pesos_reescalados(settings: Settings, *, dias: int, pedido: int | None) -> int:
    """What the worker's rule says about the weights the SAP rescaled — read only.

    Never writes: the past is not altered (Marcelo, 05/10/2026); only the worker undoes a
    rescale, and only from `GRAVA_A_PARTIR_DE` on. `--pedido` looks at one order whatever the
    date, says why each line is left alone, and reads the order from the Service Layer (a GET,
    with production writes blocked in the client itself) to check the two things the worker's
    write relies on: an ETag for `If-Match`, and the SL showing the same lines as HANA.
    """
    from datetime import date, datetime, timedelta

    from wbcpython.application.pesos_reescalados import conferir, mesma_linha
    from wbcpython.infrastructure.hana.pesos_reescalados import RepositorioPesosReescaladosHana
    from wbcpython.infrastructure.service_layer.client import ServiceLayerClient
    from wbcpython.infrastructure.service_layer.documentos import (
        ETAG_FIXA,
        RepositorioDocumentosVendaServiceLayer,
        TipoDocumento,
    )

    _preparar_log(settings)
    log = logging.getLogger("wbcpython.pesos")
    log.info(settings.describe_environment())
    log.info("Só leitura: nada é gravado no SAP.")
    try:
        repositorio = RepositorioPesosReescaladosHana.de_settings(settings)
    except ValueError as exc:
        log.error(f"{ERRO}{exc}")
        return 1
    try:
        linhas = repositorio.linhas(
            desde=None if pedido else date.today() - timedelta(days=dias), pedido=pedido
        )
        log.info(f"{len(linhas)} linha(s) aberta(s) de pedido(s) da integração com alteração de pessoa.")
        resumo = conferir(linhas, agora=datetime.now(), detalhar=pedido is not None)
        log.info(resumo.texto)
        if pedido and linhas:
            with ServiceLayerClient(
                settings.service_layer,
                production_company_db=settings.production_company_db,
                block_production_writes=True,
            ) as cliente:
                etag, no_sap = RepositorioDocumentosVendaServiceLayer(cliente).estado_das_linhas(
                    TipoDocumento.PEDIDO, linhas[0].doc_entry
                )
            if not etag:
                situacao = "AUSENTE — a gravação do worker iria sem If-Match."
            elif etag == ETAG_FIXA:
                situacao = "FIXA (a mesma para todo pedido) — o If-Match não protege; vale a releitura das linhas."
            else:
                situacao = f"presente ({etag})."
            log.info(f"Service Layer: ETag {situacao}")
            for linha in linhas:
                bate = mesma_linha(no_sap.get(linha.line_num), linha.atual)
                log.info(
                    f"  linha {linha.line_num}: SL {no_sap.get(linha.line_num)} × HANA "
                    f"({linha.atual.quantidade:g}, {linha.atual.peso:g}) — "
                    + ("bate." if bate else "NÃO bate: o worker nunca gravaria esta linha.")
                )
        return 0
    except Exception as exc:  # noqa: BLE001 — the command reports, never crashes
        log.error(f"{ERRO}{type(exc).__name__}: {exc}")
        return 1
    finally:
        repositorio.close()


def _preparar_log(settings: Settings, *, rotacionar: bool = False) -> Path | None:
    """Turn on the screen and the file for this command.

    It lives here, and not in each command, so that "what shows on screen" and "what the painel
    reads" are never configured in two different ways.

    ``rotacionar`` only for the continuous worker — see `logs.configurar`.
    """
    from wbcpython import logs

    return logs.configurar(
        nivel=settings.log_level, arquivo=settings.caminho_do_log, rotacionar=rotacionar
    )


def _inteiro(valor: object) -> int | None:
    try:
        return int(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _docnum(documento: object) -> int | None:
    if not isinstance(documento, dict):
        return None
    return _inteiro(documento.get("DocNum"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wbcpython",
        description="Integração WBC x SAP Business One via Service Layer.",
    )
    parser.add_argument("--version", action="version", version=f"WBCPython {__version__}")
    sub = parser.add_subparsers(dest="comando")

    sub.add_parser("env", help="mostra o ambiente atual (não acessa rede)")
    sub.add_parser("doctor", help="diagnostica a instalação (não acessa rede)")
    sub.add_parser("check-sap", help="testa a conexão com o Service Layer (somente leitura)")
    sub.add_parser("check-hana", help="testa o HANA e diz onde as views existem (somente leitura)")
    p_worker = sub.add_parser("worker", help="inicia o worker de integração (contínuo)")
    p_worker.add_argument("--orcamento", help="processa apenas este orçamento")

    p_prev = sub.add_parser(
        "pendentes",
        help="mostra o que um ciclo faria, sem executar nada (somente leitura)",
    )
    p_prev.add_argument("--orcamento", help="avalia apenas este orçamento")
    p_prev.add_argument(
        "--com-acao",
        action="store_true",
        help="lista apenas os que resultariam em escrita no SAP (avalia todos mesmo assim)",
    )
    p_prev.add_argument(
        "--exportar",
        metavar="ARQUIVO.json",
        help="grava o retrato em JSON para a aba 'Próximo ciclo' do painel ler",
    )
    p_prev.add_argument(
        "--meses",
        type=int,
        metavar="N",
        help=(
            "ensaia com esta janela em vez da que está em vigor — para ver o que "
            "uma janela maior faria ANTES de armá-la. Não arma nada."
        ),
    )

    p_ciclo = sub.add_parser("ciclo", help="executa um único ciclo de integração e sai")
    p_ciclo.add_argument("--orcamento", help="processa apenas este orçamento")
    p_ciclo.add_argument(
        "--simular",
        action="store_true",
        help=(
            "ensaio: decide tudo e preenche o painel, sem escrever nada no SAP. "
            "O teto de escrita não se aplica, então a janela inteira é avaliada"
        ),
    )
    p_ciclo.add_argument(
        "--cancelados",
        action="store_true",
        help=f"processa apenas os orçamentos cancelados no WBC (SitCode {SITCODE_CANCELADO})",
    )
    p_painel = sub.add_parser("dashboard", help="inicia o painel de acompanhamento")
    p_painel.add_argument(
        "--host",
        default=None,
        help=(
            "endereço de escuta, só para esta execução (o padrão vem de "
            "PAINEL_HOST no .env, e é 127.0.0.1 — só a própria máquina). Use "
            "0.0.0.0 para expor na rede interna, ciente de que o painel não "
            "tem autenticação"
        ),
    )
    p_painel.add_argument(
        "--porta", type=int, default=None, help="porta (padrão: PAINEL_PORTA, ou 8079)"
    )
    p_pesos = sub.add_parser(
        "pesos",
        help="recalcula o peso (Weight1) das linhas de um pedido já criado",
    )
    alvo = p_pesos.add_mutually_exclusive_group(required=True)
    alvo.add_argument("--pedido", type=int, metavar="DOCNUM", help="número do pedido no SAP")
    alvo.add_argument("--orcamento", help="orçamento do WBC (usa o pedido vigente dele)")
    p_pesos.add_argument(
        "--simular",
        action="store_true",
        help="mostra o que mudaria e não grava nada",
    )

    p_reescalados = sub.add_parser(
        "pesos-reescalados",
        help="mostra os pesos que o SAP multiplicou e o que a integração faria (só leitura)",
    )
    p_reescalados.add_argument(
        "--dias", type=int, default=30, metavar="N",
        help="pedidos que uma pessoa salvou nos últimos N dias (padrão: 30)",
    )
    p_reescalados.add_argument(
        "--pedido", type=int, metavar="DOCNUM", help="só este pedido, de qualquer data"
    )

    sub.add_parser(
        "datas-de-abertura",
        help="preenche a data de abertura nas linhas antigas do acompanhamento",
    )
    p_faxina = sub.add_parser(
        "faxina",
        help="apaga eventos de decisão mais velhos que a retenção (ações e erros ficam)",
    )
    p_faxina.add_argument(
        "--dias",
        type=int,
        metavar="N",
        help="retenção em dias (padrão: EVENTOS_RETENCAO_DIAS do .env)",
    )

    p_janela = sub.add_parser(
        "janela",
        help="mostra, arma ou limpa o pedido de janela estendida do próximo ciclo",
    )
    p_janela.add_argument(
        "--armar",
        type=int,
        metavar="MESES",
        help="arma a janela estendida para a próxima passada (volta ao padrão depois)",
    )
    p_janela.add_argument(
        "--limpar",
        action="store_true",
        help="devolve a janela ao padrão agora",
    )
    p_janela.add_argument(
        "--por",
        default="linha de comando",
        metavar="QUEM",
        help="quem está pedindo, para a auditoria (padrão: 'linha de comando')",
    )

    args = parser.parse_args(argv)

    if args.comando is None:
        parser.print_help()
        return 0

    settings = get_settings()
    if args.comando == "env":
        return _cmd_env(settings)
    if args.comando == "doctor":
        return _cmd_doctor(settings)
    if args.comando == "check-sap":
        return _cmd_check_sap(settings)
    if args.comando == "check-hana":
        return _cmd_check_hana(settings)
    if args.comando == "pendentes":
        return _cmd_pendentes(
            settings,
            orcamento=getattr(args, "orcamento", None),
            apenas_com_acao=getattr(args, "com_acao", False),
            exportar=getattr(args, "exportar", None),
            meses_pedidos=getattr(args, "meses", None),
        )
    if args.comando == "dashboard":
        return _cmd_dashboard(
            settings,
            host=getattr(args, "host", None),
            porta=getattr(args, "porta", None),
        )
    if args.comando == "datas-de-abertura":
        return _cmd_datas_de_abertura(settings)
    if args.comando == "faxina":
        return _cmd_faxina(settings, dias=getattr(args, "dias", None))
    if args.comando == "janela":
        return _cmd_janela(
            settings,
            armar=getattr(args, "armar", None),
            limpar=getattr(args, "limpar", False),
            por=getattr(args, "por", "linha de comando"),
        )
    if args.comando == "pesos":
        return _cmd_pesos(
            settings,
            pedido=getattr(args, "pedido", None),
            orcamento=getattr(args, "orcamento", None),
            simular=getattr(args, "simular", False),
        )
    if args.comando == "pesos-reescalados":
        return _cmd_pesos_reescalados(settings, dias=args.dias, pedido=args.pedido)
    if args.comando in ("worker", "ciclo"):
        return _cmd_worker(
            settings,
            um_ciclo=args.comando == "ciclo",
            orcamento=getattr(args, "orcamento", None),
            apenas_sitcode=(SITCODE_CANCELADO if getattr(args, "cancelados", False) else None),
            somente_leitura=getattr(args, "simular", False),
        )

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
