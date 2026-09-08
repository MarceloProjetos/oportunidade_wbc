"""On-demand diagnosis of the server + dependencies (the API's ``/status`` endpoint).

Runs **only when called** (there is no background polling), so it does not compete with
the services. Each call opens test connections to SAP, SQL Server (WBC) and Supabase and
measures latency; it also collects system metrics (host/IP/OS/disk via stdlib; CPU and
memory via ``psutil``, if installed — without it the rest of ``/status`` still works).

Use sparingly: each call opens (and closes) 3 test connections.
"""

from __future__ import annotations

import json
import logging
import math
import os
import platform
import shutil
import socket
import sqlite3
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional

import windows_update
from config import get_settings

logger = logging.getLogger(__name__)

# Project directory (where this module lives) — base for relative paths.
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# (Approximate) start of the API process — used for the uptime in /status.
_PROC_START = time.time()

# Slack over INTERVALO_MINUTOS before considering the scheduler stopped. The threshold is
# DERIVED (interval + slack), not fixed: it used to be a hard-coded
# `SCHEDULER_STALE_MIN = 35` while the interval is configurable — with
# INTERVALO_MINUTOS=60 the /status started screaming "scheduler stopped" all day long (and
# ?strict=1 returned a permanent 503) even though everything was fine. The slack covers the
# load's duration + the trigger's delay.
SCHEDULER_STALE_FOLGA_MIN = 5


def scheduler_stale_min() -> int:
    """Minutes without a load that mean the scheduler is stopped (= interval + slack)."""
    return get_settings().intervalo_minutos + SCHEDULER_STALE_FOLGA_MIN
# Disk alerts for the drive the app runs on.
DISK_LOW_GB = 5.0      # less than this free
DISK_PCT_ALERT = 90.0  # or more than this used

# Checks that ?checks= can select (system is always included, it is local/cheap).
#
# ⚠️ CROSS-REPO CONTRACT — these names are a PUBLIC API, not an internal detail.
# The web app (web_orcaview_V117) sends them in `?checks=` from `sap_os_service.py`
# (`CHECKS_ACEITOS` there mirrors this tuple), and since 2026-07-16 `collect_status`
# answers **400** to an unknown name. So renaming/removing an entry here does not
# degrade the web — it BREAKS it (the /status panel and the Mira assistant). Adding is
# safe; renaming means updating `CHECKS_ACEITOS` on the web side too. The `_CHECK_ALIASES`
# in api.py (sql→sql_server, wu→windows_update, …) are part of the same contract.
SELECTABLE_CHECKS = ('sap', 'sql_server', 'supabase', 'scheduler', 'scheduled_task', 'wbc_worker',
                     'windows_update')
# Note: classifying LastTaskResult (success/running/never-run/refused) lives in
# monitor_wbc_task.ps1 — Python only READS the state JSON the script writes.
# Note 2: `windows_update` is cheap HERE (~0.2 ms of winreg + a cache read) because the
# expensive search (3.1 s measured on this machine) runs on a daemon thread fired at API
# start — see windows_update.py. It is in ?checks= so it can be requested IN ISOLATION,
# without paying for the 3 test connections (SAP/SQL/Supabase); that is what the MCP tool
# does.


