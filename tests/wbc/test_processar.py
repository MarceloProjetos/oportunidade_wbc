"""Testes do caso de uso de processamento — com dublês, sem rede.

Verificam a **orquestração**: que a decisão do domínio vira as chamadas certas,
na ordem certa, e que tudo fica registrado no acompanhamento. A regra em si já
é coberta por `tests/domain/test_sitcode.py`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from wbcpython.application.processar import ProcessadorDeOrcamento
from wbcpython.infrastructure.service_layer.documentos import TipoDocumento
from wbcpython.infrastructure.wbc_sql.models import ItemOrcamentoWbc, OrcamentoWbc
from wbcpython.tracking import RepositorioTracking, StatusIntegracao


class WbcFalso:
    def __init__(self, orcamento: OrcamentoWbc | None, *, pesos=None) -> None:
        self._orcamento = orcamento
        #: `ORCITM` → peso do nível 1 da árvore. Vazio é o caso normal nos
        #: testes: quem não passa peso continua exercitando o pedido sem
        #: `Weight1`, que é o comportamento de antes da regra existir.
        self._pesos = pesos or {}

    def buscar_orcamento(self, orcnum: str) -> OrcamentoWbc | None:
        return self._orcamento

    def pesos_por_item(self, orcnum: str) -> dict[int, Decimal]:
        return dict(self._pesos)


class DocumentosFalso:
    def __init__(
        self,
        *,
        tem_cotacao=False,
        tem_pedido=False,
        revisoes=None,
        parceiros=None,
        fechados=None,
        congelados=None,
        total_apos_atualizar=None,
    ) -> None:
        self._tem = {TipoDocumento.COTACAO: tem_cotacao, TipoDocumento.PEDIDO: tem_pedido}
        self._revisoes = revisoes or {}
        self._parceiros = parceiros or {}
        #: Tipos com `DocStatus = 'C'`. Aberto é o padrão: um pedido fechado não
        #: aceita alteração nem cancelamento, e deixá-lo como padrão faria a
        #: maioria destes testes exercitar a guarda em vez da regra.
        self._fechados = set(fechados or ())
        #: Tipos com `U_INO_Congelado = 'Y'`. **Não** é o padrão aqui, ao
        #: contrário da produção (onde os 2.581 pedidos vigentes estão em
        #: `'Y'`): a maioria destes testes exercita a montagem das linhas, e
        #: congelar por padrão faria todos passarem pela guarda em vez da regra.
        self._congelados = set(congelados or ())
        #: O que o SAP devolve ao ser relido depois de um `atualizar`.
        #:
        #: `None` significa "gravou o que mandamos" — o caso normal. Um número
        #: força a divergência que motivou a conferência: o Service Layer
        #: responde 204 e as linhas continuam como estavam.
        self._total_apos_atualizar = total_apos_atualizar
        self.chamadas: list[tuple[str, Any, Any]] = []
        self._proximo_docentry = 100

    def existe(self, tipo, orcamento):
        return self._tem[tipo]

    def revisao_aplicada(self, tipo, orcamento):
        return self._revisoes.get(tipo, "")

    def parceiro_aplicado(self, tipo, orcamento):
        return self._parceiros.get(tipo, "")

    def esta_congelado(self, tipo, orcamento):
        return tipo in self._congelados

    def total_das_linhas(self, tipo, doc_entry):
        from decimal import Decimal

        if self._total_apos_atualizar is not None:
            return Decimal(str(self._total_apos_atualizar))
        ultimo = next((c for c in reversed(self.chamadas) if c[0] == "atualizar"), None)
        if ultimo is None:
            return Decimal(0)
        return sum(
            (
                Decimal(str(l.get("Quantity") or 0)) * Decimal(str(l.get("Price") or 0))
                for l in (ultimo[2].get("DocumentLines") or ())
            ),
            Decimal(0),
        )

    def esta_fechado(self, tipo, orcamento):
        return tipo in self._fechados

    def doc_entry(self, tipo, orcamento):
        return 55 if self._tem[tipo] else None

    def criar(self, tipo, dados):
        self.chamadas.append(("criar", tipo, dados))
        self._proximo_docentry += 1
        return {"DocEntry": self._proximo_docentry, "DocNum": 900, "DocTotal": 1500.5}

    def atualizar(self, tipo, doc_entry, dados):
        # Guarda o payload, e não o DocEntry: o que precisa ser verificado numa
        # atualização é o conteúdo enviado, igual à criação.
        self.chamadas.append(("atualizar", tipo, dados))

    def cancelar(self, tipo, doc_entry):
        self.chamadas.append(("cancelar", tipo, doc_entry))

    def cancelar_e_recriar(self, tipo, orcamento, dados):
        self.chamadas.append(("cancelar_e_recriar", tipo, dados))
        return {"DocEntry": 777, "DocNum": 901, "DocTotal": 2000}


class OportunidadesFalso:
    def __init__(self) -> None:
        self.chamadas: list[tuple[str, Any]] = []

    def atualizar_status(self, oppr_id, sitcode):
        self.chamadas.append(("status", (oppr_id, sitcode)))

    def encerrar(self, oppr_id, sitcode):
        self.chamadas.append(("encerrar", (oppr_id, sitcode)))

    def vincular_documento(self, oppr_id, tipo, doc_entry, total=None):
        self.chamadas.append(("vincular", (oppr_id, tipo, doc_entry, total)))

    def limpar_marca_de_troca_de_pn(self, oppr_id):
        self.chamadas.append(("limpar_pn", oppr_id))


class OrcDetalheFalso:
    def __init__(self, *, falhar: bool = False) -> None:
        self.snapshots: list[dict[str, Any]] = []
        self._falhar = falhar

    def criar_snapshot(self, dados):
        if self._falhar:
            raise RuntimeError("SAP recusou o snapshot")
        self.snapshots.append(dados)
        return {"DocEntry": 513888}


def _orcamento(**campos: Any) -> OrcamentoWbc:
    base: dict[str, Any] = {
        "orcnum": "00123316",
        "revisao": "A",
        "sitcode": 30,
        "cliente_nome": "BALTEAU",
        "representante": "043",
        "municipio": "ITAJUBA",
        "uf": "MG",
        "itens": (
            ItemOrcamentoWbc(orcitm=1, produto="P", quantidade=Decimal(2), valor=Decimal(10)),
        ),
    }
    base.update(campos)
    return OrcamentoWbc(**base)


def _oportunidade(**campos: Any) -> dict[str, Any]:
    base = {"SequentialNo": 77, "U_ORCNUM_WBC": "00123316", "CardCode": "C001"}
    base.update(campos)
    return base


@pytest.fixture
def tracking(tmp_path) -> RepositorioTracking:
    return RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/t.db")


class GruposFalso:
    """De-para de grupos, com o grupo de fallback `2` cadastrado.

    Passou a ser o padrão destes testes quando a integração ganhou a recusa de
    documento sem valor: sem de-para, o payload sai sem `DocumentLines`, e um
    documento sem linhas é exatamente o que o guarda barra. Manter os testes
    sem de-para os faria exercitar um caminho que o ciclo real nunca percorre —
    o `grupo_produtos` é sempre injetado pelo worker.
    """

    def carregar(self):
        from wbcpython.infrastructure.service_layer.grupo_produtos import GrupoDeProduto

        return {"2": GrupoDeProduto("2", "Porta-Paletes", "I000003")}


def _processador(tracking, wbc, docs, oport, orcdet, **kw):
    kw.setdefault("grupo_produtos", GruposFalso())
    return ProcessadorDeOrcamento(
        wbc=wbc,
        orcdetalhe=orcdet,
        documentos=docs,
        oportunidades=oport,
        tracking=tracking,
        **kw,
    )


class TestCriacaoDeCotacao:
    def test_sitcode_30_sem_cotacao_cria_e_vincula(self, tracking) -> None:
        docs, oport, orcdet = DocumentosFalso(), OportunidadesFalso(), OrcDetalheFalso()
        resultado = _processador(
            tracking, WbcFalso(_orcamento(sitcode=30)), docs, oport, orcdet
        ).processar(_oportunidade())

        assert resultado.sucesso
        assert ("criar", TipoDocumento.COTACAO) in [(c[0], c[1]) for c in docs.chamadas]
        assert any(c[0] == "vincular" for c in oport.chamadas)

    def test_vinculo_usa_docentry_e_total_nao_docnum(self, tracking) -> None:
        """Regressão: o vínculo aponta para o DocEntry, não para o DocNum.

        O campo se chama `DocumentNumber`, o que convida ao erro. Os vínculos
        reais em homologação apontam todos para o DocEntry, e sem
        `MaxLocalTotal` o SAP recusa com -5002 [OOPR.MaxSumLoc]. O dublê devolve
        DocEntry diferente de DocNum justamente para separar os dois.
        """
        docs, oport, orcdet = DocumentosFalso(), OportunidadesFalso(), OrcDetalheFalso()
        _processador(tracking, WbcFalso(_orcamento(sitcode=30)), docs, oport, orcdet).processar(
            _oportunidade()
        )

        vinculos = [c for c in oport.chamadas if c[0] == "vincular"]
        assert vinculos, "nenhum vínculo registrado"
        _oppr, _tipo, doc_entry, total = vinculos[0][1]
        assert doc_entry != 900, "usou o DocNum em vez do DocEntry"
        assert total == 1500.5

    def test_status_no_acompanhamento(self, tracking) -> None:
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            DocumentosFalso(),
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        registro = tracking.obter("00123316")
        assert registro is not None
        assert registro.status is StatusIntegracao.COTACAO_CRIADA
        assert registro.cliente == "BALTEAU"
        assert registro.cotacao_docnum == 900

    def test_grava_o_snapshot_do_orcdetalhe(self, tracking) -> None:
        orcdet = OrcDetalheFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            DocumentosFalso(),
            OportunidadesFalso(),
            orcdet,
        ).processar(_oportunidade())
        assert len(orcdet.snapshots) == 1
        assert orcdet.snapshots[0]["U_INO_COD"] == "00123316"


class TestSemAcao:
    def test_nao_toca_no_sap(self, tracking) -> None:
        docs, oport, orcdet = (
            DocumentosFalso(tem_cotacao=True),
            OportunidadesFalso(),
            OrcDetalheFalso(),
        )
        resultado = _processador(
            tracking, WbcFalso(_orcamento(sitcode=30)), docs, oport, orcdet
        ).processar(_oportunidade(U_INO_StatusWBC="30"))

        assert resultado.decisao.regra == "emitido_ja_sincronizado"
        assert docs.chamadas == []
        assert oport.chamadas == []
        assert orcdet.snapshots == []
        assert tracking.obter("00123316").status is StatusIntegracao.SEM_ACAO


class TestPedido:
    def test_sitcode_60_cria_pedido_e_atualiza_cotacao(self, tracking) -> None:
        docs = DocumentosFalso(tem_cotacao=True)
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        acoes = [(c[0], c[1]) for c in docs.chamadas]
        assert ("atualizar", TipoDocumento.COTACAO) in acoes
        assert ("criar", TipoDocumento.PEDIDO) in acoes
        assert acoes.index(("atualizar", TipoDocumento.COTACAO)) < acoes.index(
            ("criar", TipoDocumento.PEDIDO)
        )

    def test_status_final_prioriza_o_pedido(self, tracking) -> None:
        # A cotação também foi atualizada, mas o que importa é o pedido criado.
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60)),
            DocumentosFalso(tem_cotacao=True),
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())
        assert tracking.obter("00123316").status is StatusIntegracao.PEDIDO_CRIADO

    def test_pedido_e_cotacao_recebem_conjuntos_diferentes_de_udf(self, tracking) -> None:
        """No legado são dois blocos de código distintos — e divergem de fato.

        A cotação leva prazo, transporte, embalagem, contato, condição de
        pagamento e montagem; o pedido leva comissão. E o `U_INO_VL_MT` sai de
        colunas diferentes nos dois: `ORCVALMON` na cotação, `ORCBAS3` no pedido.
        """
        from wbcpython.infrastructure.wbc_sql.models import DadosImpressaoWbc

        impressao = DadosImpressaoWbc(
            valor_montagem=Decimal("2500.50"),
            base3=Decimal("7000.25"),
            percentual_comissao=Decimal("3.5"),
            valor_comissao=Decimal(1200),
        )
        docs = DocumentosFalso(tem_cotacao=True)
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60, impressao=impressao)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        cotacao = next(c[2] for c in docs.chamadas if c[1] is TipoDocumento.COTACAO)
        pedido = next(c[2] for c in docs.chamadas if c[1] is TipoDocumento.PEDIDO)

        assert cotacao["U_INO_VL_MT"] == 2500.50
        assert "U_INO_PrazoEntrega" in cotacao
        assert "U_INO_VL_COM" not in cotacao

        assert pedido["U_INO_VL_MT"] == 7000.25
        assert pedido["U_INO_VL_COM"] == 1200.0
        assert "U_INO_PrazoEntrega" not in pedido

    def test_cada_documento_passa_pelo_seu_procedimento(self, tracking) -> None:
        """A separação chega até as linhas, não só até o cabeçalho.

        `_executar_cotacao` e `_executar_pedido` são caminhos distintos; este
        teste falha se alguém voltar a montar os dois pelo mesmo lugar.
        """
        from wbcpython.infrastructure.wbc_sql.models import DadosImpressaoWbc

        docs = DocumentosFalso(tem_cotacao=True)
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60, impressao=DadosImpressaoWbc(acabamento="X"))),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
            grupo_produtos=GrupoProdutosFalso(),
        ).processar(_oportunidade())

        cotacao = next(c[2] for c in docs.chamadas if c[1] is TipoDocumento.COTACAO)
        pedido = next(c[2] for c in docs.chamadas if c[1] is TipoDocumento.PEDIDO)

        linha_cot = cotacao["DocumentLines"][0]
        linha_ped = pedido["DocumentLines"][0]

        assert linha_cot["U_INO_ACAB"] == "X"
        assert "U_INO_Composicao" not in linha_cot

        assert "U_INO_Composicao" in linha_ped
        assert "U_INO_ACAB" not in linha_ped


class TestTrocaDeParceiro:
    def test_pn_correc_com_update_recria_o_pedido(self, tracking) -> None:
        """`PN_Correc` preenchido, `Update = 'Y'` e pedido no parceiro antigo: refaz.

        O `U_INO_Update="Y"` não é decoração do teste: sem ele a combinação
        significa "já resolvido à mão" e o pedido fica congelado — ver
        `EstadoIntegracao.pedido_corrigido_a_mao`."""
        docs = DocumentosFalso(
            tem_cotacao=True, tem_pedido=True, parceiros={TipoDocumento.PEDIDO: "C001"}
        )
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_PN_Correc="C999", CardCode="C001", U_INO_Update="Y"))

        assert any(c[0] == "cancelar_e_recriar" for c in docs.chamadas)

    def test_nao_escreve_na_oportunidade_para_baixar_marca(self, tracking) -> None:
        """Sem escrita avulsa no fim da troca de parceiro.

        `U_INO_Update` e `U_INO_PN_Correc` são UDFs da oportunidade no SAP. O
        primeiro sinaliza alteração de **valores** (não a troca), e o segundo é
        o registro de qual correção foi pedida — apagá-lo destruiria essa
        informação. A idempotência da troca vem do próprio pedido.

        A integração baixa o `U_INO_Update` num caso, e só nele: ao vincular o
        pedido, no mesmo PATCH do estágio. Isso é outro caminho, com teste
        próprio em `tests/infrastructure/test_documentos.py`.
        """
        docs = DocumentosFalso(
            tem_cotacao=True, tem_pedido=True, parceiros={TipoDocumento.PEDIDO: "C001"}
        )
        oport = OportunidadesFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60)),
            docs,
            oport,
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_PN_Correc="C999", CardCode="C001"))

        assert not any(c[0] == "limpar_pn" for c in oport.chamadas)

    def test_pedido_ja_no_pn_novo_nao_e_refeito(self, tracking) -> None:
        """As 578 oportunidades com `PN_Correc` residual caem aqui."""
        docs = DocumentosFalso(
            tem_cotacao=True, tem_pedido=True, parceiros={TipoDocumento.PEDIDO: "C999"}
        )
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_PN_Correc="C999", CardCode="C001"))

        assert not any(c[0] == "cancelar_e_recriar" for c in docs.chamadas)

    def test_usa_o_pn_novo_no_pedido(self, tracking) -> None:
        docs = DocumentosFalso(
            tem_cotacao=True, tem_pedido=True, parceiros={TipoDocumento.PEDIDO: "C001"}
        )
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_PN_Correc="C999", U_INO_Update="Y"))

        dados = next(c[2] for c in docs.chamadas if c[0] == "cancelar_e_recriar")
        assert dados["CardCode"] == "C999"

    def test_a_cotacao_nunca_muda_de_parceiro(self, tracking) -> None:
        """No legado `CriaCotacao` recebe o `CardCode` da oportunidade; `PNNew`
        só aparece no pedido. A cotação acompanha a oportunidade, não a
        correção."""
        docs = DocumentosFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(CardCode="C011151", U_INO_PN_Correc="C011081"))

        dados = next(c[2] for c in docs.chamadas if c[0] == "criar")
        assert dados["CardCode"] == "C011151"


class TestEncerramento:
    @pytest.mark.parametrize("sitcode", [70, 90, 99])
    def test_encerra_a_oportunidade(self, tracking, sitcode: int) -> None:
        oport = OportunidadesFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=sitcode)),
            DocumentosFalso(tem_cotacao=True),
            oport,
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        assert ("encerrar", (77, sitcode)) in oport.chamadas
        assert tracking.obter("00123316").status is StatusIntegracao.ENCERRADA


class TestIsolamentoDeFalhas:
    def test_orcamento_inexistente_no_wbc_vira_erro_registrado(self, tracking) -> None:
        resultado = _processador(
            tracking,
            WbcFalso(None),
            DocumentosFalso(),
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        assert resultado.sucesso is False
        assert tracking.obter("00123316").status is StatusIntegracao.ERRO

    def test_oportunidade_sem_numero_nao_derruba(self, tracking) -> None:
        """No legado, isto encerrava a execução inteira com `return`."""
        resultado = _processador(
            tracking,
            WbcFalso(_orcamento()),
            DocumentosFalso(),
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar({"SequentialNo": 1, "U_ORCNUM_WBC": ""})

        assert resultado.sucesso is False
        assert resultado.decisao.regra == "sem_numero_de_orcamento"

    def test_excecao_no_sap_vira_erro_registrado(self, tracking) -> None:
        class DocsQueFalham(DocumentosFalso):
            def criar(self, tipo, dados):
                raise RuntimeError("SAP fora do ar")

        resultado = _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            DocsQueFalham(),
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        assert resultado.sucesso is False
        registro = tracking.obter("00123316")
        assert registro.status is StatusIntegracao.ERRO
        assert "SAP fora do ar" in registro.ultimo_erro

    def test_falha_no_snapshot_nao_desfaz_o_documento(self, tracking) -> None:
        """O snapshot é histórico: falhar nele não invalida o que já foi criado."""
        docs = DocumentosFalso()
        resultado = _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(falhar=True),
        ).processar(_oportunidade())

        assert resultado.sucesso is True
        assert any(c[0] == "criar" for c in docs.chamadas)
        assert tracking.obter("00123316").status is StatusIntegracao.COTACAO_CRIADA


class TestRegistroDeHistorico:
    def test_gera_eventos_com_a_regra_aplicada(self, tracking) -> None:
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            DocumentosFalso(),
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        eventos = tracking.eventos("00123316")
        assert eventos
        assert any(e.regra == "emitido_sem_cotacao" for e in eventos)

    def test_motivo_legivel_fica_registrado(self, tracking) -> None:
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            DocumentosFalso(),
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        assert any("cotação" in e.mensagem.lower() for e in tracking.eventos("00123316"))


class TestJanelaDeDatas:
    def test_calcula_meses_para_tras(self) -> None:
        from datetime import date

        from wbcpython.host.worker import janela_padrao

        assert janela_padrao(meses=6, hoje=date(2026, 8, 25)) == date(2026, 2, 1)

    def test_atravessa_a_virada_de_ano(self) -> None:
        from datetime import date

        from wbcpython.host.worker import janela_padrao

        # O defeito do legado era exatamente aqui: ano e mês vinham de
        # deslocamentos diferentes e a data saía incoerente.
        assert janela_padrao(meses=6, hoje=date(2026, 3, 15)) == date(2025, 9, 1)
        assert janela_padrao(meses=12, hoje=date(2026, 1, 5)) == date(2025, 1, 1)


class TestPayloadDoDocumento:
    """O SAP recusa a criação sem CardCode (-2028), verificado no ambiente real."""

    def test_inclui_o_parceiro_da_oportunidade(self, tracking) -> None:
        docs = DocumentosFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(CardCode="C001"))

        dados = next(c[2] for c in docs.chamadas if c[0] == "criar")
        assert dados["CardCode"] == "C001"

    def test_troca_de_pn_usa_o_parceiro_novo(self, tracking) -> None:
        docs = DocumentosFalso(tem_cotacao=True, tem_pedido=True)
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_Update="Y", U_INO_PN_Correc="C999", CardCode="C001"))

        dados = next(c[2] for c in docs.chamadas if c[0] == "cancelar_e_recriar")
        assert dados["CardCode"] == "C999"

    def test_pn_correc_nao_desvia_a_cotacao(self, tracking) -> None:
        """`U_INO_PN_Correc` sem `U_INO_Update='Y'` é dado residual.

        O legado nunca baixava a marca: 578 oportunidades em homologação (580
        em produção) carregam um `PN_Correc` preenchido e diferente do
        `CardCode`, com `Update='N'`. O payload fazia
        `parceiro_novo or parceiro_atual` sem olhar `alterado`, e mandava
        **todas** para o parceiro errado.

        O SAP só reclamava quando o contato da oportunidade por acaso não
        existia no parceiro errado (`-5002 Invalid contact person code`);
        quando existia, o documento saía em silêncio para o cliente errado.
        """
        docs = DocumentosFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(CardCode="C011151", U_INO_PN_Correc="C011081", U_INO_Update="N"))

        dados = next(c[2] for c in docs.chamadas if c[0] == "criar")
        assert dados["CardCode"] == "C011151"

    def test_troca_de_pn_nao_leva_o_contato_do_parceiro_antigo(self, tracking) -> None:
        """O contato pertence ao PN da oportunidade e não existe no novo.

        O SAP recusa o documento inteiro com `-5002 Invalid contact person code
        [OQUT.CntctCode]`. Documento sem contato é melhor que documento nenhum.
        """
        docs = DocumentosFalso(tem_cotacao=True, tem_pedido=True)
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(
            _oportunidade(
                U_INO_Update="Y",
                U_INO_PN_Correc="C999",
                CardCode="C001",
                ContactPerson=8801,
            )
        )

        dados = next(c[2] for c in docs.chamadas if c[0] == "cancelar_e_recriar")
        assert dados["CardCode"] == "C999"
        assert "ContactPersonCode" not in dados

    def test_sem_troca_o_contato_e_enviado(self, tracking) -> None:
        """A guarda é para a troca de PN, não uma remoção geral do contato."""
        docs = DocumentosFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(CardCode="C001", ContactPerson=8801))

        dados = next(c[2] for c in docs.chamadas if c[0] == "criar")
        assert dados["ContactPersonCode"] == 8801

    def test_sem_parceiro_o_campo_e_omitido(self, tracking) -> None:
        # Omitir deixa o erro do SAP explícito; mandar vazio o mascararia.
        docs = DocumentosFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar({"SequentialNo": 77, "U_ORCNUM_WBC": "00123316"})

        dados = next(c[2] for c in docs.chamadas if c[0] == "criar")
        assert "CardCode" not in dados

    def test_sempre_vincula_ao_orcamento(self, tracking) -> None:
        docs = DocumentosFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        dados = next(c[2] for c in docs.chamadas if c[0] == "criar")
        assert dados["U_INO_COTWBC"] == "00123316"
        assert dados["U_INO_VERSAOWBC"] == "A"

    def test_campos_da_impressao_vao_no_cabecalho(self, tracking) -> None:
        """Prazo, transporte e embalagem saem de `INTEGRACAO_ORCIMP`.

        São UDFs de texto (`ServiceProcess.cs:1177-1179`). O prazo vai como
        inteiro puro; os dois valores com quatro casas — que é como as cotações
        do legado estão gravadas em homologação.
        """
        from wbcpython.infrastructure.wbc_sql.models import DadosImpressaoWbc

        impressao = DadosImpressaoWbc(
            prazo_entrega=42,
            valor_transporte=Decimal("113026.88"),
            valor_embalagem=Decimal(0),
        )
        docs = DocumentosFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30, impressao=impressao)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        dados = next(c[2] for c in docs.chamadas if c[0] == "criar")
        assert dados["U_INO_PrazoEntrega"] == "42"
        assert dados["U_INO_ValorTransp"] == "113026.8800"
        assert dados["U_INO_ValorEmbalagem"] == "0.0000"

    def test_sem_impressao_os_campos_vao_zerados(self, tracking) -> None:
        """`impressao` nunca é nula — sem dados, os campos vão zerados, como no
        legado, que também escrevia o que quer que viesse da consulta."""
        docs = DocumentosFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        dados = next(c[2] for c in docs.chamadas if c[0] == "criar")
        assert dados["U_INO_PrazoEntrega"] == "0"
        assert dados["U_INO_ValorTransp"] == "0.0000"
        assert dados["U_INO_ValorEmbalagem"] == "0.0000"


class GrupoProdutosFalso:
    """De-para em memória, com os mesmos códigos do ambiente real."""

    def __init__(self, mapa=None) -> None:
        from wbcpython.infrastructure.service_layer.grupo_produtos import GrupoDeProduto

        self.mapa = (
            mapa
            if mapa is not None
            else {
                "1": GrupoDeProduto("1", "Estantes", "I000002"),
                "2": GrupoDeProduto("2", "Porta-Paletes", "I000003"),
            }
        )

    def carregar(self):
        return self.mapa


class TestDocumentLines:
    """O documento precisa nascer com linhas — sem elas o SAP recusa.

    O erro real observado em homologação era
    `-5002 Document total value must be zero or greater than zero`, que não diz
    nada sobre a causa. Estes testes existem para que ele não volte em silêncio.
    """

    def _payload_criado(self, docs) -> dict[str, Any]:
        for chamada in docs.chamadas:
            if chamada[0] == "criar":
                return chamada[2]
        raise AssertionError("nenhum documento foi criado")

    def test_documento_sai_com_linhas(self, tracking) -> None:
        docs = DocumentosFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
            grupo_produtos=GrupoProdutosFalso(),
        ).processar(_oportunidade())

        linhas = self._payload_criado(docs)["DocumentLines"]
        assert len(linhas) == 1
        assert linhas[0]["ItemCode"] == "I000003"
        assert linhas[0]["WarehouseCode"] == "08"

    def test_sem_de_para_nenhum_documento_e_criado(self, tracking) -> None:
        """Antes preferia-se documento sem linha a item inventado. Hoje não se
        cria documento nenhum — sem linhas o SAP recusa com -5002, e o que era
        uma escolha prudente virava erro de ciclo."""
        docs = DocumentosFalso()
        resultado = _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
            grupo_produtos=None,
        ).processar(_oportunidade())

        assert docs.chamadas == []
        assert resultado.sucesso

    def test_grupo_desconhecido_e_registrado_no_acompanhamento(self, tracking) -> None:
        """O aviso não pode ficar só no log: o dashboard precisa mostrá-lo."""
        docs = DocumentosFalso()
        orcamento = _orcamento(
            sitcode=30,
            itens=(ItemOrcamentoWbc(orcitm=1, grupo=99, produto="P", valor=Decimal(100)),),
        )
        _processador(
            tracking,
            WbcFalso(orcamento),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
            grupo_produtos=GrupoProdutosFalso(),
        ).processar(_oportunidade())

        eventos = tracking.eventos("00123316")
        assert any("99" in (e.mensagem or "") for e in eventos)
        # E, ainda assim, o documento saiu com o item de fallback.
        assert self._payload_criado(docs)["DocumentLines"][0]["ItemCode"] == "I000003"


class TestEspelhamentoDeStatus:
    """Mexeu em documento → o `U_INO_StatusWBC` da oportunidade acompanha.

    Sem isto o SAP continuava dizendo `"0"` depois de a cotação ter sido
    criada, e na execução seguinte a integração criava **outra** cotação para o
    mesmo orçamento. Foi observado no ambiente real: a oportunidade 15145 ficou
    com status `'0'` mesmo depois da cotação criada e vinculada.
    """

    def test_criar_cotacao_espelha_o_status(self, tracking) -> None:
        oport = OportunidadesFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=40)),
            DocumentosFalso(),
            oport,
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_StatusWBC="0"))

        assert ("status", (77, 40)) in oport.chamadas

    def test_atualizar_cotacao_tambem_espelha(self, tracking) -> None:
        """`atualizar_cotacao` não gera documento novo, então não há vínculo —
        e era justamente aí que o status ficava para trás."""
        docs = DocumentosFalso(tem_cotacao=True)
        oport = OportunidadesFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=40, revisao="B")),
            docs,
            oport,
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_StatusWBC="30"))

        assert any(c[0] == "status" for c in oport.chamadas)

    def test_nao_escreve_se_o_sap_ja_reflete(self, tracking) -> None:
        """Escrita à toa é ida à rede e ruído no histórico da oportunidade."""
        docs = DocumentosFalso(tem_cotacao=True)
        oport = OportunidadesFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=40)),
            docs,
            oport,
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_StatusWBC="40"))

        assert not [c for c in oport.chamadas if c[0] == "status"]

    def test_encerramento_nao_ganha_escrita_extra_de_status(self, tracking) -> None:
        """`encerrar()` já grava o `U_INO_StatusWBC` junto com o `Status`.

        Nem o domínio pede espelhamento no encerramento, nem o processador o
        acrescenta depois: qualquer PATCH a mais seria o mesmo valor gravado
        duas vezes na mesma oportunidade, no mesmo ciclo.
        """
        oport = OportunidadesFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=99)),
            DocumentosFalso(tem_cotacao=True),
            oport,
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_StatusWBC="40"))

        assert [c[0] for c in oport.chamadas].count("status") == 0
        assert [c[0] for c in oport.chamadas].count("encerrar") == 1
        assert "encerrar" in [c[0] for c in oport.chamadas]

    def test_encerramento_cancela_a_cotacao(self, tracking) -> None:
        """O defeito relatado: oportunidade encerrada, cotação seguia aberta."""
        docs = DocumentosFalso(tem_cotacao=True)
        oport = OportunidadesFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=99)),
            docs,
            oport,
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_StatusWBC="40"))

        assert ("cancelar", TipoDocumento.COTACAO, 55) in docs.chamadas
        assert "encerrar" in [c[0] for c in oport.chamadas]

    def test_recusa_do_sap_no_cancelamento_nao_impede_o_encerramento(self, tracking) -> None:
        """A cotação pode ter sido fechada por alguém entre a leitura e a
        escrita. Vira aviso; o encerramento — o que o legado já fazia — segue."""

        class DocumentosQueRecusam(DocumentosFalso):
            def cancelar(self, tipo, doc_entry):
                raise RuntimeError("SAP recusou o cancelamento")

        oport = OportunidadesFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=99)),
            DocumentosQueRecusam(tem_cotacao=True),
            oport,
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_StatusWBC="40"))

        assert "encerrar" in [c[0] for c in oport.chamadas]

    def test_sem_acao_nenhuma_nao_toca_no_status(self, tracking) -> None:
        oport = OportunidadesFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=5)),
            DocumentosFalso(),
            oport,
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_StatusWBC="0"))

        assert oport.chamadas == []


class TestVinculoComOSnapshot:
    """`U_INO_ORCAMENTO` liga o documento ao snapshot do OrcDetalhe.

    O campo estava saindo vazio nas cotações criadas por esta integração,
    enquanto as do legado o trazem preenchido com o `DocEntry` do registro em
    `@INO_ORCAM`. Sem ele, o documento no SAP não aponta de volta para o
    retrato do orçamento que o originou.

    Isso impõe uma ordem: **o snapshot precisa existir antes do documento.**
    """

    def _payload_criado(self, docs) -> dict[str, Any]:
        for chamada in docs.chamadas:
            if chamada[0] == "criar":
                return chamada[2]
        raise AssertionError("nenhum documento foi criado")

    def test_documento_leva_o_docentry_do_snapshot(self, tracking) -> None:
        docs, orcdet = DocumentosFalso(), OrcDetalheFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            docs,
            OportunidadesFalso(),
            orcdet,
        ).processar(_oportunidade())

        assert self._payload_criado(docs)["U_INO_ORCAMENTO"] == 513888

    def test_snapshot_e_gravado_antes_do_documento(self, tracking) -> None:
        """A ordem é o requisito, não um detalhe: o DocEntry precisa existir."""
        ordem: list[str] = []

        docs = DocumentosFalso()
        orcdet = OrcDetalheFalso()
        criar_original = docs.criar
        snapshot_original = orcdet.criar_snapshot

        def criar_espiao(tipo, dados):
            ordem.append("documento")
            return criar_original(tipo, dados)

        def snapshot_espiao(dados):
            ordem.append("snapshot")
            return snapshot_original(dados)

        docs.criar = criar_espiao
        orcdet.criar_snapshot = snapshot_espiao

        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            docs,
            OportunidadesFalso(),
            orcdet,
        ).processar(_oportunidade())

        assert ordem[0] == "snapshot", f"ordem observada: {ordem}"

    def test_falha_no_snapshot_nao_impede_o_documento(self, tracking) -> None:
        """Perder o histórico é ruim; perder a cotação é pior.

        O legado se comporta assim: sem o valor, o UDF vai vazio e o documento
        é criado do mesmo jeito.
        """
        docs = DocumentosFalso()
        resultado = _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(falhar=True),
        ).processar(_oportunidade())

        assert resultado.sucesso
        payload = self._payload_criado(docs)
        assert "U_INO_ORCAMENTO" not in payload

    def test_campo_numerico_e_omitido_e_nao_zerado(self, tracking) -> None:
        """`U_INO_ORCAMENTO` é NUMBER no SAP; mandar vazio é erro `SAP 205`.

        Omitir é diferente de mandar `''` ou `0` — o primeiro deixa o campo em
        paz, os outros afirmam um valor que não temos.
        """
        docs = DocumentosFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(falhar=True),
        ).processar(_oportunidade())

        payload = self._payload_criado(docs)
        assert "U_INO_ORCAMENTO" not in payload
        # Zero em outros campos é legítimo (`U_INO_VL_MT` é numérico e pode ser
        # zero de verdade); o que não pode é o vínculo virar zero.
        assert payload.get("U_INO_ORCAMENTO") is None

    def test_sem_gravar_snapshot_o_campo_nao_vai(self, tracking) -> None:
        docs = DocumentosFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
            gravar_snapshot=False,
        ).processar(_oportunidade())

        assert "U_INO_ORCAMENTO" not in self._payload_criado(docs)


class TestDocumentoSemValor:
    """O SAP recusa documento sem valor; a integração não tenta.

    `HTTP 400 | SAP -5002 | "Document total value must be zero or greater than
    zero"`. Chegar até lá transformava um orçamento ainda sem itens — situação
    normal no WBC — em erro de ciclo no dashboard.
    """

    def test_orcamento_sem_itens_nao_vira_cotacao(self, tracking) -> None:
        docs = DocumentosFalso()
        resultado = _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30, itens=())),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        assert docs.chamadas == []
        assert resultado.sucesso
        assert resultado.erro == ""

    def test_itens_sem_valor_tambem_nao_viram_cotacao(self, tracking) -> None:
        """O orçamento tem item, mas o item não chegou a preço."""
        docs = DocumentosFalso()
        _processador(
            tracking,
            WbcFalso(
                _orcamento(
                    sitcode=30,
                    itens=(ItemOrcamentoWbc(orcitm=1, produto="P", valor=Decimal(0)),),
                )
            ),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        assert docs.chamadas == []

    def test_o_motivo_fica_no_acompanhamento(self, tracking) -> None:
        """Sem isso o orçamento simplesmente some: nada criado, nada dito."""
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30, itens=())),
            DocumentosFalso(),
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        mensagens = [e.mensagem for e in tracking.eventos("00123316")]
        assert any("sem itens" in m for m in mensagens)

    def test_nao_espelha_status_de_documento_que_nao_existe(self, tracking) -> None:
        """A ação não pode contar como executada.

        Se contasse, o status da oportunidade seria espelhado como se houvesse
        cotação, e o teto de escrita gastaria uma vaga com um documento que
        nunca chegou ao SAP.
        """
        oport = OportunidadesFalso()
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30, itens=())),
            DocumentosFalso(),
            oport,
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_StatusWBC="0"))

        assert oport.chamadas == []

    def test_valor_pequeno_nao_e_confundido_com_zero(self, tracking) -> None:
        """Centavos são valor. O total é somado em `Decimal` justamente para
        que um documento legítimo de valor baixo não seja barrado."""
        docs = DocumentosFalso()
        _processador(
            tracking,
            WbcFalso(
                _orcamento(
                    sitcode=30,
                    itens=(ItemOrcamentoWbc(orcitm=1, produto="P", valor=Decimal("0.01")),),
                )
            ),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        assert [c[0] for c in docs.chamadas] == ["criar"]

    def test_pedido_tambem_e_barrado(self, tracking) -> None:
        """A recusa do SAP vale para os dois documentos."""
        docs = DocumentosFalso(tem_cotacao=True)
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60, itens=())),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_StatusWBC="30"))

        assert [c[0] for c in docs.chamadas] == []


class TestParceiroCorrigidoInexistente:
    """`ChecaPN` do legado: o PN novo tem de existir antes de cancelar o pedido.

    A ordem em `cancelar_e_recriar` é cancelar-depois-criar, e ela é
    deliberada. Se o parceiro corrigido não existir, cancelar deixa o orçamento
    sem pedido nenhum — pior que o estado inicial, e irreversível.
    """

    class ParceirosFalso:
        def __init__(self, existentes: set[str]) -> None:
            self._existentes = existentes
            self.consultados: list[str] = []

        def existe(self, card_code: str) -> bool:
            self.consultados.append(card_code)
            return card_code in self._existentes

    def _rodar(self, tracking, parceiros):
        docs = DocumentosFalso(
            tem_cotacao=True, tem_pedido=True, parceiros={TipoDocumento.PEDIDO: "C001"}
        )
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
            parceiros=parceiros,
        ).processar(
            # `U_INO_Update="Y"`: sem ele a combinação com `PN_Correc` significa
            # "já resolvido à mão", o pedido fica congelado e a troca — que é o
            # que esta classe testa — nem chega a ser considerada.
            _oportunidade(U_INO_PN_Correc="C999", CardCode="C001", U_INO_Update="Y")
        )
        return docs

    def test_pn_inexistente_nao_cancela_o_pedido(self, tracking) -> None:
        docs = self._rodar(tracking, self.ParceirosFalso(set()))
        assert docs.chamadas == []

    def test_pn_existente_refaz_normalmente(self, tracking) -> None:
        docs = self._rodar(tracking, self.ParceirosFalso({"C999"}))
        assert any(c[0] == "cancelar_e_recriar" for c in docs.chamadas)

    def test_o_motivo_fica_no_acompanhamento(self, tracking) -> None:
        self._rodar(tracking, self.ParceirosFalso(set()))
        mensagens = [e.mensagem for e in tracking.eventos("00123316")]
        assert any("não existe no SAP" in m for m in mensagens)

    def test_sem_troca_nao_consulta_parceiro(self, tracking) -> None:
        """Uma ida à rede por orçamento seria caro e inútil."""
        parceiros = self.ParceirosFalso({"C001"})
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            DocumentosFalso(),
            OportunidadesFalso(),
            OrcDetalheFalso(),
            parceiros=parceiros,
        ).processar(_oportunidade())

        assert parceiros.consultados == []


class TestPesoNoPedidoCriado:
    """O peso chega ao pedido de verdade — do WBC até o payload.

    O teste de domínio prova a regra; este prova a fiação: que o processador
    lê os pesos e os passa **para o pedido e só para ele**.
    """

    def test_pedido_criado_leva_o_peso_unitario(self, tracking) -> None:
        """O orçamento de teste tem quantidade 2, e a divisão aparece:
        `floor((760,65 / 2) × 1,10) = floor(418,3575) = 418`."""
        docs = DocumentosFalso(tem_cotacao=True)
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60), pesos={1: Decimal("760.65")}),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_StatusWBC="60"))

        criados = [c for c in docs.chamadas if c[0] == "criar" and c[1] is TipoDocumento.PEDIDO]
        assert criados, "o pedido deveria ter sido criado"
        linha = criados[0][2]["DocumentLines"][0]
        assert linha["Quantity"] == 2.0
        assert linha["Weight1"] == 418.0

    def test_a_cotacao_do_mesmo_ciclo_nao_leva(self, tracking) -> None:
        """SitCode 60 sem pedido atualiza a cotação e cria o pedido no mesmo
        ciclo: é o cenário que revelaria um vazamento do peso para a cotação."""
        docs = DocumentosFalso(tem_cotacao=True)
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60), pesos={1: Decimal("760.65")}),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_StatusWBC="60"))

        cotacoes = [c for c in docs.chamadas if c[1] is TipoDocumento.COTACAO]
        assert cotacoes, "a cotação deveria ter sido atualizada"
        for chamada in cotacoes:
            for linha in chamada[2].get("DocumentLines") or ():
                assert "Weight1" not in linha

    def test_sem_arvore_o_pedido_sai_sem_o_campo(self, tracking) -> None:
        """As 2 linhas em 316 que não têm árvore: o pedido é criado do mesmo
        jeito, com o peso do cadastro do item."""
        docs = DocumentosFalso(tem_cotacao=True)
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_StatusWBC="60"))

        criados = [c for c in docs.chamadas if c[0] == "criar" and c[1] is TipoDocumento.PEDIDO]
        assert criados
        assert "Weight1" not in criados[0][2]["DocumentLines"][0]


class TestPedidoFechado:
    """O ciclo não escreve em pedido fechado — verificado no orquestrador.

    A regra tem teste próprio em `tests/domain/test_sitcode.py`. O que só
    aparece aqui é o caminho completo: o `DocStatus` que veio da consulta chega
    ao estado e nenhuma chamada de escrita sai.
    """

    def test_nao_atualiza_pedido_fechado(self, tracking) -> None:
        """O caso `00124268`: revisão mais nova no WBC, pedido fechado no SAP."""
        docs = DocumentosFalso(
            tem_cotacao=True,
            tem_pedido=True,
            revisoes={TipoDocumento.PEDIDO: "A"},
            fechados={TipoDocumento.PEDIDO},
        )
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60, revisao="D")),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        assert not any(c[0] == "atualizar" for c in docs.chamadas)
        assert not any(c[0] == "cancelar_e_recriar" for c in docs.chamadas)

    def test_nao_cancela_pedido_fechado_na_troca_de_pn(self, tracking) -> None:
        docs = DocumentosFalso(
            tem_cotacao=True,
            tem_pedido=True,
            parceiros={TipoDocumento.PEDIDO: "C001"},
            fechados={TipoDocumento.PEDIDO},
        )
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_PN_Correc="C999", CardCode="C001", U_INO_Update="Y"))

        assert docs.chamadas == []

    def test_a_regra_fica_no_acompanhamento(self, tracking) -> None:
        """Quem for investigar precisa achar o motivo, não um silêncio."""
        docs = DocumentosFalso(
            tem_cotacao=True,
            tem_pedido=True,
            revisoes={TipoDocumento.PEDIDO: "A"},
            fechados={TipoDocumento.PEDIDO},
        )
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60, revisao="D")),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        registro = tracking.obter("00123316")
        assert registro is not None
        assert "pedido_fechado_no_sap" in registro.regra_aplicada

    def test_pedido_aberto_continua_sendo_atualizado(self, tracking) -> None:
        """A guarda do pedido fechado não pode congelar o que está aberto.

        `U_INO_Update = 'Y'` na oportunidade porque, desde a regra confirmada
        pelo negócio, atualizar exige as duas condições — revisão nova **e**
        alteração pedida. Sem isso o teste passaria pela guarda errada.
        """
        docs = DocumentosFalso(
            tem_cotacao=True, tem_pedido=True, revisoes={TipoDocumento.PEDIDO: "A"}
        )
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=60, revisao="D")),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_Update="Y"))

        assert any(c[0] == "atualizar" for c in docs.chamadas)


class TestDocumentosJaLidosFechado:
    """A tradução do `DocStatus` que a consulta do HANA devolve."""

    def _lidos(self, **pedido: Any):
        from wbcpython.application.documentos_lidos import DocumentosJaLidos

        return DocumentosJaLidos({"pedido": {"DocEntry": 1, **pedido}})

    def test_c_e_fechado(self) -> None:
        assert self._lidos(DocStatus="C").esta_fechado(TipoDocumento.PEDIDO, "x") is True

    def test_o_e_aberto(self) -> None:
        assert self._lidos(DocStatus="O").esta_fechado(TipoDocumento.PEDIDO, "x") is False

    def test_sem_documento_nao_e_fechado(self) -> None:
        """ "Não existe" não é "está fechado" — confundir os dois impediria a
        criação do primeiro pedido."""
        from wbcpython.application.documentos_lidos import DocumentosJaLidos

        assert DocumentosJaLidos({}).esta_fechado(TipoDocumento.PEDIDO, "x") is False

    @pytest.mark.parametrize("status", [None, "", "   ", "X", "bost_Whatever"])
    def test_status_desconhecido_falha_fechado(self, status) -> None:
        """Status que não é nem aberto nem fechado trata o documento como
        **fechado**, e a integração não escreve nele.

        Este teste já afirmou o contrário. O defeito do `00124268` foi uma
        coluna que não era lida; uma guarda que falhasse aberta evaporaria em
        silêncio no dia em que a coluna sumisse da consulta — sem nenhum teste
        falhar, e voltando a escrever em pedido fechado.
        """
        assert self._lidos(DocStatus=status).esta_fechado(TipoDocumento.PEDIDO, "x") is True

    def test_campo_ausente_falha_fechado(self) -> None:
        assert self._lidos().esta_fechado(TipoDocumento.PEDIDO, "x") is True

    def test_o_status_desconhecido_avisa(self, caplog) -> None:
        """Falhar fechado sem avisar esconderia a causa: o orçamento pararia de
        ser tratado e ninguém saberia por quê."""
        import logging

        with caplog.at_level(logging.WARNING):
            self._lidos(DocStatus="X").esta_fechado(TipoDocumento.PEDIDO, "00124268")

        # `getMessage()` e não `message % args`: o `%` cru estoura quando a
        # mensagem tem literais de formatação, e o que interessa é o texto final.
        textos = [r.getMessage() for r in caplog.records]
        assert any("não reconhecido" in texto for texto in textos), textos
        assert any("00124268" in texto for texto in textos), textos


class TestCicloSimulado:
    """`somente_leitura`: decide e registra tudo, e não escreve nada no SAP.

    Existe para o painel poder mostrar a janela real antes de qualquer
    documento ser tocado — em produção, antes do primeiro ciclo de verdade.

    A garantia que importa é negativa, então é ela que os testes atacam: nada
    de `criar`, `atualizar`, `cancelar` ou `vincular` em nenhum repositório,
    para os casos que **mais** escreveriam se o ensaio falhasse.
    """

    def test_nao_cria_cotacao(self, tracking) -> None:
        docs, oport, orcdet = DocumentosFalso(), OportunidadesFalso(), OrcDetalheFalso()
        resultado = _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            docs,
            oport,
            orcdet,
            somente_leitura=True,
        ).processar(_oportunidade())

        assert resultado.sucesso
        assert docs.chamadas == []
        assert oport.chamadas == []
        # A decisão continua sendo tomada: é dela que sai o número do painel.
        assert resultado.decisao.tem_acao
        assert resultado.acoes_executadas == ()

    def test_nao_encerra_oportunidade(self, tracking) -> None:
        """SitCode 99 é o caso mais destrutivo da janela de produção: cancela a
        cotação e marca a oportunidade como perdida. No ensaio, nenhum dos dois.
        """
        docs = DocumentosFalso(tem_cotacao=True)
        oport, orcdet = OportunidadesFalso(), OrcDetalheFalso()
        resultado = _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=99)),
            docs,
            oport,
            orcdet,
            somente_leitura=True,
        ).processar(_oportunidade())

        assert docs.chamadas == []
        assert oport.chamadas == []
        assert resultado.acoes_executadas == ()

    def test_registra_o_acompanhamento_mesmo_assim(self, tracking) -> None:
        """Sem isto o ensaio não serviria para nada: é o registro que enche o
        painel."""
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            DocumentosFalso(),
            OportunidadesFalso(),
            OrcDetalheFalso(),
            somente_leitura=True,
        ).processar(_oportunidade())

        registro = tracking.obter("00123316")
        assert registro is not None
        assert registro.regra_aplicada

    def test_o_historico_diz_que_foi_simulacao(self, tracking) -> None:
        """Uma linha parada em "Pendente" é indistinguível de uma que falhou em
        silêncio. O evento é o que separa as duas, semanas depois."""
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            DocumentosFalso(),
            OportunidadesFalso(),
            OrcDetalheFalso(),
            somente_leitura=True,
        ).processar(_oportunidade())

        mensagens = " ".join(e.mensagem for e in tracking.eventos("00123316"))
        assert "Simulação" in mensagens
        assert "não** executada" in mensagens

    def test_nao_rebaixa_o_status_de_um_ciclo_real(self, tracking) -> None:
        """O ensaio não desfez nada no SAP, então não pode dizer que desfez.

        Um orçamento já integrado que volte a ter ação (revisão nova, por
        exemplo) continua exibindo o que o ciclo real conseguiu — e o painel
        não passa a anunciar um retrocesso que não aconteceu.
        """
        tracking.registrar_verificacao("00123316", status=StatusIntegracao.COTACAO_CRIADA)
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=30)),
            DocumentosFalso(),
            OportunidadesFalso(),
            OrcDetalheFalso(),
            somente_leitura=True,
        ).processar(_oportunidade())

        assert tracking.obter("00123316").status is StatusIntegracao.COTACAO_CRIADA

    def test_sem_a_bandeira_escreve_normalmente(self, tracking) -> None:
        """O padrão não pode ser o ensaio: a integração existe para escrever."""
        docs, oport, orcdet = DocumentosFalso(), OportunidadesFalso(), OrcDetalheFalso()
        _processador(tracking, WbcFalso(_orcamento(sitcode=30)), docs, oport, orcdet).processar(
            _oportunidade()
        )

        assert docs.chamadas != []


class TestPedidoCongelado:
    """`U_INO_Congelado = 'Y'`: o cabeçalho vai, as linhas ficam.

    Atualizar um pedido **refaz as linhas** — apaga todas e reconstrói a partir
    do orçamento (`ServiceProcess.cs:600-672`). Edição manual em linha morre
    junto. O campo existe para impedir isso, e em produção **os 2.581 pedidos
    vigentes estão em `'Y'`**: sem esta guarda, a integração destruiria a
    edição manual dos 120 abertos no primeiro ciclo que os alcançasse.
    """

    def _atualizar(self, *, congelado: bool):
        docs = DocumentosFalso(
            tem_cotacao=True,
            tem_pedido=True,
            revisoes={TipoDocumento.PEDIDO: "A"},
            congelados=[TipoDocumento.PEDIDO] if congelado else [],
        )
        repo = RepositorioTracking.a_partir_da_url("sqlite:///:memory:")
        _processador(
            repo,
            WbcFalso(_orcamento(sitcode=60, revisao="D")),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())
        return [c for c in docs.chamadas if c[0] == "atualizar"]

    def test_congelado_nao_envia_linhas(self) -> None:
        chamadas = self._atualizar(congelado=True)

        assert chamadas, "o pedido continua sendo atualizado — só sem as linhas"
        corpo = chamadas[-1][2]
        assert "DocumentLines" not in corpo

    def test_congelado_ainda_carimba_a_revisao(self) -> None:
        """A parte que parece detalhe e não é.

        No legado o cabeçalho é atribuído **antes** do `if (Congelado != "Y")` e
        o `Update()` vem depois dele. Sem o carimbo, a revisão do WBC seguiria
        eternamente mais nova que a do pedido: o ciclo tentaria atualizar o
        mesmo pedido para sempre, consumindo o teto de escrita a cada volta.
        """
        corpo = self._atualizar(congelado=True)[-1][2]

        assert corpo["U_INO_VERSAOWBC"] == "D"

    def test_sem_congelamento_as_linhas_vao(self) -> None:
        """O padrão não pode ser congelar: seria a integração parar de
        atualizar pedido sem ninguém pedir."""
        corpo = self._atualizar(congelado=False)[-1][2]

        assert corpo.get("DocumentLines")

    def test_a_criacao_nao_consulta_o_campo(self) -> None:
        """Pedido que ainda não existe não tem linha a proteger — e no legado o
        ramo da criação não lê o campo."""
        docs = DocumentosFalso(
            tem_cotacao=True, tem_pedido=False, congelados=[TipoDocumento.PEDIDO]
        )
        repo = RepositorioTracking.a_partir_da_url("sqlite:///:memory:")
        _processador(
            repo,
            WbcFalso(_orcamento(sitcode=60)),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade())

        criadas = [c for c in docs.chamadas if c[0] == "criar"]
        assert criadas, "o pedido precisa nascer"
        assert criadas[-1][2].get("DocumentLines")


class TestConferenciaDaCotacaoAtualizada:
    """Um `204 No Content` não prova que as linhas mudaram.

    Nas cotações `00125616` e `00125577` o SAP aceitou o `PATCH`, gravou o
    cabeçalho e **deixou as linhas como estavam** — uma delas a preço zero. O
    documento foi para o cliente R$ 1.604,38 e R$ 39.802,60 abaixo do
    orçamento, e a integração registrou sucesso: o erro chegou pelo documento,
    não pelo painel.

    Cancelar e recriar é o caminho comprovado — foi o que o usuário fez à mão
    no `00125616`, e a cotação nova saiu certa.
    """

    def _rodar(self, tracking, *, total_apos_atualizar=None):
        docs = DocumentosFalso(tem_cotacao=True, total_apos_atualizar=total_apos_atualizar)
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=40, revisao="A")),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_StatusWBC="30"))
        return docs

    def test_divergencia_refaz_a_cotacao(self, tracking) -> None:
        docs = self._rodar(tracking, total_apos_atualizar=0)

        assert any(c[0] == "cancelar_e_recriar" for c in docs.chamadas)

    def test_o_documento_refeito_leva_os_valores_do_orcamento(self, tracking) -> None:
        """Refazer com o payload errado só trocaria um documento errado por
        outro."""
        docs = self._rodar(tracking, total_apos_atualizar=0)

        refeito = next(c for c in docs.chamadas if c[0] == "cancelar_e_recriar")
        linhas = refeito[2]["DocumentLines"]
        assert linhas and all(l["Price"] > 0 for l in linhas)

    def test_o_historico_registra_o_motivo(self, tracking) -> None:
        """Sem isto, um documento cancelado e refeito não teria explicação
        semanas depois."""
        self._rodar(tracking, total_apos_atualizar=0)

        mensagens = " ".join(e.mensagem for e in tracking.eventos("00123316"))
        assert "sem efeito nas linhas" in mensagens
        assert "cancelada e recriada" in mensagens.lower()

    def test_quando_grava_certo_nao_refaz(self, tracking) -> None:
        """O caminho normal não pode cancelar documento por precaução: cancelar
        cotação não se desfaz."""
        docs = self._rodar(tracking)

        assert any(c[0] == "atualizar" for c in docs.chamadas)
        assert not any(c[0] == "cancelar_e_recriar" for c in docs.chamadas)

    def test_diferenca_de_centavo_nao_refaz(self, tracking) -> None:
        """O SAP arredonda por linha; um centavo é arredondamento, não linha
        perdida. As divergências reais foram de milhares de reais."""
        docs = DocumentosFalso(tem_cotacao=True)
        _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=40, revisao="A")),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_StatusWBC="30"))
        esperado = sum(
            Decimal(str(l["Quantity"])) * Decimal(str(l["Price"]))
            for c in docs.chamadas
            if c[0] == "atualizar"
            for l in c[2]["DocumentLines"]
        )

        docs2 = self._rodar(tracking, total_apos_atualizar=esperado - Decimal("0.01"))
        assert not any(c[0] == "cancelar_e_recriar" for c in docs2.chamadas)

    def test_falha_ao_reler_nao_derruba_o_ciclo(self, tracking) -> None:
        """A conferência é uma rede de segurança; se ela mesma falhar, o ciclo
        segue e o log diz que a atualização ficou sem conferir."""

        class DocsQueNaoRelem(DocumentosFalso):
            def total_das_linhas(self, tipo, doc_entry):
                raise ConnectionError("sem rota para o host")

        docs = DocsQueNaoRelem(tem_cotacao=True)
        resultado = _processador(
            tracking,
            WbcFalso(_orcamento(sitcode=40, revisao="A")),
            docs,
            OportunidadesFalso(),
            OrcDetalheFalso(),
        ).processar(_oportunidade(U_INO_StatusWBC="30"))

        assert resultado.sucesso
        assert not any(c[0] == "cancelar_e_recriar" for c in docs.chamadas)
