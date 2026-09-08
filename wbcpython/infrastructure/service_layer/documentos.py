"""Cotações (`Quotations`) e Pedidos de Venda (`Orders`) via Service Layer.

Substitui os `GetBusinessObject(oQuotations/oOrders)` do DI-API legado.

O vínculo entre o documento do SAP e o orçamento do WBC é o UDF
**`U_INO_COTWBC`** — é por ele que o legado conta cotações e pedidos
(`GetIntIdWBCOrcamentos`, `GetIdOrcamentosPedido`) e é a chave que torna a
integração idempotente: antes de criar, pergunta-se se já existe.

`U_INO_VERSAOWBC` guarda a revisão do WBC aplicada ao documento, e é o que a
máquina de estados compara para decidir entre atualizar e deixar congelado.

Documentos **cancelados são ignorados** em todas as buscas (`Cancelled eq 'tNO'`),
espelhando o `CANCELED = 'N'` das consultas do legado: um documento cancelado
não conta como existente, senão a integração nunca recriaria o que foi anulado.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol

from wbcpython.infrastructure.service_layer.client import ServiceLayerClient

logger = logging.getLogger(__name__)

UDF_ORCAMENTO = "U_INO_COTWBC"
UDF_REVISAO = "U_INO_VERSAOWBC"

#: `U_INO_Congelado = 'Y'` no pedido: **as linhas não podem ser refeitas**.
#:
#: É o campo que o legado lê em `UpdatePedido` (`ServiceProcess.cs:585`). Sem
#: ele, a atualização apaga todas as linhas do pedido e as reconstrói a partir
#: do orçamento — e leva junto qualquer edição manual (item trocado, quantidade
#: ajustada, texto adicional).
#:
#: Protege **só as linhas**: no legado o cabeçalho é gravado fora do `if`, e o
#: `Update()` acontece depois dele.
UDF_CONGELADO = "U_INO_Congelado"


class TipoDocumento(Enum):
    """Os dois documentos de venda que a integração cria."""

    COTACAO = "Quotations"
    PEDIDO = "Orders"

    @property
    def rotulo(self) -> str:
        return "cotação" if self is TipoDocumento.COTACAO else "pedido"


class RepositorioDocumentosVenda(Protocol):
    def buscar(self, tipo: TipoDocumento, orcamento: str) -> dict[str, Any] | None: ...

    def existe(self, tipo: TipoDocumento, orcamento: str) -> bool: ...

    def parceiro_aplicado(self, tipo: TipoDocumento, orcamento: str) -> str: ...

    def esta_fechado(self, tipo: TipoDocumento, orcamento: str) -> bool: ...

    def esta_congelado(self, tipo: TipoDocumento, orcamento: str) -> bool: ...

    def total_das_linhas(self, tipo: TipoDocumento, doc_entry: int) -> Decimal: ...

    def criar(self, tipo: TipoDocumento, dados: dict[str, Any]) -> dict[str, Any]: ...

    def atualizar(self, tipo: TipoDocumento, doc_entry: int, dados: dict[str, Any]) -> None: ...

    def atualizar_pesos(
        self, tipo: TipoDocumento, doc_entry: int, pesos_por_linha: dict[int, float]
    ) -> None: ...

    def buscar_por_docnum(self, tipo: TipoDocumento, docnum: int) -> dict[str, Any] | None: ...

    def cancelar(self, tipo: TipoDocumento, doc_entry: int) -> None: ...


#: Status que significam "aberto", nas duas fontes que a integração lê.
#:
#: O Service Layer devolve `bost_Open`/`bost_Close` no campo `DocumentStatus`;
#: a consulta do HANA devolve `'O'`/`'C'` na coluna `DocStatus`. São vocabulários
#: diferentes para o mesmo fato, e este é o único lugar que conhece os dois.
_ABERTO = frozenset({"O", "BOST_OPEN"})
_FECHADO = frozenset({"C", "BOST_CLOSE"})


def esta_congelado_pelo_campo(bruto: Any) -> bool:
    """`U_INO_Congelado` do pedido, traduzido. Só `'Y'` congela.

    Segue o legado ao pé da letra: `if (Congelado != "Y")` reconstrói as linhas
    (`ServiceProcess.cs:597`). Vazio, nulo ou qualquer outro valor **não**
    congela.

    Ao contrário do `DocStatus`, aqui não há como falhar fechado sem divergir:
    "falhar congelado" no valor desconhecido pararia de atualizar linhas num
    caso que o legado atualiza. Em produção o ponto é acadêmico — os 2.581
    pedidos vigentes estão todos em `'Y'` —, mas a divergência seria silenciosa,
    e é justamente esse tipo que não se descobre depois.
    """
    return str(bruto or "").strip().upper() == "Y"


def esta_fechado_pelo_status(bruto: Any, *, documento: str) -> bool:
    """Traduz o status do documento — **falhando fechado** no desconhecido.

    Um valor que não é nem aberto nem fechado (vazio, nulo, coluna que sumiu da
    consulta, vocabulário novo do SAP) devolve `True`: o documento é tratado
    como fechado e a integração não escreve nele.

    A escolha é deliberada e segue o resto do projeto — `safety.py` e a
    `PAINEL_SENHA` também bloqueiam quando a configuração está incompleta. Aqui
    ela pesa mais: o defeito do orçamento `00124268` foi exatamente uma coluna
    que **não era lida**, e uma guarda que falha aberta evaporaria em silêncio
    no dia em que a coluna sumisse de novo — sem um teste falhar, e voltando a
    escrever em pedido fechado.

    Custo medido: nenhum. Agrupando por status nos dois ambientes, só existem
    `'O'` e `'C'` (146+2.424 em homologação, 116+2.452 em produção) — nenhuma
    linha cairia neste caminho hoje. E o aviso torna visível o dia em que uma
    cair.
    """
    valor = str(bruto or "").strip().upper()
    if valor in _FECHADO:
        return True
    if valor in _ABERTO:
        return False
    logger.warning(
        "%s: status %r não reconhecido. Tratando como FECHADO por segurança — a "
        "integração não vai alterar nem cancelar este documento. Valores esperados: "
        "%s (aberto) ou %s (fechado).",
        documento,
        str(bruto or ""),
        "/".join(sorted(_ABERTO)),
        "/".join(sorted(_FECHADO)),
    )
    return True


def _escapar(valor: str) -> str:
    return valor.replace("'", "''")


class RepositorioDocumentosVendaServiceLayer:
    """Implementação sobre o Service Layer."""

    def __init__(self, cliente: ServiceLayerClient) -> None:
        self._cliente = cliente

    # -------------------------------------------------------------- leitura

    def buscar(self, tipo: TipoDocumento, orcamento: str) -> dict[str, Any] | None:
        """Documento não cancelado mais recente para o orçamento, ou `None`."""
        registros = self._cliente.listar(
            tipo.value,
            filtro=(f"{UDF_ORCAMENTO} eq '{_escapar(orcamento)}' and Cancelled eq 'tNO'"),
            ordenar_por="DocEntry desc",
            top=1,
        )
        return registros[0] if registros else None

    def existe(self, tipo: TipoDocumento, orcamento: str) -> bool:
        """Equivale ao `COUNT(*) > 0` das consultas do legado."""
        return self.buscar(tipo, orcamento) is not None

    def revisao_aplicada(self, tipo: TipoDocumento, orcamento: str) -> str:
        """Revisão do WBC já gravada no documento (`U_INO_VERSAOWBC`).

        Devolve string vazia quando não há documento ou o campo está em branco —
        que é como a máquina de estados representa "sem revisão".
        """
        documento = self.buscar(tipo, orcamento)
        if not documento:
            return ""
        return str(documento.get(UDF_REVISAO) or "")

    def parceiro_aplicado(self, tipo: TipoDocumento, orcamento: str) -> str:
        """`CardCode` do documento vigente — vazio quando não há documento.

        É o `ChecaPNPedido` do legado: comparar o parceiro que o pedido já tem
        com o `U_INO_PN_Correc` é o que diz se a troca ainda está pendente.
        """
        documento = self.buscar(tipo, orcamento)
        return str(documento.get("CardCode") or "") if documento else ""

    def esta_fechado(self, tipo: TipoDocumento, orcamento: str) -> bool:
        """Documento fechado: o SAP não aceita alteração nem cancelamento.

        Aqui o campo é `DocumentStatus` (`bost_Open`/`bost_Close`); na consulta
        do HANA a mesma informação vem como `DocStatus` (`'O'`/`'C'`). Os dois
        caminhos respondem à mesma pergunta por nomes diferentes — é para isso
        que o método está no protocolo, e quem conhece os dois vocabulários é
        `esta_fechado_pelo_status`.

        `False` quando não há documento: "não existe" não é "está fechado", e
        confundir os dois impediria a criação do primeiro pedido.
        """
        documento = self.buscar(tipo, orcamento)
        if not documento:
            return False
        return esta_fechado_pelo_status(
            documento.get("DocumentStatus"), documento=f"{tipo.rotulo} de {orcamento}"
        )

    def esta_congelado(self, tipo: TipoDocumento, orcamento: str) -> bool:
        """Pedido com as linhas protegidas (`U_INO_Congelado = 'Y'`).

        `False` sem documento: não existindo pedido, não há linha a proteger —
        e a criação nunca consulta este campo, no legado nem aqui.
        """
        documento = self.buscar(tipo, orcamento)
        if not documento:
            return False
        return esta_congelado_pelo_campo(documento.get(UDF_CONGELADO))

    def doc_entry(self, tipo: TipoDocumento, orcamento: str) -> int | None:
        documento = self.buscar(tipo, orcamento)
        return int(documento["DocEntry"]) if documento else None

    # -------------------------------------------------------------- escrita

    def criar(self, tipo: TipoDocumento, dados: dict[str, Any]) -> dict[str, Any]:
        """Cria o documento. A trava de ambiente é aplicada pelo cliente."""
        if not dados.get(UDF_ORCAMENTO):
            raise ValueError(
                f"{UDF_ORCAMENTO} é obrigatório ao criar {tipo.rotulo}: sem ele o "
                f"documento fica órfão e a integração não consegue reencontrá-lo, "
                f"o que levaria a duplicá-lo na próxima execução."
            )

        resposta = self._cliente.post(tipo.value, json=dados)
        criado = resposta.json()
        logger.info(
            "%s criada: DocEntry=%s DocNum=%s orçamento=%s",
            tipo.rotulo.capitalize(),
            criado.get("DocEntry"),
            criado.get("DocNum"),
            dados.get(UDF_ORCAMENTO),
        )
        return criado

    def atualizar(self, tipo: TipoDocumento, doc_entry: int, dados: dict[str, Any]) -> None:
        """Atualização parcial (`PATCH`), com substituição real das linhas.

        O `PATCH` do Service Layer, sozinho, **não** troca a coleção de linhas:
        ele mescla. Linhas enviadas sem `LineNum` são acrescentadas, e as que já
        estavam lá continuam — com valor e tudo.

        Isso foi observado em homologação, não deduzido: a cotação 101857 do
        orçamento `00125527` terminou uma atualização com **três** linhas
        (R$ 69.656,20) enquanto o orçamento no WBC tem duas (R$ 52.079,03). A
        linha sobrando era de uma revisão anterior e ninguém a apagou. Numa
        cotação que vai para o cliente, isso é dinheiro errado no documento.

        `B1S-ReplaceCollectionsOnPatch` é o cabeçalho que o Service Layer expõe
        exatamente para isso: com ele, a coleção enviada substitui a existente,
        e o documento passa a ter só as linhas do orçamento atual.
        """
        self._cliente.patch(
            f"{tipo.value}({doc_entry})",
            json=dados,
            headers={"B1S-ReplaceCollectionsOnPatch": "true"},
        )
        logger.info("%s %s atualizada.", tipo.rotulo.capitalize(), doc_entry)

    def atualizar_pesos(
        self, tipo: TipoDocumento, doc_entry: int, pesos_por_linha: dict[int, float]
    ) -> None:
        """Grava `Weight1` em linhas específicas, **sem** tocar no resto.

        A diferença para `atualizar` é o cabeçalho que **não** é enviado. Com
        `B1S-ReplaceCollectionsOnPatch` a coleção enviada substitui a existente:
        mandar só as linhas que têm peso apagaria as outras, e mandar todas com
        apenas `LineNum` e `Weight1` zeraria o que não fosse repetido.

        Sem o cabeçalho, o `PATCH` **mescla** — e é justamente o comportamento
        que atrapalha ao atualizar um documento inteiro (ver `atualizar`) que
        serve aqui: linha identificada por `LineNum` tem os campos enviados
        alterados e conserva os demais. Item, preço, texto e depósito ficam como
        estavam.
        """
        if not pesos_por_linha:
            return
        corpo = {
            "DocumentLines": [
                {"LineNum": linha, "Weight1": peso}
                for linha, peso in sorted(pesos_por_linha.items())
            ]
        }
        self._cliente.patch(f"{tipo.value}({doc_entry})", json=corpo)
        logger.info(
            "%s %s: peso gravado em %d linha(s).",
            tipo.rotulo.capitalize(),
            doc_entry,
            len(pesos_por_linha),
        )

    def buscar_por_docnum(self, tipo: TipoDocumento, docnum: int) -> dict[str, Any] | None:
        """Documento pelo número que as pessoas citam (`DocNum`), não pela chave.

        `DocEntry` é a chave do Service Layer; `DocNum` é o que aparece na tela
        do SAP e no e-mail de quem pede a correção. Quem opera não deveria
        precisar traduzir um no outro à mão.
        """
        registros = self._cliente.listar(tipo.value, filtro=f"DocNum eq {int(docnum)}", top=1)
        return registros[0] if registros else None

    def cancelar(self, tipo: TipoDocumento, doc_entry: int) -> None:
        """Cancela via ação dedicada, equivalente ao `Documents.Cancel()`."""
        self._cliente.post(f"{tipo.value}({doc_entry})/Cancel", json=None)
        logger.info("%s %s cancelada.", tipo.rotulo.capitalize(), doc_entry)

    # ------------------------------------------------------------ conveniência

    def total_das_linhas(self, tipo: TipoDocumento, doc_entry: int) -> Decimal:
        """Relê o documento no SAP e soma `Quantity * Price` das linhas.

        É a conferência do que **de fato ficou gravado**, e existe porque um
        `PATCH` bem-sucedido não garante que as linhas mudaram: o Service Layer
        respondeu `204 No Content` em documentos cujas linhas continuaram como
        estavam (ver `DECISOES.md`, cotações `00125616` e `00125577`).

        Soma antes do imposto, como `total_do_payload`, para que os dois lados
        da comparação sejam a mesma grandeza — `DocTotal` inclui o imposto e não
        serviria.
        """
        documento = self._cliente.get_json(f"{tipo.value}({doc_entry})")
        total = Decimal(0)
        for linha in documento.get("DocumentLines") or ():
            quantidade = Decimal(str(linha.get("Quantity") or 0))
            preco = Decimal(str(linha.get("Price") or 0))
            total += quantidade * preco
        return total

    def cancelar_e_recriar(
        self, tipo: TipoDocumento, orcamento: str, dados: dict[str, Any]
    ) -> dict[str, Any]:
        """Cancela o documento vigente (se houver) e cria o novo.

        A ordem importa e não é intercambiável: cancelar primeiro evita que
        `buscar()` passe a encontrar dois documentos não cancelados para o mesmo
        orçamento — estado ambíguo que quebraria a idempotência das execuções
        seguintes.
        """
        atual = self.doc_entry(tipo, orcamento)
        if atual is not None:
            self.cancelar(tipo, atual)
        return self.criar(tipo, dados)
