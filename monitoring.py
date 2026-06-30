"""Diagnóstico sob demanda do servidor + dependências (endpoint ``/status`` da API).

Roda **apenas quando chamado** (não há polling em background), para não competir com os
serviços. Cada chamada abre conexões de teste com SAP, SQL Server (WBC) e Supabase e mede
a latência; coleta também métricas do sistema (host/IP/SO/disco via stdlib; CPU e memória
via ``psutil``, se instalado — sem ele, o resto do ``/status`` continua funcionando).

Use com parcimônia: cada chamada abre (e fecha) 3 conexões de teste.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import socket
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from config import get_settings

logger = logging.getLogger(__name__)

# Início (aprox.) do processo da API — usado para o uptime no /status.
_PROC_START = time.time()

# Acima deste nº de minutos sem carga de oportunidades, DENTRO da janela comercial
# (dia útil 07–18h), sinalizamos que o serviço OrcaView-Scheduler pode ter caído.
SCHEDULER_STALE_MIN = 35
# Alertas de disco da unidade onde o app roda.
DISK_LOW_GB = 5.0      # menos que isto livre
DISK_PCT_ALERT = 90.0  # ou mais que isto usado

# Checagens que o ?checks= pode selecionar (system é sempre incluído, é local/barato).
SELECTABLE_CHECKS = ('sap', 'sql_server', 'supabase', 'scheduler')


def _timed(fn: Callable[[], Optional[str]]) -> Dict[str, Any]:
    """Roda uma checagem e devolve ``{ok, ms, detail?, error?}`` medindo a latência."""
    t0 = time.monotonic()
    try:
        detail = fn()
        out: Dict[str, Any] = {'ok': True, 'ms': round((time.monotonic() - t0) * 1000)}
        if detail:
            out['detail'] = detail
        return out
    except Exception as exc:
        return {'ok': False, 'ms': round((time.monotonic() - t0) * 1000), 'error': str(exc)[:300]}


def _check_sap() -> str:
    """Conecta no SAP HANA (1 tentativa) e roda ``SELECT 1 FROM DUMMY``."""
    from sap_connection import connect_sap_hana
    s = get_settings()
    if not s.sap_ready():
        raise RuntimeError('SAP não configurado (.env)')
    # with_retry=False: o /status não pode ficar ~45s preso se o SAP estiver fora.
    conn = connect_sap_hana(
        s.sap_host, s.sap_port, s.sap_user, s.sap_password, s.sap_database,
        with_retry=False,
    )
    try:
        cur = conn.cursor()
        cur.execute('SELECT 1 FROM DUMMY')
        cur.fetchone()
        cur.close()
    finally:
        conn.close()
    return f'{s.sap_host}:{s.sap_port}'


def _check_sql_server() -> str:
    """Conecta no SQL Server (WBC) e roda ``SELECT 1``."""
    from extract_sap_to_supabase import get_sqlserver_connection
    s = get_settings()
    if not s.sql_ready():
        raise RuntimeError('SQL Server (WBC) não configurado (.env)')
    conn = get_sqlserver_connection(
        s.sql_host, s.sql_port, s.sql_user, s.sql_password, s.sql_database, s.sql_driver,
    )
    if conn is None:
        raise RuntimeError('falha ao conectar')
    try:
        cur = conn.cursor()
        cur.execute('SELECT 1')
        cur.fetchone()
        cur.close()
    finally:
        conn.close()
    return f'{s.sql_host}:{s.sql_port} / {s.sql_database}'


def _check_supabase() -> str:
    """Faz um SELECT mínimo no Supabase (lê 1 linha da tabela de log)."""
    from supabase import create_client
    from supabase.client import ClientOptions
    s = get_settings()
    if not s.supabase_ready():
        raise RuntimeError('Supabase não configurado (.env)')
    client = create_client(
        s.supabase_url, s.supabase_write_key,
        ClientOptions(postgrest_client_timeout=15),
    )
    client.table(s.sync_log_table_name).select('id').limit(1).execute()
    return s.supabase_url


def _scheduler_signal() -> Dict[str, Any]:
    """Sinal INDIRETO do agendador: idade da última carga de oportunidades (lida do log).

    Lê o registro mais recente em ``sincronizacao_log``. Só marca ``stale=True`` se estamos
    na **janela comercial** (dia útil, 07–18h) e a última carga é mais antiga que
    ``SCHEDULER_STALE_MIN`` — fora da janela/fim de semana, não ter carga recente é normal.
    """
    from supabase import create_client
    from supabase.client import ClientOptions
    from feriados_br import is_business_day
    from config import parse_janela_horas

    s = get_settings()
    if not s.supabase_ready():
        return {'error': 'Supabase não configurado (.env)', 'stale': False}
    try:
        client = create_client(
            s.supabase_url, s.supabase_write_key,
            ClientOptions(postgrest_client_timeout=15),
        )
        res = (client.table(s.sync_log_table_name)
               .select('data_hora_sincronizacao,status')
               .order('id', desc=True).limit(1).execute())
    except Exception as exc:
        return {'error': str(exc)[:200], 'stale': False}

    rows = res.data or []
    if not rows:
        return {'last_sync': None, 'minutes_ago': None, 'stale': False,
                'note': 'sem registros de sincronismo'}

    last = rows[0]
    last_iso = last.get('data_hora_sincronizacao')
    minutes: Optional[int] = None
    try:
        last_dt = datetime.fromisoformat(str(last_iso).replace('Z', '+00:00'))
        if last_dt.tzinfo is not None:               # PostgREST pode devolver com timezone
            last_dt = last_dt.astimezone().replace(tzinfo=None)  # → hora local naive
        minutes = round((datetime.now() - last_dt).total_seconds() / 60)
    except Exception:
        pass

    h_start, h_end = parse_janela_horas(s.janela_horas)
    now = datetime.now()
    in_window = is_business_day(now.date()) and h_start <= now.hour <= h_end
    stale = bool(in_window and minutes is not None and minutes > SCHEDULER_STALE_MIN)
    return {
        'last_sync': last_iso,
        'last_status': last.get('status'),
        'minutes_ago': minutes,
        'in_window': in_window,
        'threshold_min': SCHEDULER_STALE_MIN,
        'stale': stale,
    }


def _local_ip() -> Optional[str]:
    """IP local de saída (sem enviar pacote — só resolve a rota). Fallback p/ hostname."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(('8.8.8.8', 80))
        return sock.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return None
    finally:
        sock.close()


