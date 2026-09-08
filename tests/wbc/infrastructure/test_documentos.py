"""Testes de Cotações, Pedidos e Oportunidades — offline, via MockTransport."""

from __future__ import annotations

import httpx
import pytest

from tests.wbc.infrastructure.test_service_layer_client import Gravador, _cliente
from wbcpython.infrastructure.service_layer.documentos import (
    RepositorioDocumentosVendaServiceLayer,
    TipoDocumento,
    esta_congelado_pelo_campo,
)
from wbcpython.infrastructure.service_layer.errors import ServiceLayerError
from wbcpython.infrastructure.service_layer.oportunidades import (
    STATUS_PERDIDA,
    RepositorioOportunidadesServiceLayer,
)
from wbcpython.safety import ProductionWriteBlocked

PROD = "SBOALTAMIRAPROD"
LOGIN = httpx.Response(200, json={"SessionId": "abc"})


def _docs(g: Gravador, **kw: object) -> RepositorioDocumentosVendaServiceLayer:
    return RepositorioDocumentosVendaServiceLayer(_cliente(g, **kw))  # type: ignore[arg-type]


def _oport(g: Gravador, **kw: object) -> RepositorioOportunidadesServiceLayer:
    return RepositorioOportunidadesServiceLayer(_cliente(g, **kw))  # type: ignore[arg-type]


class TestBuscaDeDocumentos:
    @pytest.mark.parametrize("tipo", list(TipoDocumento))
    def test_filtra_por_orcamento_e_ignora_cancelados(self, tipo: TipoDocumento) -> None:
        """Cancelado não conta: senão a integração nunca recriaria o anulado."""
        g = Gravador([LOGIN, httpx.Response(200, json={"value": []})])
        _docs(g).buscar(tipo, "00123316")

        filtro = dict(g.requisicoes[-1].url.params)["$filter"]
        assert "U_INO_COTWBC eq '00123316'" in filtro
        assert "Cancelled eq 'tNO'" in filtro
        assert g.caminhos[-1] == f"/b1s/v1/{tipo.value}"

    def test_existe_reflete_a_presenca(self) -> None:
        g = Gravador([LOGIN, httpx.Response(200, json={"value": [{"DocEntry": 1}]})])
        assert _docs(g).existe(TipoDocumento.COTACAO, "X") is True

        g2 = Gravador([LOGIN, httpx.Response(200, json={"value": []})])
        assert _docs(g2).existe(TipoDocumento.COTACAO, "X") is False

    def test_revisao_aplicada(self) -> None:
        g = Gravador(
            [LOGIN, httpx.Response(200, json={"value": [{"DocEntry": 1, "U_INO_VERSAOWBC": "B"}]})]
        )
        assert _docs(g).revisao_aplicada(TipoDocumento.COTACAO, "X") == "B"

    def test_revisao_ausente_vira_string_vazia(self) -> None:
        # É assim que a máquina de estados representa "sem revisão".
        g = Gravador(
            [LOGIN, httpx.Response(200, json={"value": [{"DocEntry": 1, "U_INO_VERSAOWBC": None}]})]
        )
        assert _docs(g).revisao_aplicada(TipoDocumento.COTACAO, "X") == ""

    def test_sem_documento_a_revisao_e_vazia(self) -> None:
        g = Gravador([LOGIN, httpx.Response(200, json={"value": []})])
        assert _docs(g).revisao_aplicada(TipoDocumento.PEDIDO, "X") == ""

    def test_esta_fechado_le_bost_close(self) -> None:
        """O valor é o que o SAP devolve de verdade, não um palpite.

        Conferido contra o ambiente real no pedido `83988` (orçamento
        `00124268`): `DocumentStatus = 'bost_Close'`, com `Cancelled = 'tNO'` —
        fechado, não cancelado. No Service Layer o campo é `DocumentStatus`; na
        consulta do HANA a mesma informação vem como `DocStatus` em `'O'`/`'C'`.
        """
        g = Gravador(
            [
                LOGIN,
                httpx.Response(
                    200,
                    json={"value": [{"DocEntry": 17528, "DocumentStatus": "bost_Close"}]},
                ),
            ]
        )
        assert _docs(g).esta_fechado(TipoDocumento.PEDIDO, "00124268") is True

    def test_esta_fechado_e_falso_com_bost_open(self) -> None:
        g = Gravador(
            [
                LOGIN,
                httpx.Response(
                    200, json={"value": [{"DocEntry": 1, "DocumentStatus": "bost_Open"}]}
                ),
            ]
        )
        assert _docs(g).esta_fechado(TipoDocumento.PEDIDO, "X") is False

    def test_sem_documento_nao_e_fechado(self) -> None:
        """ "Não existe" não é "está fechado": confundir os dois impediria a
        criação do primeiro pedido."""
        g = Gravador([LOGIN, httpx.Response(200, json={"value": []})])
        assert _docs(g).esta_fechado(TipoDocumento.PEDIDO, "X") is False


