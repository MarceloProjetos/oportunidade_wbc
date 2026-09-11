"""Painel de acompanhamento — HTML servido por FastAPI, atualizado com HTMX.

Rodar com:

    python -m wbcpython dashboard

Lê **apenas** o banco de acompanhamento e, na aba "Próximo ciclo", um retrato
em disco gerado por `wbcpython pendentes --exportar`. Nunca consulta o SAP nem
o WBC — ver `ai_spec/03_architecture.md` e `dashboard/__init__`.

## Por que HTMX e não Streamlit

O painel virou monitor: fica aberto numa tela enquanto o worker roda sozinho.
O Streamlit reexecuta o script inteiro a cada interação e só atualiza a página
inteira, o que faz o log "piscar" e perde a posição de rolagem justamente
quando se está lendo. Aqui cada bloco se recarrega no seu próprio ritmo — o log
a cada 5 s, os números a cada 30 s — trocando só o pedaço de HTML que mudou.

## Como as rotas estão organizadas

`GET /` devolve a casca da página. Todo o resto vive em `/fragmentos/*` e
devolve **um pedaço** de HTML, não uma página: é o que o HTMX troca no lugar.
Cada fragmento é uma função pequena, sem estado, que lê os dados e renderiza um
template — o que os torna testáveis com o `TestClient` sem navegador nenhum.

O painel **não escreve no SAP**. A única ação é registrar um pedido de
reprocessamento no banco de acompanhamento, que o worker executa no próximo
ciclo (ver `_reprocessar`).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from wbcpython import logs
from wbcpython.config import Settings, get_settings
from wbcpython.dashboard import comandos as cmd
from wbcpython.dashboard import dados
from wbcpython.dashboard import previsao as prev
from wbcpython.dashboard.dados import (
    calcular_kpis,
    linha_para_tabela,
    registrar_reprocessamento,
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

#: Cookie que prova que a chave já foi conferida neste navegador. Guarda um
#: token derivado da chave (HMAC), nunca a chave.
COOKIE_DE_ACESSO = "wbc_painel"

#: O que abre sem chave: a própria tela de entrada e a ida para a outra tela.
#: `/static/*` também (o CSS da tela de entrada vem de lá).
ROTAS_ABERTAS = frozenset({"/entrar", "/sair", "/sincronizacao", "/favicon.ico"})

#: Quantas linhas de acompanhamento o painel busca de uma vez. O mesmo teto
#: que o Streamlit usava: alto o bastante para a janela inteira, baixo o
#: bastante para a página não virar um megabyte de HTML.
TETO_DE_LINHAS = 5000

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

#: As abas, na ordem em que aparecem. O `id` é a rota do fragmento.
ABAS = (
    ("oportunidades", "Oportunidades"),
    ("ciclo", "Próximo ciclo"),
    ("detalhe", "Detalhe"),
    ("comandos", "Executar"),
    ("execucoes", "Execuções"),
    ("log", "Log"),
)

#: De quanto em quanto tempo o console acompanha um comando em andamento.
#: Mais curto que o log: aqui alguém está olhando, esperando terminar.
RITMO_DA_EXECUCAO = 1


@dataclass(frozen=True, slots=True)
class Barra:
    """Uma linha do gráfico de barras.

    O painel desenha barras em CSS, sem biblioteca de gráfico: são sempre
    categorias com uma contagem, o navegador da fábrica é antigo e um gráfico
    que não carrega é pior do que uma barra simples que carrega sempre.
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
    """Monta o painel.

    `settings` e `tracking` entram por parâmetro para que o teste construa o
    painel sobre um SQLite temporário sem `.env` nenhum — a mesma razão pela
    qual a medição de riscos conseguiu rodar contra produção sem abrir conexão
    de escrita.
    """
    config = settings or get_settings()
    repo = tracking or RepositorioTracking.a_partir_da_url(
        config.tracking.db_url.get_secret_value()
    )
    corredor = executor or cmd.Executor(arquivo_do_retrato=str(prev.ARQUIVO_PADRAO))

    app = FastAPI(title="Integração WBC × SAP", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.env.filters["moeda"] = _moeda
    templates.env.filters["numero"] = _numero

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
        if not chave:
            return True
        cookie = request.cookies.get(COOKIE_DE_ACESSO, "")
        if cookie and _igual(cookie, token_esperado):
            return True
        enviada = request.headers.get("x-api-key") or request.query_params.get("key") or ""
        return bool(enviada) and _igual(enviada, chave)

    @app.middleware("http")
    async def exigir_chave(request: Request, call_next: Any) -> Any:
        caminho = request.url.path
        if caminho in ROTAS_ABERTAS or caminho.startswith("/static/") or _autenticado(request):
            return await call_next(request)
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
        """Esquece a chave neste navegador. Não há sessão no servidor a encerrar."""
        resposta = RedirectResponse("/entrar", status_code=303)
        resposta.delete_cookie(COOKIE_DE_ACESSO)
        return resposta

    @app.get("/sincronizacao")
    def sincronizacao(request: Request) -> RedirectResponse:
        """O botão para o Painel de Sincronização (OS e Oportunidades, API 8077).

        Redireciona em vez de embutir o endereço no HTML: quem decide onde a
        outra tela mora é o `.env` (`SIS_PAINEL_URL`), e sem nada configurado é
        o mesmo host da requisição, na porta da API — o caso da .11.
        """
        return RedirectResponse(_url_da_sincronizacao(config, request), status_code=302)

    # ------------------------------------------------------------------ casca

    @app.get("/", response_class=HTMLResponse)
    def pagina(
        request: Request, aba: str = "oportunidades", tudo: int = 0, recorte: str = ""
    ) -> HTMLResponse:
        """A página inteira. Daqui em diante o HTMX só troca pedaços.

        `aba` no endereço, e não em estado de sessão: assim o painel pode ficar
        aberto numa TV numa aba fixa, e um endereço colado no chat abre a mesma
        tela para quem receber.
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
        )

    # ------------------------------------------------------------- fragmentos

    @app.get("/fragmentos/kpis", response_class=HTMLResponse)
    def fragmento_kpis(request: Request, tudo: int = 0, recorte: str = "") -> HTMLResponse:
        """Os números do topo — que são também os botões de filtro da lista.

        O recorte ativo entra aqui só para o indicador correspondente aparecer
        marcado. Ele repinta a cada 30 s, e sem isso a marca sumiria sozinha
        pouco depois do clique.
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
    ) -> HTMLResponse:
        """A lista, com o recorte que veio do indicador clicado.

        `recorte` é a pergunta que o número do topo responde ("quais deram
        erro?"); `status` continua sendo a escolha fina de uma situação. Os dois
        se somam: dentro de "com ação" ainda dá para ver só as encerradas.
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
        return render(
            request,
            "_oportunidades.html",
            linhas=[linha_para_tabela(r) for r in registros],
            situacoes=[s.rotulo for s in StatusIntegracao],
            corte=corte,
            padrao=dados.RECORTE_PADRAO,
            busca=busca,
            status=status,
            tudo=tudo,
        )

    @app.get("/fragmentos/ciclo", response_class=HTMLResponse)
    def fragmento_ciclo(
        request: Request,
        arquivo: str = "",
        busca: str = "",
        status: str = "",
        sitcode: str = "",
        so_acao: int = 0,
        so_documento: int = 0,
    ) -> HTMLResponse:
        """O que o worker faria na próxima passada.

        Lê o retrato em disco; o painel não chama a simulação. Ver
        `dashboard/previsao` para o porquê — em resumo, abrir a página passaria
        a custar uma varredura no HANA e no SQL Server.
        """
        caminho = Path(arquivo) if arquivo.strip() else prev.ARQUIVO_PADRAO
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

    @app.post("/fragmentos/reprocessar", response_class=HTMLResponse)
    async def reprocessar(request: Request) -> HTMLResponse:
        """Registra a solicitação — **não** dispara a integração.

        O processo do painel nunca escreve no SAP: além de quebrar a separação
        de responsabilidades, contornaria a trava de execução única do worker,
        que é o que impede dois ciclos criando o mesmo documento. Quem executa
        é o worker, no ciclo seguinte.

        O nome de quem pediu é obrigatório porque a solicitação termina em
        documento financeiro criado, e "quem mandou reprocessar isso?" é uma
        pergunta que aparece semanas depois.
        """
        form = await request.form()
        orcnum = str(form.get("orcnum") or "").strip()
        solicitante = str(form.get("solicitante") or "").strip()
        if not orcnum:
            return render(request, "_aviso.html", tipo="erro", texto="Informe o orçamento.")
        if not solicitante:
            return render(
                request,
                "_aviso.html",
                tipo="erro",
                texto="Informe quem está solicitando — a ação precisa ser auditável.",
            )
        registrar_reprocessamento(repo, orcnum, solicitante=solicitante)
        return render(
            request,
            "_aviso.html",
            tipo="ok",
            texto=(
                f"Solicitação registrada por {solicitante}. O orçamento {orcnum} será "
                "reavaliado no próximo ciclo do worker."
            ),
            comando=f"python -m wbcpython ciclo --orcamento {orcnum}",
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
        arquivo: str = "",
        busca: str = "",
        nivel: str = "",
        limite: int = 300,
        seguir: int = 1,
    ) -> HTMLResponse:
        """O mesmo log que sai na tela do comando.

        É o que torna o painel um monitor: o worker roda sem ninguém no
        terminal, e é aqui que se vê o que ele fez — na mesma ordem e com o
        mesmo texto que apareceria para quem estivesse olhando. Ver
        `wbcpython.logs`, "uma fonte, duas telas".
        """
        caminho = arquivo.strip() or str(config.caminho_do_log or "")
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
    def fragmento_comandos(request: Request, escolhido: str = "") -> HTMLResponse:
        """A aba "Executar": os comandos da CLI, com o que cada um faz.

        Quem escreve no SAP aparece separado e com aviso. Misturar "testar a
        conexão" com "criar pedidos de verdade" na mesma lista é convite a
        clicar no errado.
        """
        return render(
            request,
            "_comandos.html",
            leitura=[c for c in cmd.CATALOGO if not c.protegido],
            escrita=[c for c in cmd.CATALOGO if c.protegido],
            escolhido=escolhido,
            pode_escrever=config.painel_pode_escrever,
            motivo=_por_que_nao_escreve(config),
            producao=config.targets_production,
            company_db=config.service_layer.company_db,
            ritmo=RITMO_DA_EXECUCAO,
        )

    def _meses_em_vigor() -> int:
        """A janela que o próximo ciclo vai usar — padrão, ou a que foi armada."""
        pedido = repo.janela_pedida()
        if pedido.estado is EstadoDaJanela.ARMADO:
            return pedido.meses
        return config.meses_de_janela

    def _cartao_da_janela(request: Request, *, erro: str = "") -> HTMLResponse:
        """Desenha o card da janela no estado em que ele está agora.

        Todas as rotas da janela terminam aqui, inclusive as que recusam: é o
        card inteiro que volta no `hx-swap`, então quem errou a senha continua
        vendo o estado real, com a recusa ao lado — e não um aviso solto no
        lugar onde estava o controle.
        """
        pedido = repo.janela_pedida()
        agora = datetime.now()
        return render(
            request,
            "_janela.html",
            pedido=pedido,
            padrao=config.meses_de_janela,
            maximo=config.janela_maxima,
            espera_minutos=config.janela_espera_minutos,
            teto=jn.teto_de_escrita(
                pedido.meses or config.meses_de_janela,
                padrao=config.meses_de_janela,
                base=config.limite_de_escrita_por_ciclo,
                absoluto=config.teto_absoluto_de_escrita,
            ),
            pode_armar=config.painel_pode_armar_janela,
            motivo=_por_que_nao_arma(config),
            producao=config.targets_production,
            company_db=config.service_layer.company_db,
            fora_do_expediente=not (
                config.e_dia_de_trabalho(agora.date())
                and config.dentro_do_horario_do_worker(agora.time())
            ),
            expediente=(
                f"{config.dias_de_trabalho_por_extenso}, "
                f"{config.worker_horario_inicio.strftime('%H:%M')}–"
                f"{config.worker_horario_fim.strftime('%H:%M')}"
            ),
            erro=erro,
        )

    @app.get("/fragmentos/janela", response_class=HTMLResponse)
    def fragmento_janela(request: Request) -> HTMLResponse:
        return _cartao_da_janela(request)

    @app.post("/fragmentos/janela/armar", response_class=HTMLResponse)
    async def armar_janela(request: Request) -> HTMLResponse:
        """Arma a janela estendida para a próxima passada do ciclo.

        Pede senha (decisão 5 do plano): armar não escreve no SAP com as
        próprias mãos, mas é a causa direta de centenas de escritas
        irreversíveis — está do lado de lá da mesma linha que separa "ver" de
        "mandar executar" no resto desta tela.
        """
        form = await request.form()
        negativa = _autorizar(config, form, "Armar a janela", e_janela=True)
        if negativa:
            return _cartao_da_janela(request, erro=negativa)
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

        solicitante = str(form.get("solicitante") or "").strip()
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
        """A resposta "sim" à pergunta: rearma a mesma janela por mais um ciclo.

        Pede senha como o armar, e pela mesma razão — este é o botão que libera
        a próxima leva de escritas no SAP.
        """
        form = await request.form()
        negativa = _autorizar(config, form, "Rodar outro ciclo", e_janela=True)
        if negativa:
            return _cartao_da_janela(request, erro=negativa)
        pedido = repo.janela_pedida()
        if pedido.estado is not EstadoDaJanela.AGUARDANDO:
            # A pergunta venceu (ou outra pessoa respondeu) entre a tela e o
            # clique. Rearmar aqui seria decidir por conta própria uma leva de
            # escritas que ninguém acabou de autorizar.
            return _cartao_da_janela(
                request,
                erro="A pergunta já não está de pé — o pedido venceu ou alguém respondeu antes.",
            )
        solicitante = str(form.get("solicitante") or "").strip()
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
        """Volta ao padrão agora.

        **Não** pede senha, de propósito, e é a única rota da janela assim:
        desarmar só reduz o que o próximo ciclo vai escrever. Exigir senha para
        frear seria transformar a proteção em obstáculo justamente no botão que
        alguém aperta quando se assustou com o número.
        """
        repo.limpar_janela(motivo="Cancelada no painel.")
        return _cartao_da_janela(request)

    @app.post("/fragmentos/comandos/executar", response_class=HTMLResponse)
    async def executar(request: Request) -> HTMLResponse:
        """Dispara um comando.

        Duas guardas, para dois problemas diferentes: a **senha** autoriza (o
        painel não tem autenticação, e quem alcança a porta não deveria poder
        criar documento no SAP), e o **nome** audita (semanas depois alguém
        pergunta quem mandou rodar). Senha sozinha não responde à segunda.
        """
        form = await request.form()
        comando = cmd.POR_ID.get(str(form.get("comando") or ""))
        if comando is None:
            return render(request, "_aviso.html", tipo="erro", texto="Comando desconhecido.")

        valores = {c.nome: str(form.get(c.nome) or "") for c in comando.campos}

        if comando.protegido:
            negativa = _autorizar(config, form, comando.rotulo)
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

        if comando.protegido:
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


def _por_que_nao_arma(config: Settings) -> str:
    """Vazio quando dá para armar a janela; o motivo quando não dá.

    Só uma condição, e não duas: armar é exceção ao bloqueio de produção (ver
    `Settings.painel_pode_armar_janela`). A senha continua sendo exigida, e aqui
    ela é a **única** guarda.
    """
    return "" if config.painel_pode_armar_janela else FALTA_SENHA


def _por_que_nao_escreve(config: Settings) -> str:
    """A tela explica **qual** das duas guardas fechou a porta.

    "Indisponível" sem motivo faz o operador procurar defeito onde há regra —
    e as duas causas têm remédios opostos: uma se resolve preenchendo o `.env`,
    a outra não deve ser resolvida.
    """
    if config.targets_production:
        return (
            f"O painel está apontado para produção ({config.service_layer.company_db}). "
            "Execução que escreve, ali, é pelo terminal, com alguém responsável na "
            "frente — ver RISCOS_PRODUCAO.md."
        )
    if not config.painel_senha.get_secret_value():
        return FALTA_SENHA
    return ""


def _autorizar(
    config: Settings, form: Any, rotulo: str, *, e_janela: bool = False
) -> str:
    """Vazio quando pode seguir; o motivo da recusa quando não pode.

    Devolve texto, e não um fragmento pronto, para poder ser testada sem montar
    requisição — e porque quem renderiza é a rota, que tem o `render`.

    `e_janela` troca a pré-condição pela do armar, que não é barrado em
    produção. O resto — nome para auditar, senha conferida com `compare_digest`
    — é idêntico de propósito: a exceção é sobre **qual** porta se atravessa,
    não sobre atravessar sem chave.
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


