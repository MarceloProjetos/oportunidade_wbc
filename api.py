"""API HTTP mínima para disparar a sincronização de OS por NPED (sob demanda).

Pensada para ser chamada **pelo app** (web/desktop) quando o usuário pede para
sincronizar um pedido. A escrita continua sendo do backend (service_role); este
serviço só expõe um gatilho HTTP.

Endpoints
---------
- ``GET  /health``                         → ``{"status": "ok"}``
- ``GET  /ordens-servico/<nped>``           → detalhe (resumo) da OS de um pedido
- ``POST /ordens-servico/<nped>/sincronizar`` → sincroniza + devolve o resumo (par do GET)
- ``POST /sync/ordens-servico/<nped>``      → sincroniza **um** pedido
- ``POST /sync/ordens-servico``             → corpo ``{"nped": N}`` ou ``{"npeds": [...]}``

Autenticação (opcional, **recomendada em produção**)
----------------------------------------------------
Defina ``OS_API_KEY`` no ``.env``. O cliente deve enviar o header
``X-API-Key: <chave>`` (ou ``Authorization: Bearer <chave>``). Sem ``OS_API_KEY``,
o endpoint fica **aberto** (use só em rede interna confiável / desenvolvimento).

Como rodar
----------
- Dev/Produção:  ``python api.py`` (forma suportada — sobe via waitress se instalado,
                 senão Flask dev, e configura o log em arquivo ``logs/api.log``).
- Alternativa:   ``waitress-serve --listen=0.0.0.0:8077 api:app`` — importa só o ``app``,
                 então **não** passa por ``main()``: o log usa o default (sem arquivo).

Exemplo de chamada::

    curl -X POST http://localhost:8077/sync/ordens-servico/84080 \\
         -H "X-API-Key: SUA_CHAVE"
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from logging.handlers import TimedRotatingFileHandler
from typing import Any, List, Optional, Tuple

from flask import Flask, jsonify, request, send_from_directory

from config import get_settings
from extract_ordens_servico_engenharia import (
    diagnosticar_nped,
    listar_pedidos_com_os,
)
from extract_ordens_servico_engenharia import (
    main as sync_os,
)
from extract_sap_to_supabase import main as sync_oportunidades
from monitoring import SELECTABLE_CHECKS, collect_status
from pipeline_core import FileLockTimeout, coerce_positive_int, oportunidades_sync_lock

# UTF-8 console on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Configura o log (arquivo rotativo + console).

    Chamado só pelo entrypoint (``main``), **não no import** — assim importar o
    módulo nos testes não redireciona o log da suíte para ``logs/api.log``. Com
    arquivo, o log da API persiste ao fechar a janela / rodar como serviço.
    """
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        os.path.join(log_dir, 'api.log'),
        when='midnight', interval=1, backupCount=6, encoding='utf-8',
    )
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[file_handler, logging.StreamHandler()],
        force=True,
    )
    logging.getLogger('httpx').setLevel(logging.WARNING)

app = Flask(__name__)
_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')

# Serializa as cargas: nunca duas sincronizações simultâneas (evita múltiplas
# conexões SAP e corridas no replace_nped). O volume é baixo (gatilho sob demanda).
_sync_lock = threading.Lock()

# ── Rate-limit das ESCRITAS (trava anti-loop) ──────────────────────────────────────
# Janela deslizante in-process por "bucket". GENEROSO de propósito: pega runaway/loop de
# agente sem atrapalhar uso normal (uma pessoa/uso comum fica muito abaixo). Configurável
# por env: RATE_SYNC_OS_MAX (syncs de OS/min) e RATE_FORCE_OPORT_MAX (cargas completas/min).
_RATE_WINDOW_S = 60.0
_RATE_SYNC_OS_MAX = int(os.getenv('RATE_SYNC_OS_MAX', '60'))
_RATE_FORCE_OPORT_MAX = int(os.getenv('RATE_FORCE_OPORT_MAX', '6'))


