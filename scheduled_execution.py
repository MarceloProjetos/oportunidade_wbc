"""Agendador que executa a extração SAP → Supabase via APScheduler.

Roda uma carga ao iniciar (startup) e depois nos horários fixos de ``HORARIOS``.
Usa ``BackgroundScheduler`` com um laço ``time.sleep`` no thread principal: no Windows
o Ctrl+C interrompe o ``time.sleep`` na hora (um ``BlockingScheduler`` ficaria preso
num ``Event.wait`` longo e ignoraria o Ctrl+C até o próximo job). É o entrypoint
indicado para rodar como serviço/tarefa agendada 24/7.

Instalação:
    pip install apscheduler
"""

import os
import time
import signal
import logging
import threading
from logging.handlers import TimedRotatingFileHandler

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from extract_sap_to_supabase import main

# Horários das execuções diárias (hora, minuto)
HORARIOS = [(8, 12), (9, 0), (10, 12), (12, 30), (14, 12), (17, 35)]

# Dias de log a reter (rotação diária à meia-noite)
LOG_RETENCAO_DIAS = 12

# Intervalo do "heartbeat": a cada N segundos o processo loga que segue vivo e mostra
# a próxima execução agendada. Permite detectar, só pelo log, se o scheduler parou de
# disparar silenciosamente num processo 24/7 (1h = 24 linhas/dia, ruído desprezível).
HEARTBEAT_INTERVALO_S = 3600

# Garantir o diretório de logs ANTES de configurar o handler (evita erro no import)
os.makedirs('logs', exist_ok=True)

# Log com rotação diária: gera um arquivo por dia e mantém só os últimos N dias.
# Evita o log crescer sem limite num processo 24/7.
_file_handler = TimedRotatingFileHandler(
    'logs/scheduled_execution.log',
    when='midnight',
    interval=1,
    backupCount=LOG_RETENCAO_DIAS,
    encoding='utf-8',
)

# force=True garante que esta configuração prevaleça sobre a do módulo importado
# (extract_sap_to_supabase chama basicConfig no import), senão o file handler seria ignorado.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[_file_handler, logging.StreamHandler()],
    force=True,
)
logger = logging.getLogger(__name__)

# Carregar variáveis de ambiente
load_dotenv()

# Lock global de execução: serializa job_execucao para que NUNCA haja duas cargas
# simultâneas, independentemente do gatilho (startup ou qualquer horário agendado).
# Ver a nota em job_execucao sobre por que o max_instances=1 do APScheduler não basta.
_execucao_lock = threading.Lock()


def job_execucao() -> None:
    """Executa uma rodada da extração, lendo view e modo das variáveis de ambiente.

    Protegido pelo lock global ``_execucao_lock``, que garante exclusão mútua entre
    QUALQUER gatilho — a carga de startup e todos os horários agendados. O
    ``max_instances=1`` do APScheduler só impede um job de se sobrepor a si mesmo (por
    ``id``); ele não cobre dois horários distintos se sobrepondo (ex.: o de 09:00 travar
    até 10:12) nem a carga de startup, que roda fora do scheduler. Se já há uma carga em
    andamento, esta é descartada — no modo snapshot a próxima rodada repõe o dado, então
    pular é seguro e preferível a arriscar um ``insere-depois-poda`` concorrente.
    """
    if not _execucao_lock.acquire(blocking=False):
        logger.warning("Execução já em andamento; esta foi descartada para evitar sobreposição.")
        return

    try:
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
    finally:
        _execucao_lock.release()


def configurar_agenda() -> BackgroundScheduler:
    """Cria e configura o scheduler com os gatilhos de execução.

    Os ``job_defaults`` deixam o agendamento robusto para operação 24/7:
        - ``coalesce``: execuções perdidas (servidor desligado) viram uma única execução;
        - ``max_instances=1``: impede um mesmo horário de empilhar instâncias (a exclusão
          mútua entre horários distintos e o startup fica a cargo do lock global em
          ``job_execucao``);
        - ``misfire_grace_time``: tolera atraso de até 1h (ex.: boot da segunda-feira).

    Returns:
        Um ``BackgroundScheduler`` configurado (ainda não iniciado).
    """
    scheduler = BackgroundScheduler(
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

    # start() do BackgroundScheduler não bloqueia: os jobs rodam em threads próprias.
    scheduler.start()

    # SIGTERM permite parada limpa ao rodar como serviço (NSSM, Task Scheduler, etc.):
    # converte o sinal em KeyboardInterrupt, reaproveitando o mesmo tratamento abaixo.
    def _parar(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _parar)

    try:
        # time.sleep no thread principal É interrompido pelo Ctrl+C no Windows,
        # ao contrário do Event.wait longo do BlockingScheduler.
        segundos = 0
        while True:
            time.sleep(1)
            segundos += 1
            if segundos >= HEARTBEAT_INTERVALO_S:
                segundos = 0
                # Heartbeat: prova de vida no log + próxima carga agendada. Em try/except
                # próprio para que uma falha de log (ex.: disco cheio) não derrube o laço.
                try:
                    proximos = [j.next_run_time for j in scheduler.get_jobs() if j.next_run_time]
                    proxima_txt = min(proximos).strftime('%d/%m %H:%M:%S') if proximos else 'n/d'
                    logger.info(f"Scheduler vivo (heartbeat). Próxima execução: {proxima_txt}")
                except Exception as hb_exc:
                    logger.warning(f"Falha no heartbeat (ignorada): {hb_exc}")
    except (KeyboardInterrupt, SystemExit):
        logger.info("Interrompendo scheduler...")
        scheduler.shutdown()
        logger.info("Scheduler finalizado")


if __name__ == "__main__":
    # main_scheduler já trata Ctrl+C/SIGTERM internamente (saída limpa, código 0).
    # Aqui capturamos só falhas FATAIS fora dos jobs (ex.: erro ao iniciar o scheduler):
    # logamos o traceback no arquivo (senão iria só para o stderr e se perderia) e saímos
    # com código != 0, sinalizando ao supervisor (NSSM/Task Scheduler) que deve reiniciar.
    try:
        main_scheduler()
    except Exception:
        logger.exception("Falha fatal no scheduler — encerrando com código de erro")
        raise SystemExit(1)
