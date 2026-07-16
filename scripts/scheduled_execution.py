"""APScheduler entrypoint: SAP → Supabase on interval (business days only)."""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import scripts._bootstrap  # noqa: F401
from config import get_settings, parse_janela_horas
from extract_sap_to_supabase import main
from feriados_br import is_business_day, is_national_holiday
from pipeline_core import FileLockTimeout, oportunidades_sync_lock

LOG_RETENTION_DAYS = 6
HEARTBEAT_INTERVAL_S = 3600

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Configure logging (rotating file + console).

    Called only by the entrypoint (``main_scheduler``), **not on import** — that way
    importing the module in tests does not redirect the suite's log into the production
    file ``logs/scheduled_execution.log``.
    """
    os.makedirs('logs', exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        'logs/scheduled_execution.log',
        when='midnight',
        interval=1,
        backupCount=LOG_RETENTION_DAYS,
        encoding='utf-8',
    )
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[file_handler, logging.StreamHandler()],
        force=True,
    )

_execution_lock = threading.Lock()


def is_within_commercial_window(
    *, janela_horas: str, now: Optional[datetime] = None,
) -> bool:
    """True on business day within inclusive hour window."""
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
    return is_within_commercial_window(janela_horas=get_settings().janela_horas, now=now)


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
            # File lock: never runs alongside the API's "force sync".
            with oportunidades_sync_lock(timeout=0):
                success = main(view_name=view_name, execution_mode=settings.execution_mode)
            logger.info('Run finished: %s', 'OK' if success else 'FAILED')
        except FileLockTimeout:
            logger.warning(
                'Run skipped: sincronização de oportunidades já em andamento (lock de arquivo)'
            )
        except Exception as exc:
            logger.error('Run error: %s', exc)
        logger.info('=' * 60)
    finally:
        _execution_lock.release()


def configurar_agenda() -> BackgroundScheduler:
    settings = get_settings()
    # JANELA_HORAS already validated in get_settings(); re-check for scheduler startup log
    try:
        parse_janela_horas(settings.janela_horas)
    except ValueError as exc:
        logger.error('[CONFIG] Scheduler startup aborted — fix .env: %s', exc)
        raise

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
    _configure_logging()
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
