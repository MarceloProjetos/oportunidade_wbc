"""Testes da máquina de estados do SitCode.

Cada teste corresponde a um ramo do encadeamento de `if/else` do `Main` legado
(`Program.cs`). O objetivo é que qualquer mudança futura na regra de negócio
quebre um teste com nome autoexplicativo, em vez de passar despercebida.
"""

from __future__ import annotations

import pytest

from wbcpython.domain.sitcode import (
    Acao,
    EstadoIntegracao,
    decidir,
)


def estado(**campos: object) -> EstadoIntegracao:
    """Constrói um estado com padrões neutros, sobrescrevendo o que interessa."""
    base: dict[str, object] = {"orcamento": "00123456", "sitcode_wbc": 30}
    base.update(campos)
    return EstadoIntegracao(**base)  # type: ignore[arg-type]


#: Tudo que cria, altera ou cancela documento no SAP. Serve para afirmar
#: "não mexeu em documento nenhum" sem listar as ações a cada teste — e sem
#: exigir `acoes == ()`, que negaria o espelhamento de status, que é legítimo.
_ACOES_DE_DOCUMENTO = frozenset(
    {
        Acao.CRIAR_COTACAO,
        Acao.ATUALIZAR_COTACAO,
        Acao.CANCELAR_E_RECRIAR_COTACAO,
        Acao.CANCELAR_COTACAO_NO_ENCERRAMENTO,
        Acao.CRIAR_PEDIDO,
        Acao.ATUALIZAR_PEDIDO,
        Acao.CANCELAR_E_RECRIAR_PEDIDO,
    }
)


class TestFiltroDeSitCode:
    @pytest.mark.parametrize("sitcode", [0, 1, 10])
    def test_sitcode_ate_10_nao_e_processado(self, sitcode: int) -> None:
        decisao = decidir(estado(sitcode_wbc=sitcode))
        assert decisao.acoes == ()
        assert decisao.regra == "sitcode_abaixo_do_minimo"

    def test_sitcode_11_ja_e_processado(self) -> None:
        # O legado usa `> 10`, não `>= 10`.
        assert decidir(estado(sitcode_wbc=11, tem_cotacao=False)).tem_acao


class TestSitCode30OrcamentoEmitido:
    def test_sem_cotacao_cria(self) -> None:
        decisao = decidir(estado(sitcode_wbc=30, tem_cotacao=False))
        assert Acao.CRIAR_COTACAO in decisao.acoes
        assert Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE in decisao.acoes
        assert decisao.regra == "emitido_sem_cotacao"

    @pytest.mark.parametrize("sitcode_sap", ["40", "55"])
    def test_com_cotacao_e_sap_em_revisao_cancela_e_recria(self, sitcode_sap: str) -> None:
        # O WBC retrocedeu para 30 enquanto o SAP já registrava revisão.
        decisao = decidir(estado(sitcode_wbc=30, tem_cotacao=True, sitcode_sap=sitcode_sap))
        assert Acao.CANCELAR_E_RECRIAR_COTACAO in decisao.acoes
        assert decisao.regra == "emitido_apos_revisao_no_sap"

    def test_com_cotacao_e_sap_sincronizado_nao_faz_nada(self) -> None:
        decisao = decidir(estado(sitcode_wbc=30, tem_cotacao=True, sitcode_sap="30"))
        assert decisao.acoes == ()
        assert decisao.regra == "emitido_ja_sincronizado"


class TestSitCode40e55Revisao:
    @pytest.mark.parametrize("sitcode_wbc", [40, 55])
    def test_sap_em_30_atualiza_a_cotacao(self, sitcode_wbc: int) -> None:
        decisao = decidir(estado(sitcode_wbc=sitcode_wbc, tem_cotacao=True, sitcode_sap="30"))
        assert Acao.ATUALIZAR_COTACAO in decisao.acoes
        assert decisao.regra == "revisao_sobre_cotacao_emitida"

    @pytest.mark.parametrize("sitcode_wbc", [40, 55])
    def test_sap_em_40_com_revisao_mais_nova_recria(self, sitcode_wbc: int) -> None:
        decisao = decidir(
            estado(
                sitcode_wbc=sitcode_wbc,
                tem_cotacao=True,
                sitcode_sap="40",
                revisao_wbc="C",
                revisao_cotacao_sap="B",
            )
        )
        assert Acao.CANCELAR_E_RECRIAR_COTACAO in decisao.acoes
        assert decisao.regra == "revisao_mais_nova_recria_cotacao"

    @pytest.mark.parametrize(
        ("revisao_wbc", "revisao_sap"),
        [("B", "B"), ("A", "B"), ("", "A"), ("", "")],
    )
    def test_sap_em_40_sem_revisao_mais_nova_congela(
        self, revisao_wbc: str, revisao_sap: str
    ) -> None:
        decisao = decidir(
            estado(
                sitcode_wbc=40,
                tem_cotacao=True,
                sitcode_sap="40",
                revisao_wbc=revisao_wbc,
                revisao_cotacao_sap=revisao_sap,
            )
        )
        assert decisao.acoes == ()
        assert decisao.regra == "revisao_congelada"

    def test_sem_cotacao_cai_no_ramo_de_criacao(self) -> None:
        decisao = decidir(estado(sitcode_wbc=40, tem_cotacao=False))
        assert Acao.CRIAR_COTACAO in decisao.acoes
        assert decisao.regra == "sem_cotacao_cria"


