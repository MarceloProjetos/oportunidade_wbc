"""Interface de linha de comando — mesma lógica de negócio dos módulos web, sem depender
do servidor FastAPI/navegador subir.

Motivação (pedido do usuário, 14/09/2026): antes de terminar a UI web, ter uma forma de
disparar cada tarefa direto do terminal — mais rápido para testar contra um ambiente de
homologação real (Service Layer + HANA + SQL Server WBC) e para automatizar depois via
cron/agendador do SO, se um dia fizer sentido (hoje a decisão é "botões manuais": a CLI
não roda nada sozinha, só quando chamada).

Cada comando aqui chama exatamente as mesmas funções de `controleproducao/modules/<modulo>/service.py`
usadas pelas rotas web (`controleproducao/modules/<modulo>/router.py`) — não existe lógica de negócio
duplicada, só uma camada de I/O diferente (terminal em vez de HTML).

Uso:
    python -m controleproducao --help
    python -m controleproducao pedidos-wbc buscar
    python -m controleproducao pedidos-wbc processar-novos 00125431
    python -m controleproducao manutencao-op buscar 84391
    python -m controleproducao conexoes testar
"""
from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.table import Table

from controleproducao.config import get_settings
from controleproducao.core.guardas import ProductionWriteBlocked, ambiente_descrito, aviso_de_escrita
from controleproducao.core.hana_reader import HanaDirectReader
from controleproducao.core.perf import PERFIL
from controleproducao.core.service_layer_client import ServiceLayerClient
from controleproducao.core.sql_ligado import ligar
from controleproducao.core.sqlserver_client import WbcSqlServerClient
from controleproducao.modules.manutencao_op import service as manutencao_op_service
from controleproducao.modules.pedidos_wbc import queries as pedidos_wbc_queries
from controleproducao.modules.pedidos_wbc import service as pedidos_wbc_service
from controleproducao.modules.romaneio import service as romaneio_service

app = typer.Typer(help="Controle de Produção — WBC (CLI). Ver migration_guide.md para o contexto completo.")
console = Console()

pedidos_wbc_app = typer.Typer(
    help="Módulo 2 — Integração de Pedidos (WBC). Prioridade 2, implementado "
         "(ver seção 7.4 do migration_guide.md)."
)
manutencao_op_app = typer.Typer(
    help="Módulo 3 — Manutenção de OP: buscar, liberar, replanejar e encerrar "
         "(com movimentação de estoque)."
)
romaneio_app = typer.Typer(help="Módulo 4 — Romaneio. Prioridade 4, em construção.")
conexoes_app = typer.Typer(help="Diagnóstico de conectividade (Service Layer / HANA / WBC SQL Server).")
diag_app = typer.Typer(help="Diagnóstico da Service Layer — descobrir nomes reais de entidade/campo.")

app.add_typer(pedidos_wbc_app, name="pedidos-wbc")
app.add_typer(manutencao_op_app, name="manutencao-op")
app.add_typer(romaneio_app, name="romaneio")
app.add_typer(conexoes_app, name="conexoes")
app.add_typer(diag_app, name="diag")

_LOOPBACK = ("127.0.0.1", "localhost", "::1")


@app.command()
def web(
    host: str | None = typer.Option(None, help="Endereço de escuta (padrão: CP_HOST do .env)."),
    porta: int | None = typer.Option(None, help="Porta (padrão: CP_PORTA do .env)."),
) -> None:
    """Sobe a tela (uvicorn) — é o que o serviço OrcaView-ControleProducao roda.

    Log em arquivo próprio (``CP_LOG_FILE``, 5 MB × 3, formato do SIS) mais a tela, como o
    painel WBC; ``log_config=None`` para o uvicorn propagar ao root em vez de instalar o
    seu (o padrão dele põe ``propagate=False`` e o arquivo ficaria sem "startup complete").
    ``log_level="info"`` is what lets "Application startup complete" reach the file — the
    proof that the bind worked; ``access_log=False`` keeps the per-request noise out.
    """
    import uvicorn

    from controleproducao.main import app as web_app
    from wbcpython import logs

    settings = get_settings()
    host = host or settings.cp_host
    porta = porta or settings.cp_porta
    logs.configurar(nivel=settings.log_level, arquivo=settings.cp_log_file, tela=True)
    registro = logging.getLogger("controleproducao.web")
    if host not in _LOOPBACK and not settings.os_api_key.get_secret_value():
        registro.warning(
            "Tela exposta na rede (%s:%s) SEM OS_API_KEY: leitura aberta, escrita responde 503.",
            host, porta,
        )
    registro.info("Controle de Produção em http://%s:%s — %s", host, porta, ambiente_descrito(settings))
    uvicorn.run(web_app, host=host, port=porta, log_config=None, log_level="info", access_log=False)


def _run(coro):
    """Roda uma corrotina a partir de um comando Typer (que é síncrono)."""
    return asyncio.run(coro)


def _grava_tambem_em_arquivo() -> None:
    """The CLI's progress also goes to `CP_CLI_LOG_FILE` (30/09/2026), same format and rotation
    as the service log, starting with the command line that was run.

    The writes that are CLI-only — Replanejar, Reprocessar, `--force` — used to leave no trace
    but the terminal that ran them. Its own file, not the service's: two processes rotating
    one file on Windows collide. A file that cannot be opened never stops the command.
    """
    from wbcpython.logs import ARQUIVOS_MANTIDOS, FORMATO_DA_DATA, FORMATO_DO_ARQUIVO, TAMANHO_MAXIMO

    settings = get_settings()
    caminho = getattr(settings, "cp_cli_log_file", None)
    if not isinstance(caminho, str) or not caminho.strip():
        # A stand-in settings object (the CLI tests patch `get_settings`) has no real path;
        # without this check its repr became a folder in the repo.
        return
    destino = Path(caminho)
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        em_arquivo = logging.handlers.RotatingFileHandler(
            destino, maxBytes=TAMANHO_MAXIMO, backupCount=ARQUIVOS_MANTIDOS, encoding="utf-8"
        )
    except OSError as exc:
        logging.getLogger(__name__).warning("Sem log da CLI em arquivo (%s): %s", destino, exc)
        return
    em_arquivo.setFormatter(logging.Formatter(FORMATO_DO_ARQUIVO, datefmt=FORMATO_DA_DATA))
    logging.getLogger().addHandler(em_arquivo)
    logging.getLogger(__name__).info(
        "CLI: python -m controleproducao %s — %s", " ".join(sys.argv[1:]), ambiente_descrito(settings)
    )


def _ativa_perfil_e_progresso() -> None:
    """Liga a medição de tempo e joga o progresso (logger.info dos services) no console.

    Adicionado em 16/09/2026 para diagnosticar a lentidão na criação das OPs."""
    PERFIL.ativar()
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False, log_time_format="%H:%M:%S")],
        force=True,
    )
    _grava_tambem_em_arquivo()


