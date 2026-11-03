"""Models of the data read from the WBC (SQL Server, database `WBCCAD`).

They mirror the columns the legacy system actually queried (`Querys.resx` -> `GetOrcsWBC`): `INTEGRACAO_ORCLST`
(header), `INTEGRACAO_ORCIMP` (item lines and print data) and `INTEGRACAO_ORCCAB` (commercial data).
`INTEGRACAO_ORCITM` is no longer read: it stopped receiving data (see `queries.ORCAMENTO_POR_NUMERO`).

The legacy returned this flattened, with the header repeated on every item row. Here the structure is rebuilt:
one `OrcamentoWbc` with its `itens`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class ItemOrcamentoWbc(BaseModel):
    """One item line of the quote (`INTEGRACAO_ORCIMP`; `INTEGRACAO_ORCITM` is no longer read)."""

    model_config = {"frozen": True}

    orcitm: int = 0
    """Sequência do item dentro do orçamento (`ORCITM`)."""

    grupo: int = 0
    """`GRPCOD` — grupo de produto. **É a chave que escolhe o item do SAP**,
    através do de-para `@INO_GRP_PRODUTOS`. Não é o texto da linha que decide."""

    subgrupo: int = 0
    produto: str = ""
    """`ORCPRDCOD`. Vem vazio em todos os dados reais; mantido por fidelidade."""

    quantidade: Decimal | None = None
    """`ORCPRDQTD`. **`None` é o caso normal**, não a exceção: a coluna é nula
    nas 20.997 linhas da tabela. Use `quantidade_para_documento`."""

    texto: str = ""
    """`ORCTXT` — descrição livre da linha. Vai para o UDF `U_INO_D_Adicionais`
    do documento; **não** participa da escolha do item."""

    valor: Decimal = Decimal(0)
    """`ORCVAL` — valor **total** da linha, não unitário."""

    ipi: Decimal = Decimal(0)
    icms: Decimal = Decimal(0)
    id_integracao: int = 0
    """`idIntegracao_OrcImp` — identificador da linha na tabela de integração."""

    @property
    def quantidade_para_documento(self) -> Decimal:
        """Quantity to send to SAP, in three tiers.

        1. A positive `ORCPRDQTD` is honored. The column is null across the whole table today, but the rule is written
           for the day the WBC starts filling it: this is not a hard-coded `1` in disguise.
        2. Porta-paletes lines read "N Módulos" from the text (`quantidade_no_texto`). Decision of 15/09/2026: the
           PORTA-PALETES item was born in SAP as 1 unit when the quote said 14 modules.
        3. `1` for everything else: empty, null, inconsistent, zero, or a line that is not porta-paletes.

        Negative counts as inconsistent: a negative quote quantity has no business meaning and would make SAP compute
        a negative total.
        """
        if self.quantidade is not None and self.quantidade > 0:
            return self.quantidade
        # Import local de propósito: `domain.mapeamento` importa este módulo, e
        # importar `domain.linhas` no topo daqui fecharia o ciclo na carga.
        from wbcpython.domain.linhas import quantidade_no_texto

        lida = quantidade_no_texto(self.texto)
        return Decimal(lida) if lida else Decimal(1)

    @property
    def preco_unitario(self) -> Decimal:
        """Unit price: `ORCVAL` (the line total) divided by the quantity.

        Since 15/09/2026 the document does **not** carry this number: the line goes with `LineTotal = ORCVAL` and SAP
        derives the price (see `domain.linhas`). It stays here for the log and the preview, which is what people check
        against the SAP screen.
        """
        return self.valor / self.quantidade_para_documento


class DadosImpressaoWbc(BaseModel):
    """The `INTEGRACAO_ORCIMP` fields that feed the OrcDetalhe snapshot.

    Grouped here, not loose on the header, because they have a single destination: the `OrcDetalhe` UDO. The legacy
    reads them through the `NovaTabelaQuot` query.

    Some names mislead and were renamed:

    * `montagem_tipo` is `TIPMONCOD`, which is **not** a currency type: it is the assembly-type text
      (e.g. "A combinar (não inclusa).").
    * `pagamento_codigo` (`PGTCOD`) holds a description, not a code
      (e.g. "100% NA ENTREGA|a contra embarque da mercadoria").
    """

    model_config = {"frozen": True}

    valor_venda: Decimal = Decimal(0)
    valor_lista: Decimal = Decimal(0)
    valor_investimento: Decimal = Decimal(0)
    valor_lucro: Decimal = Decimal(0)
    valor_expedicao: Decimal = Decimal(0)
    valor_comissao: Decimal = Decimal(0)
    percentual_comissao: Decimal = Decimal(0)
    valor_transporte: Decimal = Decimal(0)
    valor_embalagem: Decimal = Decimal(0)
    valor_montagem: Decimal = Decimal(0)
    base1: Decimal = Decimal(0)
    base2: Decimal = Decimal(0)
    base3: Decimal = Decimal(0)
    cliente_codigo: int = 0
    contato_codigo: int = 0
    contato: str = ""
    pagamento_codigo: str = ""
    pagamento_texto: str = ""
    montagem_tipo: str = ""
    prazo_entrega: int = 0
    revisao: str = ""
    """`ORCIMP_REVISAO` — a revisão **da impressão**, distinta do `REVISAO` do
    cabeçalho que dirige a máquina de estados. O legado usa esta no OrcDetalhe
    e aquela na decisão; manter as duas separadas preserva os dois
    comportamentos."""

    email: str = ""
    fone: str = ""
    cidade: str = ""
    uf: str = ""
    tipo_venda: str = ""
    transporte: str = ""
    acabamento: str = ""
    montagem: str = ""
    tabela_preco: str = ""


class ItemArvoreWbc(BaseModel):
    """One row of the product tree (`INTEGRACAO_ORCPRDARV`).

    It is the engineering breakdown of the quote (what actually makes up each item) and supplies the OrcDetalhe
    snapshot lines when it exists. Not every quote has one: those without fall back to lines with only sequence
    and text.
    """

    model_config = {"frozen": True}

    orcitm: int = 0
    grupo: int = 0
    subgrupo: int = 0
    produto: str = ""
    nivel: int = 0
    cor: str = ""
    descricao: str = ""
    quantidade: Decimal = Decimal(0)
    total: Decimal = Decimal(0)
    peso: Decimal = Decimal(0)
    id_integracao: int = 0

    @property
    def preco_unitario(self) -> Decimal:
        """Unit price = `total / quantidade`.

        The legacy computes it only when the total is non-zero and divides without checking the quantity (infinity if
        it were zero). Here both cases degrade to zero, which is what an unknown price should be in a historical record.
        """
        if not self.total or not self.quantidade:
            return Decimal(0)
        return self.total / self.quantidade


class OrcamentoWbc(BaseModel):
    """Quote header in the WBC, with its items."""

    model_config = {"frozen": True}

    # --- INTEGRACAO_ORCLST -------------------------------------------------
    orcnum: str
    """Número do orçamento — chave de negócio da integração."""

    revisao: str = ""
    """Revisão/versão (`REVISAO`): "0", depois "A", "B", "C"…"""

    sitcode: int = 0
    """Situação do orçamento (`SITCOD`) — dirige a máquina de estados."""

    cliente_nome: str = ""
    representante: str = ""
    municipio: str = ""
    uf: str = ""
    data_orcamento: date | None = None
    data_alteracao: datetime | None = None

    # --- INTEGRACAO_ORCCAB (comercial) -------------------------------------
    percentual_comissao: Decimal = Decimal(0)
    valor_comissao: Decimal = Decimal(0)
    base2: Decimal = Decimal(0)

    # --- INTEGRACAO_ORCIMP (impressão) -------------------------------------
    data_ultima_alteracao_impressao: datetime | None = None
    retorno: Decimal = Decimal(0)
    negociacao: Decimal = Decimal(0)
    indice_vendas: Decimal = Decimal(0)

    itens: tuple[ItemOrcamentoWbc, ...] = Field(default_factory=tuple)

    impressao: DadosImpressaoWbc = Field(default_factory=DadosImpressaoWbc)
    """Campos de impressão que alimentam o snapshot do OrcDetalhe."""

    arvore: tuple[ItemArvoreWbc, ...] = Field(default_factory=tuple)
    """Árvore de produtos, quando existe. Vazia é o caso comum."""

    @property
    def quantidade_total_itens(self) -> Decimal:
        """Sum of the item quantities (`quantidade_para_documento`).

        Equivalent to the legacy `item.OrcProdQuantidade` passed on to quote and order creation.
        """
        return sum((item.quantidade_para_documento for item in self.itens), Decimal(0))
