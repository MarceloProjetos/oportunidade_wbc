"""Testes do repositório do UDO OrcDetalhe — offline, via MockTransport."""

from __future__ import annotations

import json as _json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from tests.wbc.infrastructure.test_service_layer_client import Gravador, _cliente
from wbcpython.domain.mapeamento import montar_payload_orcdetalhe
from wbcpython.infrastructure.service_layer import (
    RepositorioOrcDetalheServiceLayer,
    ServiceLayerError,
)
from wbcpython.infrastructure.wbc_sql.models import (
    DadosImpressaoWbc,
    ItemArvoreWbc,
    ItemOrcamentoWbc,
    OrcamentoWbc,
)
from wbcpython.safety import ProductionWriteBlocked

PROD = "SBOALTAMIRAPROD"


def _repo(gravador: Gravador, **kwargs: object) -> RepositorioOrcDetalheServiceLayer:
    return RepositorioOrcDetalheServiceLayer(_cliente(gravador, **kwargs))  # type: ignore[arg-type]


class TestUltimoSnapshot:
    def test_resolve_pelo_maior_docentry(self) -> None:
        """O mesmo código existe em vários DocEntry — vale o maior."""
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(200, json={"value": [{"DocEntry": 513927}]}),
                httpx.Response(200, json={"DocEntry": 513927, "U_INO_COD": "00123316"}),
            ]
        )
        snapshot = _repo(g).ultimo_snapshot("00123316")

        assert snapshot is not None
        assert snapshot["DocEntry"] == 513927
        consulta = dict(g.requisicoes[1].url.params)
        assert consulta["$orderby"] == "DocEntry desc"
        assert consulta["$top"] == "1"
        assert consulta["$filter"] == "U_INO_COD eq '00123316'"

    def test_busca_o_documento_completo_para_trazer_as_linhas(self) -> None:
        # A listagem não traz INO_ORC_LINHACollection; só o GET por chave traz.
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(200, json={"value": [{"DocEntry": 99}]}),
                httpx.Response(200, json={"DocEntry": 99, "INO_ORC_LINHACollection": [{}]}),
            ]
        )
        snapshot = _repo(g).ultimo_snapshot("00000001")
        assert snapshot is not None
        assert "INO_ORC_LINHACollection" in snapshot
        assert g.caminhos[-1] == "/b1s/v1/OrcDetalhe(99)"

    def test_codigo_inexistente_devolve_none(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(200, json={"value": []}),
            ]
        )
        assert _repo(g).ultimo_snapshot("00000000") is None

    def test_apostrofo_no_codigo_e_escapado(self) -> None:
        """Sem escape, um apóstrofo quebraria (ou alteraria) o $filter."""
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(200, json={"value": []}),
            ]
        )
        _repo(g).ultimo_snapshot("00'123")
        assert dict(g.requisicoes[1].url.params)["$filter"] == "U_INO_COD eq '00''123'"


class TestHistorico:
    def test_devolve_os_snapshots_do_mais_novo_ao_mais_antigo(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(
                    200,
                    json={"value": [{"DocEntry": 513927}, {"DocEntry": 513925}]},
                ),
            ]
        )
        historico = _repo(g).historico("00123316")
        assert [r["DocEntry"] for r in historico] == [513927, 513925]


class TestCriarSnapshot:
    def test_faz_post_e_devolve_o_criado(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(201, json={"DocEntry": 513930, "U_INO_COD": "00125537"}),
            ]
        )
        criado = _repo(g).criar_snapshot({"U_INO_COD": "00125537"})

        assert criado["DocEntry"] == 513930
        assert g.metodos[-1] == "POST"
        assert g.caminhos[-1] == "/b1s/v1/OrcDetalhe"

    def test_exige_o_codigo_do_orcamento(self) -> None:
        g = Gravador()
        with pytest.raises(ValueError, match="U_INO_COD"):
            _repo(g).criar_snapshot({"U_CLINOM": "ALGUEM"})
        assert g.requisicoes == []  # nem chegou a tentar

    def test_bloqueado_em_producao(self) -> None:
        g = Gravador()
        with pytest.raises(ProductionWriteBlocked):
            _repo(g, company_db=PROD).criar_snapshot({"U_INO_COD": "00000001"})
        assert g.requisicoes == []

    def test_resposta_nao_json_vira_erro_claro(self) -> None:
        g = Gravador(
            [
                httpx.Response(200, json={"SessionId": "abc"}),
                httpx.Response(201, text="<html>ok</html>"),
            ]
        )
        with pytest.raises(ServiceLayerError, match="não é JSON"):
            _repo(g).criar_snapshot({"U_INO_COD": "00000001"})