class TestRamoSemCotacao:
    @pytest.mark.parametrize("sitcode", [20, 40, 55, 60])
    def test_qualquer_sitcode_sem_cotacao_cria_cotacao(self, sitcode: int) -> None:
        """No legado este ramo vinha com uma tautologia; o efeito é este."""
        decisao = decidir(estado(sitcode_wbc=sitcode, tem_cotacao=False))
        assert Acao.CRIAR_COTACAO in decisao.acoes
        assert decisao.regra == "sem_cotacao_cria"

    @pytest.mark.parametrize("sitcode", [70, 90, 99])
    def test_orcamento_encerrado_sem_cotacao_nao_cria_nada(self, sitcode: int) -> None:
        """A guarda que impede o ciclo criar/cancelar/criar.

        No legado, um orçamento encerrado sem cotação caía aqui e a cotação era
        criada — situação que na prática não ocorria, porque o legado nunca
        cancelava cotação e portanto `tem_cotacao` nunca voltava a ser falso.

        Cancelando no encerramento, o estado passa a existir: na execução
        seguinte a cotação não está mais lá. Sem esta guarda, a integração
        recriaria a cotação de um orçamento perdido só para cancelá-la de novo
        no ciclo seguinte, para sempre.
        """
        decisao = decidir(estado(sitcode_wbc=sitcode, tem_cotacao=False))
        assert Acao.CRIAR_COTACAO not in decisao.acoes


class TestSitCode60Pedido:
    def test_sem_pedido_atualiza_cotacao_e_cria_pedido(self) -> None:
        decisao = decidir(estado(sitcode_wbc=60, tem_cotacao=True, tem_pedido=False))
        assert Acao.ATUALIZAR_COTACAO in decisao.acoes
        assert Acao.CRIAR_PEDIDO in decisao.acoes
        # A ordem importa: a cotação é atualizada antes de gerar o pedido.
        assert decisao.acoes.index(Acao.ATUALIZAR_COTACAO) < decisao.acoes.index(Acao.CRIAR_PEDIDO)

    def test_pedido_existente_com_revisao_mais_nova_e_update_atualiza(self) -> None:
        """Revisão nova **e** `U_INO_Update = 'Y'`: as duas condições.

        O `alterado=True` passou a ser necessário quando o negócio confirmou
        que o `Update` congela o pedido — antes o teste passava com `'N'`,
        porque a decisão olhava só a revisão.
        """
        decisao = decidir(
            estado(
                sitcode_wbc=60,
                tem_cotacao=True,
                tem_pedido=True,
                alterado=True,
                revisao_wbc="B",
                revisao_pedido_sap="A",
            )
        )
        assert Acao.ATUALIZAR_PEDIDO in decisao.acoes

    def test_pedido_existente_sem_revisao_nova_nao_mexe_no_pedido(self) -> None:
        decisao = decidir(
            estado(
                sitcode_wbc=60,
                tem_cotacao=True,
                tem_pedido=True,
                alterado=False,
                revisao_wbc="A",
                revisao_pedido_sap="A",
            )
        )
        assert Acao.ATUALIZAR_PEDIDO not in decisao.acoes
        assert Acao.CRIAR_PEDIDO not in decisao.acoes
        # ...mas o status da oportunidade continua sendo espelhado.
        assert Acao.ATUALIZAR_STATUS_OPORTUNIDADE in decisao.acoes


