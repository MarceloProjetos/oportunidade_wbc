"""Diagnóstico sob demanda do servidor + dependências (endpoint ``/status`` da API).

Roda **apenas quando chamado** (não há polling em background), para não competir com os
serviços. Cada chamada abre conexões de teste com SAP, SQL Server (WBC) e Supabase e mede
a latência; coleta também métricas do sistema (host/IP/SO/disco via stdlib; CPU e memória
via ``psutil``, se instalado — sem ele, o resto do ``/status`` continua funcionando).

Use com parcimônia: cada chamada abre (e fecha) 3 conexões de teste.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import socket
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional

import windows_update
from config import get_settings

logger = logging.getLogger(__name__)

# Diretório do projeto (onde vive este módulo) — base para caminhos relativos.
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# Início (aprox.) do processo da API — usado para o uptime no /status.
_PROC_START = time.time()

# Folga sobre o INTERVALO_MINUTOS antes de considerar o agendador parado. O limiar é
# DERIVADO (intervalo + folga), não fixo: era `SCHEDULER_STALE_MIN = 35` hard-coded
# enquanto o intervalo é configurável — com INTERVALO_MINUTOS=60 o /status passava a
# gritar "agendador parado" o dia inteiro (e ?strict=1 devolvia 503 permanente), embora
# tudo estivesse certo. A folga cobre a duração da carga + o atraso do trigger.
SCHEDULER_STALE_FOLGA_MIN = 5


def scheduler_stale_min() -> int:
    """Minutos sem carga que caracterizam agendador parado (= intervalo + folga)."""
    return get_settings().intervalo_minutos + SCHEDULER_STALE_FOLGA_MIN
# Alertas de disco da unidade onde o app roda.
DISK_LOW_GB = 5.0      # menos que isto livre
DISK_PCT_ALERT = 90.0  # ou mais que isto usado

# Checagens que o ?checks= pode selecionar (system é sempre incluído, é local/barato).
SELECTABLE_CHECKS = ('sap', 'sql_server', 'supabase', 'scheduler', 'scheduled_task',
                     'windows_update')
# Nota: a classificação dos LastTaskResult (sucesso/running/never-run/recusado) vive em
# monitor_wbc_task.ps1 — o Python só LÊ o JSON de estado que o script grava.
# Nota 2: `windows_update` é barato AQUI (~0,2 ms de winreg + leitura de cache) porque a
# busca cara (3,1 s medidos nesta máquina) roda numa thread daemon disparada no start da
# API — ver windows_update.py. Ele entra no ?checks= para poder ser pedido ISOLADO, sem
# pagar as 3 conexões de teste (SAP/SQL/Supabase); é o que a tool MCP faz.


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

    Lê o registro mais recente em ``sincronizacao_log``. Só marca ``stale=True`` quando a
    ausência de carga é de fato ANORMAL, ou seja, quando **todas** valem:

    * estamos na **janela comercial** (dia útil, dentro de ``JANELA_HORAS``) — fora dela e
      no fim de semana, não ter carga recente é o esperado;
    * a janela já abriu há mais que um ciclo (``warming_up=False``) — na abertura, a última
      carga é a de ontem à noite e "velha" é normal até a 1ª execução do dia;
    * a última carga é mais antiga que ``scheduler_stale_min()`` (= ``INTERVALO_MINUTOS`` +
      folga), limiar **derivado** do intervalo configurado, não um número fixo.

    O rigor é proposital: este sinal vira alerta e, com ``?strict=1``, **HTTP 503**. Alarme
    falso recorrente treina todo mundo a ignorar o monitor — o que é pior que não ter monitor.
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
        if last_dt.tzinfo is not None:               # PostgREST pode devolver com timezone
            last_dt = last_dt.astimezone().replace(tzinfo=None)  # → hora local naive
        minutes = round((datetime.now() - last_dt).total_seconds() / 60)
    except Exception:
        pass

    h_start, h_end = parse_janela_horas(s.janela_horas)
    now = datetime.now()
    in_window = is_business_day(now.date()) and h_start <= now.hour <= h_end
    limite = scheduler_stale_min()

    # Carência na ABERTURA da janela. Todo dia útil às 07:00 a última carga era a de
    # ~18:5x do dia anterior (~780 min atrás) → 'stale' → alerta (e 503 no ?strict=1) até
    # a 1ª execução do dia, que o IntervalTrigger pode levar até um intervalo inteiro para
    # disparar. Eram ~30 min de alarme falso TODO dia — e monitor que grita à toa é monitor
    # que ninguém olha. Dentro da carência, ausência de carga é esperada, não sintoma.
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
    """Converte um ISO-8601 (com ou sem timezone) em ``datetime`` local *naive*.

    O monitor grava horários locais (``yyyy-MM-ddTHH:mm:ss``); ainda assim tratamos o
    caso com timezone para robustez. Devolve ``None`` se não der para interpretar.
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
    """Idade em minutos (inteiros) de um carimbo ISO até agora; ``None`` se ilegível."""
    dt = _parse_local_dt(value)
    if dt is None:
        return None
    return round((datetime.now() - dt).total_seconds() / 60)


def _wbc_task_state_path() -> str:
    """Caminho absoluto do JSON de estado gravado pelo ``monitor_wbc_task.ps1``."""
    configured = get_settings().wbc_task_state_file
    if os.path.isabs(configured):
        return configured
    return os.path.join(_PROJECT_DIR, configured)


