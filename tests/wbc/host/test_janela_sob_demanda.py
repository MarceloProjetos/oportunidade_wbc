"""A janela sob demanda vista de dentro do ciclo.

O que estes testes protegem, e que nenhum teste de unidade do domínio pega:

1. a janela é resolvida **a cada ciclo**, e não no arranque do worker — que era
   o motivo de mudar de 6 para 24 exigir restart, e o motivo deste trabalho;
2. o pedido volta ao padrão sozinho, por todos os caminhos;
3. o que **não** consome o pedido: o ensaio e o `--orcamento`.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from wbcpython.application.processar import ResultadoProcessamento
from wbcpython.config import Settings
from wbcpython.domain.janela import EstadoDaJanela
from wbcpython.domain.sitcode import Acao, Decisao
from wbcpython.host import worker as mod_worker
from wbcpython.tracking import RepositorioTracking


def _oportunidades(quantidade: int) -> list[dict[str, Any]]:
    return [
        {
            "SequentialNo": 1000 + i,
            "U_ORCNUM_WBC": f"001255{i:02d}",
            "documentos": {"cotacao": None, "pedido": None},
        }
        for i in range(quantidade)
    ]


class ProcessadorQueEscreve:
    """Todo orçamento resulta em escrita — o pior caso para o teto."""

    def __init__(self, **kw: Any) -> None:
        pass

    def processar(self, oportunidade: dict[str, Any], situacao: Any = None):
        return ResultadoProcessamento(
            orcnum=oportunidade["U_ORCNUM_WBC"],
            decisao=Decisao(regra="sem_cotacao_cria", acoes=(Acao.CRIAR_COTACAO,)),
            acoes_executadas=(Acao.CRIAR_COTACAO,),
        )


class ProcessadorQueExplode(ProcessadorQueEscreve):
    def processar(self, oportunidade: dict[str, Any], situacao: Any = None):
        raise RuntimeError("HANA fora do ar")


class HanaEspiao:
    """Guarda o corte de cada leitura: é ele que prova qual janela valeu."""

    cortes: ClassVar[list[Any]] = []
    quantidade = 3

    def __init__(self, settings: Any, *, company_db: str) -> None:
        pass

    def pendentes_de_integracao(self, *, desde, orcamento=None):
        HanaEspiao.cortes.append(desde)
        return _oportunidades(HanaEspiao.quantidade)

    def close(self) -> None:
        return None


@pytest.fixture
def ambiente(monkeypatch: pytest.MonkeyPatch):
    HanaEspiao.cortes = []
    HanaEspiao.quantidade = 3

    class ClienteFalso:
        def __init__(self, *a: Any, **kw: Any) -> None: ...
        def __enter__(self): return self
        def __exit__(self, *a: object) -> None: return None

    class RepoFalso:
        def __init__(self, *a: Any, **kw: Any) -> None: ...

    class WbcFalso:
        @classmethod
        def a_partir_de(cls, settings: Any): return cls()
        def situacoes_atuais(self, orcnums: Any): return {o: (40, "") for o in orcnums}

    for nome in (
        "RepositorioOportunidadesServiceLayer",
        "RepositorioDocumentosVendaServiceLayer",
        "RepositorioGrupoProdutosServiceLayer",
        "RepositorioOrcDetalheServiceLayer",
        "RepositorioParceirosServiceLayer",
    ):
        monkeypatch.setattr(mod_worker, nome, RepoFalso)
    monkeypatch.setattr(mod_worker, "ServiceLayerClient", ClienteFalso)
    monkeypatch.setattr(mod_worker, "RepositorioOportunidadesHana", HanaEspiao)
    monkeypatch.setattr(mod_worker, "RepositorioOrcamentosWbcSql", WbcFalso)
    monkeypatch.setattr(mod_worker, "ProcessadorDeOrcamento", ProcessadorQueEscreve)
    return HanaEspiao


@pytest.fixture
def tracking(tmp_path) -> RepositorioTracking:
    return RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/t.db")


def _worker(tracking: RepositorioTracking, **env) -> mod_worker.WorkerIntegracao:
    env.setdefault("meses_de_janela", 6)
    env.setdefault("limite_de_escrita_por_ciclo", 200)
    return mod_worker.WorkerIntegracao(
        Settings(_env_file=None, **env),  # type: ignore[call-arg]
        tracking=tracking,
    )


class TestAJanelaEResolvidaPorCiclo:
    def test_sem_pedido_vale_o_padrao(self, ambiente, tracking) -> None:
        _worker(tracking).executar_ciclo()

        assert HanaEspiao.cortes[0] == mod_worker.janela_padrao(meses=6)

    def test_um_pedido_armado_depois_da_construcao_ja_vale(self, ambiente, tracking) -> None:
        """O coração do trabalho.

        Antes, `self._meses` era decidido na construção do worker e carregado
        até o restart: armar pela tela seria "aceito, mas só vale depois que o
        Marcelo reiniciar o serviço". O worker é construído **antes** do pedido
        de propósito — é assim que acontece em produção, onde ele já está de pé
        há horas quando alguém arma.
        """
        w = _worker(tracking)
        tracking.armar_janela(18, por="vendas")

        w.executar_ciclo()

        assert HanaEspiao.cortes[0] == mod_worker.janela_padrao(meses=18)

    def test_aguardando_resposta_os_ciclos_voltam_ao_padrao(self, ambiente, tracking) -> None:
        """Enquanto a pergunta espera, o automático não varre a janela larga.

        Sem isto o worker seguiria lendo 24 meses a cada intervalo enquanto
        ninguém responde — o ciclo pesado rodando sozinho, que é exatamente o
        que este desenho existe para evitar.
        """
        from datetime import timedelta

        w = _worker(tracking)
        tracking.armar_janela(24, por="vendas")
        tracking.janela_aguardando_resposta(
            faltaram=10, detalhe="parou", espera=timedelta(minutes=15)
        )

        w.executar_ciclo()

        assert HanaEspiao.cortes[0] == mod_worker.janela_padrao(meses=6)


class TestOTetoEscalonado:
    def test_a_janela_estendida_leva_o_teto_da_banda(self, ambiente, tracking) -> None:
        HanaEspiao.quantidade = 40
        w = _worker(tracking, limite_de_escrita_por_ciclo=10)
        tracking.armar_janela(12, por="vendas")  # banda 3× → teto 30

        resultado = w.executar_ciclo()

        assert resultado.escritas == 30


class TestODestinoDoPedido:
    def test_cumprido_volta_ao_padrao_sozinho(self, ambiente, tracking) -> None:
        tracking.armar_janela(18, por="vendas")

        _worker(tracking).executar_ciclo()

        assert tracking.janela_pedida().estado is EstadoDaJanela.OCIOSO

    def test_ao_bater_no_teto_pergunta_em_vez_de_devolver(self, ambiente, tracking) -> None:
        """O furo que o plano existe para tapar.

        Devolver o pedido aqui abandonaria o represamento que ele foi armado
        para alcançar: o ciclo escreveria o teto e voltaria a 6, e o resto nunca
        seria escrito.
        """
        HanaEspiao.quantidade = 40
        tracking.armar_janela(12, por="vendas")

        _worker(tracking, limite_de_escrita_por_ciclo=10).executar_ciclo()

        pedido = tracking.janela_pedida()
        assert pedido.estado is EstadoDaJanela.AGUARDANDO
        assert pedido.faltaram == 10
        assert "00125530" in pedido.detalhe  # o ponto de retomada

    def test_erro_nao_consome_o_pedido(
        self, ambiente, tracking, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Queda de rede não pode custar o pedido de quem está esperando."""
        monkeypatch.setattr(mod_worker, "ProcessadorDeOrcamento", ProcessadorQueExplode)
        tracking.armar_janela(18, por="vendas")

        _worker(tracking).executar_ciclo()

        pedido = tracking.janela_pedida()
        assert pedido.estado is EstadoDaJanela.ARMADO
        assert pedido.tentativas == 1

    def test_erro_teimoso_devolve_o_pedido(
        self, ambiente, tracking, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Erro que se repete não pode virar ciclo pesado a cada intervalo."""
        monkeypatch.setattr(mod_worker, "ProcessadorDeOrcamento", ProcessadorQueExplode)
        tracking.armar_janela(18, por="vendas")

        w = _worker(tracking)
        for _ in range(mod_worker.TENTATIVAS_ANTES_DE_DEVOLVER):
            w.executar_ciclo()

        assert tracking.janela_pedida().estado is EstadoDaJanela.OCIOSO

    def test_o_ensaio_usa_a_janela_mas_nao_gasta_o_pedido(self, ambiente, tracking) -> None:
        """Ver o tamanho do estrago não pode custar a autorização de corrigi-lo.

        A simulação roda **com** a janela estendida de propósito — é com ela que
        se enxerga quantos documentos o ciclo criaria. Consumir o pedido aqui
        faria "conferir antes" ser a maneira de perder o pedido.
        """
        tracking.armar_janela(18, por="vendas")

        _worker(tracking).executar_ciclo(somente_leitura=True)

        assert HanaEspiao.cortes[0] == mod_worker.janela_padrao(meses=18)
        assert tracking.janela_pedida().estado is EstadoDaJanela.ARMADO

    def test_orcamento_avulso_nao_gasta_o_pedido(self, ambiente, tracking) -> None:
        """`--orcamento` roda na janela dirigida, que é outra coisa.

        Gastar aqui o que foi pedido para a janela inteira trocaria o pedido de
        alguém por um orçamento que nem estava em questão.
        """
        tracking.armar_janela(18, por="vendas")

        _worker(tracking).executar_ciclo(orcamento="00124045")

        assert tracking.janela_pedida().estado is EstadoDaJanela.ARMADO


class TestAPartidaDoWorker:
    """O restart devolve a janela ao padrão — mas sem inventar um histórico.

    `rodar_continuamente` não dá para exercitar em teste (agenda e bloqueia), e
    o que importa aqui é a decisão, não o laço: limpar só quando há o que
    limpar. O laço apenas repete estas duas linhas.
    """

    def test_um_pedido_armado_nao_sobrevive_ao_restart(self, tracking) -> None:
        from wbcpython.tracking import LINHA_UNICA, PedidoDeJanela

        tracking.armar_janela(24, por="joana")

        if tracking.janela_pedida().estado is not EstadoDaJanela.OCIOSO:
            tracking.limpar_janela(motivo="Devolvida ao padrão na partida do worker.")

        with tracking.sessao() as s:
            assert s.get(PedidoDeJanela, LINHA_UNICA).estado is EstadoDaJanela.OCIOSO

    def test_base_sem_pedido_nenhum_nao_ganha_texto_de_devolucao(self, tracking) -> None:
        """Limpar sempre escrevia "Devolvida ao padrão na partida do worker" numa
        base em que ninguém nunca pediu nada — e o card mostra esse texto como
        "Último pedido:". Quem abrisse a tela pela primeira vez leria que houve
        um pedido que não houve. Visto na .11 em 11/09/2026.
        """
        if tracking.janela_pedida().estado is not EstadoDaJanela.OCIOSO:
            tracking.limpar_janela(motivo="Devolvida ao padrão na partida do worker.")

        assert tracking.janela_pedida().detalhe == ""


class TestAuditoria:
    def test_a_execucao_registra_a_janela_e_o_teto_que_valeram(
        self, ambiente, tracking
    ) -> None:
        tracking.armar_janela(12, por="vendas")

        _worker(tracking).executar_ciclo()

        execucao = tracking.ultimas_execucoes(limite=1)[0]
        assert execucao.meses_da_janela == 12
        assert execucao.teto_de_escrita == 600