class TestPedidoFechadoNoSap:
    """`DocStatus = 'C'`: o SAP recusa alteração e recusa cancelamento.

    Não é caso raro. Em produção, **2.452 dos 2.568** pedidos vigentes estão
    fechados — é o estado normal de um pedido entregue ou faturado. A
    integração não enxergava isso porque `DocStatus` não era lido.
    """

    def _fechado(self, **campos: object):
        base: dict[str, object] = {
            "sitcode_wbc": 60,
            "tem_cotacao": True,
            "tem_pedido": True,
            "pedido_fechado": True,
        }
        base.update(campos)
        return decidir(estado(**base))

    def test_revisao_mais_nova_nao_atualiza_pedido_fechado(self) -> None:
        """O caso `00124268`: pedido 83988 fechado, revisão `'A'` no SAP e mais
        nova no WBC. Caía em `atualiza_pedido` e o SAP recusava a gravação."""
        decisao = self._fechado(revisao_wbc="D", revisao_pedido_sap="A")
        assert Acao.ATUALIZAR_PEDIDO not in decisao.acoes
        assert "pedido_fechado_no_sap" in decisao.regra

    def test_troca_de_pn_nao_cancela_pedido_fechado(self) -> None:
        """Cancelar também é recusado — e cancelar-e-recriar deixaria o
        orçamento sem pedido nenhum se a recriação falhasse."""
        decisao = self._fechado(
            alterado=True,
            parceiro_atual="C001",
            parceiro_novo="C999",
            parceiro_pedido_sap="C001",
        )
        assert Acao.CANCELAR_E_RECRIAR_PEDIDO not in decisao.acoes

    def test_troca_impossivel_ganha_regra_propria_e_motivo(self) -> None:
        """Troca pedida sobre pedido fechado é caso que **nunca** poderá ser
        atendido, e precisa de gente.

        Congelar em silêncio, com a mesma regra dos congelamentos benignos,
        deixaria esse caso invisível entre os 243 da janela. Ele sai com regra
        própria e com o motivo escrito.
        """
        decisao = self._fechado(
            alterado=True,
            parceiro_atual="C001",
            parceiro_novo="C999",
            parceiro_pedido_sap="C001",
        )
        assert decisao.regra.startswith("troca_de_pn_impossivel_pedido_fechado")
        assert decisao.motivos
        assert "tratamento manual" in decisao.motivos[0]
        assert "C999" in decisao.motivos[0]

    def test_congelamento_comum_tambem_explica_o_motivo(self) -> None:
        """Quem investiga precisa achar a razão, não um silêncio."""
        decisao = self._fechado(revisao_wbc="D", revisao_pedido_sap="A")
        assert decisao.motivos
        assert "fechado" in decisao.motivos[0].lower()

    def test_nenhuma_acao_toca_documento(self) -> None:
        decisao = self._fechado(revisao_wbc="D", revisao_pedido_sap="A")
        assert _ACOES_DE_DOCUMENTO.isdisjoint(decisao.acoes)

    def test_o_status_continua_sendo_espelhado(self) -> None:
        """Congela o **pedido**, não a oportunidade: espelhar o SitCode não
        toca no documento."""
        decisao = self._fechado(sitcode_sap="40", revisao_wbc="D", revisao_pedido_sap="A")
        assert Acao.ATUALIZAR_STATUS_OPORTUNIDADE in decisao.acoes

    def test_pedido_aberto_segue_o_fluxo_normal(self) -> None:
        """A guarda do pedido fechado não pode congelar o que está aberto.

        `alterado=True` porque desde a regra do `U_INO_Update` o fluxo normal
        exige as duas condições; sem isso, este teste passaria pela guarda
        errada e deixaria de vigiar a que devia.
        """
        decisao = decidir(
            estado(
                sitcode_wbc=60,
                tem_cotacao=True,
                tem_pedido=True,
                pedido_fechado=False,
                alterado=True,
                revisao_wbc="D",
                revisao_pedido_sap="A",
            )
        )
        assert Acao.ATUALIZAR_PEDIDO in decisao.acoes
        assert "atualiza_pedido" in decisao.regra

    def test_sem_pedido_a_marca_nao_impede_a_criacao(self) -> None:
        """ "Fechado" não é "inexistente". Sem pedido, cria — senão o orçamento
        ficaria sem pedido para sempre por causa de uma flag herdada."""
        decisao = decidir(
            estado(sitcode_wbc=60, tem_cotacao=True, tem_pedido=False, pedido_fechado=True)
        )
        assert Acao.CRIAR_PEDIDO in decisao.acoes
        assert "cria_pedido" in decisao.regra

    def test_a_guarda_vem_antes_da_correcao_a_mao(self) -> None:
        """As duas congelam, mas a razão exibida importa para quem investiga:
        pedido fechado é o SAP recusando, não regra de negócio.

        A correção já está aplicada (`parceiro_pedido_sap == parceiro_novo`),
        para isolar a ordem entre estas duas guardas da terceira — a da troca
        impossível, que tem regra própria.
        """
        decisao = self._fechado(parceiro_novo="C999", parceiro_pedido_sap="C999", alterado=False)
        assert "pedido_fechado_no_sap" in decisao.regra
        assert "pedido_corrigido_a_mao" not in decisao.regra