def _scheduled_task_signal() -> Dict[str, Any]:
    """Estado da tarefa agendada "Integração WBC", lido do JSON do monitor.

    Não abre conexões nem roda subprocesso: apenas lê o arquivo que o script PowerShell
    (agendado a cada 10 min) mantém atualizado. Sinaliza problema em três frentes:

    - **ausente/ilegível** → o monitor nunca rodou ou o arquivo corrompeu;
    - **``stale``** → o carimbo ``checked_at`` está mais velho que ``WBC_TASK_STALE_MIN``
      (o próprio script de monitoramento pode ter parado);
    - **``problems``** → lista de problemas da tarefa detectados pelo script (desabilitada,
      travada em execução, última execução com erro, gatilhos perdidos).
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
        with open(path, 'r', encoding='utf-8-sig') as fh:  # utf-8-sig tolera BOM eventual
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
    # Sem carimbo legível também conta como desatualizado (não dá para confiar no dado).
    data['stale'] = age is None or age > s.wbc_task_stale_min
    return data


def _windows_update_signal() -> Dict[str, Any]:
    """Reboot pendente + updates pendentes + último patch (bloco ``windows_update``).

    Barato de propósito: ``reboot_pendente()`` é ~0,2 ms de ``winreg`` (sempre fresco, não
    depende do agente do Windows Update) e ``estado_updates()`` só LÊ o cache que a thread
    de background popula. A busca que custa 3,1 s nesta máquina NÃO acontece aqui — se
    acontecesse, estouraria o timeout de quem chama o ``/status``.

    **Leia ``pendentes`` com atenção: ``None`` NÃO é zero.** Ver ``windows_update.py`` —
    com o agente sem varrer, a busca responde ``0`` e o ``0`` é mentira.
    """
    try:
        return {
            'reboot_pendente': windows_update.reboot_pendente(),
            **windows_update.estado_updates(),
        }
    except Exception as exc:
        # O /status é o endpoint de saúde: este bloco não pode derrubá-lo (viraria 500 no
        # api.py e o monitor perderia SAP/SQL/Supabase junto). Falha vira "não sei" — e
        # "não sei" NUNCA pode virar "sem reboot pendente"/"0 pendentes" (invariantes 1 e 2).
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
        sem alertas), ``checks`` (conectividade), ``scheduler`` (sinal indireto),
        ``scheduled_task`` (estado da tarefa "Integração WBC", lido do monitor),
        ``windows_update`` (reboot pendente + updates pendentes + último patch), ``system`` e
        ``alerts`` (lista de avisos legíveis: disco baixo, agendador parado, tarefa travada,
        reboot pendente…).

        ``ok``/``healthy`` refletem SÓ as checagens que rodaram — pedir um subconjunto é
        escolha explícita de quem chama, e não afirma nada sobre o resto.

    Raises:
        ValueError: se ``only`` trouxer nome fora de ``SELECTABLE_CHECKS``.

    Note:
        Falhar alto no nome inválido é DELIBERADO. Antes, um nome errado não casava com
        nenhum ``if``, ``checks`` saía vazio e ``all([])`` → ``True``: a resposta era
        ``healthy: true`` **sem nada ter sido checado**. Um monitor com typo na URL (ou um
        LLM chutando o nome do check) ficava cego para sempre, reportando saúde perfeita.
        Um 400 barulhento é melhor que um verde falso.
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
    wu_estado = _windows_update_signal() if 'windows_update' in sel else None
    system = _system_info()

    alerts = []
    if scheduler and scheduler.get('stale'):
        alerts.append(
            f"agendador possivelmente parado: última carga de oportunidades há "
            f"{scheduler.get('minutes_ago')} min (limite {scheduler.get('threshold_min')} min "
            f"na janela comercial)"
        )
    if scheduled_task is not None:
        alerts.extend(_scheduled_task_alerts(scheduled_task))
    # O bloco windows_update NÃO gera alerta — nem reboot pendente, nem update pendente.
    # Decisão do Marcelo (2026-07-16, revisando a D1 do plano): isto é INFORMAÇÃO, não
    # saúde do sistema. "Se um dia o servidor não reiniciar não importa" — o que não pode
    # é o monitor dizer que a integração está ruim por causa disso (alerta derruba
    # `healthy` e faz o ?strict=1 responder 503). Quem quiser o dado, pede e lê.
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
    if scheduled_task is not None:
        out['scheduled_task'] = scheduled_task
    if wu_estado is not None:
        out['windows_update'] = wu_estado
    return out


def _scheduled_task_alerts(task: Dict[str, Any]) -> list:
    """Traduz o estado da tarefa agendada em alertas legíveis para o ``/status``.

    Ausência/desatualização do arquivo apontam para o monitor parado; ``problems`` são os
    problemas da própria tarefa detectados pelo script PowerShell.
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
    # ConvertTo-Json do PowerShell 5.1 serializa array de 1 elemento como escalar; normaliza
    # para lista antes de iterar (senão iteraríamos os caracteres da string).
    problems = task.get('problems') or []
    if isinstance(problems, str):
        problems = [problems]
    for problema in problems:
        alerts.append(f"tarefa '{name}': {problema}")
    return alerts
