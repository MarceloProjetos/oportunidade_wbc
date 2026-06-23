"""APScheduler entrypoint: SAP → Supabase on interval (business days only)."""

from __future__ import annotations

import scripts._bootstrap  # noqa: F401

import logging
import os
import signal
import threading
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Optional, Set

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import get_settings, parse_janela_horas
from extract_sap_to_supabase import main
from feriados_br import is_business_day, is_national_holiday

LOG_RETENTION_DAYS = 12
HEARTBEAT_INTERVAL_S = 3600

_DOW_CRON = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}

os.makedirs('logs', exist_ok=True)

_file_handler = TimedRotatingFileHandler(
    'logs/scheduled_execution.log',
    when='midnight',
    interval=1,
    backupCount=LOG_RETENTION_DAYS,
    encoding='utf-8',
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[_file_handler, logging.StreamHandler()],
    force=True,
)
logger = logging.getLogger(__name__)

_execution_lock = threading.Lock()


def _parse_dias_semana(expr: str) -> Set[int]:
    """Legacy DIAS_SEMANA parser (validated at startup; runtime uses is_business_day)."""
    expr = expr.strip().lower()
    if '-' in expr:
        start, end = expr.split('-', 1)
        if start.strip() not in _DOW_CRON or end.strip() not in _DOW_CRON:
            raise ValueError(f'Invalid DIAS_SEMANA: {expr!r}')
        a, b = _DOW_CRON[start.strip()], _DOW_CRON[end.strip()]
        if a > b:
            raise ValueError(f'Invalid DIAS_SEMANA range: {expr!r}')
        return set(range(a, b + 1))
    days: Set[int] = set()
    for part in expr.split(','):
        p = part.strip()
        if p not in _DOW_CRON:
            raise ValueError(f'Invalid DIAS_SEMANA: {expr!r}')
        days.add(_DOW_CRON[p])
    return days


def is_within_commercial_window(
    *,
    janela_horas: str,
    now: Optional[datetime] = None,
    dias_semana: Optional[str] = None,
) -> bool:
    """True on business day within inclusive hour window."""
    del dias_semana
    now = now or datetime.now()
    if not is_business_day(now.date()):
        return False
    h_start, h_end = parse_janela_horas(janela_horas)
    return h_start <= now.hour <= h_end


def can_run_load(*, ignore_hour_window: bool = False, now: Optional[datetime] = None) -> bool:
    """Business day check + optional hour window."""
    now = now or datetime.now()
    if not is_business_day(now.date()):
        return False
    if ignore_hour_window:
        return True
    h_start, h_end = parse_janela_horas(get_settings().janela_horas)
    return h_start <= now.hour <= h_end


def esta_na_janela_comercial(
    *,
    janela_horas: str,
    agora: Optional[datetime] = None,
    now: Optional[datetime] = None,
    dias_semana: Optional[str] = None,
) -> bool:
    return is_within_commercial_window(
        janela_horas=janela_horas, now=agora or now, dias_semana=dias_semana,
    )


def pode_executar_carga(
    *,
    ignorar_janela_horaria: bool = False,
    ignorar_janela: bool = False,
    agora: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> bool:
    return can_run_load(
        ignore_hour_window=ignorar_janela_horaria or ignorar_janela,
        now=agora or now,
    )


def job_execucao(*, ignorar_janela: bool = False) -> None:
    """Run one ETL cycle; global lock prevents concurrent runs."""
    if not can_run_load(ignore_hour_window=ignorar_janela):
        now = datetime.now()
        if now.weekday() >= 5:
            logger.debug('Skipped: weekend')
        elif is_national_holiday(now.date()):
            logger.debug('Skipped: national holiday %s', now.date().isoformat())
        else:
            logger.debug('Skipped: outside hour window')
        return

    if not _execution_lock.acquire(blocking=False):
        logger.warning('Run skipped: another execution is in progress')
        return

    try:
        logger.info('=' * 60)
        logger.info('SCHEDULED RUN START')
        logger.info('=' * 60)

        settings = get_settings()
        view_name = settings.sap_view_name or 'SUA_VIEW_SAP'
        try:
            success = main(view_name=view_name, execution_mode=settings.execution_mode)
            logger.info('Run finished: %s', 'OK' if success else 'FAILED')
        except Exception as exc:
            logger.error('Run error: %s', exc)
        logger.info('=' * 60)
    finally:
        _execution_lock.release()


def configurar_agenda() -> BackgroundScheduler:
    settings = get_settings()
    parse_janela_horas(settings.janela_horas)
    _parse_dias_semana(settings.dias_semana)

    scheduler = BackgroundScheduler(
        job_defaults={'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 3600},
    )
    scheduler.add_job(
        job_execucao,
        trigger=IntervalTrigger(minutes=settings.intervalo_minutos),
        id='extracao_intervalar',
        name=(
            f'Every {settings.intervalo_minutos}min '
            f'({settings.janela_horas}h, Mon-Fri, no holidays)'
        ),
    )
    for job in scheduler.get_jobs():
        logger.info('Job: %s — %s', job.name, job.trigger)
    return scheduler


def main_scheduler() -> None:
    logger.info('Starting scheduler...')
    scheduler = configurar_agenda()

    logger.info('Startup run...')
    job_execucao(ignorar_janela=True)

    scheduler.start()

    def _stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)

    elapsed = 0
    try:
        while True:
            time.sleep(1)
            elapsed += 1
            if elapsed >= HEARTBEAT_INTERVAL_S:
                elapsed = 0
                try:
                    times = [j.next_run_time for j in scheduler.get_jobs() if j.next_run_time]
                    nxt = min(times).strftime('%d/%m %H:%M:%S') if times else 'n/a'
                    logger.info('Heartbeat — next run: %s', nxt)
                except Exception as exc:
                    logger.warning('Heartbeat failed (ignored): %s', exc)
    except (KeyboardInterrupt, SystemExit):
        logger.info('Shutting down scheduler...')
        scheduler.shutdown()


if __name__ == '__main__':
    try:
        main_scheduler()
    except Exception:
        logger.exception('Fatal scheduler error')
        raise SystemExit(1) from None