class TestNaoExisteAtualizacao:
    def test_repositorio_nao_expoe_metodo_de_atualizacao(self) -> None:
        """OrcDetalhe é histórico: atualizar corromperia o registro anterior."""
        publicos = [n for n in dir(RepositorioOrcDetalheServiceLayer) if not n.startswith("_")]
        for proibido in ("atualizar", "update", "editar", "modificar", "patch"):
            assert not any(n.startswith(proibido) for n in publicos), (
                f"'{proibido}' não deve existir: o UDO é append-only."
            )


class TestMapeamento:
    @staticmethod
    def _orcamento() -> OrcamentoWbc:
        return OrcamentoWbc(
            orcnum="00123316",
            revisao="C",
            sitcode=40,
            cliente_nome="BALTEAU PRODUTOS ELETRICOS",
            representante="043",
            municipio="ITAJUBA",
            uf="MG",
            percentual_comissao=Decimal("5.0"),
            valor_comissao=Decimal("3631.44"),
            base2=Decimal("3500.0"),
            retorno=Decimal("9.0"),
            indice_vendas=Decimal("1.0"),
            negociacao=Decimal(0),
            itens=(
                ItemOrcamentoWbc(
                    orcitm=1,
                    produto="PORTA-PALETE",
                    quantidade=Decimal(9),
                    valor=Decimal("100.50"),
                    texto="PORTA-PALETES",
                    id_integracao=901,
                ),
            ),
        )

    def test_mapeia_a_chave_de_negocio(self) -> None:
        payload = montar_payload_orcdetalhe(self._orcamento())
        assert payload["U_INO_COD"] == "00123316"
        # Sem revisão na impressão o campo sai vazio, mesmo com `revisao="C"`
        # no cabeçalho: são colunas diferentes. Ver `test_revisao_*` adiante.
        assert payload["U_ORCIMP_REVISAO"] == ""

    def test_campos_textuais_saem_no_formato_padronizado(self) -> None:
        # Os UDFs de texto recebem sempre ponto e 4 casas, para não perpetuar a
        # inconsistência de separador decimal existente nos dados.
        payload = montar_payload_orcdetalhe(self._orcamento())
        assert payload["U_ORCIMP_RETORNO"] == "9.0000"
        assert payload["U_ORCIMP_INDICE_VENDAS"] == "1.0000"

    def test_sequencia_do_item_sai_como_texto(self) -> None:
        # Os payloads reais trazem "1", "5", "10" — texto, não inteiro.
        linha = montar_payload_orcdetalhe(self._orcamento())["INO_ORC_LINHACollection"][0]
        assert linha["U_INO_ORCITM"] == "1"
        assert isinstance(linha["U_INO_ORCITM"], str)

    def test_sem_arvore_a_linha_leva_so_sequencia_e_texto(self) -> None:
        """Sem árvore de produtos, o legado grava apenas `ORCITM` e `ORCTXT`.

        Esta implementação chegou a gravar também quantidade, preço e total
        aqui — e o resultado *parecia* detalhado sem ser: `ORCPRDQTD` é nula em
        todas as linhas do WBC (virava 1) e `ORCVAL` é o total, não o unitário.
        Um retrato histórico com número inventado é pior que um retrato curto.
        """
        linha = montar_payload_orcdetalhe(self._orcamento())["INO_ORC_LINHACollection"][0]
        assert linha == {"U_INO_ORCITM": "1", "U_INO_ORCTXT": "PORTA-PALETES"}

    def test_orcamento_sem_itens_gera_colecao_vazia(self) -> None:
        orc = self._orcamento().model_copy(update={"itens": ()})
        assert montar_payload_orcdetalhe(orc)["INO_ORC_LINHACollection"] == []

    def test_payload_e_serializavel_em_json(self) -> None:
        # Decimal não é serializável; o mapeamento precisa converter tudo.
        _json.dumps(montar_payload_orcdetalhe(self._orcamento()))


