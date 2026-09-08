"""A janela quando o ciclo mira **um** orçamento (`--orcamento`).

A janela existe para limitar o que é varrido sem ninguém pedir. Pedir um
orçamento pelo número é o oposto disso: alguém sabe qual quer e está esperando
que aconteça. Com a janela do ciclo, um orçamento mais antigo simplesmente não
era encontrado, e o comando terminava com "0 avaliado(s)" e código 0 — um nada
silencioso que parece sucesso.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pytest

from wbcpython.config import Settings
from wbcpython.host import worker as mod_worker
from wbcpython.tracking import RepositorioTracking


@pytest.fixture
def cortes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[date, str | None]]:
    """Registra o `desde` de cada leitura, que é o que a janela decide."""
    vistos: list[tuple[date, str | None]] = []

    class HanaEspiao:
        def __init__(self, settings: Any, *, company_db: str) -> None: ...

        def pendentes_de_integracao(self, *, desde, orcamento=None):
            vistos.append((desde, orcamento))
            return []

        def close(self) -> None: ...

    monkeypatch.setattr(mod_worker, "RepositorioOportunidadesHana", HanaEspiao)
    return vistos


def _worker(tmp_path, **env) -> mod_worker.WorkerIntegracao:
    return mod_worker.WorkerIntegracao(
        Settings(_env_file=None, **env),  # type: ignore[call-arg]
        tracking=RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/t.db"),
    )


class TestJanelaDirigida:
    def test_com_orcamento_usa_a_janela_maior(self, cortes, tmp_path) -> None:
        w = _worker(tmp_path, meses_de_janela=6, meses_de_janela_dirigida=12)
        w._ler_pendentes(orcamento="00124045")

        desde, _ = cortes[0]
        assert desde == mod_worker.janela_padrao(meses=12)

    def test_sem_orcamento_usa_a_janela_do_ciclo(self, cortes, tmp_path) -> None:
        """O ciclo inteiro não pode dobrar de tamanho por tabela: varrer 12
        meses a cada volta é outra decisão, e não foi esta."""
        w = _worker(tmp_path, meses_de_janela=6, meses_de_janela_dirigida=12)
        w._ler_pendentes(orcamento=None)

        desde, _ = cortes[0]
        assert desde == mod_worker.janela_padrao(meses=6)

    def test_a_janela_dirigida_e_configuravel(self, cortes, tmp_path) -> None:
        w = _worker(tmp_path, meses_de_janela=6, meses_de_janela_dirigida=24)
        w._ler_pendentes(orcamento="00124045")

        assert cortes[0][0] == mod_worker.janela_padrao(meses=24)

    def test_o_numero_continua_chegando_ao_repositorio(self, cortes, tmp_path) -> None:
        """Alargar a janela não pode virar "varre tudo": o filtro por número é
        o que mantém a leitura barata."""
        _worker(tmp_path)._ler_pendentes(orcamento="00124045")

        assert cortes[0][1] == "00124045"

    def test_nada_encontrado_com_orcamento_avisa(
        self, cortes, tmp_path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """O silêncio era o defeito: quem pede um orçamento pelo número espera
        uma resposta sobre ele, e não um resumo vazio."""
        with caplog.at_level(logging.WARNING, logger="wbcpython.host.worker"):
            _worker(tmp_path)._ler_pendentes(orcamento="00100000")

        mensagens = " ".join(r.getMessage() for r in caplog.records)
        assert "00100000" in mensagens
        assert "não encontrado" in mensagens

    def test_nada_encontrado_sem_orcamento_nao_avisa(
        self, cortes, tmp_path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Janela vazia num ciclo normal é rotina, e aviso rotineiro deixa de
        ser lido."""
        with caplog.at_level(logging.WARNING, logger="wbcpython.host.worker"):
            _worker(tmp_path)._ler_pendentes(orcamento=None)

        assert not [r for r in caplog.records if "não encontrado" in r.getMessage()]

    def test_o_padrao_da_janela_dirigida_e_doze_meses(self) -> None:
        assert Settings(_env_file=None).meses_de_janela_dirigida == 12  # type: ignore[call-arg]
