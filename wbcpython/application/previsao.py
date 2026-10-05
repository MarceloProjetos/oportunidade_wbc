"""O **formato** do retrato da prévia — não a prévia.

A simulação em si mora no comando que a executa (`cli._cmd_pendentes`): é uma
ferramenta de linha de comando, e quem a procura espera achá-la lá. O que
precisa ser compartilhado é só o formato do que ela produz, porque o retrato
atravessa um arquivo JSON até a aba "Próximo ciclo" do painel.

Manter o formato num lugar só evita a falha silenciosa clássica desse tipo de
ponte: o comando grava um campo com um nome, a tela lê outro, e a coluna aparece
vazia sem erro nenhum.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

#: Ações que resultam em alguma escrita no SAP.
ACOES_QUE_ESCREVEM = frozenset(
    {
        "criar_cotacao",
        "atualizar_cotacao",
        "cancelar_e_recriar_cotacao",
        "criar_pedido",
        "atualizar_pedido",
        "cancelar_e_recriar_pedido",
        "marcar_oportunidade_perdida",
        "cancelar_cotacao_no_encerramento",
        "espelhar_status",
        "trocar_parceiro",
        "atualizar_status_oportunidade",
        "vincular_documento_a_oportunidade",
    }
)

#: Subconjunto que mexe em **documento** (cotação ou pedido). A distinção
#: importa para quem olha o painel: espelhar o status de uma oportunidade é
#: barato e reversível; criar ou cancelar um documento não é.
ACOES_DE_DOCUMENTO = frozenset(
    {
        "criar_cotacao",
        "atualizar_cotacao",
        "cancelar_e_recriar_cotacao",
        "cancelar_cotacao_no_encerramento",
        "criar_pedido",
        "atualizar_pedido",
        "cancelar_e_recriar_pedido",
    }
)


@dataclass(frozen=True, slots=True)
class LinhaDePrevisao:
    """O que o ciclo faria com **um** orçamento.

    `problema` é preenchido quando a linha não chega a ser decidida (sem
    `U_ORCNUM_WBC`, ou ausente no WBC). Ela continua na lista de propósito:
    esconder problema de dado é o oposto do que a prévia serve para fazer.
    """

    orcnum: str = ""
    oportunidade: int | None = None
    cliente: str = ""
    parceiro: str = ""
    sitcode_wbc: int = 0
    sitcode_sap: str = ""
    revisao_wbc: str = ""
    revisao_cotacao: str = ""
    revisao_pedido: str = ""
    status_oportunidade: str = ""
    cotacao_docnum: int | None = None
    pedido_docnum: int | None = None
    parceiro_corrigido: str = ""
    parceiro_do_pedido_no_sap: str = ""
    troca_de_parceiro: bool = False
    regra: str = ""
    motivo: str = ""
    acoes: tuple[str, ...] = ()
    valor_do_documento: Decimal | None = None
    linhas_do_documento: tuple[str, ...] = ()
    avisos: tuple[str, ...] = ()
    problema: str = ""

    @property
    def escreve(self) -> bool:
        return any(acao in ACOES_QUE_ESCREVEM for acao in self.acoes)

    @property
    def toca_documento(self) -> bool:
        return any(acao in ACOES_DE_DOCUMENTO for acao in self.acoes)

    @property
    def sem_valor(self) -> bool:
        """Documento que o SAP recusaria (`-5002`) por não ter valor."""
        return (
            self.toca_documento
            and self.valor_do_documento is not None
            and (self.valor_do_documento <= 0)
        )


#: Rótulos legíveis do `OOPR."Status"`.
STATUS_OPORTUNIDADE = {"O": "Aberta", "W": "Vendida", "L": "Perdida"}


@dataclass(frozen=True, slots=True)
class Previsao:
    """A janela inteira, mais a procedência do retrato."""

    gerado_em: datetime
    company_db: str
    corte: str
    meses_de_janela: int
    limite_de_escrita: int
    linhas: tuple[LinhaDePrevisao, ...] = ()
    grupos_no_de_para: int = 0

    @property
    def total(self) -> int:
        return len(self.linhas)


def _inteiro(valor: Any) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _docnum(documento: Any) -> int | None:
    if not isinstance(documento, dict):
        return None
    return _inteiro(documento.get("DocNum"))


def para_json(previsao: Previsao) -> dict[str, Any]:
    """Serializa o retrato — é isto que o painel lê.

    O formato é um instantâneo com procedência embutida (`meta`): quem abre o
    painel precisa saber de qual company e de que momento o retrato veio, senão
    olha para produção achando que é homologação.
    """
    return {
        "meta": {
            "gerado_em": previsao.gerado_em.isoformat(),
            "company_db": previsao.company_db,
            "corte": previsao.corte,
            "meses_de_janela": previsao.meses_de_janela,
            "limite_de_escrita": previsao.limite_de_escrita,
            "grupos_no_de_para": previsao.grupos_no_de_para,
            "total": previsao.total,
        },
        "linhas": [_linha_para_json(linha) for linha in previsao.linhas],
    }


def _linha_para_json(linha: LinhaDePrevisao) -> dict[str, Any]:
    return {
        "orcnum": linha.orcnum,
        "oportunidade": linha.oportunidade,
        "cliente": linha.cliente,
        "parceiro": linha.parceiro,
        "sitcode_wbc": linha.sitcode_wbc,
        "sitcode_sap": linha.sitcode_sap,
        "revisao_wbc": linha.revisao_wbc,
        "revisao_cotacao": linha.revisao_cotacao,
        "revisao_pedido": linha.revisao_pedido,
        "status_oportunidade": linha.status_oportunidade,
        "cotacao_docnum": linha.cotacao_docnum,
        "pedido_docnum": linha.pedido_docnum,
        "parceiro_corrigido": linha.parceiro_corrigido,
        "parceiro_do_pedido_no_sap": linha.parceiro_do_pedido_no_sap,
        "troca_de_parceiro": linha.troca_de_parceiro,
        "regra": linha.regra,
        "motivo": linha.motivo,
        "acoes": list(linha.acoes),
        "valor_do_documento": (
            None if linha.valor_do_documento is None else str(linha.valor_do_documento)
        ),
        "linhas_do_documento": list(linha.linhas_do_documento),
        "avisos": list(linha.avisos),
        "problema": linha.problema,
        "escreve": linha.escreve,
        "toca_documento": linha.toca_documento,
    }


def de_json(dados: dict[str, Any]) -> Previsao:
    """Reconstrói o retrato lido de um arquivo."""
    meta = dados.get("meta") or {}
    linhas = tuple(_linha_de_json(item) for item in dados.get("linhas") or ())
    return Previsao(
        gerado_em=_momento(meta.get("gerado_em")),
        company_db=str(meta.get("company_db") or ""),
        corte=str(meta.get("corte") or ""),
        meses_de_janela=int(meta.get("meses_de_janela") or 0),
        limite_de_escrita=int(meta.get("limite_de_escrita") or 0),
        linhas=linhas,
        grupos_no_de_para=int(meta.get("grupos_no_de_para") or 0),
    )


def _linha_de_json(item: dict[str, Any]) -> LinhaDePrevisao:
    valor = item.get("valor_do_documento")
    return LinhaDePrevisao(
        orcnum=str(item.get("orcnum") or ""),
        oportunidade=item.get("oportunidade"),
        cliente=str(item.get("cliente") or ""),
        parceiro=str(item.get("parceiro") or ""),
        sitcode_wbc=int(item.get("sitcode_wbc") or 0),
        sitcode_sap=str(item.get("sitcode_sap") or ""),
        revisao_wbc=str(item.get("revisao_wbc") or ""),
        revisao_cotacao=str(item.get("revisao_cotacao") or ""),
        revisao_pedido=str(item.get("revisao_pedido") or ""),
        status_oportunidade=str(item.get("status_oportunidade") or ""),
        cotacao_docnum=item.get("cotacao_docnum"),
        pedido_docnum=item.get("pedido_docnum"),
        parceiro_corrigido=str(item.get("parceiro_corrigido") or ""),
        parceiro_do_pedido_no_sap=str(item.get("parceiro_do_pedido_no_sap") or ""),
        troca_de_parceiro=bool(item.get("troca_de_parceiro")),
        regra=str(item.get("regra") or ""),
        motivo=str(item.get("motivo") or ""),
        acoes=tuple(item.get("acoes") or ()),
        valor_do_documento=None if valor is None else Decimal(str(valor)),
        linhas_do_documento=tuple(item.get("linhas_do_documento") or ()),
        avisos=tuple(item.get("avisos") or ()),
        problema=str(item.get("problema") or ""),
    )


def _momento(valor: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(valor))
    except (TypeError, ValueError):
        return datetime.now(UTC)