class TestCabecalhoCompleto:
    """O snapshot é um retrato histórico — meia dúzia de campos não serve.

    Regressão de um caso real: comparado a um registro do legado, o nosso saía
    com 17 campos vazios. Todos vinham de `INTEGRACAO_ORCIMP`, que já era lida —
    faltava trazer as colunas.
    """

    def _impressao(self, **campos) -> DadosImpressaoWbc:
        base = {
            "valor_venda": Decimal("3615.70"),
            "valor_lista": Decimal("3615.70"),
            "valor_investimento": Decimal("50.44"),
            "valor_lucro": Decimal("580.06"),
            "valor_expedicao": Decimal("12.00"),
            "valor_comissao": Decimal("162.71"),
            "percentual_comissao": Decimal("4.5"),
            "valor_transporte": Decimal("36.16"),
            "valor_embalagem": Decimal("7.5"),
            "valor_montagem": Decimal("3.25"),
            "base1": Decimal("1.1"),
            "base2": Decimal("2.2"),
            "base3": Decimal("3.3"),
            "cliente_codigo": 4321,
            "contato_codigo": 77,
            "contato": "SOLICITANTE",
            "pagamento_codigo": "100% NA ENTREGA|a contra embarque",
            "pagamento_texto": "NA ENTREGA 0 PERCENTUAL 100",
            "montagem_tipo": "A combinar (não inclusa).",
            "prazo_entrega": 42,
            "revisao": "C",
            "email": "manoel@exemplo.com",
            "fone": "34 3211-6300",
            "cidade": "UBERLANDIA",
            "uf": "MG",
            "tipo_venda": "MERCANTIL",
            "transporte": "FOB- Retira em nossa Fábrica",
            "acabamento": "Cinza Padrão Altamira",
            "montagem": "Por conta do cliente.",
            "tabela_preco": "DT",
        }
        base.update(campos)
        return DadosImpressaoWbc(**base)

    def _orcamento(self, **campos):
        base = {
            "orcnum": "00125476",
            "revisao": "A",
            "sitcode": 40,
            "cliente_nome": "ADESC",
            "representante": "043",
            "municipio": "OUTRA",
            "uf": "SP",
            "impressao": self._impressao(),
        }
        base.update(campos)
        return OrcamentoWbc(**base)

    def test_valores_financeiros_sao_preenchidos(self) -> None:
        p = montar_payload_orcdetalhe(self._orcamento())
        assert p["U_ORCVALVND"] == pytest.approx(3615.70)
        assert p["U_ORCVALINV"] == pytest.approx(50.44)
        assert p["U_ORCVALLUC"] == pytest.approx(580.06)
        assert p["U_ORCVALTRP"] == pytest.approx(36.16)
        assert p["U_ORCPERCOM"] == pytest.approx(4.5)
        assert p["U_ORCVALCOM"] == pytest.approx(162.71)
        assert p["U_ORCBAS1"] == pytest.approx(1.1)
        assert p["U_ORCBAS3"] == pytest.approx(3.3)

    def test_condicoes_comerciais_e_contato(self) -> None:
        p = montar_payload_orcdetalhe(self._orcamento())
        assert p["U_PGTCOD"] == "100% NA ENTREGA|a contra embarque"
        assert p["U_ORCPGT"] == "NA ENTREGA 0 PERCENTUAL 100"
        assert p["U_TIPMONCOD"] == "A combinar (não inclusa)."
        assert p["U_PRZENT"] == 42
        assert p["U_TABELA_PRECO"] == "DT"
        assert p["U_CLICOD"] == 4321
        assert p["U_CLICONCOD"] == 77
        assert p["U_CLICON"] == "SOLICITANTE"

    def test_dados_de_impressao(self) -> None:
        p = montar_payload_orcdetalhe(self._orcamento())
        assert p["U_ORCIMP_EMAIL"] == "manoel@exemplo.com"
        assert p["U_ORCIMP_FONE"] == "34 3211-6300"
        assert p["U_ORCIMP_TIPO_VENDA"] == "MERCANTIL"
        assert p["U_ORCIMP_TRANSPORTE"] == "FOB- Retira em nossa Fábrica"
        assert p["U_ORCIMP_ACABAMENTO"] == "Cinza Padrão Altamira"
        assert p["U_ORCIMP_MONTAGEM"] == "Por conta do cliente."

    def test_revisao_do_snapshot_e_a_da_impressao(self) -> None:
        """São colunas distintas no WBC e o legado usa cada uma no seu lugar.

        `REVISAO` (cabeçalho) dirige a máquina de estados; `ORCIMP_REVISAO` é a
        que vai para o snapshot. Confundi-las mudaria decisão ou histórico.
        """
        p = montar_payload_orcdetalhe(self._orcamento())
        assert p["U_ORCIMP_REVISAO"] == "C"  # e não "A", do cabeçalho

    def test_cidade_e_uf_vem_da_impressao(self) -> None:
        p = montar_payload_orcdetalhe(self._orcamento())
        assert p["U_ORCIMP_CIDADE"] == "UBERLANDIA"
        assert p["U_ORCIMP_UF"] == "MG"

    def test_sem_impressao_cai_para_o_cabecalho(self) -> None:
        """Orçamento sem dados de impressão não pode ficar sem cidade nenhuma."""
        orc = self._orcamento(impressao=DadosImpressaoWbc())
        p = montar_payload_orcdetalhe(orc)
        assert p["U_ORCIMP_CIDADE"] == "OUTRA"
        assert p["U_ORCIMP_UF"] == "SP"

    def test_revisao_nao_cai_para_a_do_cabecalho(self) -> None:
        """São duas colunas diferentes no WBC, e o legado usa só a da impressão.

        Havia um `or orcamento.revisao` aqui. O efeito é silencioso: no
        orçamento `00125528` a impressão não tem revisão e o cabeçalho tem
        `'A'` — a produção grava vazio, e nós gravávamos `'A'`. O campo passava
        a significar uma coisa ou outra conforme o dado.
        """
        orc = self._orcamento(impressao=DadosImpressaoWbc(), revisao="A")
        assert montar_payload_orcdetalhe(orc)["U_ORCIMP_REVISAO"] == ""

    def test_revisao_da_impressao_e_a_que_vale(self) -> None:
        orc = self._orcamento(impressao=DadosImpressaoWbc(revisao="C"), revisao="A")
        assert montar_payload_orcdetalhe(orc)["U_ORCIMP_REVISAO"] == "C"

    def test_valor_de_embalagem_sai_como_texto_padronizado(self) -> None:
        p = montar_payload_orcdetalhe(self._orcamento())
        assert p["U_ORCVALEMB"] == "7.5000"