def _timed(fn: Callable[[], Optional[str]]) -> Dict[str, Any]:
    """Run a check and return ``{ok, ms, detail?, error?}``, measuring the latency."""
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
    """Connect to SAP HANA (1 attempt) and run ``SELECT 1 FROM DUMMY``."""
    from sap_connection import connect_sap_hana
    s = get_settings()
    if not s.sap_ready():
        raise RuntimeError('SAP não configurado (.env)')
    # with_retry=False: /status cannot hang for ~45s if SAP is down.
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
    """Connect to SQL Server (WBC) and run ``SELECT 1``."""
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
    """Run a minimal SELECT on Supabase (reads 1 row from the log table)."""
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
    """INDIRECT scheduler signal: age of the last oportunidades load (read from the log).

    Reads the most recent record in ``sincronizacao_log``. It only marks ``stale=True``
    when the absence of a load is genuinely ABNORMAL, i.e. when **all** hold:

    * we are in the **business window** (business day, within ``JANELA_HORAS``) — outside
      it and on weekends, having no recent load is expected;
    * the window opened more than one cycle ago (``warming_up=False``) — right at the
      open, the last load is last night's and "old" is normal until the day's 1st run;
    * the last load is older than ``scheduler_stale_min()`` (= ``INTERVALO_MINUTOS`` +
      slack), a threshold **derived** from the configured interval, not a fixed number.

    The strictness is deliberate: this signal becomes an alert and, with ``?strict=1``,
    **HTTP 503**. A recurring false alarm trains everyone to ignore the monitor — which is
    worse than having no monitor.
    """
    from supabase import create_client
    from supabase.client import ClientOptions

    from config import parse_janela_horas
    from feriados_br import is_business_day

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
        if last_dt.tzinfo is not None:               # PostgREST may return with a timezone
            last_dt = last_dt.astimezone().replace(tzinfo=None)  # → naive local time
        minutes = round((datetime.now() - last_dt).total_seconds() / 60)
    except Exception:
        pass

    h_start, h_end = parse_janela_horas(s.janela_horas)
    now = datetime.now()
    in_window = is_business_day(now.date()) and h_start <= now.hour <= h_end
    limite = scheduler_stale_min()

    # Grace period at the window's OPEN. Every business day at 07:00 the last load was the
    # previous day's ~18:5x one (~780 min ago) → 'stale' → alert (and 503 under ?strict=1)
    # until the day's 1st run, which IntervalTrigger may take a full interval to fire. That
    # was ~30 min of false alarm EVERY day — and a monitor that cries wolf is a monitor
    # nobody looks at. Within the grace period, no load is expected, not a symptom.
    minutos_de_janela = (now.hour - h_start) * 60 + now.minute
    aquecendo = in_window and minutos_de_janela < limite

    stale = bool(
        in_window and not aquecendo and minutes is not None and minutes > limite
    )
    return {
        'last_sync': last_iso,
        'last_status': last.get('status'),
        'minutes_ago': minutes,
        'in_window': in_window,
        'warming_up': aquecendo,
        'threshold_min': limite,
        'stale': stale,
    }


