"""Execução em segundo plano com acompanhamento (22/09/2026).

Decisão do Anderson para a camada web: operação longa roda em segundo plano e o usuário
acompanha. O motivo é concreto — `pedidos-wbc processar-novos` leva ~7s num orçamento
médio (seção 7.14) e `manutencao-op encerrar --pedido` de 68 OPs faz cerca de 270
chamadas à Service Layer, na casa de minutos. Nada disso cabe num request HTTP com o
usuário esperando a página responder.

**Escopo deliberado: em processo, em memória, sem fila externa.**

A aplicação é de uso interno, com operações disparadas manualmente por uma pessoa de cada
vez — é o modelo do addon legado, e a decisão de manter "botões manuais" foi tomada em
14/09. Um Celery/RQ traria broker, worker e deploy próprios para um problema que não temos
ainda. O custo dessa escolha está escrito e é real: **reiniciou a aplicação, o histórico
das tarefas some**. Quando isso incomodar, o caminho é persistir em banco (o
`TRACKING_DB_URL` do `.env` existe para isso) — não trocar de arquitetura.

⚠️ O que **não** fica só aqui: as tarefas escrevem no SAP. Este módulo não decide o que
pode ser executado; ele executa o que lhe entregam. As travas (produção, confirmação de
operação irreversível) ficam na camada que cria a tarefa.
"""
from __future__ import annotations

import asyncio
import logging
import traceback
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Quantas tarefas terminadas ficam no histórico. Sem limite, um processo de longa duração
# acumularia resultados até o fim dos tempos.
MAX_TAREFAS = 200

# Linhas de log guardadas por tarefa. O `encerrar --pedido` de um pedido grande gera uma
# linha por OP por etapa; o suficiente para acompanhar, não para virar arquivo de log.
MAX_LINHAS_LOG = 500


@dataclass
class Tarefa:
    """Uma execução em andamento ou terminada."""

    id: str
    nome: str
    descricao: str
    criada_em: datetime
    situacao: str = "na fila"  # na fila | executando | concluída | erro | cancelada
    iniciada_em: datetime | None = None
    terminada_em: datetime | None = None
    passo: str = ""
    passos_feitos: int = 0
    passos_total: int = 0
    linhas: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LINHAS_LOG))
    resultado: Any = None
    erro: str | None = None

    @property
    def terminada(self) -> bool:
        return self.situacao in {"concluída", "erro", "cancelada"}

    @property
    def com_falhas(self) -> bool:
        """A tarefa terminou sem estourar, mas o resultado traz erros.

        Existe porque "concluída" só quer dizer que a corrotina não levantou exceção — e
        uma tela verde escrita CONCLUÍDA sobre um resultado em que os quatro pedidos
        falharam é pior que nenhum indicador: o usuário lê o verde, não o JSON.
        Convenção do projeto: toda função de serviço devolve os fracassos numa lista
        `com_erro` (mesmo formato em `muda_status`, `finalizar_ops`, `processar_*`).
        """
        return bool(isinstance(self.resultado, dict) and self.resultado.get("com_erro"))

    @property
    def duracao_segundos(self) -> float | None:
        if not self.iniciada_em:
            return None
        fim = self.terminada_em or datetime.now()
        return round((fim - self.iniciada_em).total_seconds(), 1)

    def anota(self, linha: str) -> None:
        """Acrescenta uma linha ao acompanhamento. Visível ao usuário enquanto roda."""
        self.linhas.append(f"{datetime.now():%H:%M:%S}  {linha}")

    def avanca(self, passo: str, feitos: int | None = None, total: int | None = None) -> None:
        """Atualiza o passo atual e, opcionalmente, o progresso."""
        self.passo = passo
        if feitos is not None:
            self.passos_feitos = feitos
        if total is not None:
            self.passos_total = total
        self.anota(passo)

    def para_json(self) -> dict:
        return {
            "id": self.id,
            "nome": self.nome,
            "descricao": self.descricao,
            "situacao": self.situacao,
            "terminada": self.terminada,
            "com_falhas": self.com_falhas,
            "passo": self.passo,
            "passos_feitos": self.passos_feitos,
            "passos_total": self.passos_total,
            "percentual": (
                round(100 * self.passos_feitos / self.passos_total)
                if self.passos_total else None
            ),
            "criada_em": self.criada_em.isoformat(timespec="seconds"),
            "duracao_segundos": self.duracao_segundos,
            "linhas": list(self.linhas),
            "resultado": self.resultado,
            "erro": self.erro,
        }


