"""O teto por ciclo passou a limitar **escrita**, não leitura.

Desde que a leitura vem do HANA numa consulta só, avaliar a janela inteira custa
centésimos de segundo. O que precisa de freio é o que chega ao SAP: criar,
atualizar e cancelar documentos. Estes testes fixam essa distinção — sem eles, é
fácil alguém "otimizar" o laço de volta para um teto de leitura.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from wbcpython.application.processar import ResultadoProcessamento
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


class ProcessadorFalso:
    """Todo orçamento resulta em escrita — o pior caso para o teto."""

    processados: ClassVar[list[str]] = []

    def __init__(self, **kw: Any) -> None:
        pass

    def processar(
        self, oportunidade: dict[str, Any], situacao: Any = None
    ) -> ResultadoProcessamento:
        orcnum = oportunidade["U_ORCNUM_WBC"]
        ProcessadorFalso.processados.append(orcnum)
        return ResultadoProcessamento(
            orcnum=orcnum,
            decisao=Decisao(regra="sem_cotacao_cria", acoes=(Acao.CRIAR_COTACAO,)),
            acoes_executadas=(Acao.CRIAR_COTACAO,),
        )


class ProcessadorSemEscrita(ProcessadorFalso):
    """Nada é escrito: o teto não pode interromper a varredura."""

    def processar(
        self, oportunidade: dict[str, Any], situacao: Any = None
    ) -> ResultadoProcessamento:
        orcnum = oportunidade["U_ORCNUM_WBC"]
        ProcessadorFalso.processados.append(orcnum)
        return ResultadoProcessamento(orcnum=orcnum, decisao=Decisao(regra="revisao_congelada"))


@pytest.fixture
def ambiente(monkeypatch: pytest.MonkeyPatch, tmp_path):
    ProcessadorFalso.processados = []

    class ClienteFalso:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a: object) -> None:
            return None

    class RepoFalso:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

    class WbcFalso:
        @classmethod
        def a_partir_de(cls, settings: Any) -> WbcFalso:
            return cls()

        def situacoes_atuais(self, orcnums: Any) -> dict[str, tuple[int, str]]:
            return {orcnum: (40, "") for orcnum in orcnums}

    class HanaFalso:
        quantidade = 25

        def __init__(self, settings: Any, *, company_db: str) -> None:
            pass

        def pendentes_de_integracao(self, *, desde: Any, orcamento: Any = None):
            return _oportunidades(HanaFalso.quantidade)

        def close(self) -> None:
            return None

    monkeypatch.setattr(mod_worker, "ServiceLayerClient", ClienteFalso)
    monkeypatch.setattr(mod_worker, "RepositorioOportunidadesServiceLayer", RepoFalso)
    monkeypatch.setattr(mod_worker, "RepositorioDocumentosVendaServiceLayer", RepoFalso)
    monkeypatch.setattr(mod_worker, "RepositorioGrupoProdutosServiceLayer", RepoFalso)
    monkeypatch.setattr(mod_worker, "RepositorioOrcDetalheServiceLayer", RepoFalso)
    monkeypatch.setattr(mod_worker, "RepositorioOportunidadesHana", HanaFalso)
    monkeypatch.setattr(mod_worker, "RepositorioOrcamentosWbcSql", WbcFalso)
    return HanaFalso


def _worker(tmp_path, teto: int):
    from wbcpython.config import Settings

    tracking = RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/t.db")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    return mod_worker.WorkerIntegracao(
        settings, tracking=tracking, limite_de_escrita_por_ciclo=teto
    )


class TestTetoDeEscrita:
    def test_para_apos_o_numero_de_escritas_configurado(
        self, ambiente, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setattr(mod_worker, "ProcessadorDeOrcamento", ProcessadorFalso)

        resultado = _worker(tmp_path, teto=5).executar_ciclo()

        assert len(ProcessadorFalso.processados) == 5
        assert resultado.processados == 5

    def test_sem_escrita_a_janela_inteira_e_avaliada(
        self, ambiente, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Ler é barato: um orçamento sem ação não consome o teto."""
        monkeypatch.setattr(mod_worker, "ProcessadorDeOrcamento", ProcessadorSemEscrita)

        resultado = _worker(tmp_path, teto=5).executar_ciclo()

        assert len(ProcessadorFalso.processados) == 25
        assert resultado.processados == 25