def _parse_local_dt(value: Any) -> Optional[datetime]:
    """Convert an ISO-8601 string (with or without timezone) into a *naive* local
    ``datetime``.

    The monitor writes local times (``yyyy-MM-ddTHH:mm:ss``); even so we handle the
    timezone case for robustness. Returns ``None`` if it cannot be parsed.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _age_minutes(value: Any) -> Optional[int]:
    """Age in (whole) minutes of an ISO timestamp until now; ``None`` if unreadable."""
    dt = _parse_local_dt(value)
    if dt is None:
        return None
    return round((datetime.now() - dt).total_seconds() / 60)


def _wbc_task_state_path() -> str:
    """Absolute path of the state JSON written by ``monitor_wbc_task.ps1``."""
    configured = get_settings().wbc_task_state_file
    if os.path.isabs(configured):
        return configured
    return os.path.join(_PROJECT_DIR, configured)


def _scheduled_task_signal() -> Dict[str, Any]:
    """State of the "Integração WBC" scheduled task, read from the monitor's JSON.

    Opens no connections and spawns no subprocess: it only reads the file the PowerShell
    script (scheduled every 10 min) keeps up to date. It flags problems on three fronts:

    - **missing/unreadable** → the monitor never ran or the file got corrupted;
    - **``stale``** → the ``checked_at`` stamp is older than ``WBC_TASK_STALE_MIN`` (the
      monitoring script itself may have stopped);
    - **``problems``** → list of task problems detected by the script (disabled, stuck
      running, last run errored, missed triggers).
    """
    s = get_settings()
    path = _wbc_task_state_path()

    if not os.path.exists(path):
        return {
            'available': False,
            'healthy': False,
            'task_name': s.wbc_task_name,
            'error': f'estado ausente ({os.path.basename(path)}) — monitor nunca rodou?',
        }

    try:
        with open(path, 'r', encoding='utf-8-sig') as fh:  # utf-8-sig tolerates a stray BOM
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError('conteúdo não é um objeto JSON')
    except (OSError, ValueError) as exc:
        return {
            'available': False,
            'healthy': False,
            'task_name': s.wbc_task_name,
            'error': f'estado ilegível: {str(exc)[:200]}',
        }

    age = _age_minutes(data.get('checked_at'))
    data['available'] = True
    data['age_min'] = age
    # An unreadable stamp also counts as stale (the data cannot be trusted).
    data['stale'] = age is None or age > s.wbc_task_stale_min
    return data


# ------------------------------------------------------------------ WBC worker

_WBC_TABELAS = ('acompanhamento', 'eventos', 'execucoes', 'travas')
_WBC_COLUNAS_EXECUCAO = ('id', 'inicio', 'fim', 'status', 'processados', 'sucessos', 'erros', 'detalhe')


def _wbc_tracking_db_path() -> Optional[str]:
    """Absolute path of the WBC tracking SQLite taken from ``TRACKING_DB_URL``.

    ``None`` when the URL is not a file-backed SQLite (PostgreSQL, ``:memory:``): the check
    then reports itself unavailable instead of guessing. Relative paths are resolved from
    the project root — the worker runs with cwd = root (``run_wbc_worker.bat``), so both
    read the same file.
    """
    url = (get_settings().wbc_tracking_db_url or '').strip()
    if not url.lower().startswith('sqlite:'):
        return None
    resto = url[len('sqlite:'):]
    # sqlite:///./state/x.db → ./state/x.db · sqlite:///D:/x.db → D:/x.db · sqlite:////var/x.db → /var/x.db
    caminho = resto[3:] if resto.startswith('////') else resto.lstrip('/')
    caminho = caminho.split('?', 1)[0]
    if not caminho or caminho.startswith(':memory:') or caminho.startswith('file:'):
        return None
    if os.path.isabs(caminho):
        return caminho
    return os.path.normpath(os.path.join(_PROJECT_DIR, caminho))


def _wbc_worker_in_window(now: datetime) -> bool:
    """Whether the worker is supposed to be cycling right now — by ITS OWN schedule
    (``WORKER_HORARIO_*``, ``WORKER_DIAS_DE_TRABALHO``; same ``.env`` line the worker
    reads). Outside it, silence is normal and never alarms. ``fim`` before ``inicio`` is a
    shift crossing midnight, as in ``wbcpython.config``.
    """
    s = get_settings()
    try:
        dias = {int(d) for d in s.wbc_worker_dias.split(',') if d.strip()}
        h_ini = datetime.strptime(s.wbc_worker_horario_inicio.strip(), '%H:%M').time()
        h_fim = datetime.strptime(s.wbc_worker_horario_fim.strip(), '%H:%M').time()
    except ValueError:
        # Unreadable schedule: the worker itself refuses to start on it. Treating it as
        # "always in window" makes the silence visible instead of hiding it.
        return True
    if now.isoweekday() not in dias:
        return False
    agora = now.time()
    if h_ini <= h_fim:
        return h_ini <= agora <= h_fim
    return agora >= h_ini or agora <= h_fim


def _wbc_worker_threshold_min() -> int:
    """Silence tolerated inside the window: two intervals, never under 10 min. A cycle takes
    9–14 s (measured 2026-09-08), but one Service Layer hiccup can hold a cycle for minutes
    and that is not the worker being dead."""
    return max(10, math.ceil(2 * get_settings().wbc_worker_interval_s / 60))


def _wbc_worker_signal() -> Dict[str, Any]:
    """State of the WBC → SAP integration worker, read from its tracking DB (SQLite).

    Opens the file read-only, no connection to SAP/WBC. Three levels, on purpose:

    - **not installed** (``installed=False``): the DB does not exist — the integration never
      ran on this machine. ``healthy=None`` and NO alert: on the .11 before the cutover
      this is the normal state, and an alert here would turn ``?strict=1`` into a 503 for
      a service nobody started yet.
    - **installed, never ran** (``last=None``): tables exist (the painel creates them) but
      the worker has not recorded a cycle. Information, no alert.
    - **running**: from the first recorded cycle on, silence inside the worker's own
      window beyond ``threshold_min`` → ``stale``; a cycle ``em_andamento`` for longer than
      that → ``stuck``; last cycle ``falhou`` → unhealthy. Each becomes a readable alert.
    """
    s = get_settings()
    agora = datetime.now()
    base: Dict[str, Any] = {
        'available': False, 'installed': False, 'healthy': None,
        'in_window': _wbc_worker_in_window(agora),
        'threshold_min': _wbc_worker_threshold_min(),
        'interval_s': s.wbc_worker_interval_s,
        'db': None,
    }
    caminho = _wbc_tracking_db_path()
    base['db'] = caminho
    if caminho is None:
        return {**base, 'note': 'TRACKING_DB_URL não é um SQLite em arquivo — check indisponível'}
    if not os.path.exists(caminho):
        return {**base, 'note': ('banco de acompanhamento ainda não existe — a integração WBC '
                                 'nunca rodou nesta máquina')}

    try:
        con = sqlite3.connect(f'file:{caminho}?mode=ro', uri=True, timeout=2)
        try:
            tabelas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            faltam = sorted(set(_WBC_TABELAS) - tabelas)
            ultima: Optional[Dict[str, Any]] = None
            hoje: Dict[str, Any] = {}
            if 'execucoes' in tabelas:
                row = con.execute(
                    'SELECT id, inicio, fim, status, processados, sucessos, erros, detalhe '
                    'FROM execucoes ORDER BY id DESC LIMIT 1'
                ).fetchone()
                if row:
                    ultima = dict(zip(_WBC_COLUNAS_EXECUCAO, row))
                n, falhas = con.execute(
                    "SELECT COUNT(*), COALESCE(SUM(CASE WHEN status = 'falhou' THEN 1 ELSE 0 END), 0) "
                    'FROM execucoes WHERE inicio >= ?', (agora.strftime('%Y-%m-%d'),)
                ).fetchone()
                hoje = {'execucoes': int(n or 0), 'falhas': int(falhas or 0)}
        finally:
            con.close()
    except sqlite3.Error as exc:
        return {**base, 'installed': True, 'healthy': False,
                'error': f'banco de acompanhamento ilegível: {str(exc)[:200]}'}

    out = {**base, 'installed': True, 'available': True, 'tabelas_faltando': faltam, 'hoje': hoje}
    if faltam:
        return {**out, 'healthy': False, 'error': f"banco sem as tabelas: {', '.join(faltam)}"}
    if ultima is None:
        return {**out, 'last': None,
                'note': 'sem execuções registradas — o worker ainda não rodou nesta máquina'}

    ultima['detalhe'] = (ultima.get('detalhe') or '')[:200]
    running = ultima.get('fim') is None and ultima.get('status') == 'em_andamento'
    referencia = ultima.get('inicio') if running else (ultima.get('fim') or ultima.get('inicio'))
    minutes_ago = _age_minutes(referencia)
    limite = out['threshold_min']
    stale = out['in_window'] and (minutes_ago is None or minutes_ago > limite)
    stuck = running and (minutes_ago is None or minutes_ago > limite)
    failed = ultima.get('status') == 'falhou'
    return {**out, 'last': ultima, 'running': running, 'minutes_ago': minutes_ago,
            'stale': stale, 'stuck': stuck, 'healthy': not (stale or stuck or failed)}


def _wbc_worker_alerts(w: Dict[str, Any]) -> list:
    """Readable alerts for the ``wbc_worker`` block. Nothing before the first recorded
    cycle on this machine (see ``_wbc_worker_signal``)."""
    if not w.get('installed'):
        return []
    if w.get('error'):
        return [f"worker WBC: {w['error']}"]
    last = w.get('last')
    if not last:
        return []
    alerts = []
    m = w.get('minutes_ago')
    idade = f"há {m} min" if m is not None else "com carimbo ilegível"
    if w.get('stale'):
        alerts.append(
            f"worker WBC sem ciclo {idade} dentro do expediente (limite {w.get('threshold_min')} min, "
            f"intervalo {w.get('interval_s')} s) — serviço OrcaView-WBC-Worker parado?"
        )
    if w.get('stuck'):
        alerts.append(
            f"worker WBC: ciclo #{last.get('id')} em andamento {idade} sem terminar — "
            "processo morreu no meio?"
        )
    if last.get('status') == 'falhou':
        alerts.append(
            f"worker WBC: última execução (#{last.get('id')}) falhou — {last.get('detalhe') or 'sem detalhe'}"
        )
    return alerts


def _windows_update_signal() -> Dict[str, Any]:
    """Pending reboot + pending updates + last patch (the ``windows_update`` block).

    Cheap on purpose: ``reboot_pendente()`` is ~0.2 ms of ``winreg`` (always fresh, it does
    not depend on the Windows Update agent) and ``estado_updates()`` only READS the cache
    the background thread fills. The search that costs 3.1 s on this machine does NOT
    happen here — if it did, it would blow the timeout of whoever calls ``/status``.

    **Read ``pendentes`` carefully: ``None`` is NOT zero.** See ``windows_update.py`` —
    with the agent not scanning, the search answers ``0`` and that ``0`` is a lie.
    """
    try:
        return {
            'reboot_pendente': windows_update.reboot_pendente(),
            **windows_update.estado_updates(),
        }
    except Exception as exc:
        # /status is the health endpoint: this block must not take it down (it would become
        # a 500 in api.py and the monitor would lose SAP/SQL/Supabase along with it). A
        # failure becomes "don't know" — and "don't know" can NEVER become "no pending
        # reboot"/"0 pending" (invariants 1 and 2).
        logger.warning('Falha ao ler o estado do Windows Update: %s', exc)
        return {
            'reboot_pendente': {'pendente': None, 'motivos': [], 'erro': str(exc)[:200]},
            'estado': 'erro',
            'patching_automatico': None,
            'pendentes': None,
            'pendentes_motivo': f'falha ao ler o Windows Update: {str(exc)[:120]}',
            'ultima_varredura': None,
            'ultimo_patch': None,
            'dias_sem_patch': None,
        }


def _local_ip() -> Optional[str]:
    """Local outbound IP (sends no packet — only resolves the route). Falls back to hostname."""
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
    """Host, IP, OS, Python, disk (stdlib) + CPU/memory (psutil, if available)."""
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
        import psutil  # optional — only for CPU and memory
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
    """Collect the state of the server + dependencies (on demand).

    Args:
        only: subset of ``SELECTABLE_CHECKS`` to run (from ``?checks=``). ``None``/empty
            runs all of them. ``system`` (host/IP/CPU/disk) is always included (local and
            cheap).

    Returns:
        Dict with ``ok`` (every connection that ran is green), ``healthy`` (``ok`` and no
        alerts), ``checks`` (connectivity), ``scheduler`` (indirect signal),
        ``scheduled_task`` (state of the "Integração WBC" task, read from the monitor),
        ``wbc_worker`` (the WBC → SAP integration worker, read from its tracking DB),
        ``windows_update`` (pending reboot + pending updates + last patch), ``system``,
        ``api_auth`` (whether OS_API_KEY is configured — information only, never an
        alert) and ``alerts`` (list of readable warnings: low disk, scheduler stopped,
        task stuck, pending reboot…).

        ``ok``/``healthy`` reflect ONLY the checks that ran — asking for a subset is the
        caller's explicit choice, and says nothing about the rest.

    Raises:
        ValueError: if ``only`` carries a name outside ``SELECTABLE_CHECKS``.

    Note:
        Failing loudly on an invalid name is DELIBERATE. Before, a wrong name matched no
        ``if``, ``checks`` came out empty and ``all([])`` → ``True``: the response was
        ``healthy: true`` **without anything having been checked**. A monitor with a typo
        in the URL (or an LLM guessing the check's name) went blind forever, reporting
        perfect health. A noisy 400 beats a false green.
    """
    if only:
        desconhecidos = sorted(set(only) - set(SELECTABLE_CHECKS))
        if desconhecidos:
            raise ValueError(
                f"check(s) desconhecido(s): {', '.join(desconhecidos)}. "
                f"Validos: {', '.join(sorted(SELECTABLE_CHECKS))}"
            )

    sel = set(SELECTABLE_CHECKS) if not only else only

    checks: Dict[str, Any] = {}
    if 'sap' in sel:
        checks['sap'] = _timed(_check_sap)
    if 'sql_server' in sel:
        checks['sql_server'] = _timed(_check_sql_server)
    if 'supabase' in sel:
        checks['supabase'] = _timed(_check_supabase)

    scheduler = _scheduler_signal() if 'scheduler' in sel else None
    scheduled_task = _scheduled_task_signal() if 'scheduled_task' in sel else None
    wbc_worker = _wbc_worker_signal() if 'wbc_worker' in sel else None
    wu_estado = _windows_update_signal() if 'windows_update' in sel else None
    system = _system_info()
    # Whether the API requires X-API-Key. Without OS_API_KEY every route except the SAP
    # write falls OPEN (api.py fail-open) and only the boot log warns — this field makes
    # a lost .env line visible to whoever reads /status. Like `system`, it is always
    # included (local, zero cost). INFORMATION, not health (Marcelo, 2026-08-31): it must
    # NEVER become an alert — an alert drops `healthy` and turns ?strict=1 into 503,
    # paging the watchdog over a config issue that does not affect the integration.
    api_auth = {'api_key_configurada': bool(get_settings().os_api_key)}

    alerts = []
    if scheduler and scheduler.get('stale'):
        alerts.append(
            f"agendador possivelmente parado: última carga de oportunidades há "
            f"{scheduler.get('minutes_ago')} min (limite {scheduler.get('threshold_min')} min "
            f"na janela comercial)"
        )
    if scheduled_task is not None:
        alerts.extend(_scheduled_task_alerts(scheduled_task))
    if wbc_worker is not None:
        alerts.extend(_wbc_worker_alerts(wbc_worker))
    # The windows_update block raises NO alert — neither pending reboot nor pending update.
    # Marcelo's decision (2026-07-16, revising the plan's D1): this is INFORMATION, not
    # system health. "If one day the server does not reboot, it does not matter" — what
    # must not happen is the monitor claiming the integration is bad because of it (an
    # alert drops `healthy` and makes ?strict=1 answer 503). Whoever wants the data asks
    # for it and reads it.
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
        'api_auth': api_auth,
        'alerts': alerts,
    }
    if scheduler is not None:
        out['scheduler'] = scheduler
    if scheduled_task is not None:
        out['scheduled_task'] = scheduled_task
    if wbc_worker is not None:
        out['wbc_worker'] = wbc_worker
    if wu_estado is not None:
        out['windows_update'] = wu_estado
    return out


def _scheduled_task_alerts(task: Dict[str, Any]) -> list:
    """Turn the scheduled task's state into readable alerts for ``/status``.

    A missing/stale file points to a stopped monitor; ``problems`` are the task's own
    problems as detected by the PowerShell script.
    """
    name = task.get('task_name', 'Integração WBC')
    if not task.get('available'):
        return [f"monitor da tarefa '{name}': {task.get('error', 'estado indisponível')}"]

    alerts = []
    if task.get('stale'):
        age = task.get('age_min')
        idade = f"há {age} min" if age is not None else "com carimbo ilegível"
        alerts.append(
            f"monitor da tarefa '{name}' desatualizado (última verificação {idade}, "
            f"limite {get_settings().wbc_task_stale_min} min) — script de monitoramento parado?"
        )
    # PowerShell 5.1's ConvertTo-Json serializes a 1-element array as a scalar; normalize
    # to a list before iterating (otherwise we would iterate the string's characters).
    problems = task.get('problems') or []
    if isinstance(problems, str):
        problems = [problems]
    for problema in problems:
        alerts.append(f"tarefa '{name}': {problema}")
    return alerts