class RegistroDeTarefas:
    """Guarda as tarefas e roda uma de cada vez por módulo.

    **Uma por módulo, não uma por vez no geral**: duas execuções do mesmo módulo em
    paralelo disputariam os mesmos pedidos e OPs — é assim que se cria OP duplicada. Já
    módulos diferentes são independentes, e serializar tudo faria o usuário esperar sem
    motivo.
    """

    def __init__(self) -> None:
        self._tarefas: dict[str, Tarefa] = {}
        self._ordem: deque[str] = deque(maxlen=MAX_TAREFAS)
        self._em_execucao: dict[str, str] = {}  # módulo -> id da tarefa
        self._jobs: dict[str, asyncio.Task] = {}

    def em_execucao(self, modulo: str) -> Tarefa | None:
        """Tarefa ainda rodando naquele módulo, se houver."""
        tarefa_id = self._em_execucao.get(modulo)
        tarefa = self._tarefas.get(tarefa_id) if tarefa_id else None
        if tarefa and tarefa.terminada:
            self._em_execucao.pop(modulo, None)
            return None
        return tarefa

    def criar(
        self,
        modulo: str,
        nome: str,
        descricao: str,
        corrotina: Callable[[Tarefa], Awaitable[Any]],
    ) -> Tarefa:
        """Registra e dispara a tarefa. Levanta `RuntimeError` se o módulo já tem uma."""
        ocupada = self.em_execucao(modulo)
        if ocupada:
            raise RuntimeError(
                f"O módulo '{modulo}' já tem uma execução em andamento "
                f"({ocupada.nome}, iniciada às {ocupada.criada_em:%H:%M:%S}). "
                "Duas execuções simultâneas no mesmo módulo disputariam os mesmos "
                "pedidos e OPs."
            )

        tarefa = Tarefa(
            id=uuid.uuid4().hex[:12], nome=nome, descricao=descricao, criada_em=datetime.now()
        )
        self._guardar(tarefa)
        self._em_execucao[modulo] = tarefa.id
        self._jobs[tarefa.id] = asyncio.create_task(self._executar(tarefa, modulo, corrotina))
        return tarefa

    async def _executar(
        self, tarefa: Tarefa, modulo: str, corrotina: Callable[[Tarefa], Awaitable[Any]]
    ) -> None:
        tarefa.situacao = "executando"
        tarefa.iniciada_em = datetime.now()
        try:
            tarefa.resultado = await corrotina(tarefa)
            tarefa.situacao = "concluída"
        except asyncio.CancelledError:
            tarefa.situacao = "cancelada"
            tarefa.anota("Execução cancelada.")
            raise
        except Exception as exc:  # noqa: BLE001 - a falha é o resultado da tarefa
            tarefa.situacao = "erro"
            tarefa.erro = str(exc)
            tarefa.anota(f"ERRO: {exc}")
            # O traceback vai para o log do servidor, não para a tela: a mensagem o usuário
            # já tem, e o rastro completo é para quem for investigar.
            logger.error("Tarefa %s (%s) falhou:\n%s", tarefa.id, tarefa.nome, traceback.format_exc())
        finally:
            tarefa.terminada_em = datetime.now()
            self._em_execucao.pop(modulo, None)
            self._jobs.pop(tarefa.id, None)

    def obter(self, tarefa_id: str) -> Tarefa | None:
        return self._tarefas.get(tarefa_id)

    def listar(self, limite: int = 20) -> list[Tarefa]:
        """Mais recentes primeiro."""
        ids = list(self._ordem)[-limite:]
        return [self._tarefas[i] for i in reversed(ids) if i in self._tarefas]

    async def cancelar(self, tarefa_id: str) -> bool:
        """Pede o cancelamento. Devolve se havia o que cancelar.

        ⚠️ Cancelar **não desfaz** o que já foi gravado no SAP. Interrompe entre passos;
        documentos já criados continuam lá. Quem chama precisa deixar isso claro na tela.
        """
        job = self._jobs.get(tarefa_id)
        if not job or job.done():
            return False
        job.cancel()
        return True

    def _guardar(self, tarefa: Tarefa) -> None:
        if len(self._ordem) == MAX_TAREFAS:
            mais_antiga = self._ordem[0]
            self._tarefas.pop(mais_antiga, None)
        self._tarefas[tarefa.id] = tarefa
        self._ordem.append(tarefa.id)


class _PonteDeLog(logging.Handler):
    """Espelha o log de um módulo para o acompanhamento de uma tarefa."""

    def __init__(self, tarefa: Tarefa) -> None:
        super().__init__()
        self._tarefa = tarefa

    def emit(self, registro: logging.LogRecord) -> None:
        try:
            self._tarefa.anota(registro.getMessage())
        except Exception:  # noqa: BLE001 - log nunca derruba a execução
            pass


@contextmanager
def acompanha_log(tarefa: Tarefa, *modulos: str, nivel: int = logging.INFO):
    """Faz o `logger.info` dos módulos indicados aparecer no acompanhamento da tarefa.

    **Por que uma ponte e não chamadas de progresso novas.** Os serviços já narram o que
    estão fazendo — "grupo 2/5", "criando OP do item X", "Gravando U_INO_OP em 6 linhas" —
    com `logger.info`, e a CLI liga isso no console com `--perfil`. Duplicar essas
    mensagens como `tarefa.anota(...)` criaria duas narrativas do mesmo processo que
    divergem assim que alguém mexer numa delas. Aqui a web passa a ouvir a que já existe.

    O nível fica no handler, não no logger: mexer no nível do logger do módulo mudaria o
    comportamento do processo inteiro, inclusive de outra tarefa rodando em paralelo em
    outro módulo.
    """
    ponte = _PonteDeLog(tarefa)
    ponte.setLevel(nivel)
    alvos = [logging.getLogger(nome) for nome in modulos]
    anteriores = [(alvo, alvo.level) for alvo in alvos]
    try:
        for alvo in alvos:
            alvo.addHandler(ponte)
            # Se o logger estiver acima do nível desejado, o registro nem chega ao
            # handler. Só abaixamos quando necessário, e devolvemos no fim.
            if alvo.level == logging.NOTSET or alvo.level > nivel:
                alvo.setLevel(nivel)
        yield
    finally:
        for alvo, nivel_anterior in anteriores:
            alvo.removeHandler(ponte)
            alvo.setLevel(nivel_anterior)


# Registro único do processo. A camada web importa este objeto.
TAREFAS = RegistroDeTarefas()
