"""Agendador que executa a extração SAP → Supabase via APScheduler.

Roda uma carga ao iniciar (startup) e depois em intervalo fixo dentro da janela
comercial (default: a cada 30 min, 07h–18h59, seg–sex — configurável via ``.env``).
Não executa em finais de semana nem em feriados nacionais brasileiros (calendário
até 2030, ver ``feriados_br.py``).
Substitui a lista de horários fixos de antes (08:12, 09:00, 10:12, 12:30, 14:12,
17:35), cujos buracos de até 3h deixavam o app mobile com status defasado
(caso XCMG, 2026-06-11).

Usa ``BackgroundScheduler`` com um laço ``time.sleep`` no thread principal: no Windows
o Ctrl+C interrompe o ``time.sleep`` na hora (um ``BlockingScheduler`` ficaria preso
num ``Event.wait`` longo e ignoraria o Ctrl+C até o próximo job). É o entrypoint
indicado para rodar como serviço/tarefa agendada 24/7.

Variáveis de ambiente (``.env``, todas opcionais):
    INTERVALO_MINUTOS: minutos entre cargas (default 30; piso 5; sem limite de 59).
    JANELA_HORAS: faixa de horas inclusiva (default ``7-18``).
    DIAS_SEMANA: legado/documentação (execução efetiva: seg–sex + feriados BR).

Instalação:
    pip install apscheduler
"""

import os
import re
import time
import signal
import logging
import threading
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Optional, Set

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from config import get_settings
from extract_sap_to_supabase import main
from feriados_br import eh_dia_util, eh_feriado_nacional

# Dias de log a reter (rotação diária à meia-noite)
LOG_RETENCAO_DIAS = 12

# Intervalo do "heartbeat": a cada N segundos o processo loga que segue vivo e mostra
# a próxima execução agendada. Permite detectar, só pelo log, se o scheduler parou de
# disparar silenciosamente num processo 24/7 (1h = 24 linhas/dia, ruído desprezível).
HEARTBEAT_INTERVALO_S = 3600

# Mapa dia-da-semana no formato cron do APScheduler (seg=0 … dom=6)
_DOW_CRON = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}
_JANELA_HORAS_RE = re.compile(r'^\d{1,2}-\d{1,2}$')

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

# Lock global de execução: serializa job_execucao para que NUNCA haja duas cargas
# simultâneas, independentemente do gatilho (startup ou qualquer horário agendado).
# Ver a nota em job_execucao sobre por que o max_instances=1 do APScheduler não basta.
_execucao_lock = threading.Lock()


def _parse_janela_horas(expr: str) -> tuple[int, int]:
    """Interpreta ``JANELA_HORAS`` (ex.: ``7-18``) como faixa inclusiva de horas."""
    expr = expr.strip()
    if not _JANELA_HORAS_RE.fullmatch(expr):
        raise ValueError(f"JANELA_HORAS inválida: {expr!r} (esperado ex.: '7-18')")
    h_ini, h_fim = (int(x) for x in expr.split('-', 1))
    if not (0 <= h_ini <= 23 and 0 <= h_fim <= 23 and h_ini <= h_fim):
        raise ValueError(f"JANELA_HORAS fora de 0-23 ou invertida: {expr!r}")
    return h_ini, h_fim


def _parse_dias_semana(expr: str) -> Set[int]:
    """Interpreta ``DIAS_SEMANA`` no formato cron (ex.: ``mon-sat``, ``mon,wed,fri``)."""
    expr = expr.strip().lower()
    if '-' in expr:
        ini, fim = expr.split('-', 1)
        if ini.strip() not in _DOW_CRON or fim.strip() not in _DOW_CRON:
            raise ValueError(f"DIAS_SEMANA inválido: {expr!r}")
        a, b = _DOW_CRON[ini.strip()], _DOW_CRON[fim.strip()]
        if a > b:
            raise ValueError(f"DIAS_SEMANA inválido (faixa invertida): {expr!r}")
        return set(range(a, b + 1))
    dias: Set[int] = set()
    for parte in expr.split(','):
        p = parte.strip()
        if p not in _DOW_CRON:
            raise ValueError(f"DIAS_SEMANA inválido: {expr!r}")
        dias.add(_DOW_CRON[p])
    return dias


