"""Modelos de dados do módulo Pedidos WBC.

Equivalentes às classes em `Models/`: `OportunidadeDoc`, `EstruturaPrd`, `SemiAcabado`,
`ORCCAB`, `ORCPRD`, `NewOrcPrd`/`OrcPrdLinha`.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OportunidadeDoc(BaseModel):
    """Equivalente a `Models/OportunidadeDoc.cs` — uma linha do orçamento fechado no WBC."""

    orc_num: str
    versao: str
    sit_code: int
    cli_nom: str
    recp_cod: str
    cli_mun: str
    est_cod: str
    orc_date: datetime
    orc_alt_dth: datetime
    grp_code: int
    sub_grup_code: int
    orc_item: int
    orc_prod_code: str
    orc_prod_quantidade: float
    orc_txt: str
    orc_val: float
    orc_ipi: float
    orc_icm: float
    id_integracao: int


class EstruturaPrd(BaseModel):
    """Equivalente a `Models/EstruturaPrd.cs` — um nó da árvore de estrutura do orçamento
    (`INTEGRACAO_ORCPRDARV`). É o objeto central da recursão de semiacabados
    (`ProcessDefault.ChecaSemiAcabado`) — ver seção 4.4.1, passo 6, do migration_guide.md.
    """

    orc_num: str
    grp_code: int
    sub_group_cod: int
    orc_itm: int
    prd_code: str
    nivel: int
    cor_cod: str
    prd_desc: str
    quantidade: float
    total: float
    peso: float
    id_integracao_orc: int
    linha_orc: str
    prd_arv: str

    # `linha` (nº da linha do pedido de venda já criada, distinto de `linha_orc`) —
    # usado por `CriaOP`/`ChecaSemiAcabado`/`AtualizaDoc`. É preenchido depois que a
    # linha do pedido é conhecida (equivalente a `EstruturaPrd.linha` no C#).
    linha: int = 0

    # campos de controle usados durante o processamento (não vêm do banco)
    processado_itens: str | None = None
    estrutura_final: str | None = None


class OrcPrdLinha(BaseModel):
    """Equivalente a `Models/NewOrcPrd.cs` classe `OrcPrdLinha` — uma linha filha do UDO
    `OrcDetalhe`/`INO_ORC_LINHA`, no caminho de fallback de `preencheTabela` (quando o
    orçamento não tem estrutura detalhada em `INTEGRACAO_ORCPRDARV`). Vem da query
    `NOVA_TABELA_QUOT_LINHA` — só `codigo_item`/`orc_val`/`orc_itm`/`orc_txt` vêm
    preenchidos de verdade no legado; os demais campos ficam vazios (comportamento
    replicado de propósito, não é lacuna da transcrição).
    """

    peso: str
    codigo_item: int
    quantidade: str
    produto: str
    cor: str
    preco: str
    orc_val: float
    nivel: str
    linha: str
    orc_itm: int
    orc_txt: str


class NewOrcPrd(BaseModel):
    """Equivalente a `Models/NewOrcPrd.cs` — cabeçalho do orçamento vindo de
    `INTEGRACAO_ORCIMP` (query `NOVA_TABELA_QUOT`), usado por `preencheTabela` para
    montar os campos de cabeçalho do UDO `OrcDetalhe`. Cada instância carrega também
    sua própria lista de linhas (`linhas`) no caminho de fallback (ver `OrcPrdLinha`).
    """

    orc_val_vnd: float
    orc_val_lst: float
    orc_val_inv: float
    orc_val_luc: float
    orc_val_exp: float
    orc_val_com: float
    orc_per_com: float
    rep_cod: str
    cli_cod: int
    cli_nom: str
    cli_con_cod: int
    cli_con: str
    orc_val_trp: float
    orc_val_emb: float
    orc_val_mon: float
    pgt_cod: str
    tip_mon_cod: str
    przent: int
    orc_bas1: float
    orc_bas2: float
    orc_bas3: float
    orc_pgt: str
    orc_imp_revisao: str
    orc_imp_email: str
    orc_imp_fone: str
    orc_imp_cidade: str
    orc_imp_uf: str
    orc_imp_tipo_venda: str
    orc_imp_transporte: str
    orc_imp_acabamento: str
    orc_imp_montagem: str
    tabela_preco: str
    orc_imp_retorno: str
    orc_imp_indice_vendas: str
    orc_imp_negociacao: str
    linhas: list[OrcPrdLinha] = []


class TabelaValdixsonLinha(BaseModel):
    """Equivalente a `Models/ORCPRD.cs` — uma linha da estrutura detalhada do orçamento
    vinda de `INTEGRACAO_ORCPRDARV` (query `GET_TABLE_VALDIXSON`), usada por
    `preencheTabela` no caminho principal (quando existe estrutura detalhada).
    """

    orc_num: str
    grp_code: int
    sub_grup_code: int
    orc_item: int
    orc_prod_code: str
    orc_prd_arv_nivel: int
    cor_code: str
    prd_desc: str
    orc_qtd: float
    orc_tot: float
    orc_pes: float
    id_integracao_orc_prd: int


class Linha(BaseModel):
    """Equivalente a `Models/EstruturaPrd.cs` classe `Linha` — dados manuais da linha do
    pedido (query `MANUAL_LINHA`), usados por `UpdatePedido`/`UpdateTabPedidoCong` para
    reconstituir campos que o usuário preencheu manualmente antes do reprocessamento."""

    ped_cliente: str
    item_cliente: str
    nf: str
    cor: str


class SemiAcabado(BaseModel):
    """Equivalente a `Models/EstruturaPrd.cs` classe `SemiAcabado`."""

    item_code: str
    quantidade: float
    peso: float
    id_int: int
    linha_orc: str


class OrccCab(BaseModel):
    """Equivalente a `Models/ORCCAB.cs`."""

    orc_per_com: float
    orc_val_com: float
    tip_mon_cod: str
    orc_bas3: float
    rep_cod: str
    przent: int
    pgt_cod: str


class PedidoParaIntegrar(BaseModel):
    """Uma linha do grid "Pedidos Novos" do `Form2` (query `BuscaPedidosParaIntegrar`) —
    pedidos já vinculados a uma oportunidade integrada e ainda não processados
    (`U_INO_ProcessWBC='N'`), candidatos a `pedidos-wbc processar-novos`.

    ⚠️ Modelo/consulta adicionados em 15/09/2026 (a pedido do Anderson, para listar pedidos
    de homologação a testar) — não existiam antes nesta reescrita; a query em si já vinha
    transcrita do C# original."""

    selecionar: str = "N"
    opp_id: int
    doc_num: int
    cod_cliente: str
    nome_cliente: str
    total_pedido: float
    data_lancamento: str
    orc_num_masc: str
    #: `ORDR.U_U_INO_NotaEspelho = 'Y'` — a mirror order; the screen flags it.
    nota_espelho: bool = False
