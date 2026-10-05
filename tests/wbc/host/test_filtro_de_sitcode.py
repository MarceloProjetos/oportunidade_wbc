"""`ciclo --cancelados` — restringir a execução a um SitCode do WBC.

Cancelar cotação é destrutivo e não se desfaz. Poder confinar uma execução à
leva dos cancelados é o que permite tratá-los sem arrastar junto criações e
atualizações de documento que nada têm a ver com eles.

O ponto delicado, e a razão destes testes: o filtro tem de olhar o SitCode do
**WBC**, e não o espelho `U_INO_StatusWBC` guardado no SAP. O espelho fica para
trás exatamente quando o orçamento acabou de mudar — que é o caso que se quer
pegar.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.wbc.host.test_teto_de_escrita import (
    ProcessadorFalso,
    ambiente,  # noqa: F401 - fixture
)
from wbcpython.host import worker as mod_worker
from wbcpython.tracking import RepositorioTracking

SITCODE_POR_ORCAMENTO = {
    "00125500": 99,  # cancelado
    "00125501": 99,  # cancelado
    "00125502": 40,  # em revisão
    "00125503": 60,  # pedido fechado
}


@pytest.fixture
def wbc_com_sitcodes_variados(monkeypatch: pytest.MonkeyPatch, ambiente):  # noqa: F811
    """Quatro orçamentos, dois deles cancelados."""

    class WbcVariado:
        @classmethod
        def a_partir_de(cls, settings: Any) -> WbcVariado:
            return cls()

        def situacoes_atuais(self, orcnums: Any) -> dict[str, tuple[int, str]]:
            return {
                orcnum: (SITCODE_POR_ORCAMENTO.get(orcnum, 30), "")
                for orcnum in orcnums
            }

    ambiente.quantidade = 4
    monkeypatch.setattr(mod_worker, "RepositorioOrcamentosWbcSql", WbcVariado)
    monkeypatch.setattr(mod_worker, "ProcessadorDeOrcamento", ProcessadorFalso)
    return ambiente


def _worker(tmp_path):
    from wbcpython.config import Settings

    tracking = RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/t.db")
    return mod_worker.WorkerIntegracao(
        Settings(_env_file=None),  # type: ignore[call-arg]
        tracking=tracking,
        limite_de_escrita_por_ciclo=100,
    )


class TestFiltroDeSitCode:
    def test_processa_somente_o_sitcode_pedido(
        self, wbc_com_sitcodes_variados, tmp_path
    ) -> None:
        resultado = _worker(tmp_path).executar_ciclo(apenas_sitcode=99)

        assert ProcessadorFalso.processados == ["00125500", "00125501"]
        assert resultado.processados == 2

    def test_sem_filtro_processa_a_janela_inteira(
        self, wbc_com_sitcodes_variados, tmp_path
    ) -> None:
        resultado = _worker(tmp_path).executar_ciclo()
        assert resultado.processados == 4

    def test_orcamento_sem_situacao_no_wbc_fica_de_fora(
        self, ambiente, monkeypatch: pytest.MonkeyPatch, tmp_path  # noqa: F811
    ) -> None:
        """Sem situação no WBC não há como afirmar que é 99.

        Incluir na dúvida seria o pior dos dois lados: a ação da leva é
        destrutiva, e o orçamento entraria numa execução que o operador pediu
        para ser só de cancelados.
        """

        class WbcVazio:
            @classmethod
            def a_partir_de(cls, settings: Any) -> WbcVazio:
                return cls()

            def situacoes_atuais(self, orcnums: Any) -> dict[str, tuple[int, str]]:
                return {}

        ambiente.quantidade = 3
        monkeypatch.setattr(mod_worker, "RepositorioOrcamentosWbcSql", WbcVazio)
        monkeypatch.setattr(mod_worker, "ProcessadorDeOrcamento", ProcessadorFalso)

        resultado = _worker(tmp_path).executar_ciclo(apenas_sitcode=99)

        assert ProcessadorFalso.processados == []
        assert resultado.processados == 0