class TestTrocaDeParceiroDeNegocios:
    def test_troca_sinalizada_cancela_e_recria_o_pedido(self) -> None:
        decisao = decidir(
            estado(
                sitcode_wbc=60,
                tem_cotacao=True,
                tem_pedido=True,
                alterado=True,
                parceiro_atual="C001",
                parceiro_novo="C999",
            )
        )
        assert Acao.CANCELAR_E_RECRIAR_PEDIDO in decisao.acoes
        assert "troca_de_pn" in decisao.regra

    def test_troca_sem_parceiro_novo_informado_nao_age(self) -> None:
        # Defesa: o legado chamaria a verificação de PN com string vazia.
        decisao = decidir(
            estado(
                sitcode_wbc=60,
                tem_cotacao=True,
                tem_pedido=True,
                parceiro_atual="C001",
                parceiro_novo="",
            )
        )
        assert Acao.CANCELAR_E_RECRIAR_PEDIDO not in decisao.acoes

    def test_troca_ja_aplicada_nao_recria(self) -> None:
        """Idempotência: o pedido já está no PN novo.

        É o `ChecaPNPedido` do legado, e é a guarda que torna seguro agir
        **sempre** que o `PN_Correc` está preenchido — sem depender de ninguém
        baixar uma marca depois. As 578 oportunidades de homologação com
        `PN_Correc` residual caem aqui.
        """
        decisao = decidir(
            estado(
                sitcode_wbc=60,
                tem_cotacao=True,
                tem_pedido=True,
                parceiro_atual="C001",
                parceiro_novo="C999",
                parceiro_pedido_sap="C999",
            )
        )
        assert Acao.CANCELAR_E_RECRIAR_PEDIDO not in decisao.acoes

    def test_a_troca_exige_u_ino_update(self) -> None:
        """`PN_Correc` preenchido com `Update = 'N'` é caso resolvido à mão.

        Este teste já afirmou o contrário. A regra antiga agia sempre que o
        `PN_Correc` estivesse preenchido e o pedido não estivesse nele, e o
        negócio esclareceu depois que essa combinação significa "o pedido já
        está correto em nome de outro PN" — não "troca pendente".

        Os dados sustentam: em produção, das oportunidades que a regra antiga
        ainda consideraria com troca pendente, **todas as 4** são casos antigos
        cujo pedido vivo está num terceiro parceiro, resolvidos à mão. Nenhuma
        era trabalho legítimo.
        """
        decisao = decidir(
            estado(
                sitcode_wbc=60,
                tem_cotacao=True,
                tem_pedido=True,
                alterado=False,
                parceiro_atual="C011151",
                parceiro_novo="C011081",
                parceiro_pedido_sap="C011151",
            )
        )
        assert Acao.CANCELAR_E_RECRIAR_PEDIDO not in decisao.acoes
        assert "pedido_corrigido_a_mao" in decisao.regra
        # O congelamento é **do pedido**. Espelhar o SitCode não toca no
        # documento nem no `CardCode`, e continua acontecendo — foi o escopo
        # escolhido pelo negócio.
        assert _ACOES_DE_DOCUMENTO.isdisjoint(decisao.acoes)

    def test_troca_acontece_quando_o_wbc_marca_alteracao(self) -> None:
        """Com `Update = 'Y'`, a troca é trabalho de verdade e acontece."""
        decisao = decidir(
            estado(
                sitcode_wbc=60,
                tem_cotacao=True,
                tem_pedido=True,
                alterado=True,
                parceiro_atual="C011151",
                parceiro_novo="C011081",
                parceiro_pedido_sap="C011151",
            )
        )
        assert Acao.CANCELAR_E_RECRIAR_PEDIDO in decisao.acoes
        assert "troca_de_pn" in decisao.regra

    def test_pedido_ja_no_pn_corrigido_nao_e_atualizado(self) -> None:
        """O caso `00124045`, que motivou a regra.

        A correção **já estava aplicada**: o pedido vigente estava no
        `PN_Correc` e o antigo, cancelado. A troca não estava pendente, então o
        fluxo caía na comparação de revisão e chamava `atualizar_pedido` — que
        reenvia `CardCode` (`campos_comuns` o manda sempre) com o parceiro da
        *oportunidade*, desfazendo a correção.
        """
        decisao = decidir(
            estado(
                sitcode_wbc=60,
                tem_cotacao=True,
                tem_pedido=True,
                alterado=False,
                revisao_wbc="3",
                revisao_pedido_sap="1",
                parceiro_atual="C008483",
                parceiro_novo="C011608",
                parceiro_pedido_sap="C011608",
            )
        )
        assert Acao.ATUALIZAR_PEDIDO not in decisao.acoes
        assert "pedido_corrigido_a_mao" in decisao.regra
        assert _ACOES_DE_DOCUMENTO.isdisjoint(decisao.acoes)

    def test_sem_pn_correc_a_regra_nao_se_aplica(self) -> None:
        """A guarda é sobre correção de parceiro, e não sobre revisão.

        Sem `PN_Correc`, um pedido com revisão velha e `Update = 'Y'` continua
        sendo atualizado normalmente — quem barra o `'N'` é outra guarda, com
        outro nome, testada em `TestUpdateCongelaOPedido`.
        """
        decisao = decidir(
            estado(
                sitcode_wbc=60,
                tem_cotacao=True,
                tem_pedido=True,
                alterado=True,
                revisao_wbc="3",
                revisao_pedido_sap="1",
                parceiro_atual="C008483",
                parceiro_novo="",
                parceiro_pedido_sap="C008483",
            )
        )
        assert Acao.ATUALIZAR_PEDIDO in decisao.acoes
        assert "atualiza_pedido" in decisao.regra

    def test_sem_pedido_a_guarda_nao_impede_a_criacao(self) -> None:
        """`PN_Correc` preenchido não pode impedir o primeiro pedido: sem
        pedido não há nada corrigido à mão para preservar."""
        decisao = decidir(
            estado(
                sitcode_wbc=60,
                tem_cotacao=True,
                tem_pedido=False,
                alterado=False,
                parceiro_atual="C008483",
                parceiro_novo="C011608",
            )
        )
        assert Acao.CRIAR_PEDIDO in decisao.acoes
        assert "cria_pedido" in decisao.regra

    def test_troca_de_pn_tem_precedencia_sobre_comparacao_de_revisao(self) -> None:
        decisao = decidir(
            estado(
                sitcode_wbc=60,
                tem_cotacao=True,
                tem_pedido=True,
                alterado=True,
                parceiro_atual="C001",
                parceiro_novo="C999",
                revisao_wbc="Z",
                revisao_pedido_sap="A",
            )
        )
        assert Acao.CANCELAR_E_RECRIAR_PEDIDO in decisao.acoes
        assert Acao.ATUALIZAR_PEDIDO not in decisao.acoes


