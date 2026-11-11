"""Data models of the Pedidos WBC module.

Equivalent to the classes in `Models/`: `OportunidadeDoc`, `EstruturaPrd`, `SemiAcabado`,
`ORCCAB`, `ORCPRD`, `NewOrcPrd`/`OrcPrdLinha`.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OportunidadeDoc(BaseModel):
    """Equivalent to `Models/OportunidadeDoc.cs` — one row of the closed quote in the WBC."""

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
    """Equivalent to `Models/EstruturaPrd.cs` — one node of the quote's structure tree
    (`INTEGRACAO_ORCPRDARV`). Central object of the semi-finished recursion
    (`ProcessDefault.ChecaSemiAcabado`); see section 4.4.1, step 6, of migration_guide.md.
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
    """Equivalent to `Models/NewOrcPrd.cs` class `OrcPrdLinha` — a child row of the UDO
    `OrcDetalhe`/`INO_ORC_LINHA`, on the `preencheTabela` fallback path (quote without a
    detailed structure in `INTEGRACAO_ORCPRDARV`). Comes from `NOVA_TABELA_QUOT_LINHA`; in
    the legacy only `codigo_item`/`orc_val`/`orc_itm`/`orc_txt` are really filled and the rest
    stay empty (replicated on purpose, not a transcription gap).
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
    """Equivalent to `Models/NewOrcPrd.cs` — quote header from `INTEGRACAO_ORCIMP` (query
    `NOVA_TABELA_QUOT`) or, when that row is on another revision, from `_cabecalho_da_revisao`; used by
    `preencheTabela` to build the header fields of the UDO
    `OrcDetalhe`. On the fallback path each instance also carries its own `linhas` (see
    `OrcPrdLinha`).
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
    """Equivalent to `Models/ORCPRD.cs` — one row of the quote's detailed structure from
    `INTEGRACAO_ORCPRDARV` (query `GET_TABLE_VALDIXSON`), used by `preencheTabela` on the main
    path (when a detailed structure exists).
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
    """Equivalent to `Models/EstruturaPrd.cs` class `Linha` — manual order-line data (query
    `MANUAL_LINHA`), used by `UpdatePedido`/`UpdateTabPedidoCong` to restore fields the user
    filled in by hand before the reprocessing."""

    ped_cliente: str
    item_cliente: str
    nf: str
    cor: str


class SemiAcabado(BaseModel):
    """Equivalent to `Models/EstruturaPrd.cs` class `SemiAcabado`."""

    item_code: str
    quantidade: float
    peso: float
    id_int: int
    linha_orc: str


class PedidoParaIntegrar(BaseModel):
    """One row of the `Form2` grid, built by `service.buscar_pedidos_para_integrar` for both
    modes: "Pedidos Novos" (`BuscaPedidosParaIntegrar`: pedidos linked to an integrated
    Oportunidade and not yet processed, `U_INO_ProcessWBC='N'`, candidates for
    `pedidos-wbc processar-novos`) and "Pedidos Integrados" (`BuscaPedidosIntegrados`,
    `U_INO_ProcessWBC='Y'`, candidates for Reprocessar).

    Model and listing added 15/09/2026 (Anderson's request, to list homologation pedidos to
    test); the query itself was already transcribed from the original C#."""

    selecionar: str = "N"
    opp_id: int
    doc_num: int
    cod_cliente: str
    nome_cliente: str
    total_pedido: float
    data_lancamento: str
    # Current revision of the quote in the WBC; empty on the first one or with the WBC down.
    versao: str = ""
    orc_num_masc: str
    #: `ORDR.U_U_INO_NotaEspelho = 'Y'` — a mirror order; the screen flags it.
    nota_espelho: bool = False
    #: Closed by the future-delivery NF with the remessa still pending ("Pedidos novos" only);
    #: the screen flags it. Its lines are closed, so they never get `U_INO_OP`.
    entrega_futura: bool = False
    #: Live OPs left on a CANCELLED order of the same quote, ``{"pedido", "op", "status"}``.
    #: Not empty = Processar is refused (`acoes.monta_plano`). Filled for "Pedidos novos" only.
    ops_orfas: list[dict] = []
