"""API HTTP mínima para disparar a sincronização de OS por NPED (sob demanda).

Pensada para ser chamada **pelo app** (web/desktop) quando o usuário pede para
sincronizar um pedido. A escrita continua sendo do backend (service_role); este
serviço só expõe um gatilho HTTP.

Endpoints
---------
- ``GET  /health``                         → ``{"status": "ok"}``
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
from logging.handlers import TimedRotatingFileHandler
from typing import Any, List, Optional, Tuple

from flask import Flask, jsonify, request, send_from_directory

from config import get_settings
from pipeline_core import coerce_positive_int, oportunidades_sync_lock, FileLockTimeout
from extract_ordens_servico_engenharia import (
    main as sync_os,
    diagnosticar_nped,
    listar_pedidos_com_os,
)
from extract_sap_to_supabase import main as sync_oportunidades

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
        when='midnight', interval=1, backupCount=12, encoding='utf-8',
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


def _autorizado() -> bool:
    """True se ``OS_API_KEY`` não está definido (aberto) ou se a chave bate."""
    chave = get_settings().os_api_key
    if not chave:
        return True
    enviado = request.headers.get('X-API-Key')
    if not enviado:
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            enviado = auth[len('Bearer '):]
    return enviado == chave


def _sync_one(nped: int) -> dict:
    """Sincroniza um NPED. Antes, diagnostica via OWOR: se não há OS ainda, ou se a
    OS está cancelada, devolve um aviso **sem** tentar sincronizar (não gera log de falha).
    """
    try:
        diag = diagnosticar_nped(nped)
    except Exception as exc:
        logger.error("Erro ao diagnosticar NPED %s: %s", nped, exc)
        diag = {'erro': str(exc)}

    if diag.get('tem_os') is False:
        return {'nped': nped, 'ok': False, 'tipo': 'sem_os',
                'motivo': 'OS ainda não gerada para este pedido.'}
    if diag.get('cancelada'):
        return {'nped': nped, 'ok': False, 'tipo': 'cancelada',
                'motivo': 'A OS deste pedido está cancelada.'}

    # OS existe (ou não deu para diagnosticar) → tenta sincronizar
    try:
        ok = bool(sync_os(nped))
    except Exception as exc:  # nunca deixa a request estourar 500 silenciosamente
        logger.error("Erro ao sincronizar NPED %s: %s", nped, exc)
        ok = False

    if ok:
        return {'nped': nped, 'ok': True, 'tipo': None, 'motivo': None}
    return {'nped': nped, 'ok': False, 'tipo': 'erro', 'motivo': 'Não foi possível sincronizar.'}


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
    return jsonify(status='ok', service='ordens-servico-engenharia')


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
        return jsonify(ok=False, error='falha ao ler o histórico'), 502
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
        return jsonify(ok=False, error='falha ao limpar o histórico'), 502
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
        return jsonify(ok=False, error='SAP indisponível'), 502
    return jsonify(ok=True, items=pedidos)


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
        return jsonify(ok=False, error='falha ao ler o histórico'), 502
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
        return jsonify(ok=False, error='falha ao limpar o histórico'), 502
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
    try:
        with oportunidades_sync_lock(timeout=0):
            ok = bool(sync_oportunidades())
    except FileLockTimeout:
        return jsonify(ok=False, tipo='ocupado',
                       motivo='Já há uma sincronização de oportunidades em andamento.'), 409
    except Exception as exc:
        logger.error("Erro ao sincronizar oportunidades: %s", exc)
        return jsonify(ok=False, tipo='erro', motivo='Não foi possível sincronizar.'), 502
    if ok:
        return jsonify(ok=True)
    return jsonify(ok=False, tipo='erro', motivo='Não foi possível sincronizar (0 registros?).')


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
