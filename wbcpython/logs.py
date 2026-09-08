"""O log da integração — uma fonte, duas telas.

O que o `pendentes`, o `ciclo` e o `worker` relatam vai para o `logging`, e daí
para dois lugares ao mesmo tempo: a tela de quem rodou o comando e um arquivo
que o painel lê. É o mesmo texto nos dois lugares, e não duas narrativas do
mesmo evento que podem discordar.

Por que arquivo, e não o banco de acompanhamento: o tracking registra o que
**aconteceu** com cada orçamento (a decisão, o documento criado, o erro), e é
consultado por orçamento. O log registra a **execução** — a ordem dos eventos, o
que veio antes do erro, o que o worker estava fazendo às 3h da manhã. São
perguntas diferentes, e responder as duas com a mesma tabela deixaria as duas
piores.

Formato do arquivo (uma linha por evento, campos separados por ` | `):

    2026-09-01 14:32:07 | INFO     | wbcpython.host.worker | ciclo concluído

O separador é explícito para que a leitura no painel não precise adivinhar onde
a mensagem começa — mensagens contêm ` ` e `:` o tempo todo.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from dataclasses import dataclass
from pathlib import Path

#: Marca os handlers instalados por este módulo. Sem ela, chamar `configurar`
#: duas vezes no mesmo processo (o que os testes fazem, e o recarregador do
#: uvicorn também)
#: empilharia handlers e cada linha sairia repetida.
_MARCA = "wbcpython"

SEPARADOR = " | "
FORMATO_DO_ARQUIVO = (
    f"%(asctime)s{SEPARADOR}%(levelname)-8s{SEPARADOR}%(name)s{SEPARADOR}%(message)s"
)
FORMATO_DA_DATA = "%Y-%m-%d %H:%M:%S"

#: 5 MB por arquivo, 3 arquivos: cobre semanas de worker num disco de
#: desenvolvimento sem exigir rotação externa.
TAMANHO_MAXIMO = 5 * 1024 * 1024
ARQUIVOS_MANTIDOS = 3

NIVEIS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

#: Bibliotecas que registram uma linha INFO por requisição. No arquivo elas são
#: exatamente o que se quer quando algo dá errado — mostram a chamada que
#: precedeu o erro. Na tela, afogam o relatório: uma prévia de 700 orçamentos
#: intercalaria centenas de "HTTP Request: GET ..." entre as linhas que a pessoa
#: veio ler. Por isso o filtro é só do console.
RUIDOSAS = ("httpx", "httpcore", "hdbcli", "urllib3")


class _SoORelatorio(logging.Filter):
    """Deixa passar as linhas da aplicação; das bibliotecas, só avisos e erros."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        return not record.name.startswith(RUIDOSAS)


def configurar(
    *,
    nivel: str = "INFO",
    arquivo: Path | str | None = None,
    tela: bool = True,
) -> Path | None:
    """Instala os handlers e devolve o arquivo em uso (ou `None`).

    `tela` escreve em `sys.stdout` — e não no `stderr` padrão do `logging` — para
    que a saída do comando seja uma coisa só: redirecionar `>` num terminal tem
    de levar o relatório inteiro junto.

    A tela recebe o relatório; o arquivo recebe **tudo**, inclusive o rastro HTTP
    das bibliotecas (ver `RUIDOSAS`). É a assimetria certa: quem está no terminal
    quer ler o resultado, e quem investiga um erro depois quer a chamada que veio
    antes dele.

    Se o arquivo não puder ser aberto (diretório somente leitura, por exemplo), a
    tela continua funcionando e um aviso é registrado. Um comando de leitura não
    pode falhar por causa do log.
    """
    raiz = logging.getLogger()
    raiz.setLevel(getattr(logging, nivel.upper(), logging.INFO))

    for handler in list(raiz.handlers):
        if getattr(handler, "_wbcpython", None) == _MARCA:
            raiz.removeHandler(handler)
            handler.close()

    if tela:
        # `%(message)s` puro: na tela o texto é o relatório que a pessoa veio
        # ler, e prefixar cada linha com data e nível o tornaria ilegível.
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter("%(message)s"))
        console.addFilter(_SoORelatorio())
        console._wbcpython = _MARCA  # type: ignore[attr-defined]
        raiz.addHandler(console)

    if arquivo is None:
        return None

    destino = Path(arquivo)
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        em_arquivo = logging.handlers.RotatingFileHandler(
            destino, maxBytes=TAMANHO_MAXIMO, backupCount=ARQUIVOS_MANTIDOS, encoding="utf-8"
        )
    except OSError as exc:
        logging.getLogger(__name__).warning("Sem log em arquivo (%s): %s", destino, exc)
        return None

    em_arquivo.setFormatter(logging.Formatter(FORMATO_DO_ARQUIVO, datefmt=FORMATO_DA_DATA))
    em_arquivo._wbcpython = _MARCA  # type: ignore[attr-defined]
    raiz.addHandler(em_arquivo)
    return destino


@dataclass(frozen=True, slots=True)
class LinhaDeLog:
    """Uma linha do arquivo, já separada em campos."""

    momento: str = ""
    nivel: str = ""
    origem: str = ""
    mensagem: str = ""

    @property
    def grave(self) -> bool:
        return self.nivel in ("ERROR", "CRITICAL")


def ler(
    arquivo: Path | str,
    *,
    limite: int = 500,
    nivel: str | None = None,
    busca: str = "",
) -> list[LinhaDeLog]:
    """Últimas linhas do log, mais novas primeiro.

    Lê o arquivo inteiro e corta no fim: com rotação em 5 MB isso é barato, e
    uma leitura por trás (`seek` a partir do fim) complicaria o código para
    economizar milissegundos numa tela que ninguém recarrega em laço.

    Linha que não casa com o formato vira mensagem pura — traceback de exceção
    ocupa várias linhas e some se a leitura descartar o que não reconhece.
    """
    origem = Path(arquivo)
    if not origem.exists():
        return []

    termo = busca.strip().lower()
    encontradas: list[LinhaDeLog] = []
    with origem.open(encoding="utf-8", errors="replace") as aberto:
        for bruta in aberto:
            linha = _separar(bruta.rstrip("\n"))
            if nivel and linha.nivel != nivel:
                continue
            if termo and termo not in bruta.lower():
                continue
            encontradas.append(linha)

    return list(reversed(encontradas[-limite:]))


def _separar(bruta: str) -> LinhaDeLog:
    partes = bruta.split(SEPARADOR, 3)
    if len(partes) < 4 or partes[1].strip() not in NIVEIS:
        return LinhaDeLog(mensagem=bruta)
    return LinhaDeLog(
        momento=partes[0].strip(),
        nivel=partes[1].strip(),
        origem=partes[2].strip(),
        mensagem=partes[3],
    )