def _conferir_alvo_do_peso(valores: dict[str, str]) -> str:
    """O `pesos` aceita pedido **ou** orçamento, nunca os dois.

    A CLI já recusa (grupo mutuamente exclusivo do argparse), mas a recusa dela
    sairia como texto de uso no console — a tela consegue dizer melhor.
    """
    pedido = valores.get("pedido", "").strip()
    orcamento = valores.get("orcamento", "").strip()
    if pedido and orcamento:
        return "Informe o pedido **ou** o orçamento, não os dois."
    if not pedido and not orcamento:
        return "Informe o pedido ou o orçamento."
    return ""


def _janela(config: Settings, tudo: int, meses: int | None = None) -> date | None:
    """`None` = mostrar o histórico inteiro, inclusive o que o ciclo já não olha.

    O corte é o **mesmo** do worker (`OOPR.OpenDate`): quando a janela encolheu
    de 6 para 3 meses, 167 orçamentos de maio continuaram no painel como se o
    ciclo ainda os avaliasse. Duas telas sobre o mesmo assunto não podem dar
    números diferentes.

    `meses` carrega a janela **em vigor**, que desde a janela sob demanda pode
    ser maior que a do `.env`. Sem isto, armar 24 meses mudaria o que o ciclo
    varre sem mudar o que a lista mostra — e a tela esconderia justamente as
    oportunidades antigas que alguém acabou de pedir para alcançar.
    """
    return None if tudo else janela_padrao(meses=meses or config.meses_de_janela)