class _RateLimiter:
    """Janela deslizante thread-safe: conta chamadas por 'bucket' e diz se passou do limite."""

    def __init__(self) -> None:
        self._hits: dict = {}
        self._lock = threading.Lock()

    def check(self, bucket: str, limite: int, janela_s: float) -> Tuple[bool, float]:
        """Registra um hit; retorna ``(permitido, segundos_até_liberar)``."""
        agora = time.monotonic()
        corte = agora - janela_s
        with self._lock:
            hits = [t for t in self._hits.get(bucket, ()) if t > corte]
            if len(hits) >= limite:
                self._hits[bucket] = hits
                return False, max(0.0, janela_s - (agora - hits[0]))
            hits.append(agora)
            self._hits[bucket] = hits
            return True, 0.0

    def reset(self) -> None:
        """Zera o estado (usado nos testes)."""
        with self._lock:
            self._hits.clear()


_rate_limiter = _RateLimiter()


def _checar_rate(bucket: str, limite: int):
    """Se estourou o rate-limit, devolve ``(resposta_429, 429)`` p/ retornar; senão ``None``."""
    permitido, retry = _rate_limiter.check(bucket, limite, _RATE_WINDOW_S)
    if permitido:
        return None
    espera = int(retry) + 1
    resp = jsonify(
        ok=False, error='rate_limited', retry_after_s=espera,
        motivo=(f'Trava anti-loop: muitas escritas em menos de {int(_RATE_WINDOW_S)}s '
                f'(limite {limite}). Aguarde ~{espera}s e tente de novo.'),
    )
    resp.headers['Retry-After'] = str(espera)
    logger.warning("Rate-limit '%s' estourado (limite %s/%ss)", bucket, limite, int(_RATE_WINDOW_S))
    return resp, 429


# Cliente Supabase (service_role) p/ ler o log — criado sob demanda e reaproveitado.
_supabase_client = None


def _supabase():
    global _supabase_client
    if _supabase_client is None:
        from supabase import create_client
        from supabase.client import ClientOptions
        s = get_settings()
        _supabase_client = create_client(
            s.supabase_url, s.supabase_write_key,
            ClientOptions(postgrest_client_timeout=s.supabase_timeout_s),
        )
    return _supabase_client


def _fetch_log(table: str, limit: int) -> List[dict]:
    """Últimas ``limit`` sincronizações (mais recentes primeiro) da tabela ``table``."""
    res = (
        _supabase().table(table)
        .select('*').order('id', desc=True).limit(limit).execute()
    )
    return res.data or []


def _clear_log(table: str) -> int:
    """Apaga todos os registros da tabela de log ``table``. Retorna quantos removeu."""
    # PostgREST exige um filtro no delete; 'id <> 0' casa todas as linhas (id começa em 1).
    res = _supabase().table(table).delete().neq('id', 0).execute()
    return len(res.data or [])


def _count_rows(table: str) -> Optional[int]:
    """Total de linhas da tabela ``table`` (via count exato do PostgREST)."""
    res = _supabase().table(table).select('id', count='exact').limit(1).execute()
    return res.count


# Tradução do Status da OS. A view VW_OS_INTEGRACAO traz o código cru (P/R/L/C); a
# antiga tabela de lookup status_ordens_servico_eng foi descontinuada na consolidação.
_OS_STATUS_DESC = {'P': 'Planejado', 'R': 'Liberado', 'L': 'Encerrado', 'C': 'Cancelado'}

# Colunas do detalhe/resumo de OS — enxutas de propósito (a view VW_OS_INTEGRACAO tem
# 54 colunas; puxamos só o que o resumo usa). Inclui os campos de EXPEDIÇÃO
# (ObsPedido/DtLiber/DtEntregaPED) que antes exigiam uma 2ª query no espelho separado.
_OS_DETALHE_COLS = (
    'id,N_PED,N_OP,DescItemPED,DescItemEstrut,DtPedido,'
    'CodClien,NomeClien,Status,TotalOrcam,ObsPedido,DtLiber,DtEntregaPED,'
    'Solda,Pintura,Almox,Exped,id_execucao,data_hora_extracao'
)

# Flags de PROCESSO (colunas 50-53 da view): 1 = o item passa pelo processo, 0 = não.
# Substituem, por 4 colunas, as 4 TABELAS dropadas na consolidação de 14/07
# (vw_os_solda/vw_os_pintura_v0/vw_os_almox_impressao/vw_os_exped_impressao_v2) —
# antes o processo era identificado pela TABELA em que a linha aparecia.
_OS_PROCESSOS = ('Solda', 'Pintura', 'Almox', 'Exped')


