"""Os comandos da linha de comando, disponíveis na aba "Executar" do painel.

## Por que subprocesso e não chamada direta

O painel executa a **CLI de verdade** (`python -m wbcpython ...`), em
subprocesso. Chamar as funções internas seria mais rápido e daria resultado já
estruturado, e ainda assim é a escolha errada aqui:

1. **Não diverge.** O que o painel faz é literalmente o comando. Não existe um
   "caminho do painel" que possa passar a se comportar diferente do caminho do
   terminal na primeira regra nova — que é exatamente o defeito que a prévia e o
   ciclo teriam se duplicassem a decisão (ver `DECISOES.md`).
2. **A saída é a mesma.** O que aparece na tela é o texto que apareceria no
   terminal, na mesma ordem. É a extensão natural de "uma fonte, duas telas".
3. **Isolamento.** Um ciclo que estoure não derruba o painel, e dá para
   interromper — coisa que uma chamada em processo não permite.

O custo é que o painel precisa rodar **na máquina do worker**, com os drivers
instalados e acesso ao SAP e ao WBC. É onde ele já roda.

## Concorrência

Uma execução por vez, garantida aqui (`Executor`). Isso é conveniência de tela:
a garantia que importa é a **trava de execução única** no banco de
acompanhamento, que o `executar_ciclo` toma e que vale entre processos — o
painel e o worker disputam a mesma trava, e quem perde não roda. Sem ela, um
clique no painel enquanto o worker trabalha poderia criar o mesmo documento
duas vezes.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import tempfile
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


def _agora() -> datetime:
    """Horário local ingênuo, como o resto do projeto.

    O banco de acompanhamento e o log em arquivo gravam assim de propósito (ver
    `pyproject.toml`); um carimbo em UTC no meio deles seria uma diferença de
    três horas que ninguém percebe até comparar com um documento no SAP.
    """
    return datetime.now()  # noqa: DTZ005


#: Quantas execuções passadas o painel guarda para exibir.
HISTORICO = 20

#: Teto de linhas lidas da saída de uma execução. O `doctor` cabe em 40; um
#: ciclo sobre a janela inteira passa de mil.
TETO_DE_SAIDA = 2000


@dataclass(frozen=True, slots=True)
class Campo:
    """Um argumento que o comando aceita, como aparece no formulário."""

    nome: str
    rotulo: str
    ajuda: str = ""
    tipo: str = "text"
    marcado: bool = False
    #: Nome longo da opção na CLI. Vazio significa "é um valor posicional
    #: passado como `--<nome>`", que é o caso de todos hoje.
    opcao: str = ""

    @property
    def bandeira(self) -> str:
        return self.opcao or f"--{self.nome.replace('_', '-')}"


@dataclass(frozen=True, slots=True)
class Comando:
    """Um comando exposto na tela.

    `escreve` marca os que tocam o SAP: são os que exigem senha e ficam
    indisponíveis quando o painel está apontado para produção. `escreve_tracking`
    marca quem só mexe no banco de acompanhamento — pede senha, mas não tem o
    mesmo peso: nada disso chega ao SAP.
    """

    id: str
    rotulo: str
    resumo: str
    argv: tuple[str, ...]
    escreve: bool = False
    escreve_tracking: bool = False
    campos: tuple[Campo, ...] = ()
    demora: str = ""
    aviso: str = ""
    #: Exceção deliberada à senha, e a única: o ensaio.
    #:
    #: A senha protege o SAP. Exigi-la de um comando que não o toca custaria
    #: exatamente o que ele existe para dar: comando protegido fica
    #: **indisponível** quando o painel aponta para produção, e produção antes
    #: do primeiro ciclo é justamente onde o ensaio serve para alguma coisa.
    #:
    #: O que ele escreve é o acompanhamento, que a própria leitura do ciclo
    #: reescreve na passada seguinte. Não vale para nada que chegue ao SAP —
    #: por isso a bandeira é explícita, e não derivada.
    dispensa_senha: bool = False

    @property
    def protegido(self) -> bool:
        return (self.escreve or self.escreve_tracking) and not self.dispensa_senha


CATALOGO: tuple[Comando, ...] = (
    Comando(
        id="pendentes",
        rotulo="Verificar pendentes",
        resumo=(
            "Mostra o que o ciclo faria na próxima passada, sem executar nada. "
            "Não cria, não altera e não cancela."
        ),
        argv=("pendentes",),
        demora="alguns segundos",
        campos=(
            Campo(
                nome="orcamento",
                rotulo="Orçamento (opcional)",
                ajuda="Avalia só este. Em branco, a janela inteira.",
            ),
            Campo(
                nome="com-acao",
                rotulo="Listar só quem resultaria em escrita",
                tipo="checkbox",
            ),
            # Ligado por padrão: sem isto a verificação só imprime na tela e a
            # aba "Próximo ciclo" continua mostrando o retrato antigo — que é o
            # erro que a aba existe para evitar.
            Campo(
                nome="exportar",
                rotulo="Atualizar a aba “Próximo ciclo”",
                ajuda="Grava o retrato em previsao.json, que a aba lê.",
                tipo="checkbox",
                marcado=True,
            ),
        ),
    ),
    Comando(
        id="env",
        rotulo="Ambiente",
        resumo="Para qual company DB e schema a aplicação está apontada.",
        argv=("env",),
        demora="imediato",
    ),
    Comando(
        id="doctor",
        rotulo="Diagnóstico da instalação",
        resumo="Configuração, dependências opcionais e prontidão de cada fase.",
        argv=("doctor",),
        demora="imediato",
    ),
    Comando(
        id="check-sap",
        rotulo="Testar conexão com o SAP",
        resumo="Abre uma sessão no Service Layer. Só leitura.",
        argv=("check-sap",),
        demora="alguns segundos",
    ),
    Comando(
        id="check-hana",
        rotulo="Testar o HANA",
        resumo="Conecta e diz em qual schema as views existem. Só leitura.",
        argv=("check-hana",),
        demora="alguns segundos",
    ),
    Comando(
        id="ciclo",
        rotulo="Ciclo de integração",
        resumo=(
            "Executa um ciclo e sai. Cria e atualiza cotações e pedidos no SAP, "
            "espelha status e encerra oportunidades."
        ),
        argv=("ciclo",),
        escreve=True,
        demora="de segundos a minutos, conforme a janela",
        aviso=(
            "Cancelamento de cotação e criação de pedido não se desfazem. "
            "Rode “Verificar pendentes” antes e confira o que ele faria."
        ),
        campos=(
            Campo(
                nome="orcamento",
                rotulo="Orçamento (opcional)",
                ajuda="Processa só este. Em branco, a janela inteira.",
            ),
            Campo(
                nome="cancelados",
                rotulo="Só os cancelados no WBC (SitCode 99)",
                ajuda="Confina o efeito a um tipo de mudança.",
                tipo="checkbox",
            ),
        ),
    ),
    Comando(
        id="ciclo-simulado",
        rotulo="Simular um ciclo",
        resumo=(
            "Percorre a janela inteira, decide tudo e preenche o painel — sem criar, "
            "alterar ou cancelar nada no SAP. É como o painel ganha números antes "
            "do primeiro ciclo de verdade."
        ),
        # `--simular` é parte do comando, não um campo do formulário: campo é
        # coisa que se desmarca. Um botão que escreve no SAP quando alguém
        # desmarca uma caixa é o contrário do que este comando existe para ser.
        argv=("ciclo", "--simular"),
        escreve_tracking=True,
        dispensa_senha=True,
        demora="de segundos a minutos, conforme a janela",
        campos=(
            Campo(
                nome="orcamento",
                rotulo="Orçamento (opcional)",
                ajuda="Simula só este. Em branco, a janela inteira.",
            ),
        ),
    ),
    Comando(
        id="pesos",
        rotulo="Recalcular pesos de um pedido",
        resumo=(
            "Recalcula o Weight1 das linhas a partir da árvore de produtos do WBC. "
            "A prévia é obrigatória antes de aplicar."
        ),
        argv=("pesos",),
        escreve=True,
        demora="alguns segundos",
        campos=(
            Campo(
                nome="pedido", rotulo="Pedido (DocNum)", ajuda="Informe o pedido ou o orçamento."
            ),
            Campo(nome="orcamento", rotulo="Orçamento", ajuda="Alternativa ao número do pedido."),
        ),
    ),
    Comando(
        id="datas-de-abertura",
        rotulo="Preencher datas de abertura",
        resumo=(
            "Completa a data de abertura nas linhas antigas do acompanhamento, "
            "para o painel poder aplicar a janela. Não toca no SAP."
        ),
        argv=("datas-de-abertura",),
        escreve_tracking=True,
        demora="alguns segundos",
    ),
)

POR_ID = {comando.id: comando for comando in CATALOGO}


class JaEmExecucao(RuntimeError):
    """Já existe um comando rodando pelo painel."""


class PreviaObrigatoria(RuntimeError):
    """`pesos` só aplica depois de uma prévia do mesmo alvo."""


@dataclass
class Execucao:
    """Uma execução disparada pelo painel."""

    id: int
    comando: str
    rotulo: str
    linha: str
    solicitante: str
    inicio: datetime
    arquivo: Path
    fim: datetime | None = None
    codigo: int | None = None
    interrompida: bool = False
    _processo: subprocess.Popen[bytes] | None = field(default=None, repr=False)

    @property
    def rodando(self) -> bool:
        return self.fim is None

    @property
    def duracao(self) -> float:
        return ((self.fim or _agora()) - self.inicio).total_seconds()

    @property
    def situacao(self) -> str:
        if self.rodando:
            return "rodando"
        if self.interrompida:
            return "interrompida"
        return "ok" if self.codigo == 0 else "falhou"


def montar_argv(comando: Comando, valores: dict[str, str]) -> list[str]:
    """Traduz o formulário em argumentos da CLI.

    Só o que está no catálogo entra: um campo que o `Comando` não declara é
    ignorado, e o valor nunca vai para um shell — o subprocesso recebe a lista
    de argumentos direto. É o que impede que um campo de texto na tela vire
    execução de comando arbitrário na máquina do worker.
    """
    argv = list(comando.argv)
    for campo in comando.campos:
        bruto = (valores.get(campo.nome) or "").strip()
        if campo.tipo == "checkbox":
            if bruto:
                argv.append(campo.bandeira)
            continue
        if bruto:
            argv += [campo.bandeira, bruto]
    return argv


def _ajustar_exportar(comando: Comando, argv: list[str], arquivo_do_retrato: str) -> list[str]:
    """`--exportar` na tela é caixa; na CLI é opção com caminho."""
    if comando.id != "pendentes" or "--exportar" not in argv:
        return argv
    posicao = argv.index("--exportar")
    return argv[: posicao + 1] + [arquivo_do_retrato] + argv[posicao + 1 :]


class Executor:
    """Roda um comando por vez e guarda a saída.

    A serialização aqui é de tela — impedir que dois cliques disparem dois
    ciclos. A garantia entre processos é a trava no banco de acompanhamento.
    """

    def __init__(self, *, arquivo_do_retrato: str = "state/wbc_previsao.json") -> None:
        self._trava = threading.Lock()
        self._atual: Execucao | None = None
        self._historico: deque[Execucao] = deque(maxlen=HISTORICO)
        self._proximo_id = 1
        self._arquivo_do_retrato = arquivo_do_retrato
        #: Alvo da última prévia de peso bem-sucedida. É o que autoriza aplicar.
        self._previa_de_peso: str | None = None

    # ---------------------------------------------------------------- leitura

    def atual(self) -> Execucao | None:
        with self._trava:
            self._colher()
            return self._atual

    def historico(self) -> list[Execucao]:
        with self._trava:
            self._colher()
            return list(reversed(self._historico))

    def saida(self, execucao: Execucao, *, limite: int = TETO_DE_SAIDA) -> list[str]:
        try:
            texto = execucao.arquivo.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        linhas = texto.splitlines()
        return linhas[-limite:]

    def alvo_da_previa_de_peso(self) -> str | None:
        return self._previa_de_peso

    # --------------------------------------------------------------- execução

    def iniciar(
        self,
        comando: Comando,
        valores: dict[str, str],
        *,
        solicitante: str,
        aplicar_pesos: bool = False,
    ) -> Execucao:
        """Dispara o comando. `JaEmExecucao` se outro ainda estiver rodando."""
        argv = montar_argv(comando, valores)
        argv = _ajustar_exportar(comando, argv, self._arquivo_do_retrato)

        if comando.id == "pesos":
            argv = self._pesos(argv, valores, aplicar=aplicar_pesos)

        with self._trava:
            self._colher()
            if self._atual is not None:
                raise JaEmExecucao(
                    f"“{self._atual.rotulo}” ainda está rodando "
                    f"(há {self._atual.duracao:.0f}s, pedido por {self._atual.solicitante})."
                )

            destino = Path(
                tempfile.NamedTemporaryFile(  # noqa: SIM115 - fechado abaixo
                    prefix="wbcpython-painel-", suffix=".txt", delete=False
                ).name
            )
            saida = destino.open("wb")
            # `python -m wbcpython` e não o script `wbcpython`: assim usa
            # exatamente o interpretador do painel, sem depender de PATH.
            # Sem shell e com a lista de argumentos montada só a partir do
            # catálogo: um campo de texto da tela não vira comando na máquina.
            processo = subprocess.Popen(
                [sys.executable, "-m", "wbcpython", *argv],
                stdout=saida,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=self._ambiente(),
            )
            saida.close()

            execucao = Execucao(
                id=self._proximo_id,
                comando=comando.id,
                rotulo=comando.rotulo,
                linha="wbcpython " + " ".join(shlex.quote(a) for a in argv),
                solicitante=solicitante,
                inicio=_agora(),
                arquivo=destino,
                _processo=processo,
            )
            self._proximo_id += 1
            self._atual = execucao
            return execucao

    def interromper(self) -> Execucao | None:
        """Encerra o que está rodando.

        `terminate` e não `kill`: o ciclo trata o sinal e libera a trava de
        execução única ao sair. Um `kill` deixaria a trava presa até expirar, e
        o worker ficaria 30 minutos sem rodar por causa de um clique.
        """
        with self._trava:
            self._colher()
            execucao = self._atual
            if execucao is None or execucao._processo is None:
                return None
            execucao.interrompida = True
            execucao._processo.terminate()
            return execucao

    # ---------------------------------------------------------------- interno

    def _ambiente(self) -> dict[str, str]:
        """O ambiente do subprocesso, sem a senha do painel.

        A senha autoriza o clique; não tem nada a fazer dentro do comando, e
        deixá-la no ambiente de um processo filho é vazamento gratuito — ela
        apareceria para qualquer coisa que o comando venha a executar.
        """
        import os

        ambiente = dict(os.environ)
        ambiente.pop("PAINEL_SENHA", None)
        return ambiente

    def _pesos(self, argv: list[str], valores: dict[str, str], *, aplicar: bool) -> list[str]:
        """Prévia obrigatória: aplicar exige uma simulação do **mesmo** alvo.

        Não é a caixa "simular" desmarcada que libera. O que libera é ter visto
        a prévia daquele pedido: o comando altera peso de documento já criado, e
        a diferença entre o peso líquido da árvore e o de embarque (×1,10) é
        justamente o tipo de coisa que se confere olhando, não confiando.
        """
        alvo = self._alvo_de_peso(valores)
        if not aplicar:
            self._previa_de_peso = None
            return [*argv, "--simular"]
        if self._previa_de_peso != alvo:
            raise PreviaObrigatoria(
                "Rode a prévia deste pedido primeiro: o painel só aplica peso "
                "depois de mostrar o que mudaria."
            )
        return argv

    @staticmethod
    def _alvo_de_peso(valores: dict[str, str]) -> str:
        pedido = (valores.get("pedido") or "").strip()
        orcamento = (valores.get("orcamento") or "").strip()
        return f"pedido:{pedido}|orcamento:{orcamento}"

    def _colher(self) -> None:
        """Fecha a execução corrente se o processo já terminou.

        Chamado de dentro da trava, em toda leitura: não há thread vigiando o
        processo, e é a própria consulta da tela que percebe o fim. Um painel
        que ninguém está olhando não precisa perceber nada.
        """
        execucao = self._atual
        if execucao is None or execucao._processo is None:
            return
        codigo = execucao._processo.poll()
        if codigo is None:
            return
        execucao.codigo = codigo
        execucao.fim = _agora()
        execucao._processo = None
        if execucao.comando == "pesos" and codigo == 0 and "--simular" in execucao.linha:
            self._previa_de_peso = self._alvo_da_linha(execucao.linha)
        self._historico.append(execucao)
        self._atual = None

    @staticmethod
    def _alvo_da_linha(linha: str) -> str:
        partes = shlex.split(linha)

        def valor(bandeira: str) -> str:
            return partes[partes.index(bandeira) + 1] if bandeira in partes else ""

        return f"pedido:{valor('--pedido')}|orcamento:{valor('--orcamento')}"