class TestEncerramento:
    @pytest.mark.parametrize("sitcode", [70, 90, 99])
    def test_encerra_a_oportunidade(self, sitcode: int) -> None:
        decisao = decidir(estado(sitcode_wbc=sitcode, tem_cotacao=True))
        assert Acao.MARCAR_OPORTUNIDADE_PERDIDA in decisao.acoes

    @pytest.mark.parametrize("sitcode", [70, 90, 99])
    def test_encerramento_nao_pede_espelhamento_separado(self, sitcode: int) -> None:
        """`encerrar()` grava `U_INO_StatusWBC` junto com o `Status`.

        Pedir o espelhamento também seria o mesmo PATCH duas vezes na mesma
        oportunidade, no mesmo ciclo.
        """
        decisao = decidir(estado(sitcode_wbc=sitcode, tem_cotacao=True))
        assert Acao.MARCAR_OPORTUNIDADE_PERDIDA in decisao.acoes
        assert Acao.ATUALIZAR_STATUS_OPORTUNIDADE not in decisao.acoes

    @pytest.mark.parametrize("sitcode", [70, 90, 99])
    def test_cancela_a_cotacao_antes_de_encerrar(self, sitcode: int) -> None:
        """Divergência deliberada do legado — ver `_decidir_encerramento`.

        O `cancelaFechaOportunidade` legado nunca tocava nos documentos, e o
        resultado em produção são 686 de 714 oportunidades encerradas com a
        cotação ainda aberta. A ordem também é regra: cancelar antes de
        encerrar, para que uma falha no cancelamento não deixe a oportunidade
        fechada com documento aberto.
        """
        decisao = decidir(estado(sitcode_wbc=sitcode, tem_cotacao=True))
        assert decisao.acoes.index(Acao.CANCELAR_COTACAO_NO_ENCERRAMENTO) < decisao.acoes.index(
            Acao.MARCAR_OPORTUNIDADE_PERDIDA
        )
        assert "cancela_cotacao_no_encerramento" in decisao.regra

    def test_com_pedido_a_cotacao_e_preservada(self) -> None:
        """Havendo pedido, a cotação foi convertida: o SAP recusaria o
        cancelamento, e o pedido em si nunca é cancelado automaticamente."""
        decisao = decidir(estado(sitcode_wbc=90, tem_cotacao=True, tem_pedido=True))
        assert Acao.CANCELAR_COTACAO_NO_ENCERRAMENTO not in decisao.acoes
        assert Acao.CANCELAR_E_RECRIAR_PEDIDO not in decisao.acoes
        assert Acao.MARCAR_OPORTUNIDADE_PERDIDA in decisao.acoes

    def test_sem_cotacao_ainda_encerra_a_oportunidade(self) -> None:
        """A cotação pode já ter sido cancelada por uma execução anterior —
        inclusive uma que falhou no meio, entre o cancelamento e o
        encerramento. O encerramento não pode depender dela existir."""
        decisao = decidir(estado(sitcode_wbc=90, tem_cotacao=False))
        assert decisao.acoes == (Acao.MARCAR_OPORTUNIDADE_PERDIDA,)

    def test_nao_reencerra_o_que_ja_esta_encerrado(self) -> None:
        """Sem cotação a cancelar e com a oportunidade já fechada no SAP, não há
        o que fazer — senão todo ciclo repetiria o PATCH em cada oportunidade
        perdida da janela, para sempre."""
        decisao = decidir(estado(sitcode_wbc=90, tem_cotacao=False, status_oportunidade="L"))
        assert decisao.acoes == ()
        assert decisao.regra == "encerramento_ja_aplicado"

    def test_sitcode_espelhado_nao_e_prova_de_encerramento(self) -> None:
        """A armadilha que custou uma execução real.

        `U_INO_StatusWBC` é espelhado por `atualizar_status` a cada ciclo, muito
        antes de qualquer encerramento — e enquanto o `encerrar()` mandava um
        `sos_Lost` inexistente, toda oportunidade tinha o SitCode espelhado e
        seguia **aberta**. Usar esse campo como prova de encerramento silencia
        exatamente as oportunidades que ainda precisam ser fechadas.
        """
        decisao = decidir(
            estado(
                sitcode_wbc=99,
                tem_cotacao=False,
                sitcode_sap="99",
                status_oportunidade="O",
            )
        )
        assert decisao.acoes == (Acao.MARCAR_OPORTUNIDADE_PERDIDA,)

    def test_com_cotacao_reencerra_mesmo_com_o_status_ja_espelhado(self) -> None:
        """O caso das 41 de homologação: SAP já em '99', cotação ainda aberta.
        O status igual não pode mascarar o documento pendente."""
        decisao = decidir(estado(sitcode_wbc=99, tem_cotacao=True, sitcode_sap="99"))
        assert Acao.CANCELAR_COTACAO_NO_ENCERRAMENTO in decisao.acoes
        assert Acao.MARCAR_OPORTUNIDADE_PERDIDA in decisao.acoes


class TestEspelhamentoDeStatus:
    def test_sitcode_sem_tratamento_especifico_apenas_espelha(self) -> None:
        # SitCode 61 não tem tratamento próprio em nenhum ramo do legado.
        decisao = decidir(estado(sitcode_wbc=61, tem_cotacao=True))
        assert decisao.acoes == (Acao.ATUALIZAR_STATUS_OPORTUNIDADE,)
        assert decisao.regra == "espelha_status"


class TestObservabilidade:
    def test_toda_decisao_tem_motivo_legivel(self) -> None:
        casos = [
            estado(sitcode_wbc=5),
            estado(sitcode_wbc=30, tem_cotacao=False),
            estado(sitcode_wbc=30, tem_cotacao=True, sitcode_sap="30"),
            estado(sitcode_wbc=40, tem_cotacao=True, sitcode_sap="40"),
            estado(sitcode_wbc=60, tem_cotacao=True, tem_pedido=False),
            estado(sitcode_wbc=90, tem_cotacao=True),
        ]
        for caso in casos:
            decisao = decidir(caso)
            assert decisao.motivos, f"decisão sem motivo para {caso}"
            assert all(m.strip() for m in decisao.motivos)

    def test_toda_decisao_tem_regra_nomeada(self) -> None:
        assert decidir(estado(sitcode_wbc=61, tem_cotacao=True)).regra
        assert decidir(estado(sitcode_wbc=30, tem_cotacao=False)).regra


class TestPureza:
    def test_estado_e_imutavel(self) -> None:
        e = estado()
        with pytest.raises((AttributeError, TypeError)):
            e.sitcode_wbc = 99  # type: ignore[misc]

    def test_decidir_e_deterministico(self) -> None:
        e = estado(sitcode_wbc=60, tem_cotacao=True, tem_pedido=True, revisao_wbc="B")
        assert decidir(e) == decidir(e)