def _flag_ligada(valor: object) -> bool:
    """True se a flag de processo da linha está ligada (1).

    A view devolve inteiro (1/0), mas toleramos texto/decimal/None — um valor
    inesperado nunca deve derrubar o resumo, só não conta.
    """
    try:
        return int(valor) == 1
    except (TypeError, ValueError):
        return False


def _resumo_processos(linhas: List[dict]) -> dict:
    """Agrega as flags de processo: ``{processo: {'tem': bool, 'linhas': int}}``.

    As flags são POR ITEM — um pedido normalmente tem itens mistos (parte vai p/
    solda, parte não), então um booleano de cabeçalho seria enganoso. Damos as
    duas respostas: *passa pelo processo?* e *quantos itens*.
    """
    processos = {}
    for proc in _OS_PROCESSOS:
        n = sum(1 for linha in linhas if _flag_ligada(linha.get(proc)))
        processos[proc.lower()] = {'tem': n > 0, 'linhas': n}
    return processos


def _fetch_os_detalhe(nped: int) -> List[dict]:
    """Linhas (colunas enxutas) da OS de um N_PED, ordenadas por id. Vazio se não há OS."""
    res = (
        _supabase().table(get_settings().os_table_name)
        .select(_OS_DETALHE_COLS).eq('N_PED', nped).order('id').execute()
    )
    return res.data or []


def _soma_total_orcamento(linhas: List[dict]) -> Optional[float]:
    """Soma o ``TotalOrcam`` das linhas (valor de mercadorias, sem impostos).

    ``TotalOrcam`` é POR LINHA na view (350 valores distintos num pedido real),
    não um cabeçalho repetido — pegar ``linhas[0]`` devolvia um valor de item
    aleatório (a ordem entre cargas não é estável). Incidente 2026-07-06:
    pedido 84080 mostrava "96,78" p/ um orçamento de ~R$ 3,05 mi.
    """
    total = 0.0
    achou = False
    for linha in linhas:
        valor = linha.get('TotalOrcam')
        if valor is None:
            continue
        try:
            total += float(valor)
        except (TypeError, ValueError):
            continue
        achou = True
    return round(total, 2) if achou else None


def _resumo_os(linhas: List[dict]) -> dict:
    """Resumo do pedido a partir das linhas já lidas (sem query extra).

    A view VW_OS_INTEGRACAO é desnormalizada por item: os campos de CABEÇALHO
    (cliente, status, datas) se repetem em cada linha — pegamos da primeira. As OPs
    (``N_OP``) são agregadas e o ``total_orcamento`` é a SOMA das linhas (ver
    ``_soma_total_orcamento``).

    Os campos de EXPEDIÇÃO (``data_entrega``, ``data_liberacao``, ``obs``) agora saem
    da MESMA linha (antes vinham de um espelho separado). ``obs`` = ``ObsPedido``
    (observação do PEDIDO; a view tem também ``Obs``, da OP, que NÃO serve aqui).
    ``exped_disponivel`` fica ``True`` por compatibilidade com o app web — não há mais
    espelho separado que possa faltar.

    As flags de processo (Solda/Pintura/Almox/Exped) são POR ITEM, não do pedido —
    vão agregadas em ``processos`` (ver ``_resumo_processos``), não como booleanos
    de cabeçalho, que seriam enganosos num pedido com itens mistos.
    """
    primeira = linhas[0]
    status = primeira.get('Status')
    ops = sorted({l['N_OP'] for l in linhas if l.get('N_OP') is not None})
    return {
        'cliente': primeira.get('NomeClien'),
        'cod_cliente': primeira.get('CodClien'),
        'descricao': primeira.get('DescItemPED'),
        'data_pedido': primeira.get('DtPedido'),
        'status': status,
        'status_desc': _OS_STATUS_DESC.get(status, status),
        'total_orcamento': _soma_total_orcamento(linhas),
        'num_linhas': len(linhas),
        'num_ops': len(ops),
        'ops': ops,
        'data_entrega': primeira.get('DtEntregaPED'),
        'data_liberacao': primeira.get('DtLiber'),
        'obs': primeira.get('ObsPedido'),
        'exped_disponivel': True,
        'processos': _resumo_processos(linhas),
        'id_execucao': primeira.get('id_execucao'),
        'ultima_sincronizacao': primeira.get('data_hora_extracao'),
    }