class TestCriacao:
    def test_cria_e_devolve_o_documento(self) -> None:
        g = Gravador([LOGIN, httpx.Response(201, json={"DocEntry": 5, "DocNum": 900})])
        criado = _docs(g).criar(TipoDocumento.COTACAO, {"U_INO_COTWBC": "00123316"})
        assert criado["DocNum"] == 900
        assert g.metodos[-1] == "POST"

    def test_exige_o_vinculo_com_o_orcamento(self) -> None:
        """Sem U_INO_COTWBC o documento fica órfão e seria duplicado depois."""
        g = Gravador()
        with pytest.raises(ValueError, match="U_INO_COTWBC"):
            _docs(g).criar(TipoDocumento.PEDIDO, {"CardCode": "C001"})
        assert g.requisicoes == []

    def test_bloqueado_em_producao(self) -> None:
        g = Gravador()
        with pytest.raises(ProductionWriteBlocked):
            _docs(g, company_db=PROD).criar(TipoDocumento.COTACAO, {"U_INO_COTWBC": "X"})
        assert g.requisicoes == []


class TestAtualizacaoECancelamento:
    def test_atualizar_usa_patch_por_chave(self) -> None:
        g = Gravador([LOGIN, httpx.Response(204)])
        _docs(g).atualizar(TipoDocumento.COTACAO, 42, {"Comments": "x"})
        assert g.metodos[-1] == "PATCH"
        assert g.caminhos[-1] == "/b1s/v1/Quotations(42)"

    def test_atualizar_substitui_a_colecao_de_linhas(self) -> None:
        """Sem este cabeçalho o `PATCH` **mescla**: linhas velhas sobrevivem.

        Observado em homologação — a cotação 101857 ficou com três linhas
        (R$ 69.656,20) para um orçamento de duas (R$ 52.079,03). Numa cotação
        que vai ao cliente, é valor errado no documento.
        """
        g = Gravador([LOGIN, httpx.Response(204)])
        _docs(g).atualizar(TipoDocumento.COTACAO, 42, {"DocumentLines": []})
        assert g.requisicoes[-1].headers["B1S-ReplaceCollectionsOnPatch"] == "true"

    def test_cancelar_usa_a_acao_dedicada(self) -> None:
        g = Gravador([LOGIN, httpx.Response(204)])
        _docs(g).cancelar(TipoDocumento.PEDIDO, 42)
        assert g.metodos[-1] == "POST"
        assert g.caminhos[-1] == "/b1s/v1/Orders(42)/Cancel"

    @pytest.mark.parametrize("tipo", list(TipoDocumento))
    def test_cancelamento_bloqueado_em_producao(self, tipo: TipoDocumento) -> None:
        g = Gravador()
        with pytest.raises(ProductionWriteBlocked):
            _docs(g, company_db=PROD).cancelar(tipo, 1)
        assert g.requisicoes == []