class TestRevisaoAusenteNaoCancelaCotacao:
    """Ponta a ponta do mesmo defeito, agora pela máquina de estados."""

    def test_wbc_sem_revisao_contra_sap_em_zero_congela(self) -> None:
        # Antes: CANCELAR_E_RECRIAR_COTACAO — cancelava a cotação no SAP porque
        # o WBC estava com a revisão em branco.
        decisao = decidir(
            estado(
                sitcode_wbc=40,
                tem_cotacao=True,
                sitcode_sap="40",
                revisao_wbc="",
                revisao_cotacao_sap="0",
            )
        )
        assert Acao.CANCELAR_E_RECRIAR_COTACAO not in decisao.acoes
        assert decisao.regra == "revisao_congelada"

    def test_wbc_sem_revisao_nao_atualiza_pedido(self) -> None:
        decisao = decidir(
            estado(
                sitcode_wbc=60,
                tem_cotacao=True,
                tem_pedido=True,
                alterado=False,
                revisao_wbc="",
                revisao_pedido_sap="0",
            )
        )
        assert Acao.ATUALIZAR_PEDIDO not in decisao.acoes


class TestParceiroDoPedido:
    """Qual PN cada documento leva."""

    def test_sem_troca_pendente_vale_o_parceiro_da_oportunidade(self) -> None:
        estado_ = estado(
            sitcode_wbc=60,
            tem_pedido=True,
            parceiro_atual="C011151",
            parceiro_novo="C011081",
            parceiro_pedido_sap="C011081",
        )
        assert not estado_.troca_de_parceiro_pendente
        assert estado_.parceiro_do_pedido == "C011151"

    def test_com_troca_pendente_vale_o_parceiro_corrigido(self) -> None:
        estado_ = estado(
            sitcode_wbc=60,
            tem_pedido=True,
            parceiro_atual="C011151",
            parceiro_novo="C011081",
            parceiro_pedido_sap="C011151",
        )
        assert estado_.troca_de_parceiro_pendente
        assert estado_.parceiro_do_pedido == "C011081"

    def test_sem_pedido_nao_ha_troca_a_fazer(self) -> None:
        """A troca é cancelar-e-recriar um pedido; sem pedido, não se aplica.
        O pedido novo já nasce com o parceiro da oportunidade."""
        estado_ = estado(
            sitcode_wbc=60,
            tem_pedido=False,
            parceiro_atual="C011151",
            parceiro_novo="C011081",
        )
        assert not estado_.troca_de_parceiro_pendente
        assert estado_.parceiro_do_pedido == "C011151"

    def test_pn_correc_vazio_nao_e_troca(self) -> None:
        estado_ = estado(sitcode_wbc=60, tem_pedido=True, parceiro_atual="C001", parceiro_novo="")
        assert not estado_.troca_de_parceiro_pendente
        assert estado_.parceiro_do_pedido == "C001"


class TestEspelhamentoDoSitCode:
    """Espelhar o SitCode só quando o SAP discorda de fato.

    O legado regravava `U_INO_StatusWBC` a cada passagem, com a condição
    reduzida a "existe cotação". Na janela de homologação isso dava 154 de 160
    espelhamentos gravando o valor que já estava lá — escritas que consomem o
    teto do ciclo, geram evento no histórico de cada oportunidade e entram no
    resumo como trabalho feito.
    """

    def test_nao_espelha_quando_o_sap_ja_tem_o_valor(self) -> None:
        decisao = decidir(
            estado(sitcode_wbc=60, sitcode_sap="60", tem_cotacao=True, tem_pedido=True)
        )
        assert Acao.ATUALIZAR_STATUS_OPORTUNIDADE not in decisao.acoes

    def test_espelha_quando_diverge(self) -> None:
        decisao = decidir(
            estado(sitcode_wbc=60, sitcode_sap="40", tem_cotacao=True, tem_pedido=True)
        )
        assert Acao.ATUALIZAR_STATUS_OPORTUNIDADE in decisao.acoes

    def test_espelha_quando_o_sap_nunca_recebeu_o_valor(self) -> None:
        decisao = decidir(estado(sitcode_wbc=60, sitcode_sap="", tem_cotacao=True, tem_pedido=True))
        assert Acao.ATUALIZAR_STATUS_OPORTUNIDADE in decisao.acoes

    def test_espaco_em_branco_nao_conta_como_divergencia(self) -> None:
        """O campo é texto no SAP e volta com espaços; comparar cru faria toda
        oportunidade parecer divergente para sempre."""
        assert estado(sitcode_wbc=60, sitcode_sap=" 60 ").status_ja_espelhado

    def test_comparacao_e_textual(self) -> None:
        """`atualizar_status` grava `str(sitcode)` de propósito — comparar como
        número faria a leitura seguinte divergir em silêncio."""
        assert estado(sitcode_wbc=60, sitcode_sap="60").status_ja_espelhado
        assert not estado(sitcode_wbc=60, sitcode_sap="060").status_ja_espelhado

    def test_sem_cotacao_nao_espelha(self) -> None:
        """A condição do legado que sobrevive: sem cotação não há o que refletir."""
        decisao = decidir(estado(sitcode_wbc=60, sitcode_sap="40", tem_cotacao=False))
        assert Acao.ATUALIZAR_STATUS_OPORTUNIDADE not in decisao.acoes

    def test_orcamento_congelado_e_ja_espelhado_nao_gera_acao_nenhuma(self) -> None:
        """O caso de 136 orçamentos na janela: pedido congelado, SAP em dia."""
        decisao = decidir(
            estado(
                sitcode_wbc=60,
                sitcode_sap="60",
                tem_cotacao=True,
                tem_pedido=True,
                revisao_wbc="A",
                revisao_pedido_sap="A",
            )
        )
        assert decisao.acoes == ()