def _autorizado() -> bool:
    """True se ``OS_API_KEY`` não está definido (aberto) ou se a chave bate.

    Aceita a chave por: header ``X-API-Key``, ``Authorization: Bearer <chave>`` ou query
    string ``?key=`` / ``?api_key=`` (o query param permite testar no navegador, que não
    envia headers; ciente de que a chave aparece na URL/histórico do navegador).
    """
    chave = get_settings().os_api_key
    if not chave:
        return True
    enviado = request.headers.get('X-API-Key')
    if not enviado:
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            enviado = auth[len('Bearer '):]
    if not enviado:
        enviado = request.args.get('key') or request.args.get('api_key')
    return enviado == chave


def _sync_one(nped: int) -> dict:
    """Sincroniza um NPED. Antes, diagnostica via OWOR + ORDR: se não há OS ainda
    (distinguindo pedido aberto, cancelado ou inexistente), ou se a OS está cancelada,
    devolve um aviso **sem** tentar sincronizar (não gera log de falha).

    Os ``motivo`` das respostas são propositalmente SEM acento — legíveis em qualquer
    terminal/console sem depender do escape ``\\uXXXX`` do JSON.
    """
    try:
        diag = diagnosticar_nped(nped)
    except Exception as exc:
        logger.error("Erro ao diagnosticar NPED %s: %s", nped, exc)
        diag = {'erro': str(exc)}

    status_pedido = diag.get('pedido_status')
    if diag.get('tem_os') is False:
        base = {'nped': nped, 'ok': False, 'status_pedido': status_pedido}
        if diag.get('pedido_existe') is False:
            return {**base, 'tipo': 'pedido_nao_encontrado',
                    'motivo': 'Pedido nao encontrado no SAP.'}
        if diag.get('pedido_cancelado'):
            return {**base, 'tipo': 'pedido_cancelado',
                    'motivo': 'Pedido cancelado no SAP - nao ha OS a sincronizar.'}
        sufixo = f' (pedido {status_pedido.lower()})' if status_pedido else ''
        return {**base, 'tipo': 'sem_os',
                'motivo': f'OS ainda nao gerada para este pedido{sufixo}.'}
    if diag.get('cancelada'):
        return {'nped': nped, 'ok': False, 'tipo': 'cancelada',
                'status_pedido': status_pedido,
                'motivo': 'A OS deste pedido esta cancelada.'}

    # OS existe (ou não deu para diagnosticar) → tenta sincronizar
    try:
        ok = bool(sync_os(nped))
    except Exception as exc:  # nunca deixa a request estourar 500 silenciosamente
        logger.error("Erro ao sincronizar NPED %s: %s", nped, exc)
        ok = False

    if ok:
        # Carga única: a view VW_OS_INTEGRACAO já traz OS + estrutura/árvore + orçamento
        # numa só tabela — não há mais sub-syncs de árvore WBC nem de views de impressão.
        return {'nped': nped, 'ok': True, 'tipo': None, 'motivo': None,
                'status_pedido': status_pedido}
    return {'nped': nped, 'ok': False, 'tipo': 'erro', 'status_pedido': status_pedido,
            'motivo': 'Nao foi possivel sincronizar.'}


def _sincronizar(npeds: List[int]) -> Tuple[Any, int]:
    """Sincroniza os NPEDs (serializados) e devolve ``(json, http_status)``."""
    resultados = []
    with _sync_lock:
        for n in npeds:
            resultados.append(_sync_one(n))

    sucesso = sum(1 for r in resultados if r['ok'])
    total = len(resultados)
    payload = {
        'ok': sucesso == total,
        'results': resultados,
        'summary': {'total': total, 'sucesso': sucesso, 'falha': total - sucesso},
    }
    http = 200 if sucesso == total else 207  # 207 = algum não sincronizou (parcial)
    return jsonify(payload), http


@app.get('/')
def ui():
    """Página amigável (campo de pedido + chave + botão Sincronizar)."""
    return send_from_directory(_WEB_DIR, 'sincronizar.html')


@app.get('/favicon.ico')
def favicon():
    return ('', 204)  # evita 404 ruidoso no log


@app.get('/health')
def health():
    """Liveness leve (a API está de pé?). Sem chave, sem checagem externa — rápido e
    sempre disponível. Para o diagnóstico profundo, use ``/status``."""
    return jsonify(status='ok', service='ordens-servico-engenharia')