class TestTextosLongos:
    """Campos com par de continuação transbordam em vez de perder o resto."""

    def _com(self, **campos):
        return OrcamentoWbc(
            orcnum="00125476",
            sitcode=40,
            impressao=DadosImpressaoWbc(**campos),
        )

    def test_pagamento_longo_transborda_para_o_campo_2(self) -> None:
        texto = "P" * 300
        p = montar_payload_orcdetalhe(self._com(pagamento_codigo=texto))
        assert len(p["U_PGTCOD"]) == 254
        assert p["U_PGTCOD2"] == "P" * 46

    def test_acabamento_longo_transborda(self) -> None:
        """O legado corta e perde o resto; aqui o campo `_2` existe para isso."""
        p = montar_payload_orcdetalhe(self._com(acabamento="A" * 260))
        assert len(p["U_ORCIMP_ACABAMENTO"]) == 254
        assert p["U_ORCIMP_ACABAMENTO2"] == "A" * 6

    def test_texto_curto_nao_cria_campo_de_continuacao(self) -> None:
        p = montar_payload_orcdetalhe(self._com(pagamento_codigo="30 DDL"))
        assert p["U_PGTCOD"] == "30 DDL"
        assert "U_PGTCOD2" not in p

    def test_texto_vazio_e_omitido(self) -> None:
        p = montar_payload_orcdetalhe(self._com())
        assert "U_PGTCOD" not in p
        assert "U_ORCIMP_MONTAGEM" not in p

    def test_montagem_e_cortada_em_254(self) -> None:
        p = montar_payload_orcdetalhe(self._com(montagem="M" * 400))
        assert len(p["U_ORCIMP_MONTAGEM"]) == 254


