"""Documentos já resolvidos na leitura — sem ida extra ao SAP.

`montar_estado` precisa saber, para cada orçamento, se já existe cotação e
pedido, qual revisão está aplicada neles, em que parceiro o pedido está e se ele
está fechado. Perguntar isso ao Service Layer custa **seis** requisições por
orçamento: cada um desses métodos chama `buscar`, e `existe` e `revisao_aplicada`
o fazem para os dois tipos de documento.

A consulta do HANA já traz essa resposta junto com a oportunidade. Esta classe
é o que permite entregá-la a `montar_estado` sem mexer na máquina de estados:
implementa a mesma interface mínima que o repositório do Service Layer expõe,
respondendo do que já está em memória.

O ganho é grande, mas o motivo de existir é outro: manter `montar_estado` e
`decidir` ignorantes quanto à origem do dado. A decisão não pode mudar porque a
leitura mudou de lugar — e, como a interface é a mesma, os testes da máquina de
estados continuam valendo sem uma linha alterada.
"""

from __future__ import annotations

from typing import Any

from wbcpython.infrastructure.service_layer.documentos import (
    UDF_CONGELADO,
    TipoDocumento,
    esta_congelado_pelo_campo,
    esta_fechado_pelo_status,
)

#: Chave usada pelo repositório do HANA para cada tipo de documento.
_CHAVE = {TipoDocumento.COTACAO: "cotacao", TipoDocumento.PEDIDO: "pedido"}

#: UDF que guarda a revisão do WBC aplicada no documento.
UDF_REVISAO = "U_INO_VERSAOWBC"


class DocumentosJaLidos:
    """Responde sobre os documentos a partir do que a consulta já trouxe."""

    def __init__(self, documentos: dict[str, Any] | None = None) -> None:
        self._documentos = documentos or {}

    @classmethod
    def da_oportunidade(cls, oportunidade: dict[str, Any]) -> DocumentosJaLidos:
        """Extrai o bloco `documentos` que o repositório do HANA anexa."""
        return cls(oportunidade.get("documentos"))

    def buscar(self, tipo: TipoDocumento, orcamento: str) -> dict[str, Any] | None:
        del orcamento  # a consulta já filtrou pelo orçamento
        return self._documentos.get(_CHAVE[tipo])

    def existe(self, tipo: TipoDocumento, orcamento: str) -> bool:
        return self.buscar(tipo, orcamento) is not None

    def revisao_aplicada(self, tipo: TipoDocumento, orcamento: str) -> str:
        """Revisão gravada no documento, ou `''` quando não há documento.

        String vazia é como a máquina de estados representa "sem revisão" — o
        mesmo contrato do repositório do Service Layer.
        """
        documento = self.buscar(tipo, orcamento)
        if not documento:
            return ""
        return str(documento.get(UDF_REVISAO) or "")

    def parceiro_aplicado(self, tipo: TipoDocumento, orcamento: str) -> str:
        """`CardCode` do documento vigente — vazio quando não há documento."""
        documento = self.buscar(tipo, orcamento)
        return str(documento.get("CardCode") or "") if documento else ""

    def esta_fechado(self, tipo: TipoDocumento, orcamento: str) -> bool:
        """`DocStatus = 'C'`: o SAP não aceita alteração nem cancelamento.

        `False` quando não há documento — "não existe" não é "está fechado", e
        confundir os dois impediria a **criação** do primeiro pedido. Já um
        status **desconhecido** trata o documento como fechado: ver
        `esta_fechado_pelo_status`, que é onde a política mora.
        """
        documento = self.buscar(tipo, orcamento)
        if not documento:
            return False
        return esta_fechado_pelo_status(
            documento.get("DocStatus"), documento=f"{tipo.rotulo} de {orcamento}"
        )

    def esta_congelado(self, tipo: TipoDocumento, orcamento: str) -> bool:
        """`U_INO_Congelado = 'Y'`: as linhas do pedido não podem ser refeitas.

        Mesma pergunta que o Service Layer responde por `buscar`, aqui a partir
        do que a consulta do HANA já trouxe — a política de tradução mora em
        `esta_congelado_pelo_campo`.
        """
        documento = self.buscar(tipo, orcamento)
        if not documento:
            return False
        return esta_congelado_pelo_campo(documento.get(UDF_CONGELADO))

    def doc_entry(self, tipo: TipoDocumento, orcamento: str) -> int | None:
        documento = self.buscar(tipo, orcamento)
        return int(documento["DocEntry"]) if documento else None