# aliases aceitos no ?checks= → nome canônico da checagem
_CHECK_ALIASES = {
    'sql': 'sql_server', 'sqlserver': 'sql_server', 'wbc': 'sql_server',
    'hana': 'sap', 'agendador': 'scheduler', 'sched': 'scheduler',
    'task': 'scheduled_task', 'tarefa': 'scheduled_task', 'wbc_task': 'scheduled_task',
}


@app.get('/status')
def status_detalhado():
    """Diagnóstico **sob demanda**: SAP, SQL Server (WBC), Supabase (com latência), sinal
    do agendador e sistema (CPU/memória/disco/IP/uptime).

    **Aberto** (sem chave) — pensado p/ monitoramento e p/ abrir direto no navegador.
    Roda só quando chamado (sem polling). Parâmetros:
    - ``?checks=sap,sql`` — roda só as checagens listadas (sap, sql/sql_server, supabase,
      scheduler/agendador, scheduled_task/tarefa). Omitido = todas. ``system`` vem sempre.
      Nome inválido → **400** com a lista do que é aceito (ver ``collect_status``: um
      typo devolvia ``healthy: true`` sem checar nada).
    - ``?strict=1`` — devolve **HTTP 503** se alguma conexão falhar **ou** houver alertas
      (disco baixo, agendador possivelmente parado). Útil p/ monitores por código de status.
    """
    raw = request.args.get('checks')
    only = None
    if raw:
        only = {_CHECK_ALIASES.get(c.strip().lower(), c.strip().lower())
                for c in raw.split(',') if c.strip()}

    try:
        data = collect_status(only)
    except ValueError as exc:
        # Nome de check inválido. 400 ANTES do 500 genérico: é erro do cliente, e
        # responder o que é aceito poupa a próxima tentativa às cegas.
        aceitos = sorted(set(SELECTABLE_CHECKS) | set(_CHECK_ALIASES))
        return jsonify(ok=False, error=str(exc), aceitos=aceitos), 400
    except Exception as exc:
        logger.error("Erro ao coletar status: %s", exc)
        return jsonify(ok=False, error='falha ao coletar status'), 500

    strict = request.args.get('strict') in ('1', 'true', 'yes')
    degraded = (not data['ok']) or bool(data.get('alerts'))
    return jsonify(data), (503 if strict and degraded else 200)


@app.get('/historico')
def historico():
    """Últimas sincronizações (lê a tabela de log). Requer X-API-Key."""
    if not _autorizado():
        return jsonify(ok=False, error='unauthorized'), 401
    try:
        limit = int(request.args.get('limit', 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 100))
    try:
        itens = _fetch_log(get_settings().os_sync_log_table, limit)
    except Exception as exc:
        logger.error("Erro ao ler histórico: %s", exc)
        return jsonify(ok=False, error='falha ao ler o historico'), 502
    return jsonify(ok=True, items=itens)


@app.delete('/historico')
def historico_limpar():
    """Limpa o histórico de OS (apaga a tabela de log). Requer X-API-Key."""
    if not _autorizado():
        return jsonify(ok=False, error='unauthorized'), 401
    try:
        removidos = _clear_log(get_settings().os_sync_log_table)
    except Exception as exc:
        logger.error("Erro ao limpar histórico: %s", exc)
        return jsonify(ok=False, error='falha ao limpar o historico'), 502
    return jsonify(ok=True, removed=removidos)


@app.get('/ordens-servico/disponiveis')
def os_disponiveis():
    """Lista até 30 pedidos com OS criada no SAP (NPED + cliente + data). Requer X-API-Key.

    Alimenta o botão "Buscar na Lista" do painel: o usuário escolhe os pedidos sem
    precisar digitar os NPEDs.
    """
    if not _autorizado():
        return jsonify(ok=False, error='unauthorized'), 401
    limit = _limit_arg(default=30, maximo=50)
    try:
        pedidos = listar_pedidos_com_os(limit)
    except Exception as exc:
        logger.error("Erro ao listar pedidos com OS: %s", exc)
        return jsonify(ok=False, error='falha ao listar a lista de pedidos'), 502
    if pedidos is None:
        return jsonify(ok=False, error='SAP indisponivel'), 502
    return jsonify(ok=True, items=pedidos)


