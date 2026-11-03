"""Tracking panel — HTML served by FastAPI, updated with HTMX.

Run with:

    python -m wbcpython dashboard

The pages **only read** the tracking database and, on the "Próximo ciclo" tab, a snapshot on disk written by
`wbcpython pendentes --exportar`. They never query SAP or WBC — see `docs/wbc/README.md` and `dashboard/__init__`.
It is also the entrance for the 8077 and 8080 screens: one key (`OS_API_KEY`), one cookie
(`acesso.COOKIE_DE_ACESSO`); writes by cookie need a same-origin `Origin`/`Referer`.

## Why HTMX and not Streamlit

The panel became a monitor: it stays open on a screen while the worker runs by itself. Streamlit reruns the whole
script on every interaction and only refreshes the whole page, which makes the log "blink" and loses the scroll
position exactly when one is reading. Here each block reloads at its own pace — the log every 5 s, the numbers
every 30 s — swapping only the piece of HTML that changed.

## How the routes are organized

`GET /` returns the page shell. Everything else lives under `/fragmentos/*` and returns **a piece** of HTML, not a
page: that is what HTMX swaps in. Each fragment is a small stateless function that reads the data and renders a
template — which makes them testable with `TestClient` without a browser.

The panel **never writes to SAP itself**. Its actions are: arming the on-demand window in the tracking database
(`/fragmentos/janela/*`, which the worker picks up on its next pass), and starting CLI commands in a subprocess
(`/fragmentos/comandos/executar`, see `dashboard/comandos.py`). The commands that write need `PAINEL_SENHA` and a
non-production target (`Settings.painel_pode_escrever`); arming the window is the only exception to the production
block and still needs the password; one-quote `dirigido` commands need neither.
"""

from __future__ import annotations

import secrets
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import casa
from casa import destinos
from wbcpython import logs
from wbcpython.config import Settings, get_settings
from wbcpython.dashboard import acesso, dados, usuarios
from wbcpython.dashboard import comandos as cmd
from wbcpython.dashboard import previsao as prev
from wbcpython.dashboard.dados import (
    calcular_kpis,
    linha_para_tabela,
    resumo_de_execucao,
)
from wbcpython.domain import janela as jn
from wbcpython.domain.janela import EstadoDaJanela
from wbcpython.host.worker import janela_padrao
from wbcpython.tracking import RepositorioTracking, StatusIntegracao, TipoEvento

try:
    from fastapi import FastAPI, Form, Request, Response
    from fastapi.responses import HTMLResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates
