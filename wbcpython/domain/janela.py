"""Janela de busca sob demanda: bandas, teto de escrita e o estado do pedido.

Sem I/O, como o resto de `domain/`: são as regras que decidem *quanto* um ciclo
pode escrever quando alguém pede uma janela maior que a padrão, e *quando* o
pedido volta ao padrão sozinho.

O problema que isto resolve — `docs/PLANO_JANELA_SOB_DEMANDA.md`:

Uma janela maior devolve muito mais oportunidades represadas. Com o teto fixo de
200 escritas por ciclo, "abre 24 meses por um ciclo" escreveria 200 e abandonaria
o resto, porque a janela já teria voltado a 6 — o pedido se gastaria sem ter
feito o que foi pedido. Tirar o teto é pior: um ciclo criaria milhares de
documentos irreversíveis em produção sem ninguém olhando.

A saída é um teto que **cresce junto com a janela**, e uma pergunta quando nem
ele basta.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil

#: Maior janela pedível pela tela, em meses.
#:
#: Vinte e quatro é a escolha do Marcelo (11/09/2026): cobre dois anos de
#: oportunidades antigas, que é o que vendas precisa alcançar, e mantém o pior
#: caso de escrita num número que ainda cabe na cabeça de quem autoriza.
JANELA_MAXIMA_PADRAO = 24

#: Largura de cada banda de multiplicador, em meses.
LARGURA_DA_BANDA = 6

#: Quanto o teto de escrita cresce a cada banda acima do padrão.
#:
#: Progressão aritmética (3×, 6×, 9×), e não dobra (3×, 6×, 12×): decisão 2 do
#: plano, fechada pelo Marcelo em 11/09/2026. O passo constante mantém o pior
#: caso previsível quando alguém for mexer nos números daqui a um ano.
PASSO_DO_MULTIPLICADOR = 3

#: Teto que nenhuma banda ultrapassa, por mais larga que seja a janela.
#:
#: O escalonamento é uma regra; isto é a rede embaixo dela. Existe para o dia em
#: que alguém aumentar `JANELA_MAXIMA` ou o passo sem refazer a conta do pior
#: caso — sem este limite, um número digitado errado vira um ciclo de milhares
#: de escritas irreversíveis em produção.
TETO_ABSOLUTO_PADRAO = 2000

#: Quanto tempo a pergunta "quer rodar outro ciclo?" fica de pé.
#:
#: Quinze minutos, por decisão do Marcelo (11/09/2026). Passou disso, a janela
#: volta ao padrão e o log registra onde o ciclo parou — pergunta sem resposta
#: não pode deixar uma janela larga armada esperando por horas.
ESPERA_PELA_RESPOSTA = timedelta(minutes=15)


class EstadoDaJanela(enum.StrEnum):
    """Onde o pedido de janela está.

    Os três estados existem para separar duas coisas que parecem uma só: "o
    próximo ciclo deve usar a janela larga" (`ARMADO`) e "um ciclo largo já
    rodou, estourou o teto e espera resposta" (`AGUARDANDO`). Sem essa
    separação, o worker continuaria varrendo 24 meses a cada 180 s enquanto a
    pergunta espera — que é exatamente o ciclo pesado rodando sozinho que este
    desenho existe para evitar.
    """

    OCIOSO = "ocioso"
    ARMADO = "armado"
    AGUARDANDO = "aguardando"


def multiplicador_do_teto(meses: int, *, padrao: int) -> int:
    """Quantas vezes o teto de escrita cresce para uma janela de `meses`.

    A banda é contada a partir da janela **padrão**, não do número 6: é o
    excedente que justifica o teto maior, e `MESES_DE_JANELA` continua sendo
    ajustável no `.env`. Com o padrão de 6, isto dá exatamente a tabela do plano:

    | Janela   | Multiplicador |
    |----------|---------------|
    | até 6    | 1×            |
    | 7 a 12   | 3×            |
    | 13 a 18  | 6×            |
    | 19 a 24  | 9×            |
    """
    if meses <= padrao:
        return 1
    banda = ceil((meses - padrao) / LARGURA_DA_BANDA)
    return PASSO_DO_MULTIPLICADOR * banda


def teto_de_escrita(
    meses: int,
    *,
    padrao: int,
    base: int,
    absoluto: int = TETO_ABSOLUTO_PADRAO,
) -> int:
    """O teto de escrita de um ciclo com janela de `meses`.

    `base` é o `LIMITE_DE_ESCRITA_POR_CICLO` — o teto da janela padrão, que este
    plano não altera.
    """
    return min(base * multiplicador_do_teto(meses, padrao=padrao), absoluto)


@dataclass(frozen=True, slots=True)
class Janela:
    """A janela que um ciclo vai usar, e de onde ela veio.

    `estendida` não é derivável de `meses > padrao` por quem só recebe o
    resultado, e é ela que decide se o fim do ciclo mexe no pedido — por isso
    viaja junto, em vez de ser recalculada em cada ponto de uso.
    """

    meses: int
    teto: int
    estendida: bool = False

    def __str__(self) -> str:
        origem = "estendida, a pedido" if self.estendida else "padrão"
        return f"{self.meses} meses ({origem}); teto de {self.teto} escrita(s)"


def validar_meses(meses: int, *, padrao: int, maximo: int) -> int:
    """Devolve `meses` ou levanta `ValueError` com a frase que vai à tela.

    A validação é do servidor de propósito: o campo da tela limita o que é fácil
    digitar, não o que chega ao worker. Quem arma pela CLI ou por um POST à mão
    passa por aqui igual.
    """
    if meses < padrao:
        raise ValueError(
            f"A janela pedida ({meses}) é menor que o padrão ({padrao}). "
            f"Para voltar ao padrão, use a opção de limpar o pedido."
        )
    if meses > maximo:
        raise ValueError(f"A janela pedida ({meses}) passa do máximo de {maximo} meses.")
    return meses


def expirou(
    estado: EstadoDaJanela,
    expira_em: datetime | None,
    *,
    agora: datetime,
) -> bool:
    """Se o pedido em `AGUARDANDO` já passou do prazo de resposta.

    Só `AGUARDANDO` expira. `ARMADO` é um pedido que ainda não foi atendido — e
    um pedido feito às 19h numa sexta só age na segunda, porque o worker não
    roda fora do expediente. Expirá-lo por tempo faria o sistema engolir
    calado justamente o pedido de quem esperou o fim de semana.
    """
    if estado is not EstadoDaJanela.AGUARDANDO or expira_em is None:
        return False
    return agora >= expira_em