class TestCancelarERecriar:
    def test_cancela_antes_de_criar(self) -> None:
        """A ordem evita dois documentos vigentes para o mesmo orçamento."""
        g = Gravador(
            [
                LOGIN,
                httpx.Response(200, json={"value": [{"DocEntry": 10}]}),  # busca
                httpx.Response(204),  # cancel
                httpx.Response(201, json={"DocEntry": 11}),  # create
            ]
        )
        criado = _docs(g).cancelar_e_recriar(
            TipoDocumento.COTACAO, "00123316", {"U_INO_COTWBC": "00123316"}
        )
        assert criado["DocEntry"] == 11
        assert g.caminhos[-2] == "/b1s/v1/Quotations(10)/Cancel"
        assert g.caminhos[-1] == "/b1s/v1/Quotations"

    def test_sem_documento_anterior_apenas_cria(self) -> None:
        g = Gravador(
            [
                LOGIN,
                httpx.Response(200, json={"value": []}),
                httpx.Response(201, json={"DocEntry": 1}),
            ]
        )
        _docs(g).cancelar_e_recriar(TipoDocumento.PEDIDO, "X", {"U_INO_COTWBC": "X"})
        assert not any("Cancel" in c for c in g.caminhos)


class TestOportunidades:
    def test_status_e_gravado_como_texto(self) -> None:
        """O legado compara com "30"/"40"; gravar número quebraria a leitura."""
        import json as _json

        g = Gravador([LOGIN, httpx.Response(204)])
        _oport(g).atualizar_status(77, 40)

        corpo = _json.loads(g.requisicoes[-1].content)
        assert corpo == {"U_INO_StatusWBC": "40"}
        assert isinstance(corpo["U_INO_StatusWBC"], str)

    def test_encerrar_grava_status_junto(self) -> None:
        import json as _json

        g = Gravador([LOGIN, httpx.Response(204)])
        _oport(g).encerrar(77, 90)
        corpo = _json.loads(g.requisicoes[-1].content)
        assert corpo["Status"] == "sos_Missed"
        assert corpo["U_INO_StatusWBC"] == "90"

    def test_status_de_encerramento_existe_no_enum_do_sap(self) -> None:
        """O `BoSoOsStatus` do Service Layer tem exatamente três nomes.

        Este teste existe porque o código enviava `sos_Lost` — um nome
        plausível, que não existe. O SAP recusava com
        `HTTP 400 | SAP -1013`, e nenhum teste pegava: o teste acima afirmava
        o valor errado, isto é, fixava o defeito em vez do comportamento.
        Aqui a asserção é contra a lista que o próprio SAP devolve na
        mensagem de erro, e não contra o que o código faz.
        """
        assert STATUS_PERDIDA in {"sos_Open", "sos_Missed", "sos_Sold"}

    def test_reabre_vincula_e_restaura_o_status(self) -> None:
        """Oportunidade fechada recusa estágio novo — então reabre-se.

        O SAP responde `-1029 [SalesOpportunitiesLines.SequenceNo] Field cannot
        be updated`, apontando para um campo sem relação com a causa.
        Reproduzido em homologação: reenviar a coleção funciona nas duas;
        acrescentar, só na aberta. O legado faz o mesmo em
        `AddPedidoOportunidade`.
        """
        import json as _json

        g = Gravador(
            [
                LOGIN,
                httpx.Response(200, json={"Status": "sos_Sold", "SalesOpportunitiesLines": []}),
                httpx.Response(204),  # reabre
                httpx.Response(204),  # vincula
                httpx.Response(204),  # restaura
            ]
        )
        _oport(g).vincular_documento(14234, TipoDocumento.PEDIDO, 19507, 11915.16)

        corpos = [_json.loads(r.content) for r in g.requisicoes[2:]]
        assert corpos[0] == {"Status": "sos_Open"}
        assert len(corpos[1]["SalesOpportunitiesLines"]) == 1
        assert corpos[2] == {"Status": "sos_Sold"}

    def test_restaura_o_status_mesmo_se_o_vinculo_falhar(self) -> None:
        """A oportunidade não pode ficar aberta por acidente: o estado dela é
        dado de negócio, o vínculo que falhou é problema nosso."""
        import json as _json

        g = Gravador(
            [
                LOGIN,
                httpx.Response(200, json={"Status": "sos_Missed", "SalesOpportunitiesLines": []}),
                httpx.Response(204),  # reabre
                httpx.Response(400, json={"error": {"message": "boom"}}),  # vínculo falha
                httpx.Response(204),  # restaura
            ]
        )
        with pytest.raises(ServiceLayerError):
            _oport(g).vincular_documento(14234, TipoDocumento.PEDIDO, 19507, 1.0)

        assert _json.loads(g.requisicoes[-1].content) == {"Status": "sos_Missed"}

    def test_oportunidade_aberta_nao_e_tocada_no_status(self) -> None:
        """Sem reabertura desnecessária: são duas idas à rede a mais."""
        import json as _json

        g = Gravador(
            [
                LOGIN,
                httpx.Response(200, json={"Status": "sos_Open", "SalesOpportunitiesLines": []}),
                httpx.Response(204),
            ]
        )
        _oport(g).vincular_documento(5681, TipoDocumento.COTACAO, 9486, 100.0)

        corpos = [_json.loads(r.content) for r in g.requisicoes[2:]]
        assert len(corpos) == 1
        assert "Status" not in corpos[0]

    def test_vincular_pedido_baixa_a_marca_de_atualizacao(self) -> None:
        """`U_INO_Update` marca a troca de PN pedida pelo operador.

        É um UDF da própria oportunidade no SAP, preenchido à mão, e a baixa no
        vínculo do pedido é o que fecha o ciclo: pedido novo criado, marca a
        `'N'`.

        Com o pedido gravado, a alteração está aplicada e a marca não tem mais
        o que sinalizar. É o que o legado faz em `AddPedidoOportunidade`
        (`ServiceProcess.cs:198` e `:210`).

        A baixa vai no **mesmo PATCH** do estágio: separá-la abriria uma janela
        em que a marca está baixada e o vínculo não existe.
        """
        import json as _json

        g = Gravador(
            [
                LOGIN,
                httpx.Response(200, json={"Status": "sos_Open", "SalesOpportunitiesLines": []}),
                httpx.Response(204),
            ]
        )
        _oport(g).vincular_documento(77, TipoDocumento.PEDIDO, 300, 500.0)

        corpo = _json.loads(g.requisicoes[-1].content)
        assert corpo["U_INO_Update"] == "N"
        assert len(corpo["SalesOpportunitiesLines"]) == 1

    def test_vincular_cotacao_nao_toca_na_marca(self) -> None:
        """No legado a baixa está só em `AddPedidoOportunidade`; nenhuma escrita
        de `U_INO_Update` existe em `AddCotacaoOportunidade`.

        Faz sentido: a cotação não aplica os valores, ela os propõe."""
        import json as _json

        g = Gravador(
            [
                LOGIN,
                httpx.Response(200, json={"Status": "sos_Open", "SalesOpportunitiesLines": []}),
                httpx.Response(204),
            ]
        )
        _oport(g).vincular_documento(77, TipoDocumento.COTACAO, 300, 500.0)

        assert "U_INO_Update" not in _json.loads(g.requisicoes[-1].content)

    @pytest.mark.parametrize(
        ("tipo", "percentual"),
        [(TipoDocumento.COTACAO, 0), (TipoDocumento.PEDIDO, 100)],
    )
    def test_percentual_do_estagio_por_tipo(self, tipo, percentual: int) -> None:
        """0 na cotação, 100 no pedido — o que o legado grava e o que está nos
        vínculos reais. Estávamos mandando 0 nos dois."""
        import json as _json

        g = Gravador(
            [
                LOGIN,
                httpx.Response(200, json={"Status": "sos_Open", "SalesOpportunitiesLines": []}),
                httpx.Response(204),
            ]
        )
        _oport(g).vincular_documento(77, tipo, 300, 500.0)

        estagio = _json.loads(g.requisicoes[-1].content)["SalesOpportunitiesLines"][0]
        assert estagio["PercentageRate"] == percentual

    def test_vincula_normalmente_em_oportunidade_aberta(self) -> None:
        import json as _json

        g = Gravador(
            [
                LOGIN,
                httpx.Response(
                    200,
                    json={"Status": "sos_Open", "SalesOpportunitiesLines": []},
                ),
                httpx.Response(204),
            ]
        )
        _oport(g).vincular_documento(5681, TipoDocumento.COTACAO, 9486, 100.0)

        corpo = _json.loads(g.requisicoes[-1].content)
        assert len(corpo["SalesOpportunitiesLines"]) == 1

    def test_vincular_documento_preserva_os_estagios_existentes(self) -> None:
        """PATCH substitui a coleção inteira — enviar só o novo apagaria o resto."""
        import json as _json

        g = Gravador(
            [
                LOGIN,
                httpx.Response(
                    200,
                    json={
                        "SequentialNo": 77,
                        "SalesOpportunitiesLines": [{"DocumentType": 23, "DocumentNumber": 100}],
                    },
                ),
                httpx.Response(204),
            ]
        )
        _oport(g).vincular_documento(77, TipoDocumento.PEDIDO, 200, 4527.86)

        enviados = _json.loads(g.requisicoes[-1].content)["SalesOpportunitiesLines"]
        assert len(enviados) == 2
        assert enviados[0]["DocumentNumber"] == 100  # o antigo continua lá
        assert enviados[1] == {
            "DocumentType": 17,
            "DocumentNumber": 200,
            "MaxLocalTotal": 4527.86,
            "PercentageRate": 100,  # pedido; a cotação leva 0
        }

    def test_vincular_em_oportunidade_sem_estagios(self) -> None:
        import json as _json

        g = Gravador([LOGIN, httpx.Response(200, json={"SequentialNo": 77}), httpx.Response(204)])
        _oport(g).vincular_documento(77, TipoDocumento.COTACAO, 300, 1500.0)
        enviados = _json.loads(g.requisicoes[-1].content)["SalesOpportunitiesLines"]
        assert enviados == [
            {
                "DocumentType": 23,
                "DocumentNumber": 300,
                "MaxLocalTotal": 1500.0,
                "PercentageRate": 0,
            }
        ]

    def test_total_zerado_vira_um(self) -> None:
        """`MaxLocalTotal` zero faz o SAP recusar com -5002 [OOPR.MaxSumLoc]."""
        import json as _json

        g = Gravador([LOGIN, httpx.Response(200, json={"SequentialNo": 77}), httpx.Response(204)])
        _oport(g).vincular_documento(77, TipoDocumento.COTACAO, 300, 0.0)
        enviados = _json.loads(g.requisicoes[-1].content)["SalesOpportunitiesLines"]
        assert enviados[0]["MaxLocalTotal"] == 1

    def test_sem_total_informado_vira_um(self) -> None:
        import json as _json

        g = Gravador([LOGIN, httpx.Response(200, json={"SequentialNo": 77}), httpx.Response(204)])
        _oport(g).vincular_documento(77, TipoDocumento.COTACAO, 300)
        enviados = _json.loads(g.requisicoes[-1].content)["SalesOpportunitiesLines"]
        assert enviados[0]["MaxLocalTotal"] == 1

    def test_nao_escreve_em_u_ino_update_nem_apaga_pn_correc(self) -> None:
        """A integração não baixa marca nenhuma na oportunidade.

        Houve uma `limpar_marca_de_troca_de_pn` aqui, escrevendo
        `U_INO_Update = 'N'` e `U_INO_PN_Correc = ''`. As duas escritas estavam
        erradas: `U_INO_Update` sinaliza alteração de **valores** e não é campo
        desta integração; e apagar o `PN_Correc` destruiria o registro de qual
        correção foi pedida.

        A idempotência da troca de parceiro vem do próprio pedido — comparar o
        `CardCode` que ele já tem com o `PN_Correc` —, como o `ChecaPNPedido`
        do legado.
        """
        assert not hasattr(RepositorioOportunidadesServiceLayer, "limpar_marca_de_troca_de_pn")


