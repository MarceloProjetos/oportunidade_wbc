"""O card "Janela de busca" no painel — as rotas que armam, respondem e limpam.

O que é fácil quebrar aqui sem nenhum teste unitário reclamar: a senha deixar de
ser exigida onde ela protege centenas de escritas irreversíveis no SAP, e passar
a ser exigida onde ela só atrapalha (o botão de frear).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from wbcpython.config import Settings
from wbcpython.dashboard.web import criar_app
from wbcpython.domain.janela import EstadoDaJanela
from wbcpython.tracking import RepositorioTracking, StatusIntegracao

SENHA = "senha-do-painel"


@pytest.fixture
def repo(tmp_path: Path) -> RepositorioTracking:
    return RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/painel.db")


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("TRACKING_DB_URL", f"sqlite:///{tmp_path}/painel.db")
    monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAHOMOLOG")
    monkeypatch.setenv("PAINEL_SENHA", SENHA)
    monkeypatch.setenv("LOG_FILE", "")
    return Settings()


@pytest.fixture
def cliente(config: Settings, repo: RepositorioTracking) -> TestClient:
    return TestClient(criar_app(settings=config, tracking=repo))


class TestOCartao:
    def test_mostra_o_padrao_e_explica_a_data_que_conta(self, cliente: TestClient) -> None:
        """A confusão que a ajuda existe para evitar.

        A janela conta pela data de **abertura da oportunidade**, não pela da
        última alteração do orçamento — uma oportunidade de 2025 alterada ontem
        não entra. Quem é de vendas não tem como adivinhar isso, e sem a frase o
        campo parece quebrado.
        """
        corpo = cliente.get("/fragmentos/janela").text

        assert "6 meses (padrão)" in corpo
        assert "data de abertura da oportunidade no SAP" in corpo

    def test_armado_diz_quem_pediu_e_que_volta_sozinho(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        repo.armar_janela(18, por="joana (vendas)")

        corpo = cliente.get("/fragmentos/janela").text

        assert "joana (vendas)" in corpo
        assert "volta a 6 meses sozinha" in corpo
        assert "1200" in corpo  # o teto da banda de 18 meses

    def test_aguardando_mostra_quantas_ficaram_e_onde_parou(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        repo.armar_janela(24, por="joana")
        repo.janela_aguardando_resposta(
            faltaram=340,
            detalhe="Parou no orçamento 00125533; 340 oportunidade(s) não avaliadas.",
            espera=timedelta(minutes=15),
        )

        corpo = cliente.get("/fragmentos/janela").text

        assert "340" in corpo
        assert "00125533" in corpo
        assert "Rodar outro ciclo" in corpo


class TestArmar:
    def test_arma_com_senha(self, cliente: TestClient, repo: RepositorioTracking) -> None:
        resposta = cliente.post(
            "/fragmentos/janela/armar",
            data={"meses": "24", "solicitante": "joana", "senha": SENHA},
        )

        assert resposta.status_code == 200
        pedido = repo.janela_pedida()
        assert pedido.estado is EstadoDaJanela.ARMADO
        assert pedido.meses == 24
        assert pedido.pedido_por == "joana"

    def test_sem_senha_nao_arma(self, cliente: TestClient, repo: RepositorioTracking) -> None:
        """Armar não escreve no SAP com as próprias mãos, mas é a causa direta
        de até 1.800 escritas irreversíveis — fica do lado protegido da linha."""
        resposta = cliente.post(
            "/fragmentos/janela/armar",
            data={"meses": "24", "solicitante": "joana", "senha": "errada"},
        )

        assert "Senha incorreta" in resposta.text
        assert repo.janela_pedida().estado is EstadoDaJanela.OCIOSO

    def test_sem_nome_nao_arma(self, cliente: TestClient, repo: RepositorioTracking) -> None:
        """Senha autoriza; nome audita. Uma não responde pela outra."""
        resposta = cliente.post(
            "/fragmentos/janela/armar",
            data={"meses": "24", "solicitante": "", "senha": SENHA},
        )

        assert "auditável" in resposta.text
        assert repo.janela_pedida().estado is EstadoDaJanela.OCIOSO

    def test_o_maximo_e_conferido_no_servidor(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        """O `max` do campo limita o engano, não o pedido: quem posta à mão
        passa por aqui igual."""
        resposta = cliente.post(
            "/fragmentos/janela/armar",
            data={"meses": "120", "solicitante": "joana", "senha": SENHA},
        )

        assert "máximo de 24" in resposta.text
        assert repo.janela_pedida().estado is EstadoDaJanela.OCIOSO

    def test_texto_no_lugar_do_numero_nao_derruba_a_tela(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        resposta = cliente.post(
            "/fragmentos/janela/armar",
            data={"meses": "abc", "solicitante": "joana", "senha": SENHA},
        )

        assert resposta.status_code == 200
        assert repo.janela_pedida().estado is EstadoDaJanela.OCIOSO


class TestResponderAPergunta:
    def test_continuar_rearma_a_mesma_janela(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        repo.armar_janela(24, por="joana")
        repo.janela_aguardando_resposta(faltaram=340, detalhe="", espera=timedelta(minutes=15))

        cliente.post(
            "/fragmentos/janela/continuar",
            data={"solicitante": "joana", "senha": SENHA},
        )

        pedido = repo.janela_pedida()
        assert pedido.estado is EstadoDaJanela.ARMADO
        assert pedido.meses == 24

    def test_continuar_exige_senha(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        repo.armar_janela(24, por="joana")
        repo.janela_aguardando_resposta(faltaram=340, detalhe="", espera=timedelta(minutes=15))

        resposta = cliente.post(
            "/fragmentos/janela/continuar",
            data={"solicitante": "joana", "senha": "errada"},
        )

        assert "Senha incorreta" in resposta.text
        assert repo.janela_pedida().estado is EstadoDaJanela.AGUARDANDO

    def test_continuar_recusa_quando_a_pergunta_ja_nao_esta_de_pe(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        """A pergunta venceu (ou alguém respondeu) entre a tela e o clique.

        Rearmar aqui seria liberar uma leva de escritas que ninguém acabou de
        autorizar — a tela mostrava um estado que já não existe.
        """
        resposta = cliente.post(
            "/fragmentos/janela/continuar",
            data={"solicitante": "joana", "senha": SENHA},
        )

        assert "já não está de pé" in resposta.text
        assert repo.janela_pedida().estado is EstadoDaJanela.OCIOSO


class TestLimpar:
    def test_limpar_nao_pede_senha(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        """Frear só reduz o que o próximo ciclo escreve.

        Exigir senha para desarmar transformaria a proteção em obstáculo
        justamente no botão que alguém aperta quando se assustou com o número.
        """
        repo.armar_janela(24, por="joana")

        cliente.post("/fragmentos/janela/limpar", data={})

        assert repo.janela_pedida().estado is EstadoDaJanela.OCIOSO


class TestALista:
    def test_a_lista_segue_a_janela_armada(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        """Duas telas sobre o mesmo assunto nao podem dar numeros diferentes.

        Sem isto, armar 24 meses mudaria o que o ciclo varre sem mudar o que a
        lista mostra — e a tela esconderia justamente as oportunidades antigas
        que alguem acabou de pedir para alcancar. O comentario de `_janela` ja
        registrava esse estrago quando a janela so mudava pelo `.env`: ao
        encolher de 6 para 3 meses, 167 orcamentos de maio continuaram na tela
        como se o ciclo ainda os olhasse.
        """
        from wbcpython.host.worker import janela_padrao

        # Aberta ha ~10 meses: fora do padrao de 6, dentro de uma janela de 24.
        antiga = janela_padrao(meses=10)
        repo.registrar_verificacao(
            "00099001", status=StatusIntegracao.SEM_ACAO, data_abertura=antiga
        )

        assert "00099001" not in cliente.get("/fragmentos/oportunidades").text

        repo.armar_janela(24, por="joana")

        assert "00099001" in cliente.get("/fragmentos/oportunidades").text


class TestAExcecaoDeProducao:
    """Armar é a única coisa desta tela que atravessa o bloqueio de produção.

    A primeira versão (11/09/2026) amarrou o card ao `painel_pode_escrever`, que
    é falso por desenho quando o painel aponta para produção. Resultado na .11:
    o card aparecia com a ajuda e **sem controle nenhum** — a feature morria
    exatamente onde serve, e armar voltava a ser terminal. Estes testes são o
    que impede isso de voltar.
    """

    @pytest.fixture
    def producao(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
        monkeypatch.setenv("TRACKING_DB_URL", f"sqlite:///{tmp_path}/painel.db")
        monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAPROD")
        monkeypatch.setenv("PAINEL_SENHA", SENHA)
        monkeypatch.setenv("LOG_FILE", "")
        return Settings()

    def test_em_producao_o_card_continua_armavel(
        self, producao: Settings, repo: RepositorioTracking
    ) -> None:
        cliente = TestClient(criar_app(settings=producao, tracking=repo))

        resposta = cliente.post(
            "/fragmentos/janela/armar",
            data={"meses": "24", "solicitante": "joana", "senha": SENHA},
        )

        assert resposta.status_code == 200
        assert repo.janela_pedida().estado is EstadoDaJanela.ARMADO

    def test_em_producao_o_card_avisa_que_a_escrita_e_de_verdade(
        self, producao: Settings, repo: RepositorioTracking
    ) -> None:
        """A exceção é sobre qual porta se atravessa, não sobre avisar menos."""
        cliente = TestClient(criar_app(settings=producao, tracking=repo))

        corpo = cliente.get("/fragmentos/janela").text

        assert "apontado para produção" in corpo
        assert "SBOALTAMIRAPROD" in corpo
        assert "não se desfaz" in corpo

    def test_os_comandos_do_catalogo_seguem_bloqueados_em_producao(
        self, producao: Settings, repo: RepositorioTracking
    ) -> None:
        """A exceção é só do armar. Disparar um ciclo pela tela continua fora.

        São coisas diferentes: o worker já roda sozinho em produção, e armar só
        muda quanto ele alcança para trás. "Executar ciclo agora" é o clique que
        o `RISCOS_PRODUCAO.md` barrou, e ele continua barrado.
        """
        cliente = TestClient(criar_app(settings=producao, tracking=repo))

        resposta = cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "ciclo", "solicitante": "joana", "senha": SENHA},
        )

        assert "apontado para produção" in resposta.text


class TestSemSenhaConfigurada:
    @pytest.fixture
    def sem_senha(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
        monkeypatch.setenv("TRACKING_DB_URL", f"sqlite:///{tmp_path}/painel.db")
        monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAHOMOLOG")
        monkeypatch.setenv("PAINEL_SENHA", "")
        monkeypatch.setenv("LOG_FILE", "")
        return Settings()

    def test_senha_vazia_nao_vira_porta_aberta(
        self, sem_senha: Settings, repo: RepositorioTracking
    ) -> None:
        """A armadilha que a pré-condição existe para fechar.

        Com `PAINEL_SENHA` vazia, `compare_digest("", "")` é **verdadeiro**:
        quem não digitasse nada passaria. Como armar não tem mais o bloqueio de
        produção na frente, a senha é a única guarda — e uma guarda que aprova
        o campo em branco não é guarda.
        """
        cliente = TestClient(criar_app(settings=sem_senha, tracking=repo))

        resposta = cliente.post(
            "/fragmentos/janela/armar",
            data={"meses": "24", "solicitante": "joana", "senha": ""},
        )

        assert "PAINEL_SENHA" in resposta.text
        assert repo.janela_pedida().estado is EstadoDaJanela.OCIOSO

    def test_o_card_diz_o_que_falta(
        self, sem_senha: Settings, repo: RepositorioTracking
    ) -> None:
        corpo = TestClient(criar_app(settings=sem_senha, tracking=repo)).get(
            "/fragmentos/janela"
        ).text

        assert "Indisponível neste painel" in corpo
        assert "PAINEL_SENHA" in corpo