@app.get('/ordens-servico/<nped>')
def os_detalhe(nped: str):
    """Detalhe da OS de UM pedido (lê a tabela única no Supabase). Requer X-API-Key.

    Devolve um ``resumo`` (cliente, status, total, nº de linhas e de OPs, última
    sincronização, datas de entrega/liberação e observação do pedido — todos vindos
    da mesma tabela ``vw_os_integracao``). Com ``?linhas=1`` inclui também as
    ``linhas`` (colunas enxutas). Responde **404** se o pedido não tem OS sincronizada.

    Obs.: a rota estática ``/ordens-servico/disponiveis`` tem prioridade no roteador
    do Werkzeug, então não é capturada por este ``<nped>``.
    """
    if not _autorizado():
        return jsonify(ok=False, error='unauthorized'), 401
    try:
        n = coerce_positive_int(nped, what='NPED')
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    try:
        linhas = _fetch_os_detalhe(n)
    except Exception as exc:
        logger.error("Erro ao ler a OS do NPED %s: %s", n, exc)
        return jsonify(ok=False, error='falha ao ler a OS'), 502
    if not linhas:
        return jsonify(ok=False, error='pedido sem OS sincronizada', nped=n), 404
    payload = {'ok': True, 'nped': n, 'resumo': _resumo_os(linhas)}
    if request.args.get('linhas') in ('1', 'true', 'yes'):
        payload['linhas'] = linhas
    return jsonify(payload)


@app.post('/ordens-servico/<nped>/sincronizar')
def os_sincronizar(nped: str):
    """Sincroniza (SAP → Supabase) a OS de UM pedido e devolve o ``resumo`` resultante.
    Requer X-API-Key. **Par de escrita** do ``GET /ordens-servico/<nped>``.

    Reúsa ``_sync_one`` (serializado no ``_sync_lock``): diagnostica OWOR + ORDR antes — se o
    pedido **não tem OS gerada** (tipos ``sem_os``, ``pedido_cancelado``, ``pedido_nao_encontrado``,
    conforme o status do pedido na ORDR) ou a OS está **cancelada**, devolve o aviso **sem
    sincronizar**. As respostas incluem ``status_pedido`` (Aberto/Cancelado/Fechado). É idempotente
    (``replace_nped`` substitui, não duplica) — a carga única já traz OS + árvore + orçamento. Em
    sucesso, relê a tabela e inclui o ``resumo`` fresco (cliente, status, nº de linhas/OPs, última
    sincronização).

    Status: ``200`` (sincronizado **ou** aviso de negócio sem_os/cancelada) · ``502`` (falha de
    sincronização) · ``400`` NPED inválido · ``401`` sem/má X-API-Key.
    """
    if not _autorizado():
        return jsonify(ok=False, error='unauthorized'), 401
    try:
        n = coerce_positive_int(nped, what='NPED')
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400

    limitado = _checar_rate('sync_os', _RATE_SYNC_OS_MAX)
    if limitado is not None:
        return limitado

    with _sync_lock:
        resultado = _sync_one(n)

    payload = {'ok': bool(resultado.get('ok')), 'nped': n, 'resultado': resultado}
    if resultado.get('ok'):
        try:
            linhas = _fetch_os_detalhe(n)
            if linhas:
                # Resumo fresco já sai com datas/obs — tudo vem da tabela única.
                payload['resumo'] = _resumo_os(linhas)
        except Exception as exc:  # sync já foi; só não conseguimos reler o resumo
            logger.error("Sync OK mas falha ao reler o resumo do NPED %s: %s", n, exc)

    # avisos de negócio (sem_os / cancelada / pedido_cancelado / pedido_nao_encontrado)
    # respondem 200; 'erro' = falha real de sync (502)
    http = 502 if resultado.get('tipo') == 'erro' else 200
    return jsonify(payload), http


# ===================== Oportunidades (pipeline agendado) =====================

def _limit_arg(default: int = 20, maximo: int = 100) -> int:
    try:
        limit = int(request.args.get('limit', default))
    except (TypeError, ValueError):
        limit = default
    return max(1, min(limit, maximo))