class TestSelecaoDeOportunidades:
    def test_nao_tem_filtro_fixo_de_orcamento(self) -> None:
        """O defeito nº 1 do legado não pode reaparecer aqui."""
        g = Gravador([LOGIN, httpx.Response(200, json={"value": []})])
        _oport(g).pendentes_de_integracao()

        filtro = dict(g.requisicoes[-1].url.params)["$filter"]
        assert "00121819" not in filtro
        assert "U_INO_IntegrouWBC eq 'Y'" in filtro

    def test_filtro_por_orcamento_e_opcional(self) -> None:
        g = Gravador([LOGIN, httpx.Response(200, json={"value": []})])
        _oport(g).pendentes_de_integracao(orcamento="00123316")
        assert "U_ORCNUM_WBC eq '00123316'" in dict(g.requisicoes[-1].url.params)["$filter"]

    def test_janela_de_data_e_explicita(self) -> None:
        from datetime import date

        g = Gravador([LOGIN, httpx.Response(200, json={"value": []})])
        _oport(g).pendentes_de_integracao(desde=date(2026, 1, 1))
        assert "StartDate ge '2026-01-01'" in dict(g.requisicoes[-1].url.params)["$filter"]

    def test_sem_data_nao_filtra_por_data(self) -> None:
        g = Gravador([LOGIN, httpx.Response(200, json={"value": []})])
        _oport(g).pendentes_de_integracao()
        assert "StartDate" not in dict(g.requisicoes[-1].url.params)["$filter"]