def _status(rotulo: str) -> StatusIntegracao | None:
    return next((s for s in StatusIntegracao if s.rotulo == rotulo), None)


def _agora() -> str:
    """A hora do relógio de quem opera, sem fuso.

    O banco de acompanhamento grava horários locais ingênuos de propósito (ver
    `pyproject.toml`), e este carimbo fica na tela ao lado deles. Um deles em
    UTC e outro não seria uma diferença de três horas que ninguém percebe até
    comparar com o horário de um documento no SAP.
    """
    return datetime.now().strftime("%H:%M:%S")  # noqa: DTZ005


def _moeda(valor: Any) -> str:
    if valor in (None, ""):
        return "—"
    inteiro, _, centavos = f"{valor:.2f}".partition(".")
    return f"R$ {int(inteiro):,}".replace(",", ".") + f",{centavos}"


def _numero(valor: Any) -> str:
    return f"{int(valor):,}".replace(",", ".")


def _token_da_chave(chave: str) -> str:
    """O que vai no cookie: HMAC da chave, e não a chave.

    Quem lê o cookie não recupera a chave; quem troca a chave no `.env`
    derruba todos os cookies emitidos, sem estado no servidor.
    """
    return hmac.new(chave.encode("utf-8"), b"painel-wbc", hashlib.sha256).hexdigest()


def _igual(a: str, b: str) -> bool:
    """`compare_digest` sobre bytes: comparar `str` vaza tempo, e uma chave com
    acento faria `compare_digest` levantar `TypeError` (vira 500 em vez de 401)."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _destino_local(proximo: str) -> str:
    """Só volta para um caminho DESTA página. `//outro-host` e `http://…` viram `/`:
    o `proximo` vem da URL, e a tela de entrada não pode virar redirecionador."""
    proximo = (proximo or "").strip()
    if not proximo.startswith("/") or proximo.startswith("//") or "\\" in proximo:
        return "/"
    return proximo


def _url_da_sincronizacao(config: Settings, request: Request) -> str:
    """Endereço do Painel de Sincronização: o configurado, ou o mesmo host na porta da
    API, em `/sincronizar` — a raiz da API leva de volta para cá (este painel é a
    entrada), e apontar o botão para ela seria um vaivém."""
    configurado = config.sis_painel_url.strip()
    if configurado:
        return configurado
    return f"{request.url.scheme}://{request.url.hostname}:{config.os_api_port}/sincronizar"
