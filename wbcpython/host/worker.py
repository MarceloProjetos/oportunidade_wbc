"""Worker de integração — o processo que roda periodicamente.

Substitui o `Main` do console legado, com quatro diferenças que importam:

1. **Execução única garantida.** Uma trava no banco impede duas execuções
   sobrepostas. O legado não tinha nada disso: se uma execução demorasse mais
   que o intervalo do agendador, duas rodavam juntas sobre os mesmos orçamentos.
2. **Falha isolada por registro.** Um orçamento problemático vira erro
   registrado e o laço segue. No legado, um registro sem número encerrava o
   processo e deixava todo o resto sem processar, em silêncio.
3. **Agendamento dentro do processo** (APScheduler), em vez de depender do
   Agendador de Tarefas do Windows.
4. **Histórico de execuções**, para se saber se o processo rodou e o que fez.
"""

from __future__ import annotations

import logging
import signal
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import FrameType
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

from wbcpython.application.processar import ACOES_DE_ESCRITA, ProcessadorDeOrcamento
from wbcpython.config import Settings
from wbcpython.domain import janela as jn
from wbcpython.domain.janela import EstadoDaJanela
from wbcpython.host.parada import PedidoDeParada
from wbcpython.infrastructure.hana.oportunidades import RepositorioOportunidadesHana
from wbcpython.infrastructure.service_layer.client import ServiceLayerClient
from wbcpython.infrastructure.service_layer.documentos import (
    RepositorioDocumentosVendaServiceLayer,
)
from wbcpython.infrastructure.service_layer.grupo_produtos import (
    RepositorioGrupoProdutosServiceLayer,
)
from wbcpython.infrastructure.service_layer.oportunidades import (
    RepositorioOportunidadesServiceLayer,
)
from wbcpython.infrastructure.service_layer.orcdetalhe import (
    RepositorioOrcDetalheServiceLayer,
)
from wbcpython.infrastructure.service_layer.parceiros import (
    RepositorioParceirosServiceLayer,
)
from wbcpython.infrastructure.wbc_sql.repository import RepositorioOrcamentosWbcSql
from wbcpython.tracking import (
    RepositorioTracking,
    StatusExecucao,
    TravaNaoObtida,
)

logger = logging.getLogger(__name__)

#: Janela padrão, usada quando ninguém informa outra — o valor real vem da
#: configuração (`WBC_MESES_DE_JANELA`). Mantido aqui porque `janela_padrao` é
#: função de módulo, chamada também por quem não tem `Settings` em mãos.
#:
#: O legado montava esta data misturando -6 meses para o ano e -9 para o mês, o
#: que dava um corte imprevisível. Aqui é um número só, explícito e ajustável.
MESES_DE_JANELA = 6

#: Quantos ciclos estendidos seguidos podem falhar antes de o pedido ser
#: devolvido ao padrão. Erro de rede não pode custar o pedido de quem está
#: esperando; erro que se repete não pode virar ciclo pesado a cada intervalo.
TENTATIVAS_ANTES_DE_DEVOLVER = 3


@dataclass(frozen=True, slots=True)
class ResultadoExecucao:
    """O que o ciclo olhou e o que ele de fato fez — são coisas diferentes.

    `processados` conta tudo que foi **avaliado**: o ciclo lê a janela inteira
    de propósito, porque avaliar é barato. Chamar isso de "processado com
    sucesso" dava um resumo que anunciava centenas de orçamentos tratados
    quando o ciclo não havia tocado em nenhum — e essa é justamente a linha que
    alguém lê para saber se precisa agir.
    """

    processados: int = 0
    sucessos: int = 0
    erros: int = 0
    com_acao: int = 0
    escritas: int = 0

    @property
    def resumo(self) -> str:
        if self.erros:
            final = f"{self.erros} com erro."
        else:
            final = "nenhum erro."
        return (
            f"{self.processados} orçamento(s) avaliado(s); "
            f"{self.com_acao} com ação ({self.escritas} com escrita no SAP); "
            f"{final}"
        )