class TestParceiros:
    """`ChecaPN`: o parceiro corrigido existe no SAP?"""

    def _repo(self, gravador):
        from wbcpython.infrastructure.service_layer.parceiros import (
            RepositorioParceirosServiceLayer,
        )

        return RepositorioParceirosServiceLayer(_cliente(gravador))

    def test_parceiro_existente(self) -> None:
        g = Gravador([LOGIN, httpx.Response(200, json={"CardCode": "C999"})])
        assert self._repo(g).existe("C999") is True

    def test_404_significa_nao_existe_e_nao_erro(self) -> None:
        """404 é resposta, não falha: o ciclo não pode quebrar por isso."""
        g = Gravador([LOGIN, httpx.Response(404, json={"error": {"message": "not found"}})])
        assert self._repo(g).existe("C000") is False

    def test_erro_de_verdade_sobe(self) -> None:
        """Um 500 não pode virar 'o parceiro não existe' — isso faria a
        integração parar de refazer pedidos por causa de uma falha passageira."""
        g = Gravador([LOGIN, httpx.Response(500, json={"error": {"message": "boom"}})])
        with pytest.raises(ServiceLayerError):
            self._repo(g).existe("C999")

    def test_consulta_uma_vez_por_parceiro(self) -> None:
        """Numa leva de correções o mesmo CardCode repete."""
        g = Gravador([LOGIN, httpx.Response(200, json={"CardCode": "C999"})])
        repo = self._repo(g)
        assert repo.existe("C999") is True
        assert repo.existe("C999") is True
        assert len([r for r in g.requisicoes if "BusinessPartners" in str(r.url)]) == 1

    def test_card_code_vazio_nao_vai_a_rede(self) -> None:
        g = Gravador([LOGIN])
        assert self._repo(g).existe("") is False


class TestEstaCongelado:
    """A tradução do `U_INO_Congelado`, que segue o legado ao pé da letra."""

    def test_so_y_congela(self) -> None:
        assert esta_congelado_pelo_campo("Y")
        assert esta_congelado_pelo_campo("y")
        assert esta_congelado_pelo_campo(" Y ")

    def test_vazio_e_nulo_nao_congelam(self) -> None:
        """`if (Congelado != "Y")` do legado: só o `'Y'` protege.

        Ao contrário do `DocStatus`, aqui não se falha congelado no valor
        desconhecido — seria parar de atualizar linhas num caso que o legado
        atualiza, e a divergência não apareceria em lugar nenhum.
        """
        assert not esta_congelado_pelo_campo("")
        assert not esta_congelado_pelo_campo(None)
        assert not esta_congelado_pelo_campo("N")
        assert not esta_congelado_pelo_campo("qualquer coisa")