class TestLinhasDaArvore:
    """Havendo árvore de produtos, é dela que saem as linhas do snapshot."""

    def _arvore(self, **campos) -> ItemArvoreWbc:
        base = {
            "orcitm": 1,
            "produto": "pplloz cj087",
            "descricao": "LONGARINA Z87 CH14 MED. 2300 MM",
            "cor": "LA-LB",
            "nivel": 1,
            "quantidade": Decimal(20),
            "total": Decimal("2425.80"),
            "peso": Decimal("165.27"),
            "id_integracao": 5001,
        }
        base.update(campos)
        return ItemArvoreWbc(**base)

    def _orcamento(self, *arvore, com_itens: bool = True):
        itens = (ItemOrcamentoWbc(orcitm=9, texto="TEXTO DO ITEM"),) if com_itens else ()
        return OrcamentoWbc(orcnum="00125476", sitcode=40, itens=itens, arvore=arvore)

    def test_a_arvore_tem_precedencia_sobre_o_texto(self) -> None:
        """Com árvore, as linhas de texto do ORCIMP não entram."""
        linhas = montar_payload_orcdetalhe(self._orcamento(self._arvore()))[
            "INO_ORC_LINHACollection"
        ]
        assert len(linhas) == 1
        assert "U_INO_ORCTXT" not in linhas[0]
        assert linhas[0]["U_INO_PROD"] == "LONGARINA Z87 CH14 MED. 2300 MM"

    def test_campos_do_detalhamento(self) -> None:
        linha = montar_payload_orcdetalhe(self._orcamento(self._arvore()))[
            "INO_ORC_LINHACollection"
        ][0]
        assert linha["U_INO_COR"] == "LA-LB"
        assert linha["U_INO_NIVEL"] == "1"
        assert linha["U_INO_Qtde"] == pytest.approx(20.0)
        assert linha["U_INO_PESO"] == pytest.approx(165.27)
        assert linha["U_INO_TOTAL"] == pytest.approx(2425.80)

    def test_codigo_sai_em_maiuscula(self) -> None:
        """Código com caixa variável cria duplicata onde não há."""
        linha = montar_payload_orcdetalhe(self._orcamento(self._arvore()))[
            "INO_ORC_LINHACollection"
        ][0]
        assert linha["U_INO_CODIGO"] == "PPLLOZ CJ087"

    def test_preco_unitario_e_o_total_dividido_pela_quantidade(self) -> None:
        linha = montar_payload_orcdetalhe(self._orcamento(self._arvore()))[
            "INO_ORC_LINHACollection"
        ][0]
        assert linha["U_INO_PRECO"] == pytest.approx(121.29)

    @pytest.mark.parametrize(
        ("quantidade", "total"),
        [(Decimal(0), Decimal(100)), (Decimal(20), Decimal(0))],
    )
    def test_preco_degrada_para_zero_em_vez_de_estourar(self, quantidade, total) -> None:
        """O legado divide sem checar a quantidade — zero daria infinito."""
        item = self._arvore(quantidade=quantidade, total=total)
        linha = montar_payload_orcdetalhe(self._orcamento(item))["INO_ORC_LINHACollection"][0]
        assert linha["U_INO_PRECO"] == 0.0

    def test_varias_linhas_preservam_a_ordem(self) -> None:
        a = self._arvore(nivel=1, produto="A")
        b = self._arvore(nivel=2, produto="B")
        linhas = montar_payload_orcdetalhe(self._orcamento(a, b))["INO_ORC_LINHACollection"]
        assert [linha["U_INO_CODIGO"] for linha in linhas] == ["A", "B"]

    def test_sem_arvore_e_sem_itens_a_colecao_fica_vazia(self) -> None:
        orc = self._orcamento(com_itens=False)
        assert montar_payload_orcdetalhe(orc)["INO_ORC_LINHACollection"] == []


class TestDataDoRetrato:
    """`U_INO_DATA` é quando o retrato foi tirado, não a data do orçamento.

    O OrcDetalhe é append-only: o mesmo orçamento gera vários registros ao
    longo do tempo, e a data é o que os distingue. Esta implementação gravava
    a data do orçamento, o que deixava todos os retratos com a mesma data —
    perdendo justamente a informação que o campo carrega. O legado grava
    `DateTime.Now`.
    """

    def _orcamento(self):
        return OrcamentoWbc(
            orcnum="00125476",
            sitcode=40,
            data_orcamento=date(2026, 8, 18),
        )

    def test_grava_a_data_da_captura(self) -> None:
        p = montar_payload_orcdetalhe(self._orcamento(), capturado_em=date(2026, 8, 26))
        assert p["U_INO_DATA"] == "2026-08-26"

    def test_nao_grava_a_data_do_orcamento(self) -> None:
        p = montar_payload_orcdetalhe(self._orcamento(), capturado_em=date(2026, 8, 26))
        assert p["U_INO_DATA"] != "2026-08-18"

    def test_sem_data_do_orcamento_o_campo_continua_saindo(self) -> None:
        """Antes o campo era omitido quando o orçamento não tinha data."""
        orc = self._orcamento().model_copy(update={"data_orcamento": None})
        p = montar_payload_orcdetalhe(orc, capturado_em=date(2026, 8, 26))
        assert p["U_INO_DATA"] == "2026-08-26"

    def test_dois_retratos_do_mesmo_orcamento_se_distinguem(self) -> None:
        a = montar_payload_orcdetalhe(self._orcamento(), capturado_em=date(2026, 8, 20))
        b = montar_payload_orcdetalhe(self._orcamento(), capturado_em=date(2026, 8, 26))
        assert a["U_INO_DATA"] != b["U_INO_DATA"]
