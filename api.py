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
- Dev:        ``python api.py``
- Produção:   ``waitress-serve --listen=0.0.0.0:8077 api:app``
              (ou ``python api.py`` — cai no waitress se instalado, senão no Flask dev)

Exemplo de chamada::

    curl -X POST http://localhost:8077/sync/ordens-servico/84080 \\
         -H "X-API-Key: SUA_CHAVE"
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any, List, Tuple

from flask import Flask, jsonify, request, send_from_directory

from config import get_settings
from pipeline_core import coerce_positive_int
from extract_ordens_servico_engenharia import main as sync_os

# UTF-8 console on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logging.getLogger('httpx').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

app = Flask(__name__)
_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')

# Serializa as cargas: nunca duas sincronizações simultâneas (evita múltiplas
# conexões SAP e corridas no replace_nped). O volume é baixo (gatilho sob demanda).
_sync_lock = threading.Lock()


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


def _sincronizar(npeds: List[int]) -> Tuple[Any, int]:
    """Sincroniza os NPEDs (serializados) e devolve ``(json, http_status)``."""
    resultados = []
    with _sync_lock:
        for n in npeds:
            try:
                ok = bool(sync_os(n))
            except Exception as exc:  # nunca deixa a request estourar 500 silenciosamente
                logger.error("Erro ao sincronizar NPED %s: %s", n, exc)
                ok = False
            resultados.append({'nped': n, 'ok': ok})

    sucesso = sum(1 for r in resultados if r['ok'])
    total = len(resultados)
    payload = {
        'ok': sucesso == total,
        'results': resultados,
        'summary': {'total': total, 'sucesso': sucesso, 'falha': total - sucesso},
    }
    # 200 tudo ok · 207 parcial · 502 nenhum sincronizou
    http = 200 if sucesso == total else (207 if sucesso else 502)
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
