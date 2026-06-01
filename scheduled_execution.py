"""Agendador que executa a extração SAP → Supabase via APScheduler.

Roda uma carga ao iniciar (startup) e depois nos horários fixos de ``HORARIOS``.
Usa ``BlockingScheduler`` (processo dedicado, sem busy-wait) e é o entrypoint indicado
para rodar como serviço/tarefa agendada 24/6.

Instalação:
    pip install apscheduler
"""

import os
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from extract_sap_to_supabase import main

# Horários das execuções diárias (hora, minuto)
HORARIOS = [(9, 0), (12, 30), (17, 35)]

# Garantir o diretório de logs ANTES de configurar o FileHandler (evita erro no import)
os.makedirs('logs', exist_ok=True)

# Configurar logging (console + arquivo)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/scheduled_execution.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Carregar variáveis de ambiente
load_dotenv()


def job_execucao() -> None:
    """Executa uma rodada da extração, lendo view e modo das variáveis de ambiente."""
    logger.info("="*60)
    logger.info("INICIANDO EXECUÇÃO AGENDADA")
    logger.info("="*60)

    view_name = os.getenv('SAP_VIEW_NAME', 'SUA_VIEW_SAP')
    execution_mode = os.getenv('EXECUTION_MODE', 'snapshot')

    try:
        success = main(view_name=view_name, execution_mode=execution_mode)
        if success:
            logger.info("✓ Execução concluída com sucesso")
        else:
            logger.error("❌ Execução falhou")
    except Exception as e:
        logger.error(f"Erro durante execução: {e}")

    logger.info("="*60)


def configurar_agenda() -> BlockingScheduler:
    """Cria e configura o scheduler com os gatilhos de execução.

    Os ``job_defaults`` deixam o agendamento robusto para operação 24/6:
        - ``coalesce``: execuções perdidas (servidor desligado) viram uma única execução;
        - ``max_instances=1``: nunca há duas cargas simultâneas;
        - ``misfire_grace_time``: tolera atraso de até 1h (ex.: boot da segunda-feira).

    Returns:
        Um ``BlockingScheduler`` configurado (ainda não iniciado).
    """
    scheduler = BlockingScheduler(
        job_defaults={
            'coalesce': True,
            'max_instances': 1,
            'misfire_grace_time': 3600,
        }
    )

    # Uma execução por horário definido em HORARIOS (todos os dias)
    for hora, minuto in HORARIOS:
        scheduler.add_job(
            job_execucao,
            trigger=CronTrigger(hour=hora, minute=minuto),
            id=f'extracao_{hora:02d}{minuto:02d}',
            name=f'Extração às {hora:02d}:{minuto:02d}',
        )

    logger.info("Agenda configurada:")
    for job in scheduler.get_jobs():
        logger.info(f"  - {job.name}: {job.trigger}")

    return scheduler


def main_scheduler() -> None:
    """Roda uma carga no startup e depois mantém o scheduler ativo até Ctrl+C."""
    logger.info("Iniciando scheduler de extração...")

    scheduler = configurar_agenda()

    # Execução imediata ao iniciar (run on startup) — falhas são tratadas dentro do job
    logger.info("Executando carga inicial (startup)...")
    job_execucao()

    horarios_txt = ", ".join(f"{h:02d}:{m:02d}" for h, m in HORARIOS)
    logger.info(f"Scheduler ativo. Horários: {horarios_txt}. Pressione Ctrl+C para interromper.")

    try:
        # start() é bloqueante no BlockingScheduler — não há busy-wait.
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Interrompendo scheduler...")
        scheduler.shutdown()
        logger.info("Scheduler finalizado")


if __name__ == "__main__":
    main_scheduler()
