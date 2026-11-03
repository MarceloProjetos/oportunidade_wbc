"""Data layer of the "Próximo ciclo" tab.

The panel does **not** query SAP or WBC (see `dashboard/__init__`), and this tab is no exception: it reads the
snapshot that `wbcpython pendentes --exportar` writes. Reading a file instead of calling the simulation from
inside the panel is not cosmetic — opening the panel would cost a HANA and SQL Server scan on every page reload,
and a read-only screen would become load on the source systems.

Because the aggregations live here and not in the interface, they are testable without starting any server.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from wbcpython.application.previsao import LinhaDePrevisao, Previsao, de_json

#: Onde o painel procura o retrato quando ninguém diz o caminho. Em `state/` porque é
#: runtime (tem nome de cliente e envelhece em horas) — a pasta já é ignorada pelo git.
ARQUIVO_PADRAO = Path("state/wbc_previsao.json")

#: Rótulos legíveis das ações do domínio. O valor do enum é o nome técnico;
#: quem lê o painel não deveria precisar traduzir `cancelar_e_recriar_cotacao`.
ROTULO_DA_ACAO = {
    "criar_cotacao": "Criar cotação",
    "atualizar_cotacao": "Atualizar cotação",
    "cancelar_e_recriar_cotacao": "Cancelar e recriar cotação",
    "cancelar_cotacao_no_encerramento": "Cancelar cotação (encerramento)",
    "criar_pedido": "Criar pedido",
    "atualizar_pedido": "Atualizar pedido",
    "cancelar_e_recriar_pedido": "Cancelar e recriar pedido",
    "cancelar_ops_do_pedido": "Cancelar as OPs planejadas do pedido",
    "vincular_documento_a_oportunidade": "Vincular documento à oportunidade",
    "atualizar_status_oportunidade": "Espelhar status da oportunidade",
    "marcar_oportunidade_perdida": "Marcar oportunidade como perdida",
}


def rotulo_da_acao(acao: str) -> str:
    return ROTULO_DA_ACAO.get(acao, acao.replace("_", " "))


class RetratoAusente(FileNotFoundError):
    """There is no snapshot to show — the panel explains how to generate one."""


def carregar(caminho: Path | str | None = None) -> Previsao:
    """Reads the snapshot from disk (default `ARQUIVO_PADRAO`).

    Raises `RetratoAusente` when the file is missing: the panel must tell "nothing to do" from "nobody has
    generated the snapshot yet". Showing zero in both cases would lie by omission on the very screen meant to give
    confidence before running the cycle.
    """
    destino = Path(caminho) if caminho else ARQUIVO_PADRAO
    if not destino.exists():
        raise RetratoAusente(str(destino))
    return de_json(json.loads(destino.read_text(encoding="utf-8")))


@dataclass(frozen=True, slots=True)
class KpisDaPrevisao:
    """The numbers at the top of the tab."""

    total: int = 0
    escreve: int = 0
    toca_documento: int = 0
    so_status: int = 0
    sem_acao: int = 0
    troca_de_parceiro: int = 0
    problemas: int = 0
    sem_valor: int = 0


def calcular_kpis(linhas: list[LinhaDePrevisao]) -> KpisDaPrevisao:
    escreve = [linha for linha in linhas if linha.escreve]
    documento = [linha for linha in escreve if linha.toca_documento]
    return KpisDaPrevisao(
        total=len(linhas),
        escreve=len(escreve),
        toca_documento=len(documento),
        so_status=len(escreve) - len(documento),
        sem_acao=len(linhas) - len(escreve),
        troca_de_parceiro=sum(1 for linha in linhas if linha.troca_de_parceiro),
        problemas=sum(1 for linha in linhas if linha.problema),
        sem_valor=sum(1 for linha in linhas if linha.sem_valor),
    )


def contar_acoes(linhas: list[LinhaDePrevisao]) -> list[tuple[str, int]]:
    """How many times each action would go out. One quote can yield more than one."""
    contagem: Counter[str] = Counter()
    for linha in linhas:
        contagem.update(linha.acoes)
    return [(rotulo_da_acao(acao), n) for acao, n in contagem.most_common()]


def contar(linhas: list[LinhaDePrevisao], campo: str) -> list[tuple[str, int]]:
    """Distribution of a simple field (rule, status, SitCode…); empty values count as “—”."""
    contagem: Counter[str] = Counter(str(getattr(linha, campo) or "—") for linha in linhas)
    return contagem.most_common()


def filtrar(
    linhas: list[LinhaDePrevisao],
    *,
    busca: str = "",
    status: str | None = None,
    sitcode: int | None = None,
    acao: str | None = None,
    apenas_com_acao: bool = False,
    apenas_documento: bool = False,
) -> list[LinhaDePrevisao]:
    """The table filters, in one place and testable without the interface."""
    termo = busca.strip().lower()
    resultado = []
    for linha in linhas:
        if apenas_documento and not linha.toca_documento:
            continue
        if apenas_com_acao and not linha.escreve:
            continue
        if status and linha.status_oportunidade != status:
            continue
        if sitcode is not None and linha.sitcode_wbc != sitcode:
            continue
        if acao and acao not in linha.acoes:
            continue
        if termo:
            campos = " ".join(
                str(valor or "")
                for valor in (
                    linha.orcnum,
                    linha.cliente,
                    linha.parceiro,
                    linha.cotacao_docnum,
                    linha.pedido_docnum,
                )
            ).lower()
            if termo not in campos:
                continue
        resultado.append(linha)
    return resultado


def linha_para_tabela(linha: LinhaDePrevisao) -> dict[str, Any]:
    """One row of the tab's table."""
    return {
        "Orçamento": linha.orcnum,
        "Cliente": linha.cliente,
        "Parceiro": linha.parceiro,
        "SitCode": linha.sitcode_wbc,
        "Status da oportunidade": linha.status_oportunidade or "—",
        "Rev. WBC": linha.revisao_wbc or "—",
        # Texto, não número: a coluna mistura documento existente com ausência,
        # e o número do documento não é para somar nem ordenar como grandeza.
        "Cotação": str(linha.cotacao_docnum or ""),
        "Pedido": str(linha.pedido_docnum or ""),
        "O ciclo faria": ", ".join(rotulo_da_acao(a) for a in linha.acoes) or "nada",
        "Regra": linha.regra,
    }


def idade(previsao: Previsao, *, agora: datetime | None = None) -> str:
    """How long ago the snapshot was taken — as short text.

    Always visible because the tab shows a snapshot: yesterday's picture describes a cycle that already ran, and
    whoever reads it without this decides about a world that no longer exists.
    """
    referencia = agora or datetime.now(previsao.gerado_em.tzinfo)
    minutos = int((referencia - previsao.gerado_em).total_seconds() // 60)
    if minutos < 1:
        return "agora há pouco"
    if minutos < 60:
        return f"há {minutos} min"
    horas = minutos // 60
    if horas < 24:
        return f"há {horas} h"
    return f"há {horas // 24} dia(s)"