class TestSitCode99MarcaPerdidaSemCancelar:
    """A regra confirmada com o usuário, fixada em teste.

    SitCode 99 marca a oportunidade como **perdida** e **não a cancela**. É o
    mesmo comportamento do legado (`cancelaFechaOportunidade`,
    `ServiceProcess.cs:91-122`), que também só gravava o status.

    A ação chamava-se `FECHAR_CANCELAR_OPORTUNIDADE`, e o nome dizia "cancelar"
    sobre um código que não cancelava — em produção, os 79 encerramentos
    previstos para o primeiro ciclo são todos SitCode 99, e a palavra aparecia
    no painel e no histórico de cada um deles.
    """

    def _decidir(self, **campos: object):
        base: dict[str, object] = {"sitcode_wbc": 99, "tem_cotacao": True}
        base.update(campos)
        return decidir(estado(**base))

    def test_marca_perdida(self) -> None:
        decisao = self._decidir()
        assert Acao.MARCAR_OPORTUNIDADE_PERDIDA in decisao.acoes
        assert "marca_perdida" in decisao.regra

    def test_a_acao_nao_se_chama_cancelar(self) -> None:
        """O valor viaja para o retrato da prévia, para o painel e para o
        histórico. É o texto que alguém lê ao investigar."""
        assert Acao.MARCAR_OPORTUNIDADE_PERDIDA.value == "marcar_oportunidade_perdida"
        assert not hasattr(Acao, "FECHAR_CANCELAR_OPORTUNIDADE")

    def test_o_motivo_diz_que_nao_cancela(self) -> None:
        motivos = " ".join(self._decidir().motivos)
        assert "perdida" in motivos
        assert "não** é cancelada" in motivos or "não é cancelada" in motivos

    @pytest.mark.parametrize("sitcode", [70, 90, 99])
    def test_vale_para_os_tres_sitcodes_de_encerramento(self, sitcode: int) -> None:
        """70, 90 e 99 seguem o mesmo caminho. Em produção, os 79 encerramentos
        da janela são todos 99 — mas a regra não distingue."""
        assert Acao.MARCAR_OPORTUNIDADE_PERDIDA in self._decidir(sitcode_wbc=sitcode).acoes

    def test_a_cotacao_continua_sendo_cancelada(self) -> None:
        """Divergência deliberada do legado, **mantida** na confirmação da
        regra com o usuário: o legado nunca tocava nos documentos, e 686 de 714
        oportunidades encerradas em produção seguem com a cotação aberta."""
        decisao = self._decidir(tem_pedido=False)
        assert Acao.CANCELAR_COTACAO_NO_ENCERRAMENTO in decisao.acoes

    def test_com_pedido_a_cotacao_e_preservada(self) -> None:
        """Nesse caso ela foi convertida, e o SAP recusaria o cancelamento."""
        decisao = self._decidir(tem_pedido=True)
        assert Acao.CANCELAR_COTACAO_NO_ENCERRAMENTO not in decisao.acoes
        assert Acao.MARCAR_OPORTUNIDADE_PERDIDA in decisao.acoes


class TestUpdateDesviaEntreDoisCaminhos:
    """`U_INO_Update` é um **desvio**, e não uma autorização para mexer.

    Preenchido à mão pelo operador quando ele precisa trocar o PN de um pedido.
    No legado o campo separa dois caminhos mutuamente exclusivos, dentro do
    SitCode 60 com pedido existente:

    * `!= 'Y'` → compara a revisão carimbada no pedido (`U_INO_VERSAOWBC` do
      `ORDR`) e atualiza — `Program.cs:249`.
    * `== 'Y'` → `ChecaPN` / `ChecaPNPedido`, cancela e recria no PN novo —
      `Program.cs:280`.

    Esta classe existe porque a integração já fez o contrário: congelava o
    pedido quando `'N'`. Estava invertido, e o efeito era pior do que parece —
    com `'Y'` a decisão cai no ramo da troca e retorna antes, então a
    atualização por revisão ficava sem caminho nenhum. Os testes abaixo são a
    trava para isso não voltar.
    """

    def _decidir(self, **kw):
        base = {
            "sitcode_wbc": 60,
            "tem_cotacao": True,
            "tem_pedido": True,
            "revisao_wbc": "C",
            "revisao_pedido_sap": "B",
            "sitcode_sap": "60",
            "parceiro_atual": "C001",
        }
        base.update(kw)
        return decidir(estado(**base))

    def test_com_update_n_a_revisao_nova_atualiza_o_pedido(self) -> None:
        """O caminho da revisão é o do `'N'` — e é o caso comum: em produção,
        2.566 oportunidades com pedido vigente estão em `'N'` contra 8 em
        `'Y'`."""
        decisao = self._decidir(alterado=False)

        assert Acao.ATUALIZAR_PEDIDO in decisao.acoes
        assert "atualiza_pedido" in decisao.regra

    def test_o_motivo_aponta_a_revisao_do_pedido(self) -> None:
        """Quem congela é a revisão carimbada no pedido, então é ela que o
        motivo precisa mostrar — as duas pontas da comparação."""
        motivos = " ".join(self._decidir(alterado=False).motivos)
        assert "C" in motivos and "B" in motivos
        assert "U_INO_Update" not in motivos

    def test_revisao_igual_congela_mesmo_com_update_n(self) -> None:
        """`'N'` não é "atualize sempre": a comparação de revisão continua
        mandando."""
        decisao = self._decidir(alterado=False, revisao_wbc="B")

        assert Acao.ATUALIZAR_PEDIDO not in decisao.acoes
        assert "pedido_congelado" in decisao.regra

    def test_com_update_y_e_pn_correc_desvia_para_a_troca(self) -> None:
        decisao = self._decidir(alterado=True, parceiro_novo="C999", parceiro_pedido_sap="C001")

        assert Acao.CANCELAR_E_RECRIAR_PEDIDO in decisao.acoes
        assert "troca_de_pn" in decisao.regra
        assert Acao.ATUALIZAR_PEDIDO not in decisao.acoes

    def test_com_pn_correc_e_update_n_a_troca_nao_acontece(self) -> None:
        """O `'N'` com `PN_Correc` preenchido significa correção já aplicada —
        guarda do `00124045`. É o que reproduz a exigência de `'Y'` do legado
        para o ramo da troca, ainda que por outro caminho.
        """
        decisao = self._decidir(alterado=False, parceiro_novo="C999", parceiro_pedido_sap="C001")

        assert _ACOES_DE_DOCUMENTO.isdisjoint(decisao.acoes)
        assert "pedido_corrigido_a_mao" in decisao.regra

    def test_a_criacao_do_pedido_nao_olha_o_update(self) -> None:
        """No legado o ramo da criação não menciona `alterado`
        (`Program.cs:232`)."""
        for marca in (False, True):
            decisao = self._decidir(alterado=marca, tem_pedido=False)
            assert Acao.CRIAR_PEDIDO in decisao.acoes

    def test_o_pedido_fechado_congela_dos_dois_lados(self) -> None:
        """O `DocStatus` é divergência nossa, e vale independentemente do
        desvio: o SAP recusa a escrita nos dois caminhos."""
        for marca in (False, True):
            decisao = self._decidir(alterado=marca, pedido_fechado=True)
            assert _ACOES_DE_DOCUMENTO.isdisjoint(decisao.acoes)

    def test_nenhuma_regra_congela_por_causa_do_update(self) -> None:
        """A trava explícita: a regra `pedido_congelado_sem_alteracao` existiu e
        foi removida por inverter o legado. Se voltar, este teste cai."""
        assert "sem_alteracao" not in self._decidir(alterado=False).regra


