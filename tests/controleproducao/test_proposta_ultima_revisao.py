"""The order detail always goes on the quote's current revision, with no print needed.

Pedido 84530 (00125273, 04/11/2026): quote on H, `INTEGRACAO_ORCIMP` still on G. The proposal row
only advances when someone prints the proposal, so a row on another revision is replaced by what the
WBC keeps per revision. A row on the current revision stays as it is.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from controleproducao.modules.pedidos_wbc import acoes, service
from controleproducao.modules.pedidos_wbc import queries as q
from tests.controleproducao.test_api_pedidos_wbc import (  # noqa: F401 - pytest fixtures
    API,
    CABECALHO,
    _limpa_registros,
    ambiente,
    c,
    lista,
)

ORC = "00125273"
PREENCHE_VERSOES = acoes.preenche_versoes


def _proposta(revisao="G") -> list:
    """The positional row of `NOVA_TABELA_QUOT`, on an old revision."""
    return [
        Decimal("366397.44"), Decimal("366397.44"), 1, 2, 0, 3, Decimal("1.5"), "012", 0, "ALTHAIA", 0, "PATRICIO",
        0, 0, 0, "40% antigo", ".", 38, 9000, 10000, 0, "pagamento antigo", revisao, "velho@x", "- -", "CIDADE",
        "SP", "ANTIGA", "[CR]Transporte antigo[CR]", "[CR][CR][CR][CR]Branco[CR]", "[CR]Montagem antiga[CR]",
        "XX", "1.0000", "9.0000", "9.0000",
    ]


CADASTRO = {
    "orccab_total": Decimal("345904.46"), "OrcCabPrecoLista": Decimal("342000"),
    "ORCCAB_CONTATO_EMAIL": "patricio@x", "orccab_fone": "11", "orccab_municipio": "POÇOS DE CALDAS",
    "orccab_UF": "MG", "TIPOVENDA": "MERCANTIL", "lista_precos": "DT",
}
VALORES = {
    "ORCVALINV": 2956, "ORCVALLUC": 33994, "ORCVALEXP": 0, "ORCVALCOM": 5188, "ORCPERCOM": Decimal("1.5"),
    "REPCOD": "012", "CLICOD": None, "CLINOM": "ALTHAIA", "CLICONCOD": None, "CLICON": "PATRICIO",
    "ORCVALTRP": 0, "ORCVALEMB": 0, "ORCVALMON": 0, "PGTCOD": "40% novo", "TIPMONCOD": ".", "PRZENT": 38,
    "ORCBAS1": 9000, "ORCBAS2": 10000, "ORCBAS3": 0, "ORCPGT": "pagamento novo",
}
TEXTOS = [
    {"varusrcodigo": "ACABAMENTO ", "varusrvalor": "Cinza Padrão\r\nsegunda linha"},
    {"varusrcodigo": "ADIC_001", "varusrvalor": "adicional"},
    {"varusrcodigo": "TRANSPORTE", "varusrvalor": "Por conta da Altamira"},
    {"varusrcodigo": "TXT_001", "varusrvalor": "R$ 55.000,00"},
    {"varusrcodigo": "MONTAGEM", "varusrvalor": "."},
    {"varusrcodigo": "TXT_002", "varusrvalor": None},
]
CALCULO = [{"Variavel": "[retorno]", "Valor": "6,2"}, {"Variavel": "[NEGOCIACAO]", "Valor": "0"}]


def _wbc(revisao="H", proposta=None, cadastro=(CADASTRO,), valores=(VALORES,)):
    respostas = {
        q.REVISAO_DO_ORCAMENTO: [{"REVISAO": revisao}], q.CADASTRO_DA_REVISAO: list(cadastro),
        q.VALORES_DO_ORCAMENTO: list(valores), q.TEXTOS_DA_REVISAO: TEXTOS, q.CALCULO_DA_REVISAO: CALCULO,
    }
    wbc = MagicMock()
    wbc.fetch_all = MagicMock(side_effect=lambda sql, _params: respostas[sql])
    wbc.fetch_all_values = MagicMock(
        side_effect=lambda sql, _params: [proposta or _proposta()] if sql == q.NOVA_TABELA_QUOT else [[
            "", 6, "", "", "", "", 137949, "", "", 1, "PAINEIS",
        ]]
    )
    return wbc


def _header(wbc):
    return asyncio.run(service._busca_header_nova_tabela_quot(wbc, ORC))


def test_proposta_atrasada_sai_na_revisao_do_orcamento(caplog) -> None:
    with caplog.at_level("INFO", logger=service.__name__):
        h = _header(_wbc())

    assert h.orc_imp_revisao == "H"
    assert h.orc_val_vnd == pytest.approx(345904.46) and h.orc_val_lst == 342000
    assert h.orc_pgt == "pagamento novo" and h.pgt_cod == "40% novo"
    assert h.orc_imp_email == "patricio@x" and h.orc_imp_uf == "MG"
    assert h.orc_imp_tipo_venda == "MERCANTIL" and h.tabela_preco == "DT"
    assert "Mudou: valor de venda, acabamento/cor, montagem, transporte, pagamento." in caplog.text


def test_textos_saem_no_formato_da_impressao() -> None:
    h = _header(_wbc())
    assert h.orc_imp_acabamento == "adicional[CR][CR][CR][CR]Cinza Padrão[CR]segunda linha[CR]"
    assert h.orc_imp_transporte == "[CR]Por conta da Altamira[CR]"
    assert h.orc_imp_montagem == "R$ 55.000,00[CR].[CR]"


def test_numeros_do_calculo_aceitam_virgula_e_nome_minusculo() -> None:
    h = _header(_wbc())
    assert h.orc_imp_retorno == "6.2000" and h.orc_imp_negociacao == "0.0000"
    assert h.orc_imp_indice_vendas == f"{345904.46 / 342000:.4f}"


def test_sem_a_variavel_fica_o_unico_valor_que_existe() -> None:
    calculo_sem_retorno = [CALCULO[1]]
    wbc = _wbc()
    original = wbc.fetch_all.side_effect
    wbc.fetch_all.side_effect = lambda sql, p: calculo_sem_retorno if sql == q.CALCULO_DA_REVISAO else original(sql, p)
    assert _header(wbc).orc_imp_retorno == "1.0000"


def test_le_a_revisao_pela_chave_com_a_letra() -> None:
    wbc = _wbc()
    _header(wbc)
    chaves = {chamada.args[0]: chamada.args[1] for chamada in wbc.fetch_all.call_args_list}
    assert chaves[q.CADASTRO_DA_REVISAO] == (f"{ORC}H",) and chaves[q.TEXTOS_DA_REVISAO] == (f"{ORC}H",)
    assert chaves[q.VALORES_DO_ORCAMENTO] == (ORC,)


def test_proposta_em_dia_nao_muda_nada() -> None:
    wbc = _wbc(revisao="G")
    h = _header(wbc)
    assert h.orc_imp_revisao == "G" and h.orc_imp_acabamento == "[CR][CR][CR][CR]Branco[CR]"
    assert wbc.fetch_all.call_count == 1


def test_proposta_sem_revisao_vale_como_atrasada() -> None:
    """`ISNULL(ORCIMP_REVISAO,0)`: the row printed before the first revision comes as "0"."""
    assert _header(_wbc(revisao="A", proposta=_proposta("0"))).orc_imp_revisao == "A"


def test_primeira_revisao_sem_letra_com_proposta_sem_revisao_nao_muda() -> None:
    assert _header(_wbc(revisao="", proposta=_proposta("0"))).orc_imp_acabamento.endswith("Branco[CR]")


@pytest.mark.parametrize("faltando", [{"cadastro": ()}, {"valores": ()}])
def test_revisao_que_nao_se_le_falha_em_vez_de_mandar_a_antiga(faltando) -> None:
    with pytest.raises(ValueError, match="última revisão"):
        _header(_wbc(**faltando))


def test_caminho_sem_estrutura_le_as_linhas_da_tabela_atual() -> None:
    wbc = _wbc()
    (h,) = asyncio.run(service._busca_nova_tabela_quot_com_linhas(wbc, ORC))
    assert h.orc_imp_revisao == "H"
    assert wbc.fetch_all_values.call_args_list[-1].args[0] == q.NOVA_TABELA_QUOT_LINHA_ATUAL


def test_caminho_sem_estrutura_em_dia_le_as_linhas_da_proposta() -> None:
    wbc = _wbc(revisao="G")
    asyncio.run(service._busca_nova_tabela_quot_com_linhas(wbc, ORC))
    assert wbc.fetch_all_values.call_args_list[-1].args[0] == q.NOVA_TABELA_QUOT_LINHA


def test_consultas_novas_so_leem() -> None:
    for sql in (q.REVISAO_DO_ORCAMENTO, q.CADASTRO_DA_REVISAO, q.VALORES_DO_ORCAMENTO, q.TEXTOS_DA_REVISAO,
                q.CALCULO_DA_REVISAO, q.NOVA_TABELA_QUOT_LINHA_ATUAL, q.PEDIDO_CORRIGIDO_A_MAO):
        assert sql.lstrip().upper().startswith("SELECT")


def test_plano_nao_traz_mais_a_proposta() -> None:
    assert not hasattr(acoes, "avisos_do_plano") and not hasattr(acoes, "le_propostas_desatualizadas")


class TestColunaVersao:
    REAL = PREENCHE_VERSOES

    def _pedido(self, orc):
        return SimpleNamespace(orc_num_masc=orc, versao="")

    def test_le_a_revisao_de_cada_orcamento_sem_repetir(self) -> None:
        wbc = MagicMock()
        wbc.fetch_all = MagicMock(
            return_value=[{"ORCNUM": ORC, "REVISAO": "H "}, {"ORCNUM": "00120000", "REVISAO": None}]
        )
        assert service.revisoes_dos_orcamentos(wbc, [ORC, ORC, "00120000"]) == {ORC: "H", "00120000": ""}
        sql, params = wbc.fetch_all.call_args.args
        assert sql.lstrip().upper().startswith("SELECT") and list(params) == [ORC, "00120000"]

    def test_pagina_vazia_nao_consulta(self) -> None:
        wbc = MagicMock()
        assert service.revisoes_dos_orcamentos(wbc, []) == {}
        wbc.fetch_all.assert_not_called()

    def test_preenche_os_pedidos(self) -> None:
        pedidos = [self._pedido(ORC), self._pedido("00120000")]
        cliente = MagicMock()
        cliente.__enter__.return_value = MagicMock(fetch_all=MagicMock(return_value=[{"ORCNUM": ORC, "REVISAO": "H"}]))
        with patch(f"{acoes.__name__}.WbcSqlServerClient", return_value=cliente):
            type(self).REAL(pedidos)
        assert [p.versao for p in pedidos] == ["H", ""]

    def test_wbc_fora_nao_derruba_a_lista(self) -> None:
        pedidos = [self._pedido(ORC)]
        with patch(f"{acoes.__name__}.WbcSqlServerClient", side_effect=OSError("WBC fora")):
            type(self).REAL(pedidos)
        assert pedidos[0].versao == ""


def _com_versao(pedidos) -> None:
    pedidos[0].versao = "H"


def test_tela_mostra_a_coluna_e_traco_sem_versao(c, ambiente) -> None:  # noqa: F811
    with patch.object(acoes, "le_pedidos", AsyncMock(return_value=ambiente)), \
         patch.object(acoes, "preenche_versoes", _com_versao):
        tela = c.get("/pedidos-wbc?buscar=1").text

    assert ">Versão</th>" in tela
    assert '<td class="centro">H</td>' in tela and '<td class="centro">—</td>' in tela


def test_api_traz_a_versao(c, ambiente) -> None:  # noqa: F811
    with patch.object(acoes, "le_pedidos", AsyncMock(return_value=ambiente)), \
         patch.object(acoes, "preenche_versoes", _com_versao):
        pedidos = c.get(f"{API}/pedidos", headers=CABECALHO).json()["pedidos"]

    assert pedidos[0]["versao"] == "H" and pedidos[1]["versao"] == ""


class TestCarimboDaRevisao:
    """A hand-corrected frozen order is never stamped by the worker: Processar does it."""

    def _oport(self):
        return [MagicMock(versao="H")]

    def _hana(self, n):
        hana = MagicMock()
        hana.fetch_all = MagicMock(return_value=[{"N": n}])
        return hana

    def test_corrigido_a_mao_recebe_a_revisao(self) -> None:
        assert asyncio.run(service._revisao_que_o_worker_nao_carimba(
            self._hana(1), self._oport(), "20769", "Y")) == "H"

    def test_outro_pedido_fica_com_o_worker(self) -> None:
        assert asyncio.run(service._revisao_que_o_worker_nao_carimba(
            self._hana(0), self._oport(), "20769", "Y")) is None

    def test_reprocessar_nao_carimba(self) -> None:
        hana = self._hana(1)
        assert asyncio.run(service._revisao_que_o_worker_nao_carimba(hana, self._oport(), "20769", "N")) is None
        hana.fetch_all.assert_not_called()

    def test_grava_junto_com_os_campos_de_controle(self) -> None:
        sl = MagicMock()
        sl.update_entity = AsyncMock()
        asyncio.run(service._update_tab_pedido_cong(sl, self._oport(), ORC, "20769", 547800, "Y", "H"))
        campos = sl.update_entity.await_args.args[2]
        assert campos["U_INO_VERSAOWBC"] == "H" and campos["U_INO_ORCAMENTO"] == 547800

    def test_sem_versao_nao_grava_o_campo(self) -> None:
        sl = MagicMock()
        sl.update_entity = AsyncMock()
        asyncio.run(service._update_tab_pedido_cong(sl, self._oport(), ORC, "20769", 547800, "Y"))
        assert "U_INO_VERSAOWBC" not in sl.update_entity.await_args.args[2]