def _ativa_perfil_e_progresso_silencioso() -> None:
    """Só o progresso no console (sem o relatório de tempo) — para comandos onde interessa
    acompanhar o que está sendo alterado, mas não medir performance."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False, log_time_format="%H:%M:%S")],
        force=True,
    )
    # Silencia o log de cada requisição HTTP do httpx, que aqui só polui: o progresso útil
    # ("OP X cancelada") já vem dos logs do próprio service.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _grava_tambem_em_arquivo()


def _imprime_relatorio_perfil() -> None:
    """Relatório de onde o tempo foi gasto.

    A leitura que interessa: se "abrir conexão" pesa mais que "executar query", o gargalo é
    o fato de cada consulta abrir uma conexão nova (autenticação, e TLS no SQL Server), e a
    correção é reusar a conexão. Se for o contrário, o problema é o peso das queries."""
    if not PERFIL.por_categoria:
        console.print("\n[yellow]Nenhuma operação medida.[/yellow]")
        return

    decorrido = PERFIL.decorrido
    console.print(f"\n[bold]Onde o tempo foi gasto[/bold] (execução total: {decorrido:.1f}s)")

    tabela = Table()
    tabela.add_column("Categoria")
    tabela.add_column("Chamadas", justify="right")
    tabela.add_column("Tempo total", justify="right")
    tabela.add_column("Média", justify="right")
    tabela.add_column("% do total", justify="right")
    for categoria, chamadas, segundos in PERFIL.resumo_categorias():
        tabela.add_row(
            categoria, f"{chamadas:,}", f"{segundos:.1f}s",
            f"{segundos / chamadas * 1000:.0f} ms", f"{segundos / decorrido * 100:.0f}%",
        )
    console.print(tabela)

    detalhe = Table(title="Operações mais caras (somando todas as execuções)")
    detalhe.add_column("Categoria")
    detalhe.add_column("Operação")
    detalhe.add_column("Chamadas", justify="right")
    detalhe.add_column("Tempo total", justify="right")
    for categoria, rotulo, chamadas, segundos in PERFIL.resumo_operacoes():
        detalhe.add_row(categoria, rotulo, f"{chamadas:,}", f"{segundos:.1f}s")
    console.print(detalhe)

    # Conclusão automática.
    #
    # ⚠️ Corrigido em 16/09/2026: a primeira versão comparava só "abrir conexão" contra
    # "executar query" e IGNORAVA a Service Layer. Na execução do orçamento 00125192 a
    # Service Layer era 52% do tempo e mesmo assim o relatório anunciou "gargalo: abrir
    # conexões" (20%) — apontou a segunda causa como se fosse a primeira. Agora a conclusão
    # sai da categoria que de fato lidera.
    categorias = PERFIL.resumo_categorias()
    lider, chamadas_lider, segundos_lider = categorias[0]
    fatia = segundos_lider / decorrido * 100

    console.print(f"\n[bold yellow]Maior fatia: {lider} — {segundos_lider:.1f}s ({fatia:.0f}% do total).[/bold yellow]")
    if "Service Layer" in lider:
        console.print(
            f"São {chamadas_lider} chamadas HTTP, {segundos_lider / chamadas_lider * 1000:.0f} ms cada em média. "
            "Cada requisição da Service Layer executa a lógica de negócio do B1 do outro lado, "
            "então é lenta por natureza — o caminho aqui é reduzir a QUANTIDADE de chamadas "
            "(agrupar atualizações da mesma entidade numa só), não acelerar cada uma."
        )
    elif "abrir conexão" in lider:
        console.print(
            "Cada consulta abre uma conexão nova e fecha em seguida. A correção é reusar a "
            "conexão durante a execução — sem mexer nas queries."
        )
    else:
        console.print(
            "Ver a tabela de operações: muitas chamadas da mesma query → evitar repeti-la "
            "(cache ou consulta em lote); poucas chamadas e muito tempo → otimizar a query."
        )


# ---------------------------------------------------------------------------
# Aviso de ambiente (21/09/2026 · trava removida em 22/09/2026 — ver core/guardas.py)
# ---------------------------------------------------------------------------
def _avisa_ambiente(operacao: str) -> bool:
    """Imprime o aviso quando o alvo é produção. Devolve True nesse caso.

    A trava de duas chaves (`--producao` + `WBC_BLOCK_PRODUCTION_WRITES`) foi removida a
    pedido do Anderson (22/09). Since 28/09 the gate is the machine's IP (``core/guardas``):
    a production write from anywhere but the .11 exits with code 2 here, before any
    confirmation prompt or network call. O valor de retorno segue existindo porque a
    pergunta de confirmação continua mais enfática em produção.
    """
    settings = get_settings()
    try:
        aviso = aviso_de_escrita(settings, operacao)
    except ProductionWriteBlocked as exc:
        console.print(f"[bold red]{escape(str(exc))}[/bold red]")
        raise typer.Exit(code=2) from exc
    if aviso:
        console.print(f"[bold red]{aviso}[/bold red]")
    return settings.is_production


def _confirma(pergunta: str, confirmar: bool, em_producao: bool) -> None:
    """Confirmação final. `--sim` pula a pergunta — em produção também, desde 22/09/2026.

    Antes, em produção, `--sim` não pulava: exigia digitar o nome da company DB. Isso saiu
    junto com a trava. O que ficou é a ênfase: em produção a pergunta vem em vermelho,
    dizendo o alvo — para quem está olhando a tela. Para um script com `--sim` não há mais
    barreira nenhuma, e essa é a consequência aceita da remoção.
    """
    if not confirmar:
        if em_producao:
            console.print(f"[bold red]{pergunta}[/bold red]")
            typer.confirm("Confirma?", abort=True)
        else:
            typer.confirm(pergunta, abort=True)


def _imprime_ops_puladas(resultado: dict) -> None:
    """Relata as OPs que não foram tentadas por dependerem de uma que falhou.

    Eram doze linhas copiadas em três comandos (`cancelar-ops`, `liberar/replanejar`,
    `encerrar`) — texto idêntico, incluindo a explicação em `[dim]`. Três cópias da mesma
    frase é o tipo de coisa que diverge no dia em que alguém melhora uma delas.
    """
    if not resultado.get("puladas"):
        return
    console.print(
        f"\n[yellow]Puladas:[/yellow] {len(resultado['puladas'])} OP(s) que dependiam "
        "de uma OP que falhou"
    )
    for op in resultado["puladas"]:
        console.print(f"  - OP {op['doc_num']} (item {op['item_code']})")
    console.print(
        "  [dim]Não foram tentadas de propósito: sem o insumo que a OP anterior "
        "produziria, falhariam por falta de estoque e a cascata de erros esconderia "
        "a causa real.[/dim]"
    )


def _imprime_ops_com_erro(resultado: dict) -> None:
    """Relata as OPs que falharam, uma por linha, com o motivo devolvido pelo serviço."""
    if not resultado.get("com_erro"):
        return
    console.print(f"[red]Falharam:[/red] {len(resultado['com_erro'])} OP(s)")
    for erro in resultado["com_erro"]:
        console.print(f"  - OP {erro['doc_num']} (item {erro['item_code']}): {erro['motivo']}")


def _falha_nao_implementado(modulo: str, comando: str) -> None:
    console.print(
        f"[yellow]O comando '{comando}' do módulo '{modulo}' ainda não foi implementado.[/yellow]\n"
        f"Ver TODOs em controleproducao/modules/{modulo.replace('-', '_')}/service.py e a seção 7 do "
        f"migration_guide.md para o que falta portar do C# original."
    )


# ---------------------------------------------------------------------------
# Módulo 2 — Pedidos WBC (portado em 15/09/2026 — ver seção 7.4 do migration_guide.md)
# ---------------------------------------------------------------------------
@pedidos_wbc_app.command("buscar")
def pedidos_wbc_buscar(
    integrados: bool = typer.Option(
        False, "--integrados",
        help="Lista os pedidos JÁ processados (U_INO_ProcessWBC='Y') em vez dos pendentes — "
             "é onde um pedido vai parar depois de processado, e de onde sai um candidato a "
             "reprocessar com --force.",
    ),
):
    """Lista os pedidos de venda passíveis de gerar OPs (equivalente ao botão 'Buscar' do
    Form2 no modo 'Pedidos Novos').

    Critérios (idênticos aos da grade do addon legado): a oportunidade está integrada ao WBC
    (`U_INO_IntegrouWBC='Y'`), o pedido está aberto (`DocStatus='O'`), marcado para integrar
    (`U_INO_Integrar='Y'`) e **ainda não processado** (`U_INO_ProcessWBC='N'`).

    ⚠️ Use o **`Nº Oportunidade`** de uma linha (`OOPR.U_ORCNUM_MASC`, ex. `00125431`) como
    argumento de `pedidos-wbc processar-novos` — NÃO o `Num Oportunidade`, que é a chave da
    Oportunidade no SAP (`OPR1.OpprId`, ex. `15024`). As duas colunas andam lado a lado e
    trocá-las devolve "sem pedido vinculado" em todas as linhas (erro real de 22/09/2026).

    Comando adicionado em 15/09/2026 (a pedido do Anderson); não existia listagem para
    pedidos-wbc antes, só os comandos que já exigem o opp_id em mãos."""

    async def _main():
        settings = get_settings()
        hana_reader = HanaDirectReader(settings)
        return await pedidos_wbc_service.buscar_pedidos_para_integrar(hana_reader, integrados)

    resultados = _run(_main())

    if not resultados:
        if integrados:
            console.print("[yellow]Nenhum pedido já processado encontrado.[/yellow]")
        else:
            console.print(
                "[yellow]Nenhum pedido pendente de processamento no momento.[/yellow]\n"
                "[dim]Lembre que um pedido sai desta lista assim que é processado (fica com "
                "U_INO_ProcessWBC='Y'). Para ver os já processados — e escolher um para "
                "reprocessar com --force — rode: pedidos-wbc buscar --integrados[/dim]"
            )
        raise typer.Exit(code=0)

    titulo = (
        "Pedidos JÁ processados (reprocessáveis com --force)"
        if integrados
        else "Pedidos passíveis de gerar OPs (modo 'Pedidos Novos')"
    )
    tabela = Table(title=titulo)
    tabela.add_column("Num Oportunidade")
    tabela.add_column("Nº Pedido")
    tabela.add_column("Cód. Cliente")
    tabela.add_column("Nome Cliente")
    tabela.add_column("Total Pedido", justify="right")
    tabela.add_column("Data Lançamento")
    tabela.add_column("Nº Oportunidade (mascarado)")
    for item in resultados:
        tabela.add_row(
            str(item.opp_id), str(item.doc_num), item.cod_cliente, item.nome_cliente,
            f"{item.total_pedido:,.2f}", item.data_lancamento, item.orc_num_masc,
        )
    console.print(tabela)
    # ⚠️ 24/09/2026: a dica mandava usar "<Num Oportunidade>" (a chave OPR1.OpprId, 1ª
    # coluna), mas os comandos recebem o nº do ORÇAMENTO WBC ("Nº Oportunidade", última
    # coluna) — com o outro número a resposta é "sem pedido vinculado". E, para os já
    # integrados, sugeria `processar-novos --force`, que recria OPs por cima das existentes;
    # o equivalente ao "Atualizar" do addon é `reprocessar-integrados`.
    if integrados:
        console.print(
            "\n[dim]Para reprocessar um destes: "
            "python -m controleproducao pedidos-wbc reprocessar-integrados <Nº Oportunidade>[/dim]"
        )
    else:
        console.print(
            "\n[dim]Para gerar as OPs de um destes: "
            "python -m controleproducao pedidos-wbc processar-novos <Nº Oportunidade>[/dim]"
        )


@pedidos_wbc_app.command("cancelar-ops")
def pedidos_wbc_cancelar_ops(
    identificador: str = typer.Argument(
        ..., help="Nº do pedido de vendas (DocNum). Com --orcamento, é o nº do orçamento WBC."
    ),
    por_orcamento: bool = typer.Option(
        False, "--orcamento", help="Interpreta o argumento como nº de orçamento WBC em vez de DocNum do pedido."
    ),
    confirmar: bool = typer.Option(
        False, "--sim", help="Pula a confirmação interativa (cuidado: cancela OPs no SAP)."
    ),
    manter_vinculos: bool = typer.Option(
        False, "--manter-vinculos",
        help="Só cancela as OPs, sem devolver o pedido ao estado 'não processado' "
             "(mantém U_INO_ProcessWBC='Y' e o U_INO_OP das linhas).",
    ),
):
    """Cancela TODAS as Ordens de Produção de um pedido de vendas e o devolve ao estado
    de "não processado".

    Verificação obrigatória antes de começar: todas as OPs do pedido precisam estar
    **Planejadas (P)** ou **Canceladas (C)**. Se existir qualquer uma em outro status —
    Liberada (R) ou Encerrada (L) — o processo **nem começa**, e nenhuma OP é alterada.
    A regra é lista branca: `L` bloqueia junto com `R`, porque uma OP encerrada já teve
    apontamento e movimentação de estoque, e cancelá-la seria pior ainda.

    Depois de cancelar, limpa os vínculos da integração: `U_INO_ProcessWBC = 'N'` no
    cabeçalho e `U_INO_OP = 0` nas linhas. Com isso o pedido volta a aparecer em
    `pedidos-wbc buscar` e pode ser reprocessado sem `--force`. Use `--manter-vinculos`
    para só cancelar as OPs, sem mexer no pedido.

    Exemplos:
        python -m controleproducao pedidos-wbc cancelar-ops 84245
        python -m controleproducao pedidos-wbc cancelar-ops 00125192 --orcamento
        python -m controleproducao pedidos-wbc cancelar-ops 84245 --manter-vinculos
    """
    # A trava é aplicada antes do levantamento: recusar depois de ler o pedido inteiro só
    # faria o usuário esperar para receber o mesmo "não".
    em_producao = _avisa_ambiente("pedidos-wbc cancelar-ops")

    async def _levantar():
        settings = get_settings()
        hana_reader = HanaDirectReader(settings)
        try:
            return await pedidos_wbc_service.levanta_ops_para_cancelamento(
                hana_reader,
                doc_num=None if por_orcamento else identificador,
                orc_num=identificador if por_orcamento else None,
            )
        finally:
            hana_reader.close()

    levantamento = _run(_levantar())
    pedido = levantamento["pedido"]

    if not pedido:
        rotulo = "orçamento" if por_orcamento else "pedido"
        console.print(f"[red]Nenhum pedido encontrado para o {rotulo} {identificador}.[/red]")
        raise typer.Exit(code=1)

    console.print(
        f"\nPedido [bold]{pedido['DocNum']}[/bold] (DocEntry {pedido['DocEntry']})"
        f" — {pedido.get('CardName') or pedido.get('CardCode') or ''}"
    )

    ops = levantamento["ops"]
    if not ops:
        console.print("[yellow]Este pedido não tem nenhuma Ordem de Produção vinculada.[/yellow]")
        raise typer.Exit(code=0)

    tabela = Table(title=f"Ordens de Produção do pedido {pedido['DocNum']}")
    tabela.add_column("OP (DocNum)")
    tabela.add_column("Item produzido")
    tabela.add_column("Planejada", justify="right")
    tabela.add_column("Status")
    for op in ops:
        nome_status = pedidos_wbc_service.STATUS_OP.get(op["status"], op["status"])
        cor = {
            "P": "[green]", "C": "[dim]",
        }.get(op["status"], "[bold red]")
        tabela.add_row(
            str(op["doc_num"]), op["item_code"], f"{op['planejada']:g}",
            f"{cor}{op['status']} — {nome_status}[/]",
        )
    console.print(tabela)

    # Barreira: qualquer OP fora de P/C impede o processo inteiro.
    bloqueantes = levantamento["bloqueantes"]
    if bloqueantes:
        console.print(
            f"\n[bold red]ERRO: o pedido tem {len(bloqueantes)} OP(s) em status que impede o "
            "cancelamento em lote.[/bold red]\nNenhuma OP foi alterada."
        )
        for op in bloqueantes:
            nome_status = pedidos_wbc_service.STATUS_OP.get(op["status"], op["status"])
            console.print(f"  - OP {op['doc_num']} (item {op['item_code']}): {op['status']} — {nome_status}")
        console.print(
            "\n[dim]Só é possível cancelar em lote quando todas as OPs estão Planejadas (P) ou "
            "Canceladas (C). Resolva as acima primeiro — uma OP Liberada pode ser replanejada, "
            "mas uma Encerrada já movimentou estoque e exige análise.[/dim]"
        )
        raise typer.Exit(code=1)

    a_cancelar = levantamento["a_cancelar"]
    ja_canceladas = levantamento["ja_canceladas"]

    if ja_canceladas:
        console.print(f"[dim]{len(ja_canceladas)} OP(s) já estão canceladas e serão ignoradas.[/dim]")

    # A limpeza dos vínculos vale mesmo quando não há OP a cancelar: o pedido pode estar
    # com `U_INO_ProcessWBC='Y'` e `U_INO_OP` preenchido de uma execução anterior, e é
    # justamente esse estado que impede o reprocessamento limpo.
    if not a_cancelar and manter_vinculos:
        console.print("[yellow]Nada a fazer: todas as OPs já estão canceladas.[/yellow]")
        raise typer.Exit(code=0)

    if a_cancelar:
        console.print(f"\n[bold]{len(a_cancelar)} OP(s) planejada(s) serão canceladas.[/bold]")
    else:
        console.print("\n[bold]Nenhuma OP a cancelar (todas já canceladas).[/bold]")
    if not manter_vinculos:
        console.print(
            "Em seguida o pedido será devolvido ao estado 'não processado' "
            "(U_INO_ProcessWBC='N' e U_INO_OP zerado nas linhas)."
        )

    _confirma("Confirma?", confirmar, em_producao)

    _ativa_perfil_e_progresso_silencioso()

    async def _executar():
        settings = get_settings()
        hana_reader = HanaDirectReader(settings)
        try:
            async with ServiceLayerClient(settings) as sl:
                resultado = await pedidos_wbc_service.cancela_ops_do_pedido(sl, a_cancelar)

                # A limpeza só acontece se TODOS os cancelamentos deram certo. Limpar os
                # vínculos com uma OP ainda planejada deixaria o pedido dizendo "nunca
                # processado" enquanto existe OP viva apontando para ele — estado pior que
                # o anterior, e difícil de perceber depois.
                if manter_vinculos:
                    resultado["limpeza"] = None
                elif resultado["com_erro"]:
                    resultado["limpeza"] = "pulada"
                else:
                    resultado["limpeza"] = await pedidos_wbc_service.limpa_vinculos_do_pedido(
                        sl, hana_reader, pedido["DocEntry"]
                    )
                return resultado
        finally:
            hana_reader.close()

    resultado = _run(_executar())

    if resultado["canceladas"]:
        console.print(f"\n[green]Canceladas com sucesso:[/green] {len(resultado['canceladas'])} OP(s)")

    _imprime_ops_puladas(resultado)
    if resultado["com_erro"]:
        console.print(f"[red]Falharam:[/red] {len(resultado['com_erro'])} OP(s)")
        for erro in resultado["com_erro"]:
            console.print(f"  - OP {erro['doc_num']} (item {erro['item_code']}): {erro['motivo']}")
        if resultado["limpeza"] == "pulada":
            console.print(
                "\n[yellow]A limpeza dos vínculos do pedido NÃO foi feita[/yellow] — há OP(s) que "
                "não puderam ser canceladas, e marcar o pedido como 'não processado' com OP viva "
                "apontando para ele seria pior. Resolva as falhas acima e rode o comando de novo."
            )
        raise typer.Exit(code=1)

    limpeza = resultado["limpeza"]
    if isinstance(limpeza, dict):
        console.print(
            f"[green]Pedido devolvido ao estado 'não processado'[/green] "
            f"(U_INO_OP zerado em {limpeza['linhas_limpas']} linha(s))."
        )
        console.print(
            f"\n[dim]O pedido {pedido['DocNum']} volta a aparecer em `pedidos-wbc buscar` e pode "
            "ser reprocessado sem --force.[/dim]"
        )
    elif limpeza is None:
        console.print(
            "\n[dim]Vínculos mantidos (--manter-vinculos): o pedido segue com "
            "U_INO_ProcessWBC='Y' e o U_INO_OP das linhas apontando para as OPs canceladas.[/dim]"
        )


@pedidos_wbc_app.command("comparar-ops")
def pedidos_wbc_comparar_ops(
    orc_num: str = typer.Argument(..., help="Nº do orçamento WBC (ex.: 00125391)."),
    schema_legado: str | None = typer.Option(
        None, "--schema-legado",
        help="Schema HANA onde o addon legado roda. Padrão: HANA_SCHEMA_LEGADO do .env "
             "(hoje SBOALTAMIRAPROD).",
    ),
    schema_novo: str | None = typer.Option(
        None, "--schema-novo",
        help="Schema HANA onde este porte roda. Padrão: SL_COMPANY_DB do .env (a company de "
             "escrita). Na .11 isso é produção — rode do notebook com SL_COMPANY_DB de homologação "
             "ou passe o schema aqui.",
    ),
    desde: str | None = typer.Option(
        None, "--desde",
        help="AAAA-MM-DD. Considera no lado novo só as OPs criadas a partir dessa data — "
             "útil para isolar a última execução quando a homologação acumulou OPs de "
             "execuções anteriores (reprocessos com --force).",
    ),
    detalhes: bool = typer.Option(False, "--detalhes", help="Mostra também os componentes (linhas) de cada OP."),
    com_canceladas: bool = typer.Option(
        False, "--com-canceladas",
        help="Inclui na comparação as OPs canceladas (Status='C'), que por padrão ficam de fora.",
    ),
):
    """Compara as OPs geradas por este porte (homologação) com as que o addon legado gerou
    (produção), e diagnostica as que faltaram.

    Só leitura — não grava nada, em nenhum dos dois schemas. OPs canceladas ficam FORA da
    comparação por padrão (várias foram canceladas manualmente em produção — é ruído
    operacional, não resultado da integração); o relatório mostra quantas foram descartadas
    e sinaliza os itens cuja única OP no legado está cancelada. Use `--com-canceladas`
    para incluí-las.

    Exige que o usuário HANA do .env tenha SELECT no schema de produção.

    Exemplos:
        python -m controleproducao pedidos-wbc comparar-ops 00120634
        python -m controleproducao pedidos-wbc comparar-ops 00120634 --detalhes
        python -m controleproducao pedidos-wbc comparar-ops 00120634 --desde 2026-09-15
    """

    settings = get_settings()
    legado = (schema_legado or settings.hana_schema_legado or "").strip()
    novo = (schema_novo or settings.hana_schema or "").strip()
    # Refused before any connection: since the package reads its schema from SL_COMPANY_DB
    # (28/09/2026), on the .11 the default is production on BOTH sides — every OP would show
    # up "in both" and the report would say "ok" about nothing.
    if novo and novo.casefold() == legado.casefold():
        console.print(
            f"[bold red]Os dois lados apontam para o mesmo schema ('{legado}'): a comparação não diz nada.[/bold red] "
            "Rode do notebook com SL_COMPANY_DB de homologação, ou passe --schema-novo / --schema-legado."
        )
        raise typer.Exit(code=2)

    async def _main():
        hana_reader = HanaDirectReader(settings)
        with WbcSqlServerClient(settings) as wbc:
            return await pedidos_wbc_service.comparar_ops(
                hana_reader, wbc, orc_num, legado, novo or None, desde, com_canceladas,
            )

    try:
        resultado = _run(_main())
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        console.print(f"[red]Erro ao comparar:[/red] {msg}")
        if "authorization" in msg.lower() or "not authorized" in msg.lower() or "insufficient" in msg.lower():
            console.print(
                "\n[yellow]Parece falta de permissão para ler o schema de produção.[/yellow] "
                "A comparação lê (nunca escreve) os dois schemas com o mesmo usuário HANA do .env; "
                "esse usuário precisa de SELECT em OWOR/WOR1/ORDR/OOPR/OPR1 do schema de produção."
            )
        raise typer.Exit(code=1)

    console.print(f"\n[bold]Orçamento {resultado['orc_num']}[/bold] — critério: {resultado['criterio']}")

    pedido = resultado["legado"].get("pedido") or resultado["novo"].get("pedido")
    if not pedido:
        console.print("[red]Nenhum pedido encontrado para esse orçamento.[/red]")
        raise typer.Exit(code=1)

    console.print(
        f"Pedido DocNum [bold]{pedido['DocNum']}[/bold] (DocEntry {pedido['DocEntry']}) | "
        f"EntregaMultipla='{pedido['EntregaMultipla']}' | ProcessWBC='{pedido['ProcessWBC']}' | "
        f"Congelado='{pedido['Congelado']}'"
    )

    ops_legado, ops_novo = resultado["legado"]["ops"], resultado["novo"]["ops"]
    console.print(f"\nOPs no legado: [bold]{len(ops_legado)}[/bold]   |   OPs no novo: [bold]{len(ops_novo)}[/bold]")

    # O que foi descartado não pode sumir em silêncio.
    if not resultado["incluir_canceladas"]:
        n_canc_leg = len(resultado["canceladas_legado"])
        n_canc_novo = len(resultado["canceladas_novo"])
        if n_canc_leg or n_canc_novo:
            console.print(
                f"[dim]Descartadas por estarem canceladas: {n_canc_leg} no legado, "
                f"{n_canc_novo} no novo (use --com-canceladas para incluí-las).[/dim]"
            )

    # Sem um dos lados não existe comparação — e o "nenhuma OP faltando" lá embaixo seria
    # verdadeiro por vacuidade, o que é pior do que não dizer nada. Avisa alto e claro.
    if not ops_legado:
        console.print(
            "\n[bold yellow]ATENÇÃO: não há OPs do lado 'legado' — não existe base de comparação.[/bold yellow]\n"
            "Este relatório mostra apenas o que o porte criou; ele NÃO valida nada.\n"
            "Causas possíveis: (a) este orçamento nunca foi processado pelo addon legado em produção; "
            "(b) o pedido correspondente em produção tem outro vínculo com o orçamento; "
            "(c) o schema informado não é o de produção."
        )
    elif not ops_novo:
        console.print(
            "\n[bold yellow]ATENÇÃO: não há OPs do lado 'novo' — o porte não gerou nada "
            "para este orçamento.[/bold yellow]\n"
            "Verifique se `pedidos-wbc processar-novos` chegou a rodar com sucesso para ele "
            "(e, se usou `--desde`, se a data não está excluindo as OPs geradas)."
        )
    else:
        # Homologação acumula OPs a cada reprocessamento com --force; se o lado novo tem
        # OPs de datas diferentes, provavelmente há sobra de execuções anteriores inflando
        # a contagem — o que faria a comparação acusar "OPs a mais" que não são reais.
        datas_novo = sorted({str(op["criada_em"])[:10] for op in ops_novo})
        if len(datas_novo) > 1:
            console.print(
                f"\n[yellow]Aviso: o lado novo tem OPs de {len(datas_novo)} datas diferentes "
                f"({', '.join(datas_novo)}) — provavelmente sobra de execuções anteriores do porte. "
                f"Para comparar só a última, use [bold]--desde {datas_novo[-1]}[/bold].[/yellow]"
            )

    def _tabela_ops(titulo: str, ops: list[dict]) -> Table:
        t = Table(title=titulo)
        for coluna in ("OP (DocNum)", "Item produzido", "Planejada", "Status", "LinhaRef", "Criada em", "Nº comp."):
            t.add_column(coluna)
        for op in ops:
            t.add_row(
                str(op["doc_num"]), op["item_code"], f"{op['planejada']:g}",
                op["status"] + (" [dim](cancelada)[/dim]" if op["status"] == "C" else ""),
                op["linha_ref"], str(op["criada_em"])[:10], str(len(op.get("linhas", []))),
            )
        return t

    # As duas tabelas saem na MESMA ordem (por item produzido / quantidade / linha), e não
    # por número da OP — a numeração difere entre ambientes. Assim dá para conferir as duas
    # lado a lado, linha por linha.
    console.print(_tabela_ops("OPs do addon legado (ordenadas por item / qtd / linha)", ops_legado))
    console.print(_tabela_ops("OPs deste porte (mesma ordenação)", ops_novo))

    # Comparação por item produzido
    itens_legado = {op["item_code"] for op in ops_legado}
    itens_novo = {op["item_code"] for op in ops_novo}

    so_cancelados = set(resultado["itens_so_cancelados_legado"])

    comp = Table(title="Comparação por item produzido")
    comp.add_column("Item")
    comp.add_column("OPs legado", justify="right")
    comp.add_column("OPs novo", justify="right")
    comp.add_column("Situação")
    for item in sorted(itens_legado | itens_novo):
        n_leg = sum(1 for op in ops_legado if op["item_code"] == item)
        n_novo = sum(1 for op in ops_novo if op["item_code"] == item)
        if n_leg and not n_novo:
            situacao = "[red]FALTOU no novo[/red]"
        elif n_novo and not n_leg:
            # Se no legado esse item só tem OP cancelada, o porte NÃO criou nada indevido:
            # o legado criou também, e a OP foi cancelada manualmente depois.
            situacao = (
                "[dim]no legado só cancelada — equivalente[/dim]"
                if item in so_cancelados
                else "[yellow]só no novo (a mais)[/yellow]"
            )
        elif n_leg == n_novo:
            situacao = "[green]ok[/green]"
        else:
            situacao = "[yellow]quantidade difere[/yellow]"
        comp.add_row(item, str(n_leg), str(n_novo), situacao)
    console.print(comp)

    if so_cancelados and not resultado["incluir_canceladas"]:
        console.print(
            f"[dim]{len(so_cancelados)} item(ns) aparecem só do lado novo porque a OP "
            "correspondente no legado está cancelada — não são OPs a mais.[/dim]"
        )

    # Conteúdo (componentes) dos itens presentes nos dois lados
    if detalhes:
        for item in sorted(itens_legado & itens_novo):
            comps_leg = {
                (l["item_code"], round(l["planejada"], 3))
                for op in ops_legado if op["item_code"] == item for l in op.get("linhas", [])
            }
            comps_novo = {
                (l["item_code"], round(l["planejada"], 3))
                for op in ops_novo if op["item_code"] == item for l in op.get("linhas", [])
            }
            if comps_leg == comps_novo:
                console.print(f"[green]✓[/green] {item}: componentes idênticos ({len(comps_leg)} linha(s))")
            else:
                console.print(f"[yellow]≠[/yellow] {item}: componentes diferem")
                for c in sorted(comps_leg - comps_novo):
                    console.print(f"    [red]só no legado:[/red] {c[0]} qtd={c[1]:g}")
                for c in sorted(comps_novo - comps_leg):
                    console.print(f"    [yellow]só no novo:[/yellow]  {c[0]} qtd={c[1]:g}")

    # Diagnóstico das faltantes
    if resultado["diagnosticos"]:
        console.print("\n[bold red]OPs que faltaram — possíveis motivos:[/bold red]")
        for diag in resultado["diagnosticos"]:
            console.print(f"\n  [bold]{diag['item_code']}[/bold]")
            for motivo in diag["motivos"]:
                console.print(f"    - {motivo}")
    elif itens_legado - itens_novo:
        console.print("\n[yellow]Há itens faltando, mas o diagnóstico não retornou motivos.[/yellow]")
    elif not ops_legado:
        console.print(
            "\n[yellow]Sem base de comparação (0 OPs no legado) — nada foi validado. "
            "Ver o aviso acima.[/yellow]"
        )
    else:
        console.print("\n[green]Nenhuma OP faltando em relação ao legado.[/green]")


@pedidos_wbc_app.command("processar-novos")
def pedidos_wbc_processar_novos(
    orc_nums: list[str] = typer.Argument(
        ..., help="Nº do ORÇAMENTO WBC dos pedidos a processar (coluna 'Nº Oportunidade' "
                  "do buscar, ex. 00125431) — não a chave da Oportunidade no SAP.",
    ),
    confirmar: bool = typer.Option(
        False, "--sim", help="Pula a confirmação interativa (use com cuidado — cria Itens/OPs/Recursos no SAP)."
    ),
    force: bool = typer.Option(
        False, "--force",
        help=(
            "Ignora a verificação de reprocessamento (pedido já com U_INO_ProcessWBC='Y' e/ou "
            "OP não cancelada já existente para o item) — checagem adicionada em 15/09/2026, "
            "não existe no addon C# original (ver seção 7.4/9 do migration_guide.md)."
        ),
    ),
    perfil: bool = typer.Option(
        False, "--perfil",
        help="Mostra o progresso durante a execução e, no fim, onde o tempo foi gasto "
             "(abrir conexão vs. executar query vs. Service Layer). Use para diagnosticar lentidão.",
    ),
):
    """Equivalente ao botão 'Processar' do Form2 no modo 'Pedidos Novos'.

    Grava de verdade: cria Itens (quando necessário), Recursos de rateio, e a cascata de
    Ordens de Produção (uma por grupo de item de nível 1, mais uma por semiacabado
    recursivamente) — é o módulo mais complexo do sistema (ver seção 4.4.1 do
    migration_guide.md). Rode `pedidos-wbc processar-novos --sim` só depois de validar
    contra um ambiente de homologação, comparando com o addon legado linha a linha.

    Por padrão, pula (sem gerar OP duplicada) pedidos já marcados como processados
    (`U_INO_ProcessWBC='Y'`) ou itens que já têm uma OP não cancelada para o mesmo pedido —
    use `--force` para ignorar essa verificação e reprocessar/recriar mesmo assim.
    """
    em_producao = _avisa_ambiente("pedidos-wbc processar-novos")
    _confirma(
        f"Vai processar {len(orc_nums)} pedido(s) {orc_nums} — isso cria Itens/Ordens de "
        "Produção/Recursos no SAP. Confirma?",
        confirmar, em_producao,
    )

    if perfil:
        _ativa_perfil_e_progresso()

    async def _main():
        settings = get_settings()
        hana_reader = HanaDirectReader(settings)
        with WbcSqlServerClient(settings) as wbc:
            async with ServiceLayerClient(settings) as sl:
                return await pedidos_wbc_service.processar_pedidos_novos(sl, wbc, hana_reader, orc_nums, force=force)

    resultado = _run(_main())

    if resultado["processados"]:
        console.print(f"[green]Processados com sucesso:[/green] {resultado['processados']}")
    if resultado["com_erro"]:
        console.print("[red]Com erro:[/red]")
        for erro in resultado["com_erro"]:
            console.print(f"  - Orçamento {erro['orc_num']}: {erro['motivo']}")
    # Um pedido pode constar em "processados" e ainda assim ter grupos que não geraram OP:
    # o relatório precisa dizer isso, senão a única pista fica no @INO_LOG.
    if resultado.get("sem_op"):
        console.print("[yellow]Grupos que NÃO geraram OP:[/yellow]")
        for falta in resultado["sem_op"]:
            console.print(
                f"  - Orçamento {falta['orc_num']} (GrpCode {falta['grp_code']}, "
                f"OrcItm {falta['orc_itm']}): {falta['motivo']}"
            )
    if resultado.get("sem_rateio"):
        console.print("[yellow]OPs criadas SEM a linha de rateio (recurso GGF_ não criado):[/yellow]")
        for falta in resultado["sem_rateio"]:
            console.print(
                f"  - Orçamento {falta['orc_num']}, item {falta['item']} "
                f"(recurso {falta['recurso']}): {falta['motivo']}"
            )
    if not resultado["processados"] and not resultado["com_erro"]:
        console.print("[yellow]Nada para processar (lista vazia).[/yellow]")

    if perfil:
        _imprime_relatorio_perfil()


@pedidos_wbc_app.command("reprocessar-integrados")
def pedidos_wbc_reprocessar_integrados(
    orc_nums: list[str] = typer.Argument(
        ..., help="Nº do ORÇAMENTO WBC dos pedidos já integrados (ex. 00125431).",
    ),
    confirmar: bool = typer.Option(
        False, "--sim", help="Pula a confirmação interativa (use com cuidado — atualiza a Oportunidade e cancela OPs)."
    ),
):
    """Equivalente ao botão 'Atualizar' do Form2 no modo 'Pedidos Integrados'.

    Grava de verdade: revincula o pedido à Oportunidade (marca "Ganha") e cancela as
    Ordens de Produção antigas ainda planejadas antes de recriar.
    """
    em_producao = _avisa_ambiente("pedidos-wbc reprocessar-integrados")
    _confirma(
        f"Vai reprocessar {len(orc_nums)} pedido(s) {orc_nums} — isso atualiza a Oportunidade "
        "e cancela OPs planejadas no SAP. Confirma?",
        confirmar, em_producao,
    )

    async def _main():
        settings = get_settings()
        hana_reader = HanaDirectReader(settings)
        with WbcSqlServerClient(settings) as wbc:
            async with ServiceLayerClient(settings) as sl:
                return await pedidos_wbc_service.reprocessar_pedidos_integrados(sl, wbc, hana_reader, orc_nums)

    resultado = _run(_main())

    if resultado["atualizados"]:
        console.print(f"[green]Reprocessados com sucesso:[/green] {resultado['atualizados']}")
    if resultado["com_erro"]:
        console.print("[red]Com erro:[/red]")
        for erro in resultado["com_erro"]:
            console.print(f"  - Orçamento {erro['orc_num']}: {erro['motivo']}")
    if not resultado["atualizados"] and not resultado["com_erro"]:
        console.print("[yellow]Nada para reprocessar (lista vazia).[/yellow]")


# ---------------------------------------------------------------------------
# Módulo 3 — Manutenção de OP
# ---------------------------------------------------------------------------
@manutencao_op_app.command("buscar")
def manutencao_op_buscar(
    doc_num: str = typer.Argument(..., help="Nº do pedido de venda (DocNum). Obrigatório, como no legado."),
    op_de: str | None = typer.Option(None, "--op-de", help="Nº da OP inicial. Sozinho, filtra por igualdade."),
    op_ate: str | None = typer.Option(
        None, "--op-ate", help="Nº da OP final (exige --op-de). Com ele, vira um intervalo."
    ),
    status_de: str | None = typer.Option(
        None, "--status-de",
        help="Status inicial: C (Cancelada), L (Encerrada), P (Planejada) ou R (Liberada). "
             "Sozinho, filtra por igualdade.",
    ),
    status_ate: str | None = typer.Option(
        None, "--status-ate",
        help="Status final (exige --status-de). ATENÇÃO: a faixa é ALFABÉTICA nos códigos "
             "(C < L < P < R), não na ordem do ciclo de vida da OP. Para todos os status, "
             "omita os dois filtros.",
    ),
    formato_json: bool = typer.Option(False, "--json", help="Saída em JSON em vez de tabela."),
):
    """Lista as OPs de um pedido — equivale à busca da tela ManutencaoOp.

    Exemplos:
        python -m controleproducao manutencao-op buscar 84245
        python -m controleproducao manutencao-op buscar 84245 --status-de P
        python -m controleproducao manutencao-op buscar 84245 --status-de C --status-ate R
        python -m controleproducao manutencao-op buscar 84245 --op-de 9001 --op-ate 9010
    """
    settings = get_settings()
    hana_reader = HanaDirectReader(settings)
    try:
        resultados = manutencao_op_service.buscar_ops(
            hana_reader, doc_num, op_de=op_de, op_ate=op_ate,
            status_de=status_de, status_ate=status_ate,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    finally:
        hana_reader.close()

    if formato_json:
        console.print_json(json.dumps(resultados, default=str))
        return

    if not resultados:
        console.print(f"[yellow]Nenhuma OP encontrada para o pedido {doc_num} com esses filtros.[/yellow]")
        return

    tabela = Table(title=f"OPs do pedido {doc_num}")
    for coluna in ("OP", "Status", "Item", "Produto", "Planejada", "Apontada", "Restante", "Vencimento"):
        tabela.add_column(coluna, justify="right" if coluna in ("Planejada", "Apontada", "Restante") else "left")
    for linha in resultados:
        tabela.add_row(
            str(linha["Número OP"]), str(linha["Status (descrição)"]),
            str(linha["Cód. Produto"] or ""), str(linha["Produto"] or "")[:40],
            f"{float(linha['Qtde. Planejada'] or 0):g}", f"{float(linha['Qtde. Apontada'] or 0):g}",
            f"{float(linha['Qtde. Restante'] or 0):g}", str(linha["Data Vencimento"] or "")[:10],
        )
    console.print(tabela)
    console.print(f"[dim]{len(resultados)} OP(s).[/dim]")


def _muda_status_de_ops(
    op_docnums: list[str], pedido: str | None, status: str, confirmar: bool,
) -> None:
    """Fluxo comum de Liberar e Replanejar (equivalente a `mudaStatus` do ManutencaoOp).

    Mostra as OPs com o status atual antes de agir — é o que a grade da tela original dava
    ao usuário e o que permite decidir. Pula as que já estão no destino, em vez de gastar
    uma chamada à Service Layer para não mudar nada.
    """
    if bool(op_docnums) == bool(pedido):
        console.print("[red]Informe OS NÚMEROS DAS OPs ou --pedido, não ambos (nem nenhum).[/red]")
        raise typer.Exit(code=1)

    em_producao = _avisa_ambiente(f"manutencao-op {'liberar' if status == 'l' else 'replanejar'}")

    destino = manutencao_op_service.TRANSICOES[status]

    def _levantar():
        settings = get_settings()
        hana_reader = HanaDirectReader(settings)
        try:
            return manutencao_op_service.levanta_ops(
                hana_reader, op_docnums=op_docnums or None, doc_num_pedido=pedido
            )
        finally:
            hana_reader.close()

    ops = _levantar()

    if not ops:
        alvo = f"pedido {pedido}" if pedido else f"OP(s) {', '.join(op_docnums)}"
        console.print(f"[yellow]Nenhuma OP encontrada para {alvo}.[/yellow]")
        raise typer.Exit(code=0)

    tabela = Table(title=f"Ordens de Produção — destino: {destino['nome']}")
    tabela.add_column("OP (DocNum)")
    tabela.add_column("Item")
    tabela.add_column("Planejada", justify="right")
    tabela.add_column("Apontada", justify="right")
    tabela.add_column("Status atual")
    tabela.add_column("Ação")
    for op in ops:
        ja_no_destino = op["status"] == destino["owor"]
        if ja_no_destino:
            acao = "[dim]já está[/dim]"
        elif op["status"] == "C":
            # Cancelada é estado final: o SAP não a libera nem a replaneja. Aparece na
            # tabela (o usuário digitou o número) mas não entra na alteração.
            acao = "[red]cancelada — não pode mudar de status[/red]"
        else:
            acao = f"-> {destino['nome']}"
        tabela.add_row(
            str(op["doc_num"]), op["item_code"], f"{op['planejada']:g}", f"{op['apontada']:g}",
            manutencao_op_service.STATUS_OP.get(op["status"], op["status"]), acao,
        )
    console.print(tabela)

    a_alterar = [
        op for op in ops
        if op["status"] != destino["owor"] and op["status"] != "C"
    ]
    if not a_alterar:
        console.print(f"[yellow]Nada a fazer: todas já estão {destino['nome'].lower()}s.[/yellow]")
        raise typer.Exit(code=0)

    console.print(f"\n[bold]{len(a_alterar)} OP(s) serão alteradas para {destino['nome']}.[/bold]")
    _confirma("Confirma?", confirmar, em_producao)

    _ativa_perfil_e_progresso_silencioso()

    async def _aplicar():
        settings = get_settings()
        async with ServiceLayerClient(settings) as sl:
            return await manutencao_op_service.muda_status(sl, a_alterar, status)

    resultado = _run(_aplicar())

    if resultado["alteradas"]:
        console.print(f"\n[green]Alteradas com sucesso:[/green] {len(resultado['alteradas'])} OP(s)")
    _imprime_ops_puladas(resultado)
    _imprime_ops_com_erro(resultado)
    if resultado["com_erro"]:
        raise typer.Exit(code=1)


@manutencao_op_app.command("liberar")
def manutencao_op_liberar(
    op_docnums: list[str] = typer.Argument(None, help="Números das OPs (DocNum). Omita se usar --pedido."),
    pedido: str | None = typer.Option(None, "--pedido", help="Libera todas as OPs deste pedido (DocNum)."),
    confirmar: bool = typer.Option(False, "--sim", help="Pula a confirmação interativa."),
):
    """Libera Ordens de Produção (Planejada -> Liberada).

    Equivale ao botão "Liberar" da tela ManutencaoOp (`mudaStatus("l")` + `updateOP`).
    Mostra as OPs com o status atual antes de alterar e pula as que já estão liberadas.

    Exemplos:
        python -m controleproducao manutencao-op liberar 9001 9002
        python -m controleproducao manutencao-op liberar --pedido 84245
    """
    _muda_status_de_ops(op_docnums or [], pedido, "l", confirmar)


@manutencao_op_app.command("replanejar")
def manutencao_op_replanejar(
    op_docnums: list[str] = typer.Argument(None, help="Números das OPs (DocNum). Omita se usar --pedido."),
    pedido: str | None = typer.Option(None, "--pedido", help="Replaneja todas as OPs deste pedido (DocNum)."),
    confirmar: bool = typer.Option(False, "--sim", help="Pula a confirmação interativa."),
):
    """Devolve Ordens de Produção para Planejada (Liberada -> Planejada).

    Equivale ao botão "Planejar" da tela ManutencaoOp (`mudaStatus("p")` + `updateOP`).
    É o caminho para destravar um pedido cujo `cancelar-ops` foi barrado por OP liberada.

    Exemplos:
        python -m controleproducao manutencao-op replanejar 9002
        python -m controleproducao manutencao-op replanejar --pedido 84245
    """
    _muda_status_de_ops(op_docnums or [], pedido, "p", confirmar)


@manutencao_op_app.command("encerrar")
def manutencao_op_encerrar(
    op_docnums: list[str] = typer.Argument(None, help="Números das OPs (DocNum). Omita se usar --pedido."),
    pedido: str | None = typer.Option(None, "--pedido", help="Encerra todas as OPs deste pedido (DocNum)."),
    confirmar: bool = typer.Option(False, "--sim", help="Pula a confirmação interativa."),
):
    """Encerra Ordens de Produção COM movimentação de estoque.

    Equivale ao 4º botão da tela ManutencaoOp. Para cada OP, nesta ordem:

    1. **Libera a OP**, se estiver Planejada — uma OP só pode ser apontada estando
       liberada — e força a baixa manual dos insumos;
    2. saída de mercadoria dos insumos;
    3. entrada de mercadoria do produto;
    4. encerramento.

    Aceita OP Planejada ou Liberada. Recusa Encerrada e Cancelada.

    Com `--pedido`, encerra TODAS as OPs do pedido **respeitando a hierarquia**: a filha
    antes da mãe, porque a saída de insumo de uma OP pai consome o item que a filha
    produz. A ordem aparece na coluna `#` antes da confirmação.

    Se uma OP falhar, as que dependem dela são PULADAS em vez de tentadas — sem o insumo
    que ela produziria, falhariam por falta de estoque e a cascata de erros esconderia a
    causa real. OPs independentes seguem normalmente.

    ⚠️ IRREVERSÍVEL: gera lançamentos de estoque que não podem ser desfeitos pelo comando.
    OPs cuja quantidade apontada já atingiu a planejada são ignoradas, como no legado.

    Exemplos:
        python -m controleproducao manutencao-op encerrar 9001 9002
        python -m controleproducao manutencao-op encerrar --pedido 84245
    """
    if bool(op_docnums) == bool(pedido):
        console.print("[red]Informe OS NÚMEROS DAS OPs ou --pedido, não ambos (nem nenhum).[/red]")
        raise typer.Exit(code=1)

    em_producao = _avisa_ambiente("manutencao-op encerrar")

    settings = get_settings()

    def _levantar():
        leitor = HanaDirectReader(settings)
        try:
            return manutencao_op_service.levanta_ops(
                leitor, op_docnums=list(op_docnums or []) or None, doc_num_pedido=pedido
            )
        finally:
            leitor.close()

    ops = _levantar()
    if not ops:
        alvo = f"pedido {pedido}" if pedido else f"OP(s) {', '.join(op_docnums or [])}"
        console.print(f"[yellow]Nenhuma OP encontrada para {alvo}.[/yellow]")
        raise typer.Exit(code=0)

    # Ordem de encerramento: filha antes da mãe. A saída de insumo de uma OP pai consome o
    # item que a filha produz — encerrar de cima para baixo falharia por falta de estoque.
    def _hierarquia():
        leitor = HanaDirectReader(settings)
        try:
            componentes = manutencao_op_service._componentes_por_op(
                leitor, [int(op["doc_entry"]) for op in ops]
            )
            ordenadas, em_ciclo = manutencao_op_service.ordena_por_dependencia(ops, componentes)
            deps = manutencao_op_service.dependentes_transitivos(ops, componentes)
            return ordenadas, em_ciclo, deps
        finally:
            leitor.close()

    ops, em_ciclo, dependentes = _hierarquia()

    if em_ciclo:
        console.print(
            f"\n[bold red]ERRO: {len(em_ciclo)} OP(s) formam um ciclo de dependência "
            "entre si.[/bold red]\nNenhuma OP foi alterada."
        )
        for op in em_ciclo:
            console.print(f"  - OP {op['doc_num']} (item {op['item_code']})")
        console.print(
            "\n[dim]Numa operação irreversível de estoque, encerrar em ordem arbitrária é "
            "pior que recusar: uma delas consumiria o produto da outra antes de ele "
            "existir. Resolva a estrutura dessas OPs ou encerre-as uma a uma, por número."
            "[/dim]"
        )
        raise typer.Exit(code=1)

    tabela = Table(
        title="Ordens de Produção — encerramento com movimentação de estoque"
        + (" (ordem: filha antes da mãe)" if len(ops) > 1 else "")
    )
    for coluna in ("#", "OP (DocNum)", "Item", "Planejada", "Apontada", "Status atual", "Ação"):
        tabela.add_column(coluna, justify="right" if coluna in ("Planejada", "Apontada", "#") else "left")

    a_processar = []
    for indice, op in enumerate(ops, start=1):
        completa = float(op["apontada"]) >= float(op["planejada"])
        if op["status"] == "L":
            acao = "[dim]já encerrada[/dim]"
        elif op["status"] == "C":
            # OP cancelada não pode ser liberada, e `corrigeOP` é o primeiro passo — então
            # a cadeia falharia no início, com uma mensagem obscura do SAP. A grade do
            # legado nunca mostrava canceladas (`OPS_MANUTENCAO` filtra `Status != 'C'`);
            # aqui a OP aparece, porque o usuário digitou esse número e merece saber por
            # que ela não entra, em vez de vê-la desaparecer da lista.
            acao = "[red]cancelada — não pode ser encerrada[/red]"
        elif completa:
            acao = "[dim]ignorada (apontada = planejada)[/dim]"
        elif op["status"] == "R":
            # Já liberada: o apontamento pode acontecer direto.
            acao = "[yellow]saída + entrada + encerrar[/yellow]"
            a_processar.append(op)
        else:
            # Planejada: precisa ser LIBERADA antes, porque uma OP só pode ser apontada
            # estando liberada (regra de negócio, seção 7.32 do guia). O passo aparece na
            # tabela em vez de ficar escondido dentro do `corrigeOP` — é o estado que sobra
            # se a cadeia falhar depois dele.
            acao = "[yellow]liberar + saída + entrada + encerrar[/yellow]"
            a_processar.append(op)
        tabela.add_row(
            str(indice), str(op["doc_num"]), op["item_code"],
            f"{op['planejada']:g}", f"{op['apontada']:g}",
            manutencao_op_service.STATUS_OP.get(op["status"], op["status"]), acao,
        )
    console.print(tabela)

    if not a_processar:
        console.print("[yellow]Nada a encerrar.[/yellow]")
        raise typer.Exit(code=0)

    a_liberar = [op for op in a_processar if op["status"] != "R"]
    console.print(
        f"\n[bold red]{len(a_processar)} OP(s) serão encerradas com lançamento de estoque "
        f"(saída de insumos + entrada de produto). Esta operação é IRREVERSÍVEL.[/bold red]"
    )
    if a_liberar:
        uma_so = len(a_liberar) == 1
        console.print(
            f"[yellow]{len(a_liberar)} "
            f"{'está Planejada e será LIBERADA' if uma_so else 'estão Planejadas e serão LIBERADAS'}"
            " antes — uma OP só pode ser apontada estando liberada.[/yellow]"
        )
    _confirma("Confirma?", confirmar, em_producao)

    _ativa_perfil_e_progresso_silencioso()

    async def _aplicar():
        leitor = HanaDirectReader(settings)
        try:
            async with ServiceLayerClient(settings) as sl:
                return await manutencao_op_service.finalizar_ops(
                    sl, leitor, a_processar, settings.sl_business_place_id,
                    dependentes=dependentes,
                )
        finally:
            leitor.close()

    resultado = _run(_aplicar())

    if resultado["finalizadas"]:
        console.print(f"\n[green]Encerradas:[/green] {len(resultado['finalizadas'])} OP(s)")
        for op in resultado["finalizadas"]:
            liberada = " [dim](liberada antes)[/dim]" if op.get("foi_liberada") else ""
            console.print(
                f"  - OP {op['doc_num']}: saída={op['saida_docentry'] or '—'}, "
                f"entrada={op['entrada_docentry'] or '—'}{liberada}"
            )
    _imprime_ops_puladas(resultado)
    if resultado["com_erro"]:
        console.print(f"[red]Falharam:[/red] {len(resultado['com_erro'])} OP(s)")
        for erro in resultado["com_erro"]:
            console.print(f"  - OP {erro['doc_num']} — em '{erro['etapa']}': {erro['motivo']}")
            estado = erro.get("liberacao")
            if estado == "desfeita":
                console.print("    [dim]A OP foi devolvida para Planejada (nada foi lançado).[/dim]")
            elif estado == "mantida (saída já lançada)":
                console.print(
                    "    [bold red]ATENÇÃO: a saída de insumo JÁ foi lançada e a OP continua "
                    "Liberada. Não use `replanejar` — a saída precisa ser cancelada no SAP "
                    "primeiro, senão fica estoque movimentado numa OP planejada.[/bold red]"
                )
            elif estado and estado.startswith("falhou"):
                console.print(
                    f"    [yellow]A OP ficou Liberada: a devolução para Planejada também "
                    f"falhou ({estado}). Use `manutencao-op replanejar`.[/yellow]"
                )
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Módulo 4 — Romaneio (esqueleto)
# ---------------------------------------------------------------------------
@romaneio_app.command("buscar")
def romaneio_buscar(doc_num: str = typer.Argument(..., help="Nº do pedido de venda (DocNum).")):
    """Equivalente à busca da tela Romaneio. NÃO IMPLEMENTADO."""

    async def _main():
        settings = get_settings()
        async with ServiceLayerClient(settings) as sl:
            return await romaneio_service.buscar_disponivel(sl, doc_num)

    try:
        resultados = _run(_main())
        console.print_json(json.dumps(resultados, default=str))
    except NotImplementedError:
        _falha_nao_implementado("romaneio", "buscar")
        raise typer.Exit(code=2)


# ---------------------------------------------------------------------------
# Diagnóstico
# ---------------------------------------------------------------------------
@conexoes_app.command("testar")
def conexoes_testar():
    """Testa, em sequência, o login na Service Layer, a leitura direta no HANA e a
    leitura no SQL Server do WBC — usa as credenciais do `.env`. Útil como primeiro
    passo em qualquer ambiente novo (homologação ou produção), antes de rodar qualquer
    tarefa de verdade.
    """
    settings = get_settings()

    async def _testar_service_layer() -> tuple[bool, str]:
        try:
            async with ServiceLayerClient(settings) as sl:
                await sl.login()
            return True, "login OK"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def _testar_hana() -> tuple[bool, str]:
        try:
            reader = HanaDirectReader(settings)
            reader.fetch_all('SELECT 1 AS "ok" FROM "DUMMY"')
            return True, "leitura OK"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def _testar_wbc() -> tuple[bool, str]:
        try:
            with WbcSqlServerClient(settings) as wbc:
                wbc.fetch_all("SELECT 1 AS ok")
            return True, "leitura OK"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    tabela = Table(title="Diagnóstico de conectividade")
    tabela.add_column("Alvo")
    tabela.add_column("Status")
    tabela.add_column("Detalhe")

    ok_sl, msg_sl = _run(_testar_service_layer())
    tabela.add_row("SAP Service Layer", "[green]OK[/green]" if ok_sl else "[red]FALHOU[/red]", msg_sl)

    ok_hana, msg_hana = _testar_hana()
    tabela.add_row("HANA (leitura direta)", "[green]OK[/green]" if ok_hana else "[red]FALHOU[/red]", msg_hana)

    ok_wbc, msg_wbc = _testar_wbc()
    tabela.add_row("SQL Server WBC", "[green]OK[/green]" if ok_wbc else "[red]FALHOU[/red]", msg_wbc)

    console.print(tabela)

    if not (ok_sl and ok_hana and ok_wbc):
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Diagnóstico da Service Layer (adicionado em 15/09/2026)
# ---------------------------------------------------------------------------
@diag_app.command("entidade")
def diag_entidade(
    entidade: str = typer.Argument(
        ..., help="Nome da entidade/UDO na Service Layer (ex.: OrcDetalhe, ProductionOrders)."
    ),
    campos: bool = typer.Option(
        False, "--campos", help="Mostra só a lista de nomes de campo, em vez do JSON completo."
    ),
):
    """Lê UM registro já existente da entidade e mostra os nomes reais de campo.

    Serve para confirmar, contra o ambiente real, os nomes que a Service Layer espera —
    especialmente em UDOs (`OrcDetalhe`/`INO_ORC_LINHA`) e em entidades onde a DI API usava
    Business Services, que podem mapear diferente na REST (ver perguntas 9/10 da seção 9 do
    migration_guide.md). Como o addon legado já criou registros nessas entidades, ler um de
    volta é a forma mais confiável de descobrir o nome certo — melhor do que adivinhar.

    Exemplos:
        python -m controleproducao diag entidade OrcDetalhe --campos
        python -m controleproducao diag entidade OrcDetalhe
    """

    async def _main():
        settings = get_settings()
        async with ServiceLayerClient(settings) as sl:
            return await sl.get(entidade, params={"$top": 1})

    try:
        resposta = _run(_main())
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Erro ao ler '{entidade}':[/red] {exc}")
        raise typer.Exit(code=1)

    registros = resposta.get("value", [])
    if not registros:
        console.print(
            f"[yellow]A entidade '{entidade}' respondeu, mas não tem nenhum registro para inspecionar.[/yellow]\n"
            "Os nomes de campo não podem ser descobertos por este caminho — tente uma entidade "
            "que o addon legado já tenha populado."
        )
        raise typer.Exit(code=0)

    registro = registros[0]

    if campos:
        colecoes: list[tuple[str, list]] = []

        tabela = Table(title=f"Campos de '{entidade}' (de um registro real)")
        tabela.add_column("Campo")
        tabela.add_column("Tipo")
        tabela.add_column("Exemplo")
        for chave, valor in registro.items():
            if isinstance(valor, list):
                colecoes.append((chave, valor))
                exemplo = f"<coleção com {len(valor)} linha(s)>"
            else:
                exemplo = str(valor)
            # `escape`: sem isso o Rich interpreta colchetes no conteúdo como marcação
            # e engole o texto (aconteceu na primeira versão deste comando).
            tabela.add_row(escape(chave), type(valor).__name__, escape(exemplo[:60]))
        console.print(tabela)

        # Coleções filhas (tabelas filhas de UDO) — é aqui que estão os nomes/tipos das
        # linhas, tão necessários quanto os do cabeçalho para montar o POST corretamente.
        for nome_colecao, linhas in colecoes:
            if not linhas:
                console.print(f"\n[yellow]Coleção '{nome_colecao}' veio vazia neste registro.[/yellow]")
                continue
            sub = Table(title=f"Campos de '{nome_colecao}' (1ª linha do registro)")
            sub.add_column("Campo")
            sub.add_column("Tipo")
            sub.add_column("Exemplo")
            for chave, valor in linhas[0].items():
                sub.add_row(escape(chave), type(valor).__name__, escape(str(valor)[:60]))
            console.print(sub)
    else:
        console.print_json(json.dumps(registro, default=str))



@diag_app.command("patch-parcial")
def diag_patch_parcial(
    doc_num: str | None = typer.Argument(None, help="Nº do pedido de venda (DocNum). Omita se usar --docentry."),
    doc_entry_direto: int | None = typer.Option(
        None, "--docentry",
        help="Identifica o pedido pelo DocEntry em vez do DocNum. Necessário quando o que "
             "se quer auditar é um DocEntry específico visto num log — DocNum e DocEntry "
             "são numerações distintas e não se deduzem um do outro.",
    ),
    aplicar: bool = typer.Option(
        False, "--aplicar",
        help="Executa o PATCH de prova. Sem esta flag, só faz a parte de leitura (histórico "
             "e fotografia das linhas) — que já pode ser conclusiva.",
    ),
):
    """Verifica se o PATCH parcial de `DocumentLines` preserva as linhas não enviadas.

    Responde à pendência da seção 7.11 do guia: ao mandar `DocumentLines` com apenas
    algumas linhas (só `LineNum` + o campo alterado), a Service Layer preserva as demais,
    ou substitui a coleção inteira? Todo o módulo 2 depende da primeira resposta.

    Faz em duas partes:

    1. **Forense (só leitura)** — lê `ADOC`/`ADO1`, o histórico de versões que o B1 guarda
       a cada atualização do documento. Se a contagem de linhas cair de uma versão para a
       seguinte, alguma atualização comeu linhas, e dá para ver em qual. Se o histórico
       estiver vazio, o log não está ativo e isso não prova nada.
    2. **Experimento (`--aplicar`)** — tira uma fotografia das linhas, reenvia UMA linha
       com o valor que ela já tem (mesmo formato de corpo do código de produção, mas sem
       alterar nada), tira outra fotografia e compara. Qualquer diferença é efeito
       colateral do mecanismo.

    Exemplos:
        python -m controleproducao diag patch-parcial 84263
        python -m controleproducao diag patch-parcial 84263 --aplicar
    """
    if bool(doc_num) == bool(doc_entry_direto):
        console.print("[red]Informe o DocNum OU --docentry, não ambos (nem nenhum).[/red]")
        raise typer.Exit(code=1)

    settings = get_settings()
    leitor = HanaDirectReader(settings)
    try:
        if doc_entry_direto:
            pedidos = leitor.fetch_all(
                *ligar(pedidos_wbc_queries.PEDIDO_POR_DOCENTRY, doc_entry=int(doc_entry_direto))
            )
            rotulo = f"DocEntry {doc_entry_direto}"
        else:
            pedidos = leitor.fetch_all(
                *ligar(pedidos_wbc_queries.PEDIDO_POR_DOCNUM, doc_num=doc_num)
            )
            rotulo = f"pedido {doc_num}"
        if not pedidos:
            console.print(f"[red]Nenhum pedido encontrado para {rotulo}.[/red]")
            raise typer.Exit(code=1)
        doc_entry = int(pedidos[0]["DocEntry"])
        console.print(
            f"\nPedido [bold]{pedidos[0]['DocNum']}[/bold] (DocEntry {doc_entry})"
            f" — {pedidos[0].get('CardName') or ''}"
        )

        # --- Parte 1: forense, só leitura ---------------------------------------
        historico = pedidos_wbc_service.historico_de_linhas(leitor, doc_entry)
        atuais = len(pedidos_wbc_service.foto_das_linhas(leitor, doc_entry))

        if not historico:
            console.print(
                "[yellow]Sem histórico em ADOC/ADO1 para este pedido.[/yellow] O log de "
                "alterações do documento não está ativo, ou o pedido nunca foi atualizado. "
                "Isso NÃO prova que as linhas estão intactas — use --aplicar."
            )
        else:
            tabela = Table(title="Linhas por versão do documento (ADO1)")
            tabela.add_column("Versão", justify="right")
            tabela.add_column("Nº de linhas", justify="right")
            tabela.add_column("Faixa de LineNum")
            tabela.add_column("")
            anterior = None
            perdeu = False
            for versao in historico:
                qtd = int(versao["linhas"])
                if anterior is not None and qtd < anterior:
                    marca = f"[bold red]perdeu {anterior - qtd} linha(s)[/bold red]"
                    perdeu = True
                elif anterior is not None and qtd > anterior:
                    marca = f"[dim]+{qtd - anterior}[/dim]"
                else:
                    marca = ""
                tabela.add_row(
                    str(versao["LogInstanc"]), str(qtd),
                    f'{versao["primeira_linha"]}-{versao["ultima_linha"]}', marca,
                )
                anterior = qtd
            console.print(tabela)
            console.print(f"Linhas hoje em RDR1: [bold]{atuais}[/bold]")

            if perdeu:
                console.print(
                    "\n[bold red]O documento JÁ perdeu linhas em alguma atualização.[/bold red] "
                    "Isso sozinho não aponta o culpado (outros add-ons e o próprio usuário "
                    "também atualizam pedidos), mas é motivo para investigar antes de levar "
                    "o módulo 2 para produção."
                )
            elif anterior is not None and atuais < anterior:
                console.print(
                    f"\n[bold red]A última versão tinha {anterior} linhas e hoje há {atuais}.[/bold red]"
                )
            else:
                console.print(
                    "\n[green]Nenhuma versão perdeu linhas.[/green] Se este pedido passou pelo "
                    "PATCH parcial do módulo 2, o mecanismo preservou as linhas não enviadas."
                )

        if not aplicar:
            console.print(
                "\n[dim]Para o experimento controlado (reenvia uma linha sem alterá-la e "
                "compara antes/depois), repita com --aplicar.[/dim]"
            )
            raise typer.Exit(code=0)

        # --- Parte 2: experimento controlado ------------------------------------
        em_producao = _avisa_ambiente("diag patch-parcial --aplicar")

        antes = pedidos_wbc_service.foto_das_linhas(leitor, doc_entry)
        if not antes:
            console.print("[yellow]O pedido não tem linhas — nada a testar.[/yellow]")
            raise typer.Exit(code=0)

        alvo = antes[0]
        console.print(
            f"\n{len(antes)} linha(s) no pedido. O teste reenvia SOMENTE a linha "
            f"{alvo['LineNum']} ({alvo['ItemCode']}), com o valor de U_INO_OP que ela já "
            f"tem ({int(alvo['U_INO_OP'] or 0)}).\n"
            f"Se as outras {len(antes) - 1} sobreviverem intactas, o mecanismo está correto."
        )
        _confirma("Confirma?", False, em_producao)

        async def _executar():
            async with ServiceLayerClient(settings) as sl:
                await pedidos_wbc_service.patch_parcial_de_prova(
                    sl, doc_entry, int(alvo["LineNum"]), int(alvo["U_INO_OP"] or 0)
                )

        _run(_executar())

        depois = pedidos_wbc_service.foto_das_linhas(leitor, doc_entry)
        diff = pedidos_wbc_service.compara_fotos(antes, depois)

        console.print(
            f"\nLinhas antes: [bold]{len(antes)}[/bold] · depois: [bold]{len(depois)}[/bold]"
        )

        if diff["sumiram"]:
            console.print(
                f"\n[bold red]FALHOU: {len(diff['sumiram'])} linha(s) desapareceram:[/bold red] "
                f"{diff['sumiram']}\n"
                "O PATCH parcial substitui a coleção inteira. O módulo 2 NÃO pode ir para "
                "produção como está."
            )
            raise typer.Exit(code=1)

        if diff["alteradas"]:
            console.print("\n[bold red]FALHOU: campos mudaram sem terem sido enviados:[/bold red]")
            for linha in diff["alteradas"]:
                for campo, (a, d) in linha["campos"].items():
                    console.print(f"  - linha {linha['LineNum']} · {campo}: {a!r} -> {d!r}")
            raise typer.Exit(code=1)

        console.print(
            "\n[green]PASSOU: nenhuma linha sumiu e nenhum campo mudou.[/green]\n"
            "O PATCH parcial de DocumentLines preserva o que não foi enviado — a pendência "
            "da seção 7.11 do guia pode ser fechada para este ambiente."
        )
    finally:
        leitor.close()


@diag_app.command("chave-com-barra")
def diag_chave_com_barra(
    item_code: str = typer.Argument(..., help="Código de item com barra (ex.: PPA3CPAB-1FF/D100220)."),
):
    """Prova o desvio pelo `$batch` para chaves que o servidor web recusa na URL.

    **Somente leitura.** Faz um GET do item de duas formas e compara:

    1. **Direto** — `GET /Items('...%2F...')`. É o caminho normal, e é o que falhou em
       22/09/2026 com `404 Not Found` **em HTML**: quem recusa é o servidor web à frente
       da Service Layer, que por padrão bloqueia barra codificada no caminho. A página de
       erro em HTML é o que denuncia o culpado — a Service Layer erra em JSON.
    2. **Pelo `$batch`** — a URI da operação viaja no corpo multipart. O servidor web vê
       só `POST /$batch` e não tem o que inspecionar; a Service Layer decodifica a URI ela
       mesma.

    O resultado esperado é o desvio funcionar e o direto falhar. Se **os dois**
    funcionarem, o servidor já está configurado com `AllowEncodedSlashes` e o desvio é
    inofensivo (fica sem uso). Se **os dois** falharem, o problema não é o endereçamento:
    provavelmente o item não existe nesse ambiente — teste com um código que exista.

    Exemplo:
        python -m controleproducao diag chave-com-barra "PPA3CPAB-1FF/D100220"
    """
    from controleproducao.core.service_layer_client import _formata_chave, _precisa_de_batch

    chave = _formata_chave(item_code, texto=True)
    caminho = f"Items({chave})"
    console.print(f"Código.....: [cyan]{item_code}[/cyan]")
    console.print(f"Na URL.....: [cyan]{caminho}[/cyan]")
    console.print(f"Desvio pelo $batch: {'SIM' if _precisa_de_batch(caminho) else 'não (nada a provar aqui)'}\n")

    async def _main():
        settings = get_settings()
        async with ServiceLayerClient(settings) as sl:
            direto = batch = None
            try:
                # `_request` é o caminho cru, sem o desvio — é justamente o que queremos ver falhar.
                resp = await sl._request("GET", f"/{caminho}")
                direto = ("ok", resp.json().get("ItemCode"))
            except Exception as exc:  # noqa: BLE001
                direto = ("erro", str(exc))
            try:
                dados = await sl._via_batch("GET", caminho)
                batch = ("ok", (dados or {}).get("ItemCode"))
            except Exception as exc:  # noqa: BLE001
                batch = ("erro", str(exc))
            return direto, batch

    direto, batch = _run(_main())

    def _mostra(rotulo: str, resultado):
        situacao, detalhe = resultado
        cor = "green" if situacao == "ok" else "red"
        console.print(f"[bold]{rotulo}[/bold]: [{cor}]{situacao}[/{cor}] — {str(detalhe)[:300]}")

    _mostra("Direto na URL", direto)
    _mostra("Pelo $batch  ", batch)

    if direto[0] == "erro" and batch[0] == "ok":
        console.print(
            "\n[green]Desvio confirmado[/green] — o servidor web recusa a barra na URL "
            "e o $batch passa por cima."
        )
    elif direto[0] == "ok" and batch[0] == "ok":
        console.print(
            "\n[yellow]Os dois funcionam[/yellow] — o servidor já aceita barra codificada; "
            "o desvio fica sem uso."
        )
    else:
        console.print(
            "\n[red]O $batch também falhou[/red] — não é endereçamento. "
            "Confira se esse item existe neste ambiente."
        )


if __name__ == "__main__":
    app()