except ImportError:  # pragma: no cover - depende do extra opcional
    print(
        "FastAPI não está instalado. Instale com: pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1) from None


AQUI = Path(__file__).parent
TEMPLATES = AQUI / "templates"
ESTATICOS = AQUI / "static"

#: De quantos em quantos segundos cada bloco se repinta.
#: O log é o único que muda entre um ciclo e outro do worker; os números só
#: mudam quando um ciclo termina, e repintá-los a cada 5 s seria consulta a
#: mais no banco de acompanhamento sem nada novo para mostrar.
RITMO_DO_LOG = 5
RITMO_DOS_NUMEROS = 30

#: O cookie de acesso e o HMAC moram em `acesso.py`: o Controle de Produção (outro processo,
#: CP_PORTA) valida o MESMO cookie — uma entrada vale para as duas telas. Re-exported here
#: under the old names for the painel's tests and callers.
COOKIE_DE_ACESSO = acesso.COOKIE_DE_ACESSO
_token_da_chave = acesso.token_da_chave
_igual = acesso.igual
_destino_local = acesso.destino_local

#: As telas do Controle de Produção (outro processo, CP_PORTA) atrás de cada botão do topo:
#: caminho do botão aqui → caminho na tela de lá. Sem entrada = a raiz de lá.
TELAS_DO_CONTROLE_DE_PRODUCAO = {"pedidos": "pedidos-wbc", "ops": "manutencao-op", "tarefas": "tarefas"}

#: O que abre sem chave: a própria tela de entrada e a ida para as outras telas.
#: `/static/*` e `/casa/*` também (o CSS da tela de entrada vem de lá).
ROTAS_ABERTAS = frozenset(
    {"/entrar", "/sair", "/sincronizacao", "/controle-producao", "/inicio", "/orcaview", "/favicon.ico"}
    | {f"/controle-producao/{tela}" for tela in TELAS_DO_CONTROLE_DE_PRODUCAO}
)

#: The screens of the shared top bar (casa/), seen from this painel: itself, and the redirect
#: routes below for the other two processes (they resolve host and port from the .env).
CASA_HREFS = {
    "integracao": "/",
    "pedidos": "/controle-producao/pedidos",
    "ops": "/controle-producao/ops",
    "sincronizacao": "/sincronizacao",
    "tarefas": "/controle-producao/tarefas",
}

#: Quantas linhas de acompanhamento o painel busca de uma vez. O mesmo teto
#: que o Streamlit usava: alto o bastante para a janela inteira, baixo o
#: bastante para a página não virar um megabyte de HTML.
TETO_DE_LINHAS = 5000

#: How many rows of the opportunities list go to the browser at once (29/09/2026). The
#: query still reads up to TETO_DE_LINHAS — it is SQLite, milliseconds — but rendering
#: ~2,000 rows x 13 columns made the tab heavy. The last row asks for the next page when it
#: scrolls into view (`hx-trigger="intersect once"`).
LINHAS_POR_PAGINA = 300


#: Column styles of the list: monospaced identifiers/dates and right-aligned money. One copy
#: for the header and the rows (they lived as a `{% set %}` in two templates).
COLUNAS_MONO = ("Orçamento", "SitCode", "Rev.", "Cotação", "Pedido", "Aberta em", "Última verificação")
COLUNAS_NUM = ("Valor do pedido",)


def _chave_do_orcamento(registro: Any) -> tuple[int, str]:
    """Stable order of the list: quote number, newest first (length first, so "9" < "10").

    30/09/2026: the list used to follow `atualizado_em`, which the worker rewrites on EVERY
    quote EVERY cycle — rows moved between pages while someone scrolled, and the next page
    repeated some and skipped others (reproduced: 50 of each). The quote number never moves.
    """
    return (len(registro.orcnum), registro.orcnum)

SAUDE = {
    "ok": ("ok", "Integração saudável"),
    "atencao": ("atencao", "Há orçamentos com erro"),
    "critico": ("critico", "Taxa de erro alta"),
    "sem_dados": ("neutro", "Nenhum orçamento processado ainda"),
}

#: Cor do selo de cada situação. Erro em vermelho, encerrada em neutro (é um
#: fim de linha esperado, não um problema), o resto em verde. Um selo cinza em
#: cima de um orçamento que falhou faz o erro passar despercebido na varredura.
SELO_DO_STATUS = {
    StatusIntegracao.ERRO: "critico",
    StatusIntegracao.PENDENTE: "atencao",
    StatusIntegracao.ENCERRADA: "neutro",
    StatusIntegracao.SEM_ACAO: "neutro",
}

#: The same colours by the label the list shows (the list rows carry labels, not enums).
#: Derived, so the list pill and the Detalhe badge cannot drift apart.
CLASSE_DA_SITUACAO = {s.rotulo: SELO_DO_STATUS.get(s, "ok") for s in StatusIntegracao}

#: As abas, na ordem em que aparecem. O `id` é a rota do fragmento.
def _orcamento_valido(valor: str) -> str:
    """A quote number from the URL, normalized to 8 digits — or "" for anything else."""
    numero = (valor or "").strip()
    return numero.zfill(8) if numero.isdigit() and len(numero) <= 8 else ""


#: Forms of the Executar tab that take ONE quote and are pre-filled from the Detalhe.
COMANDOS_DE_UM_ORCAMENTO = frozenset({"pendentes", "ciclo"})


ABAS = (
    ("oportunidades", "Oportunidades"),
    ("ciclo", "Próximo ciclo"),
    ("detalhe", "Detalhe"),
    ("comandos", "Executar"),
    # "Ciclos", not "Execuções" (01/10/2026, decision 4 of PLANO_CASA_COMUM_11): the shared top
    # bar has an "Execuções" of its own (the Controle de Produção's tasks). The id — and so every
    # saved `/?aba=execucoes` address — stays.
    ("execucoes", "Ciclos"),
    ("log", "Log"),
)

#: De quanto em quanto tempo o console acompanha um comando em andamento.
#: Mais curto que o log: aqui alguém está olhando, esperando terminar.
RITMO_DA_EXECUCAO = 1


@dataclass(frozen=True, slots=True)
class Barra:
    """One row of the bar chart.

    The panel draws bars in CSS, with no chart library: they are always categories with a count, the factory's browser
    is old, and a chart that fails to load is worse than a plain bar that always loads.
    """

    rotulo: str
    valor: int
    proporcao: float


def barras(pares: list[tuple[str, int]]) -> list[Barra]:
    maior = max((n for _, n in pares), default=0)
    return [Barra(r, n, (n / maior * 100) if maior else 0.0) for r, n in pares]


def criar_app(
    *,
    settings: Settings | None = None,
    tracking: RepositorioTracking | None = None,
    executor: cmd.Executor | None = None,
) -> FastAPI:
    """Builds the panel.

    `settings` and `tracking` come in as parameters so a test can build the panel over a temporary SQLite without any
    `.env` — the same reason the risk measurement could run against production without opening a write connection.
    Without `executor` it uses a `cmd.Executor` writing the snapshot to `prev.ARQUIVO_PADRAO`.
    """
    config = settings or get_settings()
    repo = tracking or RepositorioTracking.a_partir_da_url(
        config.tracking.db_url.get_secret_value()
    )
    corredor = executor or cmd.Executor(arquivo_do_retrato=str(prev.ARQUIVO_PADRAO))

    app = FastAPI(title="Integração WBC × SAP", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")
    # The shared shell of the .11 screens (top bar, palette, theme cookie) — the same files the
    # Controle de Produção and the 8077 serve.
    app.mount("/casa", StaticFiles(directory=casa.STATIC_DIR), name="casa")
    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.env.filters["moeda"] = _moeda
    templates.env.filters["numero"] = _numero
    casa.instalar(templates.env)
    templates.env.globals["CASA_HREFS"] = CASA_HREFS
    # What the bar's environment pill shows. The process's, not the request's: global.
    templates.env.globals["ambiente"] = {
        "em_producao": config.targets_production,
        "company_db": config.service_layer.company_db,
        "ambiente": f"{'produção' if config.targets_production else 'homologação'} — company DB "
                    f"'{config.service_layer.company_db}'",
    }

    def render(request: Request, nome: str, **contexto: Any) -> HTMLResponse:
        return templates.TemplateResponse(request, nome, contexto)

    # ---------------------------------------------------------------- entrada

    # A chave é a MESMA da API 8077 (`OS_API_KEY`): uma chave para as duas
    # telas, e um `.env` só. Sem chave configurada o painel fica aberto, como
    # a API — o que não muda é o comportamento de quem não configurou nada.
    #
    # Conferida uma vez, a chave vira um cookie HttpOnly com um token DERIVADO
    # dela (HMAC), não a chave em si: o navegador não a reexpõe, e trocar a
    # chave no `.env` invalida todos os cookies de uma vez, sem lista de sessões
    # para manter. Scripts e `curl` usam `X-API-Key` (ou `?key=`), como na API.
    chave = config.api_key.get_secret_value()
    token_esperado = _token_da_chave(chave) if chave else ""

    def _autenticado(request: Request) -> bool:
        return acesso.autenticado(request, chave, token_esperado)

    @app.middleware("http")
    async def exigir_chave(request: Request, call_next: Any) -> Any:
        caminho = request.url.path
        if caminho in ROTAS_ABERTAS or caminho.startswith(("/static/", "/casa/")):
            return await call_next(request)
        if _autenticado(request):
            if acesso.escrita_permitida(request, chave):
                return await call_next(request)
            return Response(
                "Recusado: o pedido não veio desta tela (origem diferente).", status_code=403
            )
        if request.headers.get("hx-request"):
            # Um fragmento pedido pelo HTMX com o cookie vencido: devolver a tela
            # de entrada dentro de um bloco da página seria uma tela quebrada.
            # `HX-Redirect` manda o navegador inteiro para /entrar.
            return Response(status_code=401, headers={"HX-Redirect": "/entrar"})
        proximo = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/entrar?proximo={quote(proximo, safe='')}", status_code=303)

    def _tela_de_entrada(request: Request, *, erro: str | None, proximo: str) -> HTMLResponse:
        return render(
            request,
            "entrar.html",
            erro=erro,
            proximo=_destino_local(proximo),
            producao=config.targets_production,
            company_db=config.service_layer.company_db,
        )

    @app.get("/entrar", response_class=HTMLResponse)
    def entrar(request: Request, proximo: str = "/") -> Any:
        if not chave:
            return RedirectResponse("/", status_code=303)
        return _tela_de_entrada(request, erro=None, proximo=proximo)

    @app.post("/entrar", response_class=HTMLResponse)
    def conferir_chave(
        request: Request, chave_informada: str = Form("", alias="chave"), proximo: str = Form("/")
    ) -> Any:
        if not chave:
            return RedirectResponse("/", status_code=303)
        if not _igual(chave_informada, chave):
            resposta = _tela_de_entrada(request, erro="Chave incorreta.", proximo=proximo)
            resposta.status_code = 401
            return resposta
        resposta = RedirectResponse(_destino_local(proximo), status_code=303)
        resposta.set_cookie(
            COOKIE_DE_ACESSO,
            token_esperado,
            max_age=30 * 24 * 3600,
            httponly=True,
            samesite="lax",
        )
        return resposta

    @app.post("/sair")
    def sair() -> RedirectResponse:
        """Forgets the key in this browser. There is no server-side session to end."""
        resposta = RedirectResponse("/entrar", status_code=303)
        resposta.delete_cookie(COOKIE_DE_ACESSO)
        return resposta

    @app.get("/sincronizacao")
    def sincronizacao(request: Request) -> RedirectResponse:
        """The button to the Painel de Sincronização (OS and Oportunidades, API 8077).

        Redirects instead of embedding the address in the HTML: the `.env` (`SIS_PAINEL_URL`) decides where the other
        screen lives, and with nothing configured it is the request's own host on the API port, at `/sincronizar` — the
        .11 case.
        """
        return RedirectResponse(_url_da_sincronizacao(config, request), status_code=302)

    @app.get("/inicio")
    def inicio(request: Request) -> RedirectResponse:
        """The brand of the shared bar → the Central's home page (``/inicio`` of the API 8077,
        beside the Sincronização). Open: it only redirects; that page asks for the key."""
        return RedirectResponse(destinos.inicio(config.sis_painel_url, request.url.scheme,
                                                request.url.hostname, config.os_api_port), status_code=302)

    @app.get("/orcaview")
    def orcaview() -> RedirectResponse:
        """The "← OrçaView" link of the shared bar (``ORCAVIEW_URL``, the .90 home). Open: it
        only redirects, and someone stuck at the key prompt must still be able to leave."""
        return RedirectResponse(config.orcaview_url, status_code=302)

    @app.get("/controle-producao")
    @app.get("/controle-producao/{tela}")
    def controle_producao(request: Request, tela: str = "") -> RedirectResponse:
        """The top-bar links to the Controle de Produção screens: ``/pedidos`` (Pedidos WBC),
        ``/ops`` (Manutenção de OP) and ``/tarefas`` (Execuções); anything else lands on its home.

        Another process on CP_PORTA, same key: the browser sends this painel's cookie
        there too (cookies ignore the port), so no second login.
        """
        caminho = TELAS_DO_CONTROLE_DE_PRODUCAO.get(tela, "")
        return RedirectResponse(destinos.na_tela(_url_do_controle_producao(config, request), caminho),
                                status_code=302)

    # ------------------------------------------------------------------ casca

    @app.get("/", response_class=HTMLResponse)
    def pagina(
        request: Request,
        aba: str = "oportunidades",
        tudo: int = 0,
        recorte: str = "",
        orcamento: str = "",
    ) -> HTMLResponse:
        """The whole page. From here on HTMX only swaps pieces.

        `aba` in the address, not in session state: the panel can stay open on a TV on a fixed tab, and an address
        pasted in the chat opens the same screen for whoever receives it. `recorte`, `tudo` and `orcamento` travel the
        same way.
        """
        corte = janela_padrao(meses=config.meses_de_janela)
        return render(
            request,
            "pagina.html",
            abas=ABAS,
            aba_ativa=aba if aba in dict(ABAS) else "oportunidades",
            tudo=bool(tudo),
            # O recorte vive no endereço, como a aba: `/?recorte=com_erro`
            # abre o painel já filtrado, e o endereço pode ser colado no chat.
            recorte=dados.recorte(recorte).id,
            corte=corte,
            meses=config.meses_de_janela,
            producao=config.targets_production,
            company_db=config.service_layer.company_db,
            ritmo_numeros=RITMO_DOS_NUMEROS,
            exige_chave=config.painel_exige_chave,
            orcamento=_orcamento_valido(orcamento),
        )

    # ------------------------------------------------------------- fragmentos

    @app.get("/fragmentos/kpis", response_class=HTMLResponse)
    def fragmento_kpis(request: Request, tudo: int = 0, recorte: str = "") -> HTMLResponse:
        """The numbers at the top — which are also the list's filter buttons.

        The active cut enters here only so the matching indicator shows as selected. It repaints every 30 s, and without
        it the mark would vanish by itself shortly after the click.
        """
        kpis = calcular_kpis(
            repo.listar(
                limite=TETO_DE_LINHAS,
                aberto_desde=_janela(config, tudo, _meses_em_vigor()),
            )
        )
        classe, texto = SAUDE[kpis.saude]
        return render(
            request,
            "_kpis.html",
            kpis=kpis,
            recortes=dados.RECORTES,
            ativo=dados.recorte(recorte).id,
            # O valor de cada indicador, por id de recorte: é o que permite ao
            # template percorrer os recortes em vez de repetir sete blocos
            # quase iguais — e o que mantém rótulo, número e filtro juntos.
            valores={
                "todos": kpis.total,
                "com_acao": kpis.com_acao,
                "sem_acao": kpis.sem_acao,
                "encerradas": kpis.encerradas,
                "com_erro": kpis.com_erro,
                "com_cotacao": kpis.com_cotacao,
                "com_pedido": kpis.com_pedido,
            },
            tudo=tudo,
            saude_classe=classe,
            saude_texto=texto,
            atualizado=_agora(),
        )

    @app.get("/fragmentos/oportunidades", response_class=HTMLResponse)
    def fragmento_oportunidades(
        request: Request,
        busca: str = "",
        status: str = "",
        tudo: int = 0,
        recorte: str = "",
        apos: str = "",
        inicio: int = 0,
    ) -> HTMLResponse:
        """The list, with the cut that came from the clicked indicator.

        `recorte` is the question the number at the top answers ("which ones failed?"); `status` remains the fine choice
        of one situation. The two add up: inside "com ação" one can still see only the closed ones.

        Paged since 29/09/2026, by key since 30/09/2026: without `apos` it renders the block with the first
        LINHAS_POR_PAGINA rows; `apos=<quote>` returns only the rows AFTER that quote (plus the row that loads the page
        after), swapped in place of the loading row. `inicio` is the offset form of 29/09, still honoured so a page left
        open across the deploy keeps working.
        """
        corte = dados.recorte(recorte)
        registros = dados.aplicar(
            repo.listar(
                status=_status(status),
                busca=busca.strip(),
                limite=TETO_DE_LINHAS,
                aberto_desde=_janela(config, tudo, _meses_em_vigor()),
            ),
            corte,
        )
        registros = sorted(registros, key=_chave_do_orcamento, reverse=True)
        total = len(registros)
        # Decided from what was ASKED, before any clamping: a "next page" request must get
        # rows back even when the filtered set emptied meanwhile — the whole block swapped
        # into a <tbody> nested a card inside the table.
        pagina_seguinte = bool(apos) or inicio > 0
        if apos:
            limite = (len(apos), apos)
            restantes = [r for r in registros if _chave_do_orcamento(r) < limite]
        else:
            restantes = registros[max(0, min(inicio, total)):]
        pagina = restantes[:LINHAS_POR_PAGINA]
        mostrando = total - len(restantes) + len(pagina)
        linhas = [linha_para_tabela(r) for r in pagina]
        mais = None
        if len(restantes) > len(pagina):
            mais = "/fragmentos/oportunidades?" + urlencode(
                {"busca": busca, "status": status, "tudo": tudo, "recorte": corte.id,
                 "apos": pagina[-1].orcnum}
            )
        contexto = dict(
            linhas=linhas,
            total=total,
            mostrando=mostrando,
            mais=mais,
            classe_situacao=CLASSE_DA_SITUACAO,
            mono=COLUNAS_MONO,
            num=COLUNAS_NUM,
        )
        if pagina_seguinte:
            return render(request, "_oportunidades_linhas.html", **contexto)
        return render(
            request,
            "_oportunidades.html",
            situacoes=[s.rotulo for s in StatusIntegracao],
            corte=corte,
            padrao=dados.RECORTE_PADRAO,
            busca=busca,
            status=status,
            tudo=tudo,
            **contexto,
        )

    @app.get("/fragmentos/ciclo", response_class=HTMLResponse)
    def fragmento_ciclo(
        request: Request,
        busca: str = "",
        status: str = "",
        sitcode: str = "",
        so_acao: int = 0,
        so_documento: int = 0,
    ) -> HTMLResponse:
        """What the worker would do on the next pass.

        Reads the snapshot on disk (`prev.ARQUIVO_PADRAO`, never a path from the URL); the panel does not call the
        simulation. See `dashboard/previsao` for why — in short, opening the page would cost a HANA and SQL Server scan.
        """
        # Never a path from the URL: `?arquivo=` used to be accepted here and in the log tab,
        # and the log tab printed ANY file of the server (the .env with every password) to
        # whoever had the key (review of 01/10/2026). The path is fixed by the painel.
        caminho = prev.ARQUIVO_PADRAO
        try:
            previsao = prev.carregar(caminho)
        except prev.RetratoAusente:
            return render(request, "_ciclo_sem_retrato.html", arquivo=str(caminho), erro=None)
        except (OSError, ValueError) as exc:
            return render(request, "_ciclo_sem_retrato.html", arquivo=str(caminho), erro=str(exc))

        linhas = list(previsao.linhas)
        kpis = prev.calcular_kpis(linhas)
        visiveis = prev.filtrar(
            linhas,
            busca=busca,
            status=status or None,
            sitcode=int(sitcode) if sitcode.strip().isdigit() else None,
            apenas_com_acao=bool(so_acao),
            apenas_documento=bool(so_documento),
        )
        return render(
            request,
            "_ciclo.html",
            previsao=previsao,
            idade=prev.idade(previsao),
            kpis=kpis,
            acima_do_teto=previsao.total > 0 and kpis.escreve > previsao.limite_de_escrita,
            acoes=barras(prev.contar_acoes(linhas)),
            por_status=barras(prev.contar(linhas, "status_oportunidade")),
            por_regra=prev.contar(linhas, "regra"),
            situacoes=[s for s, _ in prev.contar(linhas, "status_oportunidade")],
            sitcodes=sorted({linha.sitcode_wbc for linha in linhas}),
            linhas=[prev.linha_para_tabela(linha) for linha in visiveis],
            problemas=[
                (linha.orcnum or linha.oportunidade, linha.problema)
                for linha in visiveis
                if linha.problema
            ],
            visiveis=len(visiveis),
            arquivo=str(caminho),
            busca=busca,
            status=status,
            sitcode=sitcode,
            so_acao=so_acao,
            so_documento=so_documento,
        )

    @app.get("/fragmentos/detalhe", response_class=HTMLResponse)
    def fragmento_detalhe(request: Request, orcnum: str = "") -> HTMLResponse:
        alvo = orcnum.strip()
        if not alvo:
            return render(
                request, "_detalhe.html", orcnum="", registro=None, selo="neutro", eventos=[]
            )
        registro = repo.obter(alvo)
        return render(
            request,
            "_detalhe.html",
            orcnum=alvo,
            registro=registro,
            selo=SELO_DO_STATUS.get(registro.status, "ok") if registro else "neutro",
            eventos=repo.eventos(alvo) if registro else [],
        )

    @app.get("/fragmentos/execucoes", response_class=HTMLResponse)
    def fragmento_execucoes(request: Request) -> HTMLResponse:
        return render(
            request,
            "_execucoes.html",
            execucoes=[resumo_de_execucao(e) for e in repo.ultimas_execucoes()],
            ritmo=RITMO_DOS_NUMEROS,
            atualizado=_agora(),
        )

    @app.get("/fragmentos/log", response_class=HTMLResponse)
    def fragmento_log(
        request: Request,
        busca: str = "",
        nivel: str = "",
        limite: int = 300,
        seguir: int = 1,
    ) -> HTMLResponse:
        """The same log that goes to the command's screen.

        This is what makes the panel a monitor: the worker runs with nobody at the terminal, and here one sees what it
        did — in the same order and with the same text that would appear to someone watching. See `wbcpython.logs`, "one
        source, two screens". It reads `Settings.caminho_do_log`; `limite` is clamped to 10..5000 lines.
        """
        # Only the configured log — see `fragmento_ciclo` for why the URL no longer picks it.
        caminho = str(config.caminho_do_log or "")
        linhas = (
            logs.ler(
                caminho,
                limite=max(10, min(limite, 5000)),
                nivel=nivel or None,
                busca=busca,
            )
            if caminho
            else []
        )
        return render(
            request,
            "_log.html",
            arquivo=caminho,
            linhas=linhas,
            graves=sum(1 for linha in linhas if linha.grave),
            niveis=logs.NIVEIS,
            busca=busca,
            nivel=nivel,
            limite=limite,
            seguir=seguir,
            ritmo=RITMO_DO_LOG,
            atualizado=_agora(),
        )

    # -------------------------------------------------------------- comandos

    @app.get("/fragmentos/comandos", response_class=HTMLResponse)
    def fragmento_comandos(
        request: Request, escolhido: str = "", orcamento: str = ""
    ) -> HTMLResponse:
        """The "Executar" tab: the CLI commands, with what each one does.

        Three groups, kept apart: read-only commands, one-quote (`dirigido`) commands, and the protected ones that
        write. Mixing "test the connection" with "really create orders" in one list invites clicking the wrong one.
        `escolhido` and `orcamento` pre-select a card and pre-fill the quote (from the Detalhe tab).
        """
        return render(
            request,
            "_comandos.html",
            leitura=[c for c in cmd.CATALOGO
                     if not (c.escreve or c.escreve_tracking) and not c.oculto],
            # One quote at a time, no password, also in production (owner, 01/10/2026).
            dirigidos=[c for c in cmd.CATALOGO if c.dirigido and not c.oculto],
            escrita=[c for c in cmd.CATALOGO if c.protegido and not c.oculto],
            por_id=cmd.POR_ID,
            escolhido=escolhido,
            pode_escrever=config.painel_pode_escrever,
            motivo=_por_que_nao_escreve(config),
            producao=config.targets_production,
            company_db=config.service_layer.company_db,
            meses_dirigida=config.meses_de_janela_dirigida,
            ritmo=RITMO_DA_EXECUCAO,
            # From the Detalhe's "Processar este orçamento…" (01/10/2026): the per-quote
            # forms open filled in. Only a well-formed number gets through.
            orcamento_preenchido=_orcamento_valido(orcamento),
            de_um_orcamento=COMANDOS_DE_UM_ORCAMENTO,
        )

    def _meses_em_vigor() -> int:
        """The window the next cycle will use — the default, or the one that was armed."""
        pedido = repo.janela_pedida()
        if pedido.estado is EstadoDaJanela.ARMADO:
            return pedido.meses
        return config.meses_de_janela

    def _cartao_da_janela(request: Request, *, erro: str = "") -> HTMLResponse:
        """Draws the window card in the state it is in now.

        All the window routes end here, including the ones that refuse: the whole card comes back in the `hx-swap`, so
        whoever got the password wrong keeps seeing the real state, with the refusal beside it — not a loose warning
        where the control used to be.
        """
        return render(
            request,
            "_janela.html",
            pedido=repo.janela_pedida(),
            padrao=config.meses_de_janela,
            maximo=config.janela_maxima,
            producao=config.targets_production,
            pode_armar=config.painel_pode_armar_janela,
            motivo=_por_que_nao_arma(config),
            nomes=usuarios.sugestoes(config, repo),
            erro=erro,
        )

    @app.get("/fragmentos/janela", response_class=HTMLResponse)
    def fragmento_janela(request: Request) -> HTMLResponse:
        return _cartao_da_janela(request)

    @app.post("/fragmentos/janela/armar", response_class=HTMLResponse)
    async def armar_janela(request: Request) -> HTMLResponse:
        """Arms the extended window for the cycle's next pass.

        Asks for the password (decision 5 of the plan): arming does not write to SAP with its own hands, but it is the
        direct cause of hundreds of irreversible writes — it sits on the far side of the same line that separates "see"
        from "order to execute" in the rest of this screen. It works in production too (see
        `Settings.painel_pode_armar_janela`) and records a `REPROCESSAMENTO` event with who armed it.
        """
        form = await request.form()
        negativa = _autorizar(config, form, "Armar a janela", e_janela=True)
        if negativa:
            return _cartao_da_janela(request, erro=negativa)
        solicitante = str(form.get("solicitante") or "").strip()
        try:
            meses = jn.validar_meses(
                int(str(form.get("meses") or "0")),
                padrao=config.meses_de_janela,
                maximo=config.janela_maxima,
            )
        except ValueError as exc:
            # `int()` e `validar_meses` erram com a mesma exceção de propósito:
            # "abc" e "36" são o mesmo problema para quem está na tela — o
            # número não serve —, e a frase do domínio já explica o limite.
            return _cartao_da_janela(request, erro=str(exc))

        repo.armar_janela(meses, por=solicitante)
        repo.registrar_evento(
            "(ciclo)",
            tipo=TipoEvento.REPROCESSAMENTO,
            mensagem=(
                f"Janela de {meses} meses armada pelo painel por {solicitante}, "
                f"para a próxima passada."
            ),
            detalhes={"solicitante": solicitante, "meses": meses},
        )
        return _cartao_da_janela(request)

    @app.post("/fragmentos/janela/continuar", response_class=HTMLResponse)
    async def continuar_janela(request: Request) -> HTMLResponse:
        """The "yes" answer to the question: rearms the same window for one more cycle.

        Asks for the password like arming, for the same reason — this is the button that releases the next batch of
        writes to SAP. Refused if the question is no longer pending (`EstadoDaJanela.AGUARDANDO`).
        """
        form = await request.form()
        negativa = _autorizar(config, form, "Rodar outro ciclo", e_janela=True)
        if negativa:
            return _cartao_da_janela(request, erro=negativa)
        solicitante = str(form.get("solicitante") or "").strip()
        pedido = repo.janela_pedida()
        if pedido.estado is not EstadoDaJanela.AGUARDANDO:
            # A pergunta venceu (ou outra pessoa respondeu) entre a tela e o
            # clique. Rearmar aqui seria decidir por conta própria uma leva de
            # escritas que ninguém acabou de autorizar.
            return _cartao_da_janela(
                request,
                erro="A pergunta já não está de pé — o pedido venceu ou alguém respondeu antes.",
            )
        repo.armar_janela(pedido.meses, por=solicitante)
        repo.registrar_evento(
            "(ciclo)",
            tipo=TipoEvento.REPROCESSAMENTO,
            mensagem=(
                f"Novo ciclo com a janela de {pedido.meses} meses autorizado por "
                f"{solicitante} ({pedido.faltaram} oportunidade(s) tinham ficado de fora)."
            ),
            detalhes={"solicitante": solicitante, "meses": pedido.meses},
        )
        return _cartao_da_janela(request)

    @app.post("/fragmentos/janela/limpar", response_class=HTMLResponse)
    async def limpar_janela(request: Request) -> HTMLResponse:
        """Back to the default now.

        Does **not** ask for the password, on purpose, and is the only window route like that: disarming only reduces
        what the next cycle will write. Demanding a password to brake would turn the protection into an obstacle exactly
        on the button someone presses after being scared by the number.
        """
        repo.limpar_janela(motivo="Cancelada no painel.")
        return _cartao_da_janela(request)

    @app.post("/fragmentos/comandos/executar", response_class=HTMLResponse)
    async def executar(request: Request) -> HTMLResponse:
        """Starts a command.

        Two guards for two different problems: the **password** authorizes (anyone who reaches the port should not be
        able to create a document in SAP), and the **name** audits (weeks later someone asks who ordered the run). A
        password alone does not answer the second. Only `protegido` commands ask for the password; `dirigido` ones (one
        quote) skip it but must carry the quote and the name. Who started a protected or directed run is also recorded
        as a tracking event, because the in-memory history dies with the process.
        """
        form = await request.form()
        comando = cmd.POR_ID.get(str(form.get("comando") or ""))
        if comando is None:
            return render(request, "_aviso.html", tipo="erro", texto="Comando desconhecido.")
        if form.get("alternativo") and comando.alternativo:
            # O segundo botão do cartão. A troca vem **antes** da leitura dos
            # campos: cada comando declara os seus, e montar o argv com os do
            # outro daria uma linha que a CLI recusa.
            comando = cmd.POR_ID[comando.alternativo]

        valores = {c.nome: str(form.get(c.nome) or "") for c in comando.campos}

        if comando.protegido:
            negativa = _autorizar(config, form, comando.rotulo)
            if negativa:
                return render(request, "_aviso.html", tipo="erro", texto=negativa)
        if comando.dirigido:
            negativa = _conferir_dirigido(comando, valores, form)
            if negativa:
                return render(request, "_aviso.html", tipo="erro", texto=negativa)
        if comando.id == "pesos":
            problema = _conferir_alvo_do_peso(valores)
            if problema:
                return render(request, "_aviso.html", tipo="erro", texto=problema)

        solicitante = str(form.get("solicitante") or "").strip() or "(não informado)"
        try:
            corredor.iniciar(
                comando,
                valores,
                solicitante=solicitante,
                aplicar_pesos=bool(form.get("aplicar")),
            )
        except cmd.JaEmExecucao as exc:
            return render(request, "_aviso.html", tipo="atencao", texto=str(exc))
        except cmd.PreviaObrigatoria as exc:
            return render(request, "_aviso.html", tipo="atencao", texto=str(exc))
        except OSError as exc:
            return render(
                request, "_aviso.html", tipo="erro", texto=f"Não foi possível iniciar: {exc}"
            )

        if comando.protegido or comando.dirigido:
            # O painel não escreve no SAP; o comando escreve. Mas quem mandou
            # rodar precisa ficar registrado onde o histórico é lido — e o
            # histórico em memória do painel morre junto com o processo.
            repo.registrar_evento(
                valores.get("orcamento", "").strip() or "(ciclo)",
                tipo=TipoEvento.REPROCESSAMENTO,
                mensagem=f"{comando.rotulo} disparado pelo painel por {solicitante}.",
                detalhes={"solicitante": solicitante, "comando": comando.id},
            )
        return _console(request)

    @app.post("/fragmentos/comandos/interromper", response_class=HTMLResponse)
    def interromper(request: Request) -> HTMLResponse:
        corredor.interromper()
        return _console(request)

    @app.get("/fragmentos/execucao", response_class=HTMLResponse)
    def fragmento_execucao(request: Request) -> HTMLResponse:
        return _console(request)

    def _console(request: Request) -> HTMLResponse:
        atual = corredor.atual()
        historico = corredor.historico()
        # Sem nada rodando, o console mostra a última que rodou: chegar na aba
        # e ver um quadro vazio esconde justamente o resultado que se procura.
        mostrar = atual or (historico[0] if historico else None)
        return render(
            request,
            "_execucao.html",
            atual=atual,
            saida=corredor.saida(mostrar, limite=200) if mostrar else [],
            historico=historico,
            ritmo=RITMO_DA_EXECUCAO,
            alvo_da_previa=corredor.alvo_da_previa_de_peso(),
            atualizado=_agora(),
        )

    @app.get("/fragmentos/execucao/{identificador}", response_class=HTMLResponse)
    def fragmento_saida_passada(request: Request, identificador: int) -> HTMLResponse:
        alvo = next((e for e in corredor.historico() if e.id == identificador), None)
        if alvo is None:
            return render(
                request, "_aviso.html", tipo="atencao", texto="Execução não está mais na memória."
            )
        return render(
            request,
            "_saida.html",
            execucao=alvo,
            saida=corredor.saida(alvo),
        )

    return app


FALTA_SENHA = (
    "Falta definir PAINEL_SENHA no .env. Sem ela o painel não executa comando "
    "que escreve: o padrão de um campo esquecido não pode ser liberar."
)


def _por_que_nao_escreve(config: Settings) -> str:
    """The screen explains **which** of the two guards closed the door.

    "Unavailable" with no reason makes the operator look for a defect where there is a rule — and the two causes have
    opposite remedies: one is solved by filling in the `.env`, the other must not be solved.
    """
    if config.targets_production:
        return (
            f"O painel está apontado para produção ({config.service_layer.company_db}). "
            "Estes comandos, ali, são pelo terminal, com alguém responsável na "
            "frente — ver RISCOS_PRODUCAO.md. O ciclo de um orçamento, acima, roda daqui."
        )
    if not config.painel_senha.get_secret_value():
        return FALTA_SENHA
    return ""


def _por_que_nao_arma(config: Settings) -> str:
    """Empty when the window can be armed; the reason when it cannot.

    One condition, not two: arming is an exception to the production block (see `Settings.painel_pode_armar_janela`).
    The password still applies, and here it is the **only** guard.
    """
    return "" if config.painel_pode_armar_janela else FALTA_SENHA


def _autorizar(
    config: Settings, form: Any, rotulo: str, *, e_janela: bool = False
) -> str:
    """Empty when it may proceed; the reason for the refusal when it may not.

    Returns text, not a ready fragment, so it can be tested without building a request — and because the route is the
    one that renders, having `render`.

    `e_janela` swaps the precondition for arming's, which is not blocked in production. The rest — a name to audit,
    the password checked with `compare_digest` — is identical on purpose: the exception is about **which** door is
    crossed, not about crossing without a key.
    """
    if e_janela:
        if not config.painel_pode_armar_janela:
            return _por_que_nao_arma(config)
    elif not config.painel_pode_escrever:
        return _por_que_nao_escreve(config)
    if not str(form.get("solicitante") or "").strip():
        return "Informe quem está executando — a ação precisa ser auditável."
    informada = str(form.get("senha") or "")
    # `compare_digest` e não `==`: comparar string vaza tempo, e do outro lado
    # há uma rede interna inteira.
    if not secrets.compare_digest(informada, config.painel_senha.get_secret_value()):
        return f"Senha incorreta. “{rotulo}” não foi executado."
    return ""


def _conferir_dirigido(comando: cmd.Comando, valores: dict[str, str], form: Any) -> str:
    """The gate of a one-quote command: the quote (digits; normalized to the WBC's 8) and
    the operator's name. Empty when it may run.

    The quote is what makes skipping the password safe (see ``Comando.dirigido``): without
    it the CLI would run the whole window, so its absence is refused here, not left to the
    browser's ``required``. ``valores`` is normalized in place so the CLI gets ``00123566``
    even when ``123566`` was typed — the HANA filter compares the text.
    """
    for campo in comando.campos:
        if not campo.obrigatorio:
            continue
        bruto = valores.get(campo.nome, "").strip()
        if not bruto:
            return f"Informe o {campo.rotulo.lower()}: este comando age só sobre ele."
        if campo.nome == "orcamento":
            if not bruto.isdigit() or len(bruto) > 8:
                return f"Orçamento inválido: “{bruto}”. Use só os números (ex.: 00123566)."
            valores[campo.nome] = bruto.zfill(8)
    if not str(form.get("solicitante") or "").strip():
        return "Informe quem está executando — a ação precisa ser auditável."
    return ""


def _conferir_alvo_do_peso(valores: dict[str, str]) -> str:
    """`pesos` takes an order **or** a quote, never both.

    The CLI already refuses (argparse mutually exclusive group), but its refusal would come out as usage text in the
    console — the screen can say it better.
    """
    pedido = valores.get("pedido", "").strip()
    orcamento = valores.get("orcamento", "").strip()
    if pedido and orcamento:
        return "Informe o pedido **ou** o orçamento, não os dois."
    if not pedido and not orcamento:
        return "Informe o pedido ou o orçamento."
    return ""


def _janela(config: Settings, tudo: int, meses: int | None = None) -> date | None:
    """`None` = show the whole history, including what the cycle no longer looks at.

    The cut is the **same** as the worker's (`OOPR.OpenDate`): when the window shrank from 6 to 3 months, 167 May
    quotes stayed on the panel as if the cycle still evaluated them. Two screens on the same subject cannot give
    different numbers.

    `meses` carries the window **in force**, which since the on-demand window may be larger than the `.env`'s. Without
    it, arming 24 months would change what the cycle scans without changing what the list shows — and the screen would
    hide exactly the old opportunities someone just asked to reach.
    """
    return None if tudo else janela_padrao(meses=meses or config.meses_de_janela)


def _status(rotulo: str) -> StatusIntegracao | None:
    return next((s for s in StatusIntegracao if s.rotulo == rotulo), None)


def _agora() -> str:
    """The operator's wall clock, without time zone.

    The tracking database stores naive local times on purpose, and this stamp sits on the screen beside them. One of
    them in UTC and the other not would be a three-hour difference nobody notices until comparing with the time of a
    document in SAP.
    """
    return datetime.now().strftime("%H:%M:%S")  # noqa: DTZ005


def _moeda(valor: Any) -> str:
    if valor in (None, ""):
        return "—"
    inteiro, _, centavos = f"{valor:.2f}".partition(".")
    return f"R$ {int(inteiro):,}".replace(",", ".") + f",{centavos}"


def _numero(valor: Any) -> str:
    return f"{int(valor):,}".replace(",", ".")


def _url_do_controle_producao(config: Settings, request: Request) -> str:
    """Where the Controle de Produção screen lives: ``CP_URL`` verbatim, or the same host
    on ``CP_PORTA`` — the .11 case, all screens on one machine (``casa/destinos.py``)."""
    return destinos.endereco(config.cp_url, request.url.scheme, request.url.hostname, config.cp_porta)


def _url_da_sincronizacao(config: Settings, request: Request) -> str:
    """The Painel de Sincronização: ``SIS_PAINEL_URL``, or the same host on the API port at
    ``/sincronizar`` — the API root bounces back here (this painel is the entrance), so
    pointing the link at it would be a round trip."""
    return destinos.endereco(config.sis_painel_url, request.url.scheme, request.url.hostname,
                             config.os_api_port, "/sincronizar")
