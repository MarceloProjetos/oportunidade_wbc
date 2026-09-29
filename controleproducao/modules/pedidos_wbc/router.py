"""Rotas web do módulo Pedidos WBC — equivalente à tela `IntegraPedidoWBC.b1f.cs`.

A tela original tinha dois modos ("Pedidos Novos" e "Pedidos Integrados"), uma grade com
caixas de seleção e botões que agiam sobre o que estava marcado. O desenho aqui é o mesmo,
com três diferenças deliberadas, todas por causa de erros reais desta migração:

1. **Nada grava direto do botão.** Processar, reprocessar e cancelar OPs passam por um
   plano conferido em tela (`core/confirmacao.py`). No legado o botão agia sobre a seleção
   e pronto; aqui o usuário vê antes o que vai acontecer e confirma sobre isso.
2. **A execução é em segundo plano** (`core/tarefas.py`): um orçamento médio leva ~7s e um
   grande passa de minuto — tempo demais para um request segurar.
3. **A trava de produção vale aqui igual à CLI** (`core/web.py`). Mesmo `.env`, mesma regra.

O que a grade protegia e o código não (tema recorrente da migração, seção 8): o filtro da
tela é reproduzido pelas próprias queries de busca — a rota nunca aceita um identificador
digitado que não tenha passado por elas.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from controleproducao.config import get_settings
from controleproducao.core import service_layer_client
from controleproducao.core.confirmacao import PLANOS, ConfirmacaoInvalida
from controleproducao.core.formato import numero_br
from controleproducao.core.hana_reader import HanaDirectReader
from controleproducao.core.service_layer_client import ServiceLayerClient
from controleproducao.core.sqlserver_client import WbcSqlServerClient
from controleproducao.core.tarefas import TAREFAS, Tarefa, acompanha_log
from controleproducao.core.templates import templates
from controleproducao.core.web import avisa_escrita
from controleproducao.modules.pedidos_wbc import service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pedidos-wbc", tags=["pedidos_wbc"])

MODULO = "pedidos_wbc"

COLUNAS_PEDIDO = ["Pedido", "Oportunidade", "WBC", "Cliente", "Total"]
COLUNAS_OP = ["doc_num", "item_code", "status", "descricao"]


def _erro(request: Request, mensagem: str, **extra):
    return templates.TemplateResponse(
        request, "erro.html",
        {"mensagem": mensagem, "voltar": "/pedidos-wbc", **extra},
        status_code=400,
    )


def _pedidos_selecionados(pedidos, opp_ids: list[str]) -> list:
    escolhidos = {str(i) for i in opp_ids}
    return [p for p in pedidos if str(p.opp_id) in escolhidos]


# ---------------------------------------------------------------------------
# Consulta (somente leitura)
# ---------------------------------------------------------------------------
# Quantos pedidos por página. O modo "integrados" passa de 300 linhas em homologação —
# rolar tudo para achar um número é pior do que paginar.
POR_PAGINA = 15


@router.get("", response_class=HTMLResponse)
async def pagina_inicial(
    request: Request,
    modo: str = "novos",
    buscar: int = 0,
    pagina: int = 1,
):
    """Lista e paginação viajam na URL, de propósito.

    A busca era um POST, e com isso o F5 numa página de resultado pedia reenvio do
    formulário e "página 2" não tinha endereço. Sendo GET, cada página é um link comum:
    dá para voltar, recarregar e favoritar. É leitura pura — nada aqui grava.
    """
    integrados = modo == "integrados"
    pedidos: list = []
    if buscar:
        hana_reader = HanaDirectReader(get_settings())
        pedidos = await service.buscar_pedidos_para_integrar(hana_reader, integrados=integrados)

    total = len(pedidos)
    paginas = max(1, -(-total // POR_PAGINA))  # divisão para cima
    # Página fora do intervalo vira a última válida em vez de tabela vazia: o número vem
    # da URL e pode estar velho (a lista encolhe conforme os pedidos são processados).
    pagina = min(max(1, pagina), paginas)
    inicio = (pagina - 1) * POR_PAGINA

    return templates.TemplateResponse(
        request, "pedidos_wbc.html",
        {
            "modo": "integrados" if integrados else "novos",
            "pedidos": pedidos[inicio:inicio + POR_PAGINA],
            "buscou": bool(buscar),
            "total": total,
            "pagina": pagina,
            "paginas": paginas,
            "primeiro": inicio + 1 if total else 0,
            "ultimo": min(inicio + POR_PAGINA, total),
            "em_execucao": TAREFAS.em_execucao(MODULO),
        },
    )


@router.post("/buscar")
async def buscar(request: Request, modo: str = Form(default="novos")):
    """Compatibilidade: o formulário antigo postava aqui. Redireciona para a URL de busca."""
    return RedirectResponse(f"/pedidos-wbc?modo={modo}&buscar=1", status_code=303)


# ---------------------------------------------------------------------------
# Etapa 1 — planos (leem, não gravam)
# ---------------------------------------------------------------------------
@router.post("/processar/conferir", response_class=HTMLResponse)
async def conferir_processar(
    request: Request,
    opp_ids: list[str] = Form(default=[]),
    force: str = Form(default=""),
):
    return await _confere_pedidos(
        request, opp_ids, integrados=False, force=bool(force),
        operacao="Processar pedidos novos",
        acao="/pedidos-wbc/processar/executar",
        aviso="Cria Ordens de Produção, itens e recursos no SAP, e marca o pedido como "
              "processado. Não há desfazer automático.",
    )


_REPROCESSAR_SO_CLI = (
    "Reprocessar não está disponível na tela: ele cancela TODA OP planejada do pedido "
    "(de qualquer origem) e não recria. Use a CLI na .11, com o Anderson — "
    "`python -m controleproducao pedidos-wbc reprocessar-integrados` (decisão D8)."
)


@router.post("/reprocessar/conferir", response_class=HTMLResponse)
async def conferir_reprocessar(request: Request, opp_ids: list[str] = Form(default=[])):
    # D8 (F7, 28/09/2026): out of the screen. A stale page or a hand-made POST gets the
    # explicit refusal before any lookup, plan or write.
    return _erro(request, _REPROCESSAR_SO_CLI, titulo="Reprocessar é só pela CLI")


async def _confere_pedidos(
    request: Request, opp_ids: list[str], integrados: bool, force: bool,
    operacao: str, acao: str, aviso: str,
):
    if not opp_ids:
        return _erro(request, "Nenhum pedido selecionado.")

    # Relemos a lista em vez de confiar no que o formulário mandou: o que chega do
    # navegador é um número digitável, e a grade do legado nunca deixou o usuário agir
    # sobre um pedido que não estivesse no filtro. Quem não está na busca não entra.
    hana_reader = HanaDirectReader(get_settings())
    pedidos = await service.buscar_pedidos_para_integrar(hana_reader, integrados=integrados)
    selecionados = _pedidos_selecionados(pedidos, opp_ids)

    fora = sorted({str(i) for i in opp_ids} - {str(p.opp_id) for p in selecionados})
    if fora:
        return _erro(
            request,
            "Estes pedidos não estão mais na lista elegível e foram recusados: "
            + ", ".join(fora)
            + ". Refaça a busca — o estado no SAP mudou desde que a tela foi carregada.",
        )

    plano = PLANOS.criar(
        operacao=operacao,
        resumo={"pedido(s) selecionado(s)": len(selecionados),
                **({"modo": "forçado (ignora as checagens de reprocessamento)"} if force else {})},
        itens=[
            {"Pedido": p.doc_num, "Oportunidade": p.opp_id, "WBC": p.orc_num_masc,
             "Cliente": f"{p.cod_cliente} — {p.nome_cliente}",
             "Total": numero_br(p.total_pedido)}
            for p in selecionados
        ],
    )
    # O `force` viaja no plano, não no formulário de confirmação: o usuário confirma
    # exatamente o modo que foi conferido.
    plano.resumo["_force"] = force
    return templates.TemplateResponse(
        request, "confirmar.html",
        {"plano": _plano_visivel(plano), "colunas": COLUNAS_PEDIDO,
         "acao": acao, "aviso_operacao": aviso, "voltar": "/pedidos-wbc"},
    )


def _plano_visivel(plano):
    """O JSON do plano sem as chaves internas (prefixadas com `_`)."""
    dados = plano.para_json()
    dados["resumo"] = {k: v for k, v in dados["resumo"].items() if not k.startswith("_")}
    return dados


@router.post("/cancelar-ops/conferir", response_class=HTMLResponse)
async def conferir_cancelar_ops(
    request: Request,
    doc_num: str = Form(default=""),
    orc_num: str = Form(default=""),
):
    if not (doc_num or orc_num):
        return _erro(request, "Informe o nº do pedido ou o nº do orçamento.")

    hana_reader = HanaDirectReader(get_settings())
    levantamento = await service.levanta_ops_para_cancelamento(
        hana_reader, doc_num=doc_num or None, orc_num=orc_num or None
    )
    if not levantamento["pedido"]:
        return _erro(request, "Pedido não encontrado.")

    # Mesma regra da CLI: havendo qualquer OP fora dos status que permitem cancelar,
    # NADA é cancelado — nem as planejadas. Cancelar metade deixa o pedido num estado
    # que ninguém sabe descrever depois.
    if levantamento["bloqueantes"]:
        return _erro(
            request,
            f"{len(levantamento['bloqueantes'])} OP(s) deste pedido estão em status que "
            "não permite cancelamento. Nenhuma OP será cancelada.",
            titulo="Cancelamento bloqueado",
            detalhes=levantamento["bloqueantes"], colunas=COLUNAS_OP,
        )
    if not levantamento["a_cancelar"]:
        return _erro(request, "Não há OP planejada a cancelar neste pedido.")

    pedido = levantamento["pedido"]
    plano = PLANOS.criar(
        operacao=f"Cancelar OPs do pedido {pedido.get('DocNum')}",
        resumo={
            "OP(s) a cancelar": len(levantamento["a_cancelar"]),
            "OP(s) já canceladas (ignoradas)": len(levantamento["ja_canceladas"]),
            "OP(s) no pedido": len(levantamento["ops"]),
        },
        itens=levantamento["a_cancelar"],
    )
    return templates.TemplateResponse(
        request, "confirmar.html",
        {"plano": _plano_visivel(plano), "colunas": COLUNAS_OP,
         "acao": "/pedidos-wbc/cancelar-ops/executar",
         "aviso_operacao": "Cancela as Ordens de Produção listadas. Uma OP cancelada não "
                           "volta atrás — só recriando.",
         "voltar": "/pedidos-wbc"},
    )


# ---------------------------------------------------------------------------
# Etapa 2 — execução (grava; exige token e passa pela trava de produção)
# ---------------------------------------------------------------------------
def _consome(request: Request, token: str):
    """Valida o token do plano conferido. Devolve `(plano, resposta_de_erro)`.

    A conferência em duas etapas **não é** a trava de produção (removida em 22/09/2026):
    ela vale igual em homologação e existe porque a operação é irreversível — o usuário
    confirma sobre o que foi calculado e mostrado, e um F5 não reexecuta.
    """
    try:
        plano = PLANOS.consumir(token)
    except ConfirmacaoInvalida as exc:
        return None, _erro(request, str(exc), titulo="Confirmação não aceita")
    avisa_escrita(plano.operacao)
    return plano, None


def _dispara(request: Request, nome: str, descricao: str, corrotina):
    try:
        tarefa = TAREFAS.criar(MODULO, nome, descricao, corrotina)
    except RuntimeError as exc:
        return _erro(request, str(exc), titulo="Já existe execução em andamento")
    return RedirectResponse(f"/tarefas/{tarefa.id}", status_code=303)


@router.post("/processar/executar")
async def executar_processar(request: Request, token: str = Form(default="")):
    plano, erro = _consome(request, token)
    if erro:
        return erro
    alvos = _alvos(plano)
    force = bool(plano.resumo.get("_force"))

    async def executa(tarefa: Tarefa):
        return await _roda_pedidos(tarefa, alvos, modo="processar", force=force)

    return _dispara(
        request, plano.operacao,
        _descricao(alvos) + (" (forçado)" if force else ""),
        executa,
    )


@router.post("/reprocessar/executar")
async def executar_reprocessar(request: Request, token: str = Form(default="")):
    # D8: refused even with a token — no screen path can issue one any more.
    return _erro(request, _REPROCESSAR_SO_CLI, titulo="Reprocessar é só pela CLI")


def _alvos(plano) -> list[tuple[str, str]]:
    """Pares `(pedido SAP, orçamento WBC)` do plano conferido.

    Os dois números precisam viajar juntos: o serviço trabalha pelo **orçamento WBC**
    (`00125720`), mas quem lê a tela raciocina pelo **pedido do SAP** (`84371`) — é o
    número que ele abre no B1. Mostrar o do WBC como se fosse "o pedido" troca a
    identidade do documento no meio do acompanhamento.
    """
    return [(str(i["Pedido"]), str(i["WBC"])) for i in plano.itens]


def _descricao(alvos: list[tuple[str, str]]) -> str:
    return f"{len(alvos)} pedido(s): " + ", ".join(doc_num for doc_num, _ in alvos)


async def _roda_pedidos(tarefa: Tarefa, alvos: list[tuple[str, str]], modo: str, force: bool):
    """`alvos` são pares `(pedido SAP, orçamento WBC)`.

    Ao serviço vai o **orçamento** — é o que `GetOrcsWBC` e `GetIdOrcamentosPedido`
    procuram, não a chave da Oportunidade (`15149`); trocá-los dá "sem pedido vinculado"
    em todas as linhas (ver `_oppr_id_do_orcamento`). À tela vai o **pedido**.
    """
    settings = get_settings()
    hana_reader = HanaDirectReader(settings)
    wbc = WbcSqlServerClient(settings)
    tarefa.avanca(f"Conectando à Service Layer ({settings.sl_company_db})…", 0, len(alvos))
    async with ServiceLayerClient(settings) as sl:
        # Um pedido por vez, e não a lista inteira de uma vez, para o acompanhamento
        # dizer onde parou: o resultado agregado do serviço não diz em qual pedido a
        # execução estava quando alguém interrompeu.
        agregado: dict[str, list] = {}
        for i, (doc_num, orc_num) in enumerate(alvos, start=1):
            rotulo = f"Pedido {doc_num} (WBC {orc_num})"
            tarefa.avanca(f"{rotulo} ({i}/{len(alvos)})…", i - 1)
            # Cada OP criada, cada item cadastrado e cada recurso de rateio já sai no
            # `logger.info` do serviço; a ponte põe isso na tela enquanto roda, em vez de
            # deixar o usuário olhando uma linha só por vários minutos.
            with acompanha_log(tarefa, service.__name__, service_layer_client.__name__):
                if modo == "processar":
                    parcial = await service.processar_pedidos_novos(
                        sl, wbc, hana_reader, [orc_num], force=force
                    )
                else:
                    parcial = await service.reprocessar_pedidos_integrados(
                        sl, wbc, hana_reader, [orc_num]
                    )
            # O serviço identifica tudo pelo orçamento; a tela é do pedido. Acrescenta o
            # nº do pedido a cada registro para o JSON do resultado não obrigar o leitor
            # a traduzir os números de volta na cabeça.
            for chave, valores in parcial.items():
                agregado.setdefault(chave, []).extend(
                    [{**v, "doc_num": doc_num} if isinstance(v, dict)
                     else {"doc_num": doc_num, "orc_num": v}
                     for v in valores]
                )
            erros = parcial.get("com_erro", [])
            # Grupos que não geraram OP: o pedido "passou", mas parte dele não produziu
            # nada. Ficava invisível até 23/09/2026 (pedido 84426) — a tela dizia
            # "concluído" e só o @INO_LOG sabia que faltaram OPs.
            sem_op = parcial.get("sem_op", [])
            # OP criada, mas sem a linha de rateio (recurso GGF_ recusado pela Service Layer).
            sem_rateio = parcial.get("sem_rateio", [])
            # Um pedido que falhou não é "concluído": dizer as duas coisas em linhas
            # seguidas ("ERRO — …" e logo abaixo "concluído") fez o log parecer que a
            # falha tinha sido contornada.
            if erros:
                tarefa.avanca(f"{rotulo}: ERRO — {erros[0].get('motivo')}", i)
            elif sem_op or sem_rateio:
                ressalvas = []
                if sem_op:
                    ressalvas.append(
                        f"{len(sem_op)} grupo(s) sem OP: "
                        + "; ".join(str(g.get("motivo")) for g in sem_op)
                    )
                if sem_rateio:
                    ressalvas.append(
                        f"{len(sem_rateio)} OP(s) sem a linha de rateio: "
                        + "; ".join(f"{g.get('item')} — {g.get('motivo')}" for g in sem_rateio)
                    )
                tarefa.avanca(f"{rotulo}: ATENÇÃO — " + " | ".join(ressalvas), i)
            else:
                tarefa.avanca(f"{rotulo}: concluído.", i)
    return agregado


@router.post("/cancelar-ops/executar")
async def executar_cancelar_ops(request: Request, token: str = Form(default="")):
    plano, erro = _consome(request, token)
    if erro:
        return erro
    ops = plano.itens

    async def executa(tarefa: Tarefa):
        settings = get_settings()
        tarefa.avanca(f"Cancelando {len(ops)} OP(s)…", 0, len(ops))
        async with ServiceLayerClient(settings) as sl:
            resultado = await service.cancela_ops_do_pedido(sl, ops)
        tarefa.avanca(
            f"{len(resultado['canceladas'])} cancelada(s), "
            f"{len(resultado['com_erro'])} com erro.",
            len(ops),
        )
        return resultado

    return _dispara(request, plano.operacao, f"{len(ops)} OP(s)", executa)
