"""Ponto de entrada de linha de comando do WBCPython.

Comandos disponíveis:

    wbcpython env       Mostra para qual ambiente a aplicação está apontada.
    wbcpython doctor    Diagnostica a instalação: configuração, dependências
                        opcionais e prontidão para cada fase do projeto.
    wbcpython check-sap Testa a conexão real com o Service Layer (usa a rede).
    wbcpython check-hana Testa o HANA e diz em qual schema as views existem.
    wbcpython pendentes Mostra o que um ciclo faria, sem executar nada.
                        (--com-acao lista só os que resultariam em escrita;
                        --exportar grava o retrato que o painel exibe.)
    wbcpython worker    Inicia o worker de integração (agendado, contínuo).
    wbcpython ciclo     Executa UM ciclo de integração e sai.
    wbcpython dashboard Inicia o painel de acompanhamento (HTML + HTMX).
    wbcpython pesos     Recalcula o peso (Weight1) das linhas de um pedido já
                        criado, a partir da árvore de produtos do WBC.
    wbcpython datas-de-abertura
                        Preenche a data de abertura nas linhas antigas do
                        acompanhamento, para o painel poder aplicar a janela.

`env` e `doctor` são verificações locais, seguras de rodar a qualquer momento e
em qualquer máquina. Só `check-sap` acessa a rede — e apenas para leitura.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import ssl
import sys
from collections.abc import Sequence
from decimal import ROUND_FLOOR, Decimal
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
    """Mostra o ambiente atual, sem expor credenciais."""
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
    """Diagnóstico local da instalação. Não acessa rede."""
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
    """Testa a conexão real com o Service Layer. Somente leitura."""
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
    """Testa a conexão com o HANA e diagnostica o schema. Somente leitura."""
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
    """Inicia o worker (contínuo) ou executa um único ciclo."""
    from wbcpython.host.worker import WorkerIntegracao

    # Mesmo log da prévia: tela e arquivo, e o arquivo é o que o painel mostra.
    # Para o worker isso é o essencial da coisa — ele roda sem ninguém olhando o
    # terminal, e sem arquivo a execução de madrugada não deixa rastro nenhum.
    arquivo = _preparar_log(settings)
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
    """Sobe o painel de acompanhamento (FastAPI + HTMX).

    Escuta em `127.0.0.1` por padrão: o painel **não tem autenticação**, e
    qualquer pessoa que o alcance pode registrar um pedido de reprocessamento
    em nome de quem quiser, além de disparar os comandos de leitura e a
    simulação de ciclo. Expor na rede é decisão consciente — `PAINEL_HOST` no
    `.env` ou `--host` — e não o que acontece por descuido.

    A linha de comando ganha da configuração: `--host` serve para um teste
    pontual sem editar o `.env`.
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
) -> int:
    """Mostra o que um ciclo faria — **sem executar nada**.

    É a rede de segurança que faltava para testar em homologação: antes de
    rodar `ciclo`, dá para ver orçamento a orçamento qual regra o domínio
    aplicaria e quais ações sairiam dela. Usa apenas leituras (GET no Service
    Layer e SELECT no WBC), não abre transação de tracking e não chama o
    processador — que é quem escreve.

    A simulação mora aqui, no comando, e não numa camada compartilhada: é uma
    ferramenta de linha de comando, e quem a lê espera encontrá-la no comando
    que a executa. O que viaja para fora é o **retrato** (`application.previsao`
    define só o formato), não o caminho de decisão.

    O relatório sai pelo `logging`, não por `print`: assim a mesma execução
    aparece na tela de quem rodou e no arquivo que o painel lê. Ver
    `wbcpython.logs`.

    `apenas_com_acao` esconde as linhas sem ação. Numa janela de ~950
    oportunidades, as com ação são algumas dezenas: a listagem completa passa de
    2.900 linhas e enterra justamente o que se quer conferir antes de rodar o
    ciclo. O resumo do rodapé continua contando **todas** as avaliadas, para que
    filtrar a exibição nunca dê a impressão de uma janela menor do que é.

    `exportar` grava o retrato em JSON — é o que a aba "Próximo ciclo" do painel
    lê. O painel não consulta SAP nem WBC por decisão de arquitetura (ver
    `dashboard/__init__`).
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
        _OrcamentoResumido,
        fonte_de_documentos,
        montar_estado,
        total_do_payload,
    )
    from wbcpython.domain.cotacao import linhas as linhas_da_cotacao
    from wbcpython.domain.pedido import linhas as linhas_do_pedido
    from wbcpython.domain.sitcode import decidir
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
    meses = settings.meses_de_janela_dirigida if orcamento else settings.meses_de_janela
    corte = janela_padrao(meses=meses)
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
            relatar(
                f"{len(pendentes)} oportunidade(s) na janela "
                f"(OpenDate >= {corte.isoformat()}, {settings.meses_de_janela} meses; "
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

                # Decide pela situação lida em lote; só carrega o orçamento
                # inteiro (279 ms) quando há ação — igual ao ciclo.
                estado = montar_estado(
                    _OrcamentoResumido(orcnum, situacao[0], situacao[1]),
                    oportunidade,
                    fonte_de_documentos(oportunidade, documentos),
                )
                decisao = decidir(estado)
                acoes = tuple(a.value for a in decisao.acoes)
                escreve = any(a in ACOES_QUE_ESCREVEM for a in acoes)

                valor = None
                resumo_das_linhas: tuple[str, ...] = ()
                avisos: tuple[str, ...] = ()
                if escreve:
                    com_acao += 1
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
                    estado = montar_estado(
                        orc, oportunidade, fonte_de_documentos(oportunidade, documentos)
                    )
                    decisao = decidir(estado)
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
                            f"= {linha['Quantity'] * linha['Price']:.2f}"
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

                if resumo_das_linhas or registro.toca_documento:
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
    """Preenche `data_abertura` nas linhas antigas do acompanhamento.

    Sem isto, o corte de janela do painel não tem efeito sobre o histórico: as
    linhas gravadas antes da coluna existir têm data nula, e uma data nula passa
    pelo filtro de propósito (sumir por falta de um dado que nunca foi gravado
    seria pior do que aparecer). As que já saíram da janela nunca mais serão
    verificadas pelo ciclo, então ficariam nulas para sempre.

    Lê o `OpenDate` no HANA — sem filtro de janela, porque o alvo é justamente
    quem está fora dela — e escreve **só** no banco de acompanhamento. Nada é
    tocado no SAP nem no WBC. Rodar duas vezes não faz diferença.
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
    """Apaga os eventos de decisão mais velhos que a retenção. Ações e erros ficam.

    O worker faz isto sozinho uma vez por dia; o comando existe para rodar à mão
    (depois de importar um banco antigo, por exemplo) e para quem desligou a
    retenção no `.env` e quer apagar num momento escolhido.
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
    """Mostra, arma ou limpa o pedido de janela estendida.

    O painel é o caminho normal — este comando existe para operar a .11 quando a
    tela não está de pé (durante um deploy, por exemplo) e para conferir o
    estado sem abrir o navegador.
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
    """Recalcula o `Weight1` das linhas de um pedido já criado.

    Serve os pedidos que nasceram antes da regra de peso existir, e o caso em
    que a árvore do WBC foi corrigida depois de o pedido ter sido criado.

    Não refaz o pedido: grava **só** o peso, linha a linha, casando pelo
    `U_INO_ORCITM` que a integração já gravou em cada uma. Preço, item, texto e
    depósito ficam exatamente como estavam — ver `atualizar_pesos`.

    `--simular` mostra o que mudaria sem escrever nada, e é a forma recomendada
    de olhar antes de aplicar.
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
        f"Peso de embarque = peso líquido do nível 1 da árvore × "
        f"{settings.fator_de_peso_de_embarque} (FATOR_PESO_EMBARQUE), truncado."
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
                doc, pesos, settings.fator_de_peso_de_embarque, relatar, alertar
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
    fator: Decimal,
    relatar: Any,
    alertar: Any,
) -> tuple[dict[int, float], int, int]:
    """Compara o peso de cada linha do SAP com o que a regra produziria.

    Devolve `(mudanças por LineNum, linhas já corretas, linhas sem peso)`.

    O casamento é pelo `U_INO_ORCITM` gravado na linha — não pela ordem. Uma
    linha a mais ou a menos no documento não pode deslocar o peso de todas as
    outras, que é o que aconteceria comparando por posição.

    A conta é a mesma da criação (`domain.linhas._peso_unitario`), e não pode
    divergir dela: se divergisse, rodar este comando logo depois de criar o
    pedido acusaria mudança em toda linha.
    """
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

        # Mesma conta da criação: unitário, com a folga de embalagem, truncado.
        quantidade = Decimal(str(linha.get("Quantity") or 1)) or Decimal(1)
        novo = float(((peso / quantidade) * fator).to_integral_value(rounding=ROUND_FLOOR))
        if novo <= 0:
            sem_peso += 1
            alertar(
                f"{AVISO}Linha {line_num} (ORCITM {orcitm}): peso trunca para zero — "
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


def _preparar_log(settings: Settings) -> Path | None:
    """Liga a tela e o arquivo para este comando.

    Vive aqui, e não em cada comando, para que "o que aparece na tela" e "o que
    o painel lê" nunca sejam configurados de dois jeitos diferentes.
    """
    from wbcpython import logs

    return logs.configurar(nivel=settings.log_level, arquivo=settings.caminho_do_log)


def _inteiro(valor: object) -> int | None:
    try:
        return int(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _docnum(documento: object) -> int | None:
    if not isinstance(documento, dict):
        return None
    return _inteiro(documento.get("DocNum"))


def _cmd_nao_implementado(nome: str, fase: str) -> int:
    print(f"O comando '{nome}' ainda não foi implementado ({fase}).")
    print("Consulte PROGRESS.md para ver o andamento e qual é a próxima fase.")
    return 2


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
        "--porta", type=int, default=None, help="porta (padrão: PAINEL_PORTA, ou 8501)"
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
