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

from wbcpython.tracking.modelos import (
    Acompanhamento,
    Base,
    Evento,
    Execucao,
    StatusExecucao,
    StatusIntegracao,
    TipoEvento,
    Trava,
)

logger = logging.getLogger(__name__)

TRAVA_WORKER = "worker_integracao"


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
    ) -> None:
        with self._sessao() as s, s.begin():
            if s.get(Acompanhamento, orcnum) is None:
                s.add(Acompanhamento(orcnum=orcnum))
                s.flush()
            s.add(
                Evento(
                    orcnum=orcnum,
                    tipo=tipo,
                    regra=regra,
                    mensagem=mensagem,
                    detalhes=json.dumps(detalhes, ensure_ascii=False, default=str)
                    if detalhes
                    else "",
                )
            )

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

    def ultimas_execucoes(self, *, limite: int = 20) -> list[Execucao]:
        consulta = select(Execucao).order_by(Execucao.inicio.desc()).limit(limite)
        with self._sessao() as s:
            return list(s.scalars(consulta))

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