def _agora() -> datetime:
    """Relógio local ingênuo, como o resto do projeto.

    O agendador roda em `America/Sao_Paulo` e o log grava horário local; o
    horário de trabalho é o do escritório, então comparar em UTC daria três
    horas de diferença — o worker pararia às 16:00 e voltaria às 03:30.
    """
    return datetime.now()


def janela_padrao(*, meses: int = MESES_DE_JANELA, hoje: date | None = None) -> date:
    """Primeiro dia do mês, `meses` meses atrás."""
    referencia = hoje or datetime.now().date()
    ano, mes = referencia.year, referencia.month - meses
    while mes <= 0:
        mes += 12
        ano -= 1
    return date(ano, mes, 1)


class WorkerIntegracao:
    """Executa um ciclo de integração e, opcionalmente, agenda repetições."""

    def __init__(
        self,
        settings: Settings,
        *,
        tracking: RepositorioTracking | None = None,
        meses_de_janela: int | None = None,
        limite_de_escrita_por_ciclo: int | None = None,
    ) -> None:
        self._settings = settings
        self._tracking = tracking or RepositorioTracking.a_partir_da_url(
            settings.tracking.db_url.get_secret_value()
        )
        # `None` deixa a configuração decidir, como no teto de escrita: é ela
        # que o operador ajusta sem tocar em código.
        self._meses = meses_de_janela if meses_de_janela is not None else settings.meses_de_janela
        #: Janela e teto pedidos **no construtor** vencem o pedido guardado no
        #: acompanhamento, e desligam a máquina de estados deste worker.
        #:
        #: Quem constrói um worker com números explícitos está dizendo "rode
        #: exatamente assim" — um teste, ou um comando que já sabe o que quer.
        #: Deixar um pedido pendente sequestrar esse worker faria a chamada
        #: mentir, e faria o pedido ser consumido por um ciclo que ninguém
        #: associou a ele.
        self._janela_fixa = meses_de_janela is not None
        # `None` deixa a configuração decidir — é ela que o operador ajusta sem
        # tocar em código quando quiser acelerar ou frear a recuperação.
        #: Por que o worker está parado — ver `_ciclo_agendado`. Guarda o
        #: motivo, e não um booleano, para o log não repetir a mesma linha a
        #: noite inteira **e** ainda assim anunciar a virada de "fora do
        #: horário" para "fim de semana".
        self._parado_por = ""
        self._limite_de_escrita = (
            limite_de_escrita_por_ciclo
            if limite_de_escrita_por_ciclo is not None
            else settings.limite_de_escrita_por_ciclo
        )
        self._parar = threading.Event()
        #: O canal de parada que não depende de console nem de sinal — ver
        #: `host/parada.py`. Consultado entre orçamentos e entre ciclos.
        self._pedido_de_parada = PedidoDeParada(settings.worker_arquivo_de_parada)
        #: Dia em que a faxina do acompanhamento já rodou — uma vez por dia basta.
        self._faxina_em: date | None = None

    # ------------------------------------------------------------------ ciclo

    def executar_ciclo(
        self,
        *,
        orcamento: str | None = None,
        apenas_sitcode: int | None = None,
        somente_leitura: bool = False,
    ) -> ResultadoExecucao:
        """Executa um ciclo completo, sob a trava de execução única.

        `orcamento` permite reprocessar um caso específico — o que no legado só
        era possível editando a consulta no código-fonte.

        `apenas_sitcode` restringe o ciclo a um SitCode do WBC. Serve para
        separar uma leva do resto: rodar só os cancelados (99) confina o efeito
        de uma execução a um tipo de mudança, o que importa quando a leva é
        grande e a ação é destrutiva — cancelar cotação não se desfaz.

        `somente_leitura` é o ensaio: lê tudo, decide tudo, grava o
        acompanhamento e **não escreve nada no SAP**. Serve para o painel
        mostrar a janela real antes de qualquer documento ser tocado. Continua
        sob a trava de execução única de propósito — o ensaio não escreve no
        SAP, mas escreve no acompanhamento, e dois processos mexendo nas mesmas
        linhas é justamente o que a trava existe para impedir.
        """
        try:
            with self._tracking.trava_de_execucao():
                return self._ciclo(
                    orcamento=orcamento,
                    apenas_sitcode=apenas_sitcode,
                    somente_leitura=somente_leitura,
                )
        except TravaNaoObtida as exc:
            logger.warning("Ciclo ignorado: %s", exc)
            return ResultadoExecucao()

    def _janela_do_ciclo(self) -> jn.Janela:
        """Resolve a janela **deste** ciclo — e é por isso que ela não é lida no arranque.

        Antes da janela sob demanda, `self._meses` era decidido uma vez, na
        construção do worker, e o processo carregava esse número até reiniciar.
        Agora o pedido pode ser armado pela tela a qualquer momento, e um valor
        congelado significaria "o painel aceitou, mas só vale depois do próximo
        restart" — exatamente o que este trabalho existe para eliminar.

        Só `ARMADO` estende a janela. Em `AGUARDANDO` o ciclo volta ao padrão de
        propósito: a pergunta está de pé, e continuar varrendo a janela larga a
        cada intervalo seria o ciclo pesado rodando sozinho, sem ninguém ter
        respondido.
        """
        config = self._settings
        padrao = self._meses
        base = self._limite_de_escrita
        if self._janela_fixa:
            return jn.Janela(padrao, base)

        pedido = self._tracking.janela_pedida()
        if pedido.estado is not EstadoDaJanela.ARMADO:
            return jn.Janela(padrao, base)

        return jn.Janela(
            pedido.meses,
            jn.teto_de_escrita(
                pedido.meses,
                padrao=padrao,
                base=base,
                absoluto=config.teto_absoluto_de_escrita,
            ),
            estendida=True,
        )

    def _devolver_janela(
        self,
        janela: jn.Janela,
        *,
        faltaram: int,
        erro: str = "",
        onde_parou: str = "",
    ) -> None:
        """Fecha o pedido segundo o que o ciclo estendido conseguiu fazer.

        As três saídas do plano, nesta ordem de precedência:

        * **erro** — não consome o pedido; conta a tentativa. No limite, devolve.
        * **bateu no teto** — guarda o ponto de parada e pergunta se quer outro
          ciclo. Os automáticos voltam ao padrão enquanto a pergunta espera.
        * **cumpriu** — volta ao padrão sozinho, que é o comportamento que o
          pedido promete a quem o armou.
        """
        if not janela.estendida:
            return
        config = self._settings
        if erro:
            self._tracking.registrar_erro_da_janela(
                detalhe=erro, limite=TENTATIVAS_ANTES_DE_DEVOLVER
            )
            return
        if faltaram > 0:
            self._tracking.janela_aguardando_resposta(
                faltaram=faltaram,
                detalhe=onde_parou,
                espera=timedelta(minutes=config.janela_espera_minutos),
            )
            logger.warning(
                "Janela de %d meses: o teto de %d escrita(s) foi atingido e %d "
                "oportunidade(s) ficaram de fora. Os ciclos automáticos voltam a %d "
                "meses; o painel pergunta se deve rodar outro ciclo estendido "
                "(a pergunta vence em %d min). %s",
                janela.meses,
                janela.teto,
                faltaram,
                self._meses,
                config.janela_espera_minutos,
                onde_parou,
            )
            return
        self._tracking.limpar_janela(
            motivo=f"Cumprida: a janela de {janela.meses} meses coube num ciclo."
        )
        logger.info(
            "Janela de %d meses cumprida num ciclo — de volta ao padrão de %d meses.",
            janela.meses,
            self._meses,
        )

    def _ler_pendentes(self, *, orcamento: str | None, meses: int) -> list[dict[str, Any]]:
        """Lê a janela no HANA — e falha o ciclo se o HANA não responder.

        Não há queda para o Service Layer de propósito. Um fallback silencioso
        traria de volta o corte em 20 registros por página, com a integração
        parecendo saudável enquanto deixasse de processar a maior parte da
        janela. Falhar em voz alta é o comportamento seguro: o dashboard mostra
        o erro e alguém age.

        Com `--orcamento`, a janela é a **dirigida** (12 meses por padrão), e
        não a do ciclo. A janela limita o que é varrido sem ninguém pedir;
        pedir um orçamento pelo número é o oposto — alguém sabe qual quer.
        Antes, um orçamento fora da janela do ciclo apenas não era encontrado,
        e o comando terminava com "0 avaliado(s)" e código 0.
        """
        repositorio = RepositorioOportunidadesHana(
            self._settings.hana,
            # O schema é o da company que recebe a escrita — ver a nota do
            # módulo. Ler de uma company e escrever noutra seria desastroso.
            company_db=self._settings.service_layer.company_db,
        )
        meses = self._settings.meses_de_janela_dirigida if orcamento else meses
        try:
            encontrados = repositorio.pendentes_de_integracao(
                desde=janela_padrao(meses=meses), orcamento=orcamento
            )
        finally:
            repositorio.close()

        if orcamento and not encontrados:
            # O silêncio aqui era o problema: nada encontrado terminava como
            # sucesso, e quem rodou o comando concluía que não havia o que fazer.
            logger.warning(
                "Orçamento %s não encontrado na janela de %d meses (desde %s). "
                "Ou ele não existe como oportunidade no SAP, ou é mais antigo "
                "que a janela — aumente MESES_DE_JANELA_DIRIGIDA para alcançá-lo.",
                orcamento,
                meses,
                janela_padrao(meses=meses).isoformat(),
            )
        return encontrados

    def _ciclo(
        self,
        *,
        orcamento: str | None,
        apenas_sitcode: int | None = None,
        somente_leitura: bool = False,
    ) -> ResultadoExecucao:
        execucao_id = self._tracking.iniciar_execucao()
        processados = sucessos = erros = escritas = com_acao = 0
        # A janela é resolvida **por ciclo**, e uma vez só: relê-la no meio da
        # passada faria o teto mudar debaixo do laço se alguém armasse um pedido
        # enquanto o ciclo roda.
        janela = self._janela_do_ciclo()
        faltaram = 0
        onde_parou = ""

        try:
            with ServiceLayerClient(
                self._settings.service_layer,
                production_company_db=self._settings.production_company_db,
                block_production_writes=self._settings.block_production_writes,
            ) as cliente:
                oportunidades_repo = RepositorioOportunidadesServiceLayer(cliente)
                wbc = RepositorioOrcamentosWbcSql.a_partir_de(self._settings.wbc_sql)
                processador = ProcessadorDeOrcamento(
                    wbc=wbc,
                    orcdetalhe=RepositorioOrcDetalheServiceLayer(cliente),
                    documentos=RepositorioDocumentosVendaServiceLayer(cliente),
                    oportunidades=oportunidades_repo,
                    tracking=self._tracking,
                    grupo_produtos=RepositorioGrupoProdutosServiceLayer(cliente),
                    parceiros=RepositorioParceirosServiceLayer(cliente),
                    fator_de_embarque=self._settings.fator_de_peso_de_embarque,
                    somente_leitura=somente_leitura,
                )

                # A leitura vem do HANA, numa consulta só, com cotação e
                # pedido já resolvidos. Ver
                # `infrastructure.hana.oportunidades`: pelo Service Layer isto
                # custava ~90 requisições paginadas mais quatro por orçamento.
                pendentes = self._ler_pendentes(orcamento=orcamento, meses=janela.meses)

                # As situações do WBC também vêm em lote: uma consulta para a
                # janela inteira, no lugar de 1.785 de 29 ms cada. Com elas, a
                # decisão sai sem carregar as linhas do orçamento — e só quem
                # tem ação paga os 279 ms da carga completa.
                situacoes = wbc.situacoes_atuais([p["U_ORCNUM_WBC"] for p in pendentes])

                # O filtro por SitCode entra **aqui**, e não na consulta do
                # HANA: o SitCode é do WBC, e o SAP guarda apenas um espelho
                # dele (`U_INO_StatusWBC`), que fica para trás justamente
                # quando a oportunidade acabou de mudar — que é o caso que
                # interessa. Filtrar pelo espelho deixaria de fora os
                # orçamentos recém-cancelados.
                if apenas_sitcode is not None:
                    antes = len(pendentes)
                    pendentes = [
                        p
                        for p in pendentes
                        if (situacoes.get(p["U_ORCNUM_WBC"]) or (None,))[0] == apenas_sitcode
                    ]
                    logger.info(
                        "Filtro SitCode %d: %d de %d oportunidade(s) nesta execução.",
                        apenas_sitcode,
                        len(pendentes),
                        antes,
                    )

                logger.info(
                    "%d oportunidade(s) a avaliar (janela: OpenDate >= %s, %s).",
                    len(pendentes),
                    janela_padrao(meses=janela.meses).isoformat(),
                    janela,
                )

                if somente_leitura:
                    logger.warning(
                        "SIMULAÇÃO: este ciclo decide e registra tudo, e não escreve "
                        "nada no SAP. O teto de escrita não se aplica — a janela "
                        "inteira será avaliada."
                    )

                for oportunidade in pendentes:
                    if self.parada_solicitada():
                        logger.info("Parada solicitada — ciclo interrompido.")
                        break
                    if escritas >= janela.teto:
                        # O teto é de escrita, não de leitura: a avaliação da
                        # janela inteira é barata, criar documentos no SAP não é.
                        # Parar aqui deixa o resto para o próximo ciclo.
                        faltaram = len(pendentes) - processados
                        # O orçamento da vez é o ponto de retomada, e é o que
                        # sobra na tela quando a pergunta expira sem resposta.
                        onde_parou = (
                            f"Parou no orçamento {oportunidade['U_ORCNUM_WBC']}; "
                            f"{faltaram} oportunidade(s) não avaliadas."
                        )
                        logger.warning(
                            "Teto de %d escrita(s) por ciclo atingido — %d "
                            "oportunidade(s) ficaram para o próximo ciclo.",
                            janela.teto,
                            faltaram,
                        )
                        break

                    resultado = processador.processar(
                        oportunidade, situacoes.get(oportunidade["U_ORCNUM_WBC"])
                    )
                    processados += 1
                    if resultado.sucesso:
                        sucessos += 1
                    else:
                        erros += 1
                    if resultado.acoes_executadas or (
                        somente_leitura and resultado.decisao.tem_acao
                    ):
                        # No ensaio ninguém executa, então contar só o que foi
                        # executado diria "0 com ação" para uma janela cheia de
                        # ação — que é justamente o número que o ensaio existe
                        # para mostrar.
                        com_acao += 1
                    if any(a in ACOES_DE_ESCRITA for a in resultado.acoes_executadas):
                        escritas += 1

        except Exception as exc:
            logger.exception("Falha no ciclo de integração.")
            self._tracking.finalizar_execucao(
                execucao_id,
                processados=processados,
                sucessos=sucessos,
                erros=erros + 1,
                status=StatusExecucao.FALHOU,
                detalhe=f"{type(exc).__name__}: {exc}",
                meses_da_janela=janela.meses,
                teto_de_escrita=janela.teto,
            )
            if orcamento is None and not somente_leitura:
                self._devolver_janela(
                    janela,
                    faltaram=faltaram,
                    erro=f"{type(exc).__name__}: {exc}",
                )
            return ResultadoExecucao(processados, sucessos, erros + 1, com_acao, escritas)

        resultado = ResultadoExecucao(processados, sucessos, erros, com_acao, escritas)
        self._tracking.finalizar_execucao(
            execucao_id,
            processados=processados,
            sucessos=sucessos,
            erros=erros,
            detalhe=resultado.resumo,
            meses_da_janela=janela.meses,
            teto_de_escrita=janela.teto,
        )
        # Duas exclusões deliberadas. `--orcamento` roda na janela dirigida, que
        # é outra coisa: consumir o pedido ali gastaria por um orçamento o que
        # foi pedido para a janela inteira. E o ensaio **usa** a janela estendida
        # de propósito — é com ele que se vê o tamanho do estrago antes de
        # autorizá-lo —, mas não pode consumir o pedido que ainda vai valer.
        if orcamento is None and not somente_leitura:
            self._devolver_janela(janela, faltaram=faltaram, onde_parou=onde_parou)
        logger.info(resultado.resumo)
        return resultado

    # -------------------------------------------------------------- execução

    def _ciclo_agendado(self) -> ResultadoExecucao:
        """O ciclo do agendador, sujeito ao horário de trabalho.

        A guarda vive **aqui**, e não em `executar_ciclo`, porque
        `wbcpython ciclo` é alguém pedindo: recusar um pedido explícito por
        causa do relógio seria obstrução, não proteção.

        O aviso sai uma vez por transição, e não a cada volta. Com ciclo de 3
        minutos, avisar sempre encheria a noite com ~230 linhas iguais — e log
        que se repete assim deixa de ser lido justamente quando importa.
        """
        agora = _agora()
        if self.parada_solicitada():
            # Um ciclo que começasse agora só terminaria depois do `nssm stop`
            # desistir: o pedido chegou entre dois ciclos, e o certo é não abrir
            # outro.
            logger.info("Parada solicitada — ciclo não iniciado.")
            return ResultadoExecucao()
        motivo = self._motivo_para_nao_rodar(agora)

        if motivo is None:
            if self._parado_por:
                self._parado_por = ""
                logger.info(
                    "Dentro do expediente (%s, %s–%s): ciclos retomados.",
                    self._settings.dias_de_trabalho_por_extenso,
                    self._settings.worker_horario_inicio.strftime("%H:%M"),
                    self._settings.worker_horario_fim.strftime("%H:%M"),
                )
            resultado = self.executar_ciclo()
            self._faxina_diaria(agora.date())
            return resultado

        if self._parado_por != motivo:
            # O aviso sai uma vez por **motivo**, e não por volta. Guardar o
            # motivo, e não um booleano, é o que faz a sexta 19:01 dizer "fora
            # do horário" e o sábado 08:00 dizer "fim de semana", em vez de o
            # segundo silenciar por já estar parado desde o primeiro.
            self._parado_por = motivo
            logger.info("%s O worker segue vivo e não executa ciclo.", motivo)
        return ResultadoExecucao()

    def _faxina_diaria(self, hoje: date) -> None:
        """Apaga as decisões mais velhas que a retenção, uma vez por dia, depois do ciclo.

        Depois, e não antes: o ciclo é o que importa, e a faxina nunca pode
        atrasá-lo. Falha aqui vira aviso no log, não erro do ciclo — o banco
        crescer um dia a mais não é motivo para parar a integração.
        """
        dias = self._settings.eventos_retencao_dias
        if dias <= 0 or self._faxina_em == hoje:
            return
        self._faxina_em = hoje
        try:
            apagados = self._tracking.faxina_de_eventos(dias=dias)
        except Exception as exc:  # noqa: BLE001 — avisa, não derruba o worker
            logger.warning("Faxina do acompanhamento falhou: %s", exc)
            return
        logger.info(
            "Faxina do acompanhamento: %d evento(s) de decisão com mais de %d dia(s) apagado(s).",
            apagados,
            dias,
        )

    def _motivo_para_nao_rodar(self, agora: datetime) -> str | None:
        """`None` quando pode rodar; a frase do log quando não pode.

        O dia é verificado antes da hora porque é a resposta mais útil: num
        sábado às 10h, "fora do horário" estaria tecnicamente errado e mandaria
        quem lê procurar no lugar errado.
        """
        config = self._settings
        if not config.e_dia_de_trabalho(agora.date()):
            return (
                f"{config.nome_do_dia(agora.date()).capitalize()} não é dia de "
                f"trabalho (dias: {config.dias_de_trabalho_por_extenso})."
            )
        if not config.dentro_do_horario_do_worker(agora.time()):
            return (
                f"Fora do horário de trabalho "
                f"({config.worker_horario_inicio.strftime('%H:%M')}–"
                f"{config.worker_horario_fim.strftime('%H:%M')})."
            )
        return None

    def rodar_continuamente(self) -> None:
        """Agenda o ciclo e mantém o processo vivo até receber sinal de parada."""
        intervalo = self._settings.worker_interval_seconds
        agendador = BackgroundScheduler(timezone="America/Sao_Paulo")
        agendador.add_job(
            self._ciclo_agendado,
            "interval",
            seconds=intervalo,
            id="ciclo_integracao",
            # Evita empilhar execuções atrasadas se um ciclo demorar mais que o
            # intervalo — a trava já barraria, mas nem chegar a tentar é melhor.
            max_instances=1,
            coalesce=True,
        )

        self._instalar_sinais()
        # Um pedido de parada esquecido (do deploy que acabou de nos religar, ou
        # de alguém que parou o worker à mão) não pode derrubar o processo novo.
        self._pedido_de_parada.limpar(motivo="partida do worker")
        # A janela estendida não sobrevive a um restart, por desenho: o padrão
        # é sempre 6 e todo caminho termina nele. Um pedido esquecido de antes
        # do deploy não pode virar um ciclo largo na primeira volta do worker
        # novo, sem ninguém ter pedido de novo.
        #
        # Só quando há o que devolver. Limpar sempre gravava "Devolvida ao
        # padrão na partida do worker" mesmo numa base em que ninguém nunca
        # pediu nada — e o card mostra esse texto como "Último pedido:", que é
        # exatamente a impressão errada para quem abre a tela pela primeira vez.
        # Visto na .11 em 11/09/2026, na primeira subida com o card no ar.
        if self._tracking.janela_pedida().estado is not EstadoDaJanela.OCIOSO:
            self._tracking.limpar_janela(motivo="Devolvida ao padrão na partida do worker.")
        logger.info(
            "Worker iniciado (intervalo de %ds; expediente %s, %s–%s). %s",
            intervalo,
            self._settings.dias_de_trabalho_por_extenso,
            self._settings.worker_horario_inicio.strftime("%H:%M"),
            self._settings.worker_horario_fim.strftime("%H:%M"),
            self._settings.describe_environment(),
        )

        agendador.start()
        try:
            # O primeiro ciclo é imediato, mas passa pela mesma guarda: subir o
            # worker 22h não pode ser a porta dos fundos para rodar fora do
            # horário.
            self._ciclo_agendado()
            # `wait()` sem timeout não é interrompido por sinal no Windows: o
            # tratador de Ctrl+C só rodaria depois do wait — nunca. Acordar a
            # cada segundo dá vez ao tratador **e** ao arquivo de parada.
            while not self._parar.wait(1.0):
                self.parada_solicitada()
        finally:
            # `wait=True`: o ciclo em andamento termina (ele mesmo já viu o
            # pedido e interrompe entre orçamentos), a trava é liberada e a
            # execução é fechada antes de o processo sair.
            agendador.shutdown(wait=True)
            logger.info("Worker encerrado.")

    def solicitar_parada(self) -> None:
        self._parar.set()

    def parada_solicitada(self) -> bool:
        """Sinal recebido **ou** arquivo de parada presente.

        O arquivo promove a si mesmo a sinal: assim o laço principal acorda,
        `rodar_continuamente` chega ao `shutdown` e o processo termina — o que o
        `deploy_update.bat` está esperando do outro lado.
        """
        if not self._parar.is_set() and self._pedido_de_parada.pendente():
            self._parar.set()
        return self._parar.is_set()

    def _instalar_sinais(self) -> None:
        def encerrar(_sinal: int, _quadro: FrameType | None) -> None:
            logger.info("Sinal de encerramento recebido — finalizando o ciclo atual.")
            self.solicitar_parada()

        for sinal in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sinal, encerrar)
            except ValueError:
                # Fora da thread principal (ex.: em teste) não dá para instalar.
                pass


def intervalo_legivel(segundos: int) -> str:
    return str(timedelta(seconds=segundos))
