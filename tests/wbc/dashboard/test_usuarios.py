"""Os nomes oferecidos no campo "Seu nome" do cartao da janela.

Duas fontes: os perfis ativos do OrcaView (pela API 8077 da propria maquina) e
o historico de quem ja armou a janela. A segunda existe para o campo continuar
util quando a primeira nao responde -- sem ela, uma API fora do ar devolveria o
campo ao texto livre, que e o que a lista veio corrigir.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wbcpython.config import Settings
from wbcpython.dashboard import usuarios
from wbcpython.tracking import RepositorioTracking, TipoEvento


@pytest.fixture
def repo(tmp_path: Path) -> RepositorioTracking:
    return RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/u.db")


@pytest.fixture
def config() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _sem_cache() -> None:
    """O cache e de modulo: sem zerar, um teste herda a lista do anterior."""
    usuarios._cache = None


def _armou(repo: RepositorioTracking, nome: str) -> None:
    repo.registrar_evento(
        "(ciclo)",
        tipo=TipoEvento.REPROCESSAMENTO,
        mensagem=f"Janela armada por {nome}.",
        detalhes={"solicitante": nome, "meses": 24},
    )


class TestHistoricoLocal:
    def test_devolve_do_mais_recente_para_o_mais_antigo(
        self, repo: RepositorioTracking
    ) -> None:
        _armou(repo, "joana")
        _armou(repo, "carlos")

        assert repo.nomes_que_ja_pediram() == ["carlos", "joana"]

    def test_nao_repete_quem_armou_varias_vezes(self, repo: RepositorioTracking) -> None:
        _armou(repo, "joana")
        _armou(repo, "carlos")
        _armou(repo, "joana")

        assert repo.nomes_que_ja_pediram() == ["joana", "carlos"]

    def test_evento_sem_solicitante_nao_derruba(self, repo: RepositorioTracking) -> None:
        """Nem todo reprocessamento foi gravado com solicitante."""
        repo.registrar_evento(
            "00124045",
            tipo=TipoEvento.REPROCESSAMENTO,
            mensagem="Reprocessamento pedido pela lista.",
            detalhes={"origem": "tela"},
        )
        _armou(repo, "joana")

        assert repo.nomes_que_ja_pediram() == ["joana"]

    def test_base_nova_devolve_vazio(self, repo: RepositorioTracking) -> None:
        assert repo.nomes_que_ja_pediram() == []


class TestJuncao:
    def test_perfis_primeiro_historico_depois(
        self, config: Settings, repo: RepositorioTracking, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(usuarios, "_da_api", lambda _c: ["Joana Silva", "Carlos Andrade"])
        _armou(repo, "TI (terceiro)")

        assert usuarios.sugestoes(config, repo) == [
            "Joana Silva",
            "Carlos Andrade",
            "TI (terceiro)",
        ]

    def test_quem_esta_nas_duas_aparece_uma_vez(
        self, config: Settings, repo: RepositorioTracking, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Compara sem caixa: o historico guarda o que foi digitado."""
        monkeypatch.setattr(usuarios, "_da_api", lambda _c: ["Joana Silva"])
        _armou(repo, "joana silva")

        assert usuarios.sugestoes(config, repo) == ["Joana Silva"]

    def test_api_fora_do_ar_cai_no_historico(
        self, config: Settings, repo: RepositorioTracking, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """O ponto da segunda fonte."""
        monkeypatch.setattr(usuarios, "_da_api", lambda _c: [])
        _armou(repo, "joana")

        assert usuarios.sugestoes(config, repo) == ["joana"]

    def test_tudo_vazio_nao_e_erro(
        self, config: Settings, repo: RepositorioTracking, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Primeiro uso com a API fora: o campo volta a ser texto livre, e o
        cartao precisa continuar abrindo."""
        monkeypatch.setattr(usuarios, "_da_api", lambda _c: [])

        assert usuarios.sugestoes(config, repo) == []


class TestCacheDaApi:
    def test_pergunta_uma_vez_so_dentro_da_validade(
        self, config: Settings, repo: RepositorioTracking, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        idas: list[int] = []

        def _conta(_c):
            idas.append(1)
            return ["Joana Silva"]

        monkeypatch.setattr(usuarios, "_da_api", _conta)
        usuarios.sugestoes(config, repo)
        usuarios.sugestoes(config, repo)

        assert len(idas) == 1