@app.get('/oportunidades/historico')
def oport_historico():
    """Últimos sincronismos de oportunidades (lê sincronizacao_log). Requer X-API-Key."""
    if not _autorizado():
        return jsonify(ok=False, error='unauthorized'), 401
    try:
        itens = _fetch_log(get_settings().sync_log_table_name, _limit_arg())
    except Exception as exc:
        logger.error("Erro ao ler histórico de oportunidades: %s", exc)
        return jsonify(ok=False, error='falha ao ler o historico'), 502
    return jsonify(ok=True, items=itens)


@app.delete('/oportunidades/historico')
def oport_historico_limpar():
    """Limpa o log de oportunidades. Requer X-API-Key."""
    if not _autorizado():
        return jsonify(ok=False, error='unauthorized'), 401
    try:
        removidos = _clear_log(get_settings().sync_log_table_name)
    except Exception as exc:
        logger.error("Erro ao limpar histórico de oportunidades: %s", exc)
        return jsonify(ok=False, error='falha ao limpar o historico'), 502
    return jsonify(ok=True, removed=removidos)


@app.get('/oportunidades/info')
def oport_info():
    """Contexto do pipeline de oportunidades: total de linhas + agenda. Requer X-API-Key."""
    if not _autorizado():
        return jsonify(ok=False, error='unauthorized'), 401
    s = get_settings()
    total = None
    try:
        total = _count_rows(s.table_name)
    except Exception as exc:
        logger.error("Erro ao contar oportunidades: %s", exc)
    return jsonify(
        ok=True,
        total=total,
        intervalo_minutos=s.intervalo_minutos,
        janela_horas=s.janela_horas,
    )


@app.post('/oportunidades/sincronizar')
def oport_sincronizar():
    """Força a carga COMPLETA de oportunidades (a mesma do agendador). Requer X-API-Key.

    Usa um lock de arquivo cross-process: se o agendador (ou outro disparo) já estiver
    rodando, responde 409 em vez de rodar duas cargas snapshot ao mesmo tempo.
    """
    if not _autorizado():
        return jsonify(ok=False, error='unauthorized'), 401
    limitado = _checar_rate('force_oport', _RATE_FORCE_OPORT_MAX)
    if limitado is not None:
        return limitado

    try:
        with oportunidades_sync_lock(timeout=0):
            ok = bool(sync_oportunidades())
    except FileLockTimeout:
        return jsonify(ok=False, tipo='ocupado',
                       motivo='Ja ha uma sincronizacao de oportunidades em andamento.'), 409
    except Exception as exc:
        logger.error("Erro ao sincronizar oportunidades: %s", exc)
        return jsonify(ok=False, tipo='erro', motivo='Nao foi possivel sincronizar.'), 502
    if ok:
        return jsonify(ok=True)
    return jsonify(ok=False, tipo='erro', motivo='Nao foi possivel sincronizar (0 registros?).')


@app.post('/sync/ordens-servico/<nped>')
def sync_um(nped: str):
    if not _autorizado():
        return jsonify(ok=False, error='unauthorized'), 401
    try:
        n = coerce_positive_int(nped, what='NPED')
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    return _sincronizar([n])


@app.post('/sync/ordens-servico')
def sync_varios():
    if not _autorizado():
        return jsonify(ok=False, error='unauthorized'), 401
    body = request.get_json(silent=True) or {}
    bruto = body.get('npeds')
    if bruto is None and body.get('nped') is not None:
        bruto = [body['nped']]
    if not bruto:
        return jsonify(ok=False, error="informe 'nped' (int) ou 'npeds' (lista)"), 400
    if not isinstance(bruto, list):
        bruto = [bruto]
    try:
        npeds = [coerce_positive_int(n, what='NPED') for n in bruto]
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    return _sincronizar(npeds)


def main() -> None:
    """Sobe o servidor (waitress em produção; Flask dev como fallback)."""
    _configure_logging()
    s = get_settings()
    if not s.os_api_key:
        logger.warning(
            "OS_API_KEY não definido — endpoint SEM autenticação "
            "(ok p/ rede interna/dev; defina OS_API_KEY em produção)."
        )
    host, port = s.os_api_host, s.os_api_port
    try:
        from waitress import serve
        logger.info("Servindo via waitress em http://%s:%s", host, port)
        serve(app, host=host, port=port)
    except ImportError:
        logger.warning("waitress não instalado — usando o servidor de DEV do Flask.")
        app.run(host=host, port=port)


if __name__ == '__main__':
    main()