def _system_info() -> Dict[str, Any]:
    """Host, IP, SO, Python, disco (stdlib) + CPU/memória (psutil, se houver)."""
    info: Dict[str, Any] = {
        'hostname': socket.gethostname(),
        'ip': _local_ip(),
        'os': platform.platform(),
        'python': platform.python_version(),
        'psutil': False,
    }
    gb = 1024 ** 3
    info['disk_low'] = False
    try:
        total, used, free = shutil.disk_usage(os.path.dirname(os.path.abspath(__file__)))
        info['disk_total_gb'] = round(total / gb, 1)
        info['disk_free_gb'] = round(free / gb, 1)
        info['disk_percent'] = round(used / total * 100, 1)
        info['disk_low'] = info['disk_free_gb'] < DISK_LOW_GB or info['disk_percent'] >= DISK_PCT_ALERT
    except Exception as exc:
        info['disk_error'] = str(exc)[:120]

    try:
        import psutil  # opcional — só p/ CPU e memória
        mb = 1024 ** 2
        vm = psutil.virtual_memory()
        info['psutil'] = True
        info['cpu_percent'] = psutil.cpu_percent(interval=0.3)
        info['mem_percent'] = vm.percent
        info['mem_used_mb'] = round((vm.total - vm.available) / mb)
        info['mem_total_mb'] = round(vm.total / mb)
    except ImportError:
        info['cpu_percent'] = None
        info['mem_note'] = 'instale psutil p/ CPU e memória (pip install psutil)'
    except Exception as exc:
        info['psutil_error'] = str(exc)[:120]
    return info


def collect_status(only: Optional[set] = None) -> Dict[str, Any]:
    """Coleta o estado do servidor + dependências (sob demanda).

    Args:
        only: subconjunto de ``SELECTABLE_CHECKS`` a rodar (de ``?checks=``). ``None``/vazio
            roda todas. ``system`` (host/IP/CPU/disco) é sempre incluído (é local e barato).

    Returns:
        Dict com ``ok`` (todas as conexões que rodaram estão verdes), ``healthy`` (``ok`` e
        sem alertas), ``checks`` (conectividade), ``scheduler`` (sinal indireto), ``system`` e
        ``alerts`` (lista de avisos legíveis: disco baixo, agendador possivelmente parado).
    """
    sel = set(SELECTABLE_CHECKS) if not only else only

    checks: Dict[str, Any] = {}
    if 'sap' in sel:
        checks['sap'] = _timed(_check_sap)
    if 'sql_server' in sel:
        checks['sql_server'] = _timed(_check_sql_server)
    if 'supabase' in sel:
        checks['supabase'] = _timed(_check_supabase)

    scheduler = _scheduler_signal() if 'scheduler' in sel else None
    system = _system_info()

    alerts = []
    if scheduler and scheduler.get('stale'):
        alerts.append(
            f"agendador possivelmente parado: última carga de oportunidades há "
            f"{scheduler.get('minutes_ago')} min (limite {SCHEDULER_STALE_MIN} min na janela comercial)"
        )
    if system.get('disk_low'):
        alerts.append(
            f"disco baixo: {system.get('disk_free_gb')} GB livres ({system.get('disk_percent')}% usado)"
        )

    ok = all(c['ok'] for c in checks.values())
    out: Dict[str, Any] = {
        'ok': ok,
        'healthy': ok and not alerts,
        'service': 'ordens-servico-engenharia',
        'timestamp': datetime.now().isoformat(timespec='seconds'),
        'uptime_s': round(time.time() - _PROC_START),
        'checks': checks,
        'system': system,
        'alerts': alerts,
    }
    if scheduler is not None:
        out['scheduler'] = scheduler
    return out