class TestEncerramentoNaoSeRepete:
    """Oportunidade já fechada no SAP não é marcada de novo.

    Relatado pelo usuário no `00123425`: `Status = 'L'`, SitCode já espelhado,
    e mesmo assim o log de decisão trazia `marcar_oportunidade_perdida` a cada
    ciclo. A guarda existia, mas exigia **duas** condições — já encerrada *e*
    sem cotação —, então todo encerramento com cotação vinculada escapava. E
    com pedido existente a cotação nem chegava a ser cancelada: o ciclo
    regravava o mesmo status para sempre, consumindo teto de escrita e enchendo
    o histórico.
    """

    def _decidir(self, **kw):
        base = {
            "sitcode_wbc": 99,
            "sitcode_sap": "99",
            "status_oportunidade": "L",
            "tem_cotacao": True,
            "tem_pedido": True,
        }
        base.update(kw)
        return decidir(estado(**base))

    def test_encerrada_com_cotacao_e_pedido_nao_remarca(self) -> None:
        """O caso exato do `00123425`."""
        decisao = self._decidir()

        assert Acao.MARCAR_OPORTUNIDADE_PERDIDA not in decisao.acoes
        assert "encerramento_ja_aplicado" in decisao.regra

    def test_encerrada_sem_documento_nenhum_tambem_nao_remarca(self) -> None:
        decisao = self._decidir(tem_cotacao=False, tem_pedido=False)

        assert Acao.MARCAR_OPORTUNIDADE_PERDIDA not in decisao.acoes

    def test_encerrada_nao_gera_escrita_alguma(self) -> None:
        """Já encerrada e já espelhada é o estado final: o ciclo não tem o que
        fazer, e "nada a fazer" precisa significar nenhuma escrita."""
        assert self._decidir().acoes == ()

    def test_aberta_continua_sendo_marcada(self) -> None:
        """A guarda não pode engolir o encerramento de verdade."""
        decisao = self._decidir(status_oportunidade="O")

        assert Acao.MARCAR_OPORTUNIDADE_PERDIDA in decisao.acoes
        assert "marca_perdida" in decisao.regra

    def test_vendida_tambem_conta_como_encerrada(self) -> None:
        """`'W'` (vendida) é encerramento como `'L'`. Remarcar como perdida uma
        oportunidade ganha seria pior que ruído."""
        decisao = self._decidir(status_oportunidade="W")

        assert Acao.MARCAR_OPORTUNIDADE_PERDIDA not in decisao.acoes

    def test_a_cotacao_ainda_e_cancelada_quando_cabe(self) -> None:
        """A guarda pula a **marcação**, não o cancelamento: uma oportunidade
        já perdida com cotação aberta e sem pedido continua tendo a cotação
        cancelada — é a divergência deliberada do legado, e ela vale aqui."""
        decisao = self._decidir(tem_pedido=False)

        assert Acao.CANCELAR_COTACAO_NO_ENCERRAMENTO in decisao.acoes
        assert Acao.MARCAR_OPORTUNIDADE_PERDIDA not in decisao.acoes

    def test_status_desatualizado_ainda_e_espelhado(self) -> None:
        """Pular a marcação não pode deixar o `U_INO_StatusWBC` velho para
        sempre: quem cuida disso é o espelhamento, que só era suprimido porque
        a marcação acontecia."""
        decisao = self._decidir(sitcode_sap="40")

        assert Acao.ATUALIZAR_STATUS_OPORTUNIDADE in decisao.acoes
        assert "espelha_status" in decisao.regra
