"""Acesso ao banco de acompanhamento.

Diferente dos repositórios do WBC e do HANA, **este banco é de escrita** — é o
único lugar onde a integração grava estado próprio. As travas de segurança do
projeto não se aplicam aqui: elas protegem o SAP de produção e o WBC, e este
banco não é nenhum dos dois.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, create_engine, delete, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from wbcpython.domain import janela as jn
from wbcpython.domain.janela import EstadoDaJanela
from wbcpython.tracking.modelos import (
    Acompanhamento,
    Base,
    Evento,
    Execucao,
    PedidoDeJanela,
    StatusExecucao,
    StatusIntegracao,
    TipoEvento,
    Trava,
)

logger = logging.getLogger(__name__)

TRAVA_WORKER = "worker_integracao"

#: A única linha de `pedido_de_janela` — ver a docstring do modelo.
LINHA_UNICA = 1


def _tornar_ocioso(pedido: PedidoDeJanela, agora: datetime) -> None:
    """Volta o pedido ao padrão, preservando `detalhe` e `meses`.

    `meses` fica para trás de propósito: é o que a tela mostra em "a última
    janela estendida foi de N meses". Ele não tem efeito nenhum em `OCIOSO` —
    quem decide a janela nesse estado é o `MESES_DE_JANELA` da configuração.
    """
    pedido.estado = EstadoDaJanela.OCIOSO
    pedido.faltaram = 0
    pedido.tentativas = 0
    pedido.expira_em = None
    pedido.atualizado_em = agora


def _minutos(pedido: PedidoDeJanela) -> str:
    """O prazo que venceu, em texto, para a linha de log."""
    if pedido.expira_em is None or pedido.atualizado_em is None:
        return "prazo"
    minutos = round((pedido.expira_em - pedido.atualizado_em).total_seconds() / 60)
    return f"{minutos} min"


class TravaNaoObtida(RuntimeError):
    """Outra execução do worker já está em andamento."""


def identidade_do_processo() -> str:
    """Identifica quem detém a trava, para o diagnóstico ser útil.

    Sem isso, uma trava presa mostra apenas "ocupada"; com host e PID dá para
    descobrir qual processo travou e se ele ainda existe.
    """
    return f"{socket.gethostname()}:{os.getpid()}"


class RepositorioTracking:
    """Repositório do banco de acompanhamento."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._sessao = sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def a_partir_da_url(cls, url: str, *, criar_tabelas: bool = True) -> RepositorioTracking:
        engine = create_engine(url, pool_pre_ping=True)
        repo = cls(engine)
        if criar_tabelas:
            repo.criar_tabelas()
        return repo

    def criar_tabelas(self) -> None:
        Base.metadata.create_all(self._engine)
        self._acrescentar_colunas_novas()

    def _acrescentar_colunas_novas(self) -> None:
        """Adiciona colunas que o modelo ganhou depois da base já existir.

        `create_all` cria tabelas que faltam, mas **não** altera as que já
        existem: numa base de acompanhamento em uso, uma coluna nova no modelo
        vira `OperationalError: no such column` na primeira consulta. Como o
        acompanhamento é histórico operacional (quem decidiu o quê, e quando),
        recriar a base para acompanhar o modelo não é opção.

        Só faz `ADD COLUMN` de colunas **anuláveis e sem default de servidor** —
        a única alteração que SQLite e PostgreSQL aceitam sem reescrever a
        tabela, e a única que não pode perder dado. Qualquer mudança mais
        profunda (renomear, apagar, mudar tipo) fica de fora de propósito: é aí
        que uma migração automática silenciosa destrói histórico.
        """
        inspetor = inspect(self._engine)
        for tabela in Base.metadata.sorted_tables:
            if not inspetor.has_table(tabela.name):
                continue
            existentes = {c["name"] for c in inspetor.get_columns(tabela.name)}
            for coluna in tabela.columns:
                if coluna.name in existentes:
                    continue
                if not coluna.nullable:
                    # Falha em voz alta em vez de deixar a consulta estourar
                    # depois com "no such column", que não diz o que fazer.
                    logger.error(
                        "Acompanhamento: a coluna %s.%s é obrigatória e não existe na base. "
                        "Acrescente-a manualmente (ALTER TABLE) antes de rodar o ciclo.",
                        tabela.name,
                        coluna.name,
                    )
                    continue
                tipo = coluna.type.compile(self._engine.dialect)
                with self._engine.begin() as conexao:
                    conexao.execute(
                        text(f'ALTER TABLE "{tabela.name}" ADD COLUMN "{coluna.name}" {tipo}')
                    )
                logger.info(
                    "Acompanhamento: coluna %s.%s criada na base existente.",
                    tabela.name,
                    coluna.name,
                )

    @contextmanager
    def sessao(self) -> Iterator[Session]:
        with self._sessao() as s:
            yield s

    # --------------------------------------------------------- acompanhamento

    def registrar_verificacao(
        self,
        orcnum: str,
        *,
        oppr_id: int | None = None,
        cliente: str = "",
        vendedor: str = "",
        municipio: str = "",
        uf: str = "",
        sitcode_wbc: int = 0,
        revisao_wbc: str = "",
        sitcode_sap: str = "",
        status: StatusIntegracao | None = None,
        regra: str = "",
        data_abertura: date | None = None,
    ) -> Acompanhamento:
        """Cria ou atualiza a linha de acompanhamento do orçamento.

        Só sobrescreve campos que foram informados: uma verificação que não
        conhece o nome do cliente não pode apagar o que já estava lá.
        """
        with self._sessao() as s, s.begin():
            registro = s.get(Acompanhamento, orcnum)
            if registro is None:
                registro = Acompanhamento(orcnum=orcnum)
                s.add(registro)

            if oppr_id is not None:
                registro.oppr_id = oppr_id
            if cliente:
                registro.cliente = cliente
            if vendedor:
                registro.vendedor = vendedor
            if municipio:
                registro.municipio = municipio
            if uf:
                registro.uf = uf
            if data_abertura is not None:
                registro.data_abertura = data_abertura
            if sitcode_wbc:
                registro.sitcode_wbc = sitcode_wbc
            if revisao_wbc:
                registro.revisao_wbc = revisao_wbc
            if sitcode_sap:
                registro.sitcode_sap = sitcode_sap
            if status is not None:
                registro.status = status
                if status is not StatusIntegracao.ERRO:
                    registro.ultimo_erro = ""
            if regra:
                registro.regra_aplicada = regra

            registro.ultima_verificacao = datetime.now()
            s.flush()
            return registro

    def registrar_documento(
        self,
        orcnum: str,
        *,
        tipo: str,
        doc_entry: int | None,
        doc_num: int | None = None,
        valor: Decimal | None = None,
    ) -> None:
        """Guarda o documento do SAP gerado para o orçamento.

        `tipo` é "cotacao" ou "pedido".
        """
        if tipo not in ("cotacao", "pedido"):
            raise ValueError(f"tipo de documento desconhecido: {tipo!r}")

        with self._sessao() as s, s.begin():
            registro = s.get(Acompanhamento, orcnum)
            if registro is None:
                registro = Acompanhamento(orcnum=orcnum)
                s.add(registro)
            setattr(registro, f"{tipo}_docentry", doc_entry)
            if doc_num is not None:
                setattr(registro, f"{tipo}_docnum", doc_num)
            if valor is not None:
                setattr(registro, f"{tipo}_valor", valor)

    def registrar_erro(self, orcnum: str, mensagem: str, *, detalhes: str = "") -> None:
        with self._sessao() as s, s.begin():
            registro = s.get(Acompanhamento, orcnum)
            if registro is None:
                registro = Acompanhamento(orcnum=orcnum)
                s.add(registro)
            registro.status = StatusIntegracao.ERRO
            registro.ultimo_erro = mensagem
            registro.ultima_verificacao = datetime.now()
            s.add(
                Evento(
                    orcnum=orcnum,
                    tipo=TipoEvento.ERRO,
                    mensagem=mensagem,
                    detalhes=detalhes,
                )
            )

    def registrar_evento(
        self,
        orcnum: str,
        *,
        tipo: TipoEvento = TipoEvento.DECISAO,
        regra: str = "",
        mensagem: str = "",
        detalhes: dict[str, Any] | None = None,
    ) -> bool:
        """Grava o evento. `False` quando uma DECISÃO repete a última e por isso não foi gravada.

        Uma decisão igual à última do orçamento ("revisão congelada, nada a fazer")
        não é um acontecimento novo: é o ciclo passando de novo. Gravá-la a cada
        ciclo custou 934.634 linhas em 6 dias de produção — 99,4 % da tabela,
        das quais só 12.559 eram mudanças de verdade (medido em 08/09/2026, com
        1.682 oportunidades e ciclo de 3 min). O que fica é a **primeira** vez em
        que a decisão passou a valer; `acompanhamento.ultima_verificacao` diz que
        o ciclo continua olhando. Ação, erro e reprocessamento são sempre
        gravados: cada um é um acontecimento.
        """
        detalhes_json = (
            json.dumps(detalhes, ensure_ascii=False, default=str) if detalhes else ""
        )
        with self._sessao() as s, s.begin():
            if s.get(Acompanhamento, orcnum) is None:
                s.add(Acompanhamento(orcnum=orcnum))
                s.flush()
            if tipo == TipoEvento.DECISAO:
                ultimo = s.scalars(
                    select(Evento)
                    .where(Evento.orcnum == orcnum)
                    .order_by(Evento.id.desc())
                    .limit(1)
                ).first()
                if (
                    ultimo is not None
                    and ultimo.tipo == TipoEvento.DECISAO
                    and ultimo.regra == regra
                    and ultimo.mensagem == mensagem
                    and ultimo.detalhes == detalhes_json
                ):
                    return False
            s.add(
                Evento(
                    orcnum=orcnum,
                    tipo=tipo,
                    regra=regra,
                    mensagem=mensagem,
                    detalhes=detalhes_json,
                )
            )
            return True

    def faxina_de_eventos(self, *, dias: int) -> int:
        """Apaga eventos de DECISÃO com mais de `dias` dias. Devolve quantos apagou.

        Ação, erro e reprocessamento **ficam**: são o histórico que responde "por
        que este orçamento virou (ou não virou) pedido?". A decisão repetida é o
        que enchia o banco — com `registrar_evento` deixando de repeti-la, a
        faxina cuida do que já foi gravado e do que ainda muda de verdade. Com
        SQLite, compacta o arquivo quando apagou algo (o `DELETE` sozinho não
        devolve espaço ao disco).
        """
        if dias < 0:
            raise ValueError("dias não pode ser negativo")
        corte = datetime.now() - timedelta(days=dias)
        with self._sessao() as s, s.begin():
            resultado = s.execute(
                delete(Evento).where(Evento.tipo == TipoEvento.DECISAO, Evento.momento < corte)
            )
            apagados = int(resultado.rowcount or 0)
        if apagados and self._engine.dialect.name == "sqlite":
            with self._engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
                c.execute(text("VACUUM"))
        return apagados

    # ---------------------------------------------------------------- leitura

    def obter(self, orcnum: str) -> Acompanhamento | None:
        with self._sessao() as s:
            return s.get(Acompanhamento, orcnum)

    def preencher_datas_de_abertura(self, datas: dict[str, Any]) -> int:
        """Preenche `data_abertura` onde ela está nula. Devolve quantas mudaram.

        Só preenche o que está vazio: a data vinda da company nunca sobrescreve
        uma já gravada pelo ciclo. Ver `_cmd_datas_de_abertura` para o porquê de
        isto existir.
        """
        preenchidas = 0
        with self._sessao() as s, s.begin():
            for orcnum, valor in datas.items():
                data = valor.date() if isinstance(valor, datetime) else valor
                if not isinstance(data, date):
                    continue
                registro = s.get(Acompanhamento, orcnum)
                if registro is None or registro.data_abertura is not None:
                    continue
                registro.data_abertura = data
                preenchidas += 1
        return preenchidas

    def listar(
        self,
        *,
        status: StatusIntegracao | None = None,
        busca: str = "",
        limite: int = 200,
        aberto_desde: date | None = None,
    ) -> list[Acompanhamento]:
        """`aberto_desde` aplica a **mesma** janela do worker (`OOPR.OpenDate`).

        Linhas sem `data_abertura` são as anteriores à coluna existir. Elas
        passam pelo filtro de propósito: sumir do painel por falta de um dado
        que nunca foi gravado seria pior do que aparecer — o operador leria
        como "esses orçamentos não existem", e não como "não sei a data".
        """
        consulta = select(Acompanhamento)
        if status is not None:
            consulta = consulta.where(Acompanhamento.status == status)
        if aberto_desde is not None:
            consulta = consulta.where(
                (Acompanhamento.data_abertura >= aberto_desde)
                | (Acompanhamento.data_abertura.is_(None))
            )
        if busca:
            padrao = f"%{busca}%"
            consulta = consulta.where(
                Acompanhamento.orcnum.like(padrao) | Acompanhamento.cliente.like(padrao)
            )
        consulta = consulta.order_by(Acompanhamento.atualizado_em.desc()).limit(limite)
        with self._sessao() as s:
            return list(s.scalars(consulta))

    def eventos(self, orcnum: str, *, limite: int = 100) -> list[Evento]:
        consulta = (
            select(Evento)
            .where(Evento.orcnum == orcnum)
            .order_by(Evento.momento.desc(), Evento.id.desc())
            .limit(limite)
        )
        with self._sessao() as s:
            return list(s.scalars(consulta))

    def contagem_por_status(self) -> dict[StatusIntegracao, int]:
        consulta = select(Acompanhamento.status, func.count()).group_by(Acompanhamento.status)
        with self._sessao() as s:
            return {StatusIntegracao(status): total for status, total in s.execute(consulta)}

    # -------------------------------------------------------------- execuções

    def iniciar_execucao(self) -> int:
        with self._sessao() as s, s.begin():
            execucao = Execucao()
            s.add(execucao)
            s.flush()
            return execucao.id

    def finalizar_execucao(
        self,
        execucao_id: int,
        *,
        processados: int,
        sucessos: int,
        erros: int,
        status: StatusExecucao = StatusExecucao.CONCLUIDA,
        detalhe: str = "",
        meses_da_janela: int | None = None,
        teto_de_escrita: int | None = None,
    ) -> None:
        with self._sessao() as s, s.begin():
            execucao = s.get(Execucao, execucao_id)
            if execucao is None:
                return
            execucao.fim = datetime.now()
            execucao.processados = processados
            execucao.sucessos = sucessos
            execucao.erros = erros
            execucao.status = status
            execucao.detalhe = detalhe
            # `None` é "não informado" e preserva o que já estava lá: quem
            # chamar sem estes campos (um teste antigo, uma rota que só corrige
            # o resumo) não apaga a auditoria da janela.
            if meses_da_janela is not None:
                execucao.meses_da_janela = meses_da_janela
            if teto_de_escrita is not None:
                execucao.teto_de_escrita = teto_de_escrita

    def ultimas_execucoes(self, *, limite: int = 20) -> list[Execucao]:
        consulta = select(Execucao).order_by(Execucao.inicio.desc()).limit(limite)
        with self._sessao() as s:
            return list(s.scalars(consulta))

    # ------------------------------------------------- janela sob demanda

    def janela_pedida(self, *, agora: datetime | None = None) -> PedidoDeJanela:
        """O pedido de janela corrente, já com a expiração aplicada.

        A expiração é preguiçosa — acontece aqui, na leitura — porque entre um
        ciclo e outro nada roda para vigiar o relógio. É o mesmo desenho da
        trava vencida em `_adquirir_trava`: quem chega depois do prazo é quem
        limpa. Painel e worker chamam este método, então qualquer um dos dois
        serve de gatilho, e a tela nunca mostra uma pergunta que já venceu.
        """
        agora = agora or datetime.now()
        with self._sessao() as s, s.begin():
            pedido = s.get(PedidoDeJanela, LINHA_UNICA)
            if pedido is None:
                pedido = PedidoDeJanela(id=LINHA_UNICA, estado=EstadoDaJanela.OCIOSO)
                s.add(pedido)
                s.flush()
                return pedido
            if jn.expirou(pedido.estado, pedido.expira_em, agora=agora):
                # A frase de onde o ciclo parou é preservada e recebe o aviso —
                # expirar calado apagaria justamente o que alguém precisaria ler
                # para decidir se rearma.
                logger.warning(
                    "Janela de %d meses devolvida ao padrão: ninguém respondeu em %s. %s",
                    pedido.meses,
                    _minutos(pedido),
                    pedido.detalhe or "(sem registro do ponto de parada)",
                )
                pedido.detalhe = (
                    f"Expirou sem resposta. {pedido.detalhe}".strip()
                    if pedido.detalhe
                    else "Expirou sem resposta."
                )
                _tornar_ocioso(pedido, agora)
            return pedido

    def armar_janela(self, meses: int, *, por: str, agora: datetime | None = None) -> PedidoDeJanela:
        """Arma a janela estendida para a **próxima** passada do ciclo.

        Zera tentativas e faltaram: um pedido novo não herda o placar do
        anterior, senão o terceiro erro de ontem devolveria o pedido de hoje no
        primeiro tropeço.
        """
        agora = agora or datetime.now()
        with self._sessao() as s, s.begin():
            pedido = s.get(PedidoDeJanela, LINHA_UNICA) or PedidoDeJanela(id=LINHA_UNICA)
            pedido.estado = EstadoDaJanela.ARMADO
            pedido.meses = meses
            pedido.pedido_por = por
            pedido.pedido_em = agora
            pedido.faltaram = 0
            pedido.tentativas = 0
            pedido.expira_em = None
            pedido.detalhe = ""
            pedido.atualizado_em = agora
            s.add(pedido)
            s.flush()
            return pedido

    def limpar_janela(self, *, motivo: str, agora: datetime | None = None) -> PedidoDeJanela:
        """Devolve a janela ao padrão, guardando por quê.

        Chamado de quatro lugares que são a mesma coisa vista de ângulos
        diferentes: o ciclo cumpriu, o usuário desistiu, o worker reiniciou, ou
        as tentativas acabaram. Todos terminam em `OCIOSO` — é isso que garante
        que a janela larga nunca fica ligada por esquecimento.
        """
        agora = agora or datetime.now()
        with self._sessao() as s, s.begin():
            pedido = s.get(PedidoDeJanela, LINHA_UNICA) or PedidoDeJanela(id=LINHA_UNICA)
            pedido.detalhe = motivo
            _tornar_ocioso(pedido, agora)
            s.add(pedido)
            s.flush()
            return pedido

    def janela_aguardando_resposta(
        self,
        *,
        faltaram: int,
        detalhe: str,
        espera: timedelta,
        agora: datetime | None = None,
    ) -> PedidoDeJanela:
        """O ciclo estendido bateu no teto: guarda o ponto de parada e pergunta.

        Enquanto está aqui, os ciclos automáticos voltam ao padrão — quem lê
        `janela_do_ciclo` no worker só enxerga janela estendida em `ARMADO`. Sem
        isso, o worker seguiria varrendo a janela larga a cada intervalo
        enquanto a pergunta espera resposta.
        """
        agora = agora or datetime.now()
        with self._sessao() as s, s.begin():
            pedido = s.get(PedidoDeJanela, LINHA_UNICA) or PedidoDeJanela(id=LINHA_UNICA)
            pedido.estado = EstadoDaJanela.AGUARDANDO
            pedido.faltaram = faltaram
            pedido.detalhe = detalhe
            pedido.tentativas = 0
            pedido.expira_em = agora + espera
            pedido.atualizado_em = agora
            s.add(pedido)
            s.flush()
            return pedido

    def registrar_erro_da_janela(
        self,
        *,
        detalhe: str,
        limite: int,
        agora: datetime | None = None,
    ) -> PedidoDeJanela:
        """Um ciclo estendido falhou: conta a tentativa e decide se devolve.

        O pedido **não** é consumido por um erro — uma queda de rede no meio do
        ciclo não pode custar o pedido de quem está esperando. Mas um erro que
        se repete não pode virar um ciclo pesado a cada intervalo do worker: no
        `limite`, a janela volta ao padrão com o erro registrado.
        """
        agora = agora or datetime.now()
        with self._sessao() as s, s.begin():
            pedido = s.get(PedidoDeJanela, LINHA_UNICA) or PedidoDeJanela(id=LINHA_UNICA)
            pedido.tentativas += 1
            pedido.detalhe = detalhe
            pedido.atualizado_em = agora
            if pedido.tentativas >= limite:
                logger.warning(
                    "Janela de %d meses devolvida ao padrão após %d tentativa(s) com erro: %s",
                    pedido.meses,
                    pedido.tentativas,
                    detalhe,
                )
                tentativas = pedido.tentativas
                _tornar_ocioso(pedido, agora)
                pedido.detalhe = f"Devolvida após {tentativas} tentativa(s) com erro. {detalhe}"
            s.add(pedido)
            s.flush()
            return pedido

    # ----------------------------------------------------------------- trava

    @contextmanager
    def trava_de_execucao(
        self, *, validade: timedelta = timedelta(minutes=30), nome: str = TRAVA_WORKER
    ) -> Iterator[None]:
        """Garante execução única do worker.

        A trava tem validade: se um processo morrer sem liberá-la, ela expira
        sozinha e a próxima execução assume. Sem isso, um worker que caísse
        deixaria a integração parada para sempre.
        """
        dono = identidade_do_processo()
        self._adquirir_trava(nome, dono, validade)
        try:
            yield
        finally:
            with self._sessao() as s, s.begin():
                s.execute(delete(Trava).where(Trava.nome == nome, Trava.dono == dono))

    def _adquirir_trava(self, nome: str, dono: str, validade: timedelta) -> None:
        """Adquire a trava deixando a **chave primária arbitrar**, não o SELECT.

        Um "consulta se existe, senão insere" tem uma corrida clássica: dois
        processos consultam ao mesmo tempo, ambos veem vazio e ambos inserem.
        Um deles recebe um `IntegrityError` — que, se não for tratado, sobe como
        erro genérico em vez de "já tem execução rodando", e pode até derrubar o
        worker vencedor por efeito colateral.

        Aqui o `INSERT` é a única coisa que decide: quem conseguir gravar a
        chave primária levou a trava. O `IntegrityError` deixa de ser um acidente
        e passa a ser a resposta esperada de quem perdeu.
        """
        agora = datetime.now()

        if self._tentar_inserir_trava(nome, dono, agora, validade):
            return

        # Perdeu a corrida ou já havia trava. Se a existente estiver vencida,
        # remove-a de forma condicional e tenta uma única vez mais.
        with self._sessao() as s:
            existente = s.get(Trava, nome)
            if existente is not None and existente.expira_em > agora:
                raise TravaNaoObtida(
                    f"Já existe uma execução em andamento (detida por {existente.dono}, "
                    f"expira em {existente.expira_em:%d/%m/%Y %H:%M:%S})."
                )
            dono_anterior = existente.dono if existente else "(desconhecido)"

        with self._sessao() as s, s.begin():
            # A condição no DELETE garante que só some a trava realmente vencida:
            # sem ela, dois processos poderiam apagar a trava um do outro.
            s.execute(delete(Trava).where(Trava.nome == nome, Trava.expira_em <= agora))

        logger.warning(
            "Trava expirada de %s assumida por %s — o processo anterior "
            "provavelmente caiu sem liberá-la.",
            dono_anterior,
            dono,
        )

        if not self._tentar_inserir_trava(nome, dono, datetime.now(), validade):
            raise TravaNaoObtida(
                "Não foi possível obter a trava de execução: outro processo a assumiu "
                "no mesmo instante."
            )

    def _tentar_inserir_trava(
        self, nome: str, dono: str, agora: datetime, validade: timedelta
    ) -> bool:
        """Tenta gravar a trava. `False` significa que alguém chegou antes."""
        try:
            with self._sessao() as s, s.begin():
                s.add(
                    Trava(
                        nome=nome,
                        dono=dono,
                        adquirida_em=agora,
                        expira_em=agora + validade,
                    )
                )
            return True
        except IntegrityError:
            return False