def esta_na_janela_comercial(
    *,
    janela_horas: str,
    agora: Optional[datetime] = None,
    dias_semana: Optional[str] = None,
) -> bool:
    """Retorna ``True`` se ``agora`` é dia útil (seg–sex, sem feriado) e na faixa de horas.

    Args:
        janela_horas: Faixa inclusiva de horas (ex.: ``7-18``).
        agora: Momento a avaliar (default: agora).
        dias_semana: Ignorado (mantido por compatibilidade de assinatura).
    """
    del dias_semana
    agora = agora or datetime.now()
    if not eh_dia_util(agora.date()):
        return False
    h_ini, h_fim = _parse_janela_horas(janela_horas)
    return h_ini <= agora.hour <= h_fim


def pode_executar_carga(*, ignorar_janela_horaria: bool = False, agora: Optional[datetime] = None) -> bool:
    """Verifica se a carga pode rodar (dia útil + janela de horas opcional)."""
    agora = agora or datetime.now()
    if not eh_dia_util(agora.date()):
        return False
    if ignorar_janela_horaria:
        return True
    settings = get_settings()
    h_ini, h_fim = _parse_janela_horas(settings.janela_horas)
    return h_ini <= agora.hour <= h_fim


def job_execucao(*, ignorar_janela: bool = False) -> None:
    """Executa uma rodada da extração, lendo view e modo das variáveis de ambiente.

    Protegido pelo lock global ``_execucao_lock``, que garante exclusão mútua entre
    QUALQUER gatilho — a carga de startup e todos os horários agendados. O
    ``max_instances=1`` do APScheduler só impede um job de se sobrepor a si mesmo (por
    ``id``); ele não cobre dois horários distintos se sobrepondo (ex.: o de 09:00 travar
    até 10:12) nem a carga de startup, que roda fora do scheduler. Se já há uma carga em
    andamento, esta é descartada — no modo snapshot a próxima rodada repõe o dado, então
    pular é seguro e preferível a arriscar um ``insere-depois-poda`` concorrente.

    Args:
        ignorar_janela: Se ``True``, ignora apenas ``JANELA_HORAS`` (usado no startup).
            Finais de semana e feriados nacionais **sempre** bloqueiam a execução.
    """
    if not pode_executar_carga(ignorar_janela_horaria=ignorar_janela):
        agora = datetime.now()
        if agora.weekday() >= 5:
            logger.debug("Fim de semana; execução ignorada.")
        elif eh_feriado_nacional(agora.date()):
            logger.debug("Feriado nacional (%s); execução ignorada.", agora.date().isoformat())
        else:
            logger.debug("Fora da janela de horas; execução agendada ignorada.")
        return

    if not _execucao_lock.acquire(blocking=False):
        logger.warning("Execução já em andamento; esta foi descartada para evitar sobreposição.")
        return

    try:
        logger.info("="*60)
        logger.info("INICIANDO EXECUÇÃO AGENDADA")
        logger.info("="*60)

        settings = get_settings()
        view_name = settings.sap_view_name or 'SUA_VIEW_SAP'
        execution_mode = settings.execution_mode

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
    """Cria e configura o scheduler com o gatilho intervalar da janela comercial.

    Lê ``INTERVALO_MINUTOS``/``JANELA_HORAS``/``DIAS_SEMANA`` do ambiente
    (o ``load_dotenv()`` do módulo já rodou) com defaults seguros.

    Os ``job_defaults`` deixam o agendamento robusto para operação 24/7:
        - ``coalesce``: execuções perdidas (servidor desligado) viram uma única execução;
        - ``max_instances=1``: impede o job de empilhar instâncias (a exclusão
          mútua com a carga de startup fica a cargo do lock global em
          ``job_execucao``);
        - ``misfire_grace_time``: tolera atraso de até 1h (ex.: boot da segunda-feira).

    Returns:
        Um ``BackgroundScheduler`` configurado (ainda não iniciado).
    """
    settings = get_settings()
    intervalo = settings.intervalo_minutos
    janela_horas = settings.janela_horas
    dias_semana = settings.dias_semana

    # Valida a janela na inicialização (falha cedo se .env estiver malformado)
    _parse_janela_horas(janela_horas)
    _parse_dias_semana(dias_semana)

    scheduler = BackgroundScheduler(
        job_defaults={
            'coalesce': True,
            'max_instances': 1,
            'misfire_grace_time': 3600,
        }
    )

    scheduler.add_job(
        job_execucao,
        trigger=IntervalTrigger(minutes=intervalo),
        id='extracao_intervalar',
        name=f'Extração a cada {intervalo} min ({janela_horas}h, seg-sex, sem feriados)',
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
    job_execucao(ignorar_janela=True)

    logger.info("Scheduler ativo (ver agenda acima). Pressione Ctrl+C para interromper.")

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
