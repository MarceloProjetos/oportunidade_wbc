"""Modelos dos dados lidos do WBC (SQL Server, banco WBCCAD).

Espelham as colunas realmente consultadas pelo sistema legado
(`Querys.resx` → `GetOrcsWBC`), que faz um join entre `INTEGRACAO_ORCLST`
(cabeçalho), `INTEGRACAO_ORCITM` (itens), `INTEGRACAO_ORCIMP` (dados de
impressão) e `INTEGRACAO_ORCCAB` (dados comerciais).

O legado devolve isso achatado — o cabeçalho repetido em cada linha de item.
Aqui a estrutura é reconstituída: um `OrcamentoWbc` com seus `itens`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class ItemOrcamentoWbc(BaseModel):
    """Uma linha de item do orçamento (`INTEGRACAO_ORCITM`)."""

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
        """Quantidade a enviar ao SAP, com o fallback para 1.

        Regra definida pelo negócio: **a quantidade é 1 sempre que `ORCPRDQTD`
        vier vazia, nula, inconsistente ou zero** — e só nesse caso. Quando
        houver um número positivo, ele é respeitado.

        Hoje o fallback é o caminho único (a coluna é nula em toda a tabela),
        mas a regra está escrita para o dia em que o WBC passar a preenchê-la:
        não é um `1` fixo disfarçado.

        Negativo conta como inconsistente — quantidade negativa em cotação não
        tem significado de negócio e faria o SAP calcular um total negativo.
        """
        if self.quantidade is None or self.quantidade <= 0:
            return Decimal(1)
        return self.quantidade

    @property
    def preco_unitario(self) -> Decimal:
        """Preço unitário: `ORCVAL` é o total da linha, o SAP quer o unitário.

        Dividir preserva o total da linha qualquer que seja a quantidade — que é
        o que o legado faz (`Price = orcVal / U_INO_Qtde`) e o que mantém o
        documento com o mesmo valor do orçamento no WBC.
        """
        return self.valor / self.quantidade_para_documento


class DadosImpressaoWbc(BaseModel):
    """Os campos de `INTEGRACAO_ORCIMP` que alimentam o snapshot do OrcDetalhe.

    Ficam agrupados aqui, e não soltos no cabeçalho, porque têm um destino só:
    o UDO `OrcDetalhe`. O legado os lê pela consulta `NovaTabelaQuot`.

    Alguns nomes enganam e por isso estão renomeados:

    * `montagem_tipo` é o `TIPMONCOD`, que **não** é tipo de moeda — é o texto
      do tipo de montagem (ex.: "A combinar (não inclusa).").
    * `pagamento_codigo` (`PGTCOD`) traz descrição, não código
      (ex.: "100% NA ENTREGA|a contra embarque da mercadoria").
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
    """Uma linha da árvore de produtos (`INTEGRACAO_ORCPRDARV`).

    É o detalhamento de engenharia do orçamento — o que de fato compõe cada
    item — e é daqui que saem as linhas do snapshot do OrcDetalhe quando existe
    árvore. Nem todo orçamento tem: os que não têm caem no caminho alternativo,
    com linhas apenas de sequência e texto.
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
        """Preço unitário = total ÷ quantidade.

        O legado calcula assim, mas só quando o total é diferente de zero — e
        divide sem verificar a quantidade, o que produziria infinito se ela
        fosse zero. Aqui os dois casos degradam para zero, que é o que um preço
        desconhecido deve ser num registro histórico.
        """
        if not self.total or not self.quantidade:
            return Decimal(0)
        return self.total / self.quantidade


class OrcamentoWbc(BaseModel):
    """Cabeçalho do orçamento no WBC, com seus itens."""

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
        """Soma das quantidades dos itens.

        Equivale ao `item.OrcProdQuantidade` que o legado passa adiante para a
        criação de cotação e pedido.
        """
        return sum((item.quantidade_para_documento for item in self.itens), Decimal(0))
