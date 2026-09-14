"""O cartão "Janela de busca" no painel — armar, responder e limpar.

Duas guardas, para dois problemas: a **senha** autoriza, o **nome** audita. A
senha saiu por algumas horas em 14/09/2026, em nome da simplicidade, e voltou no
mesmo dia — sem o bloqueio de produção na frente, ela é a única coisa entre um
clique e centenas de escritas irreversíveis.

O que estes testes seguram, e que nenhum teste de unidade pega: que a exceção ao
bloqueio de **produção** (essa, sim, permanente) não vaze para os comandos do
catálogo, que continuam bloqueados ali.
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
    def test_explica_em_uma_linha_qual_data_conta(self, cliente: TestClient) -> None:
        """A confusão que a frase existe para evitar, no menor espaço possível.

        A janela conta pela data de **abertura da oportunidade**, não pela da
        última alteração do orçamento — uma oportunidade de 2025 alterada ontem
        não entra. Quem é de vendas não tem como adivinhar isso.
        """
        corpo = cliente.get("/fragmentos/janela").text

        assert "6 meses" in corpo
        assert "data de abertura da oportunidade" in corpo

    def test_armado_diz_quem_pediu_e_que_volta_sozinho(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        repo.armar_janela(18, por="joana (vendas)")

        corpo = cliente.get("/fragmentos/janela").text

        assert "18 meses armados" in corpo
        assert "joana (vendas)" in corpo
        assert "volta a 6 meses sozinha" in corpo

    def test_aguardando_mostra_quantas_ficaram(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        """O número é o que decide a resposta; o ponto de parada vive no log."""
        repo.armar_janela(24, por="joana")
        repo.janela_aguardando_resposta(
            faltaram=340, detalhe="Parou no orçamento 00125533.", espera=timedelta(minutes=15)
        )

        corpo = cliente.get("/fragmentos/janela").text

        assert "340" in corpo
        assert "Rodar outro ciclo" in corpo


class TestArmar:
    def test_arma_com_nome_e_senha(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
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
        de centenas de escritas irreversíveis."""
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
        """É o botão que libera a próxima leva de escritas — mesma porta do armar."""
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
    def test_limpar_nao_pede_nada(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        """Frear só reduz o que o próximo ciclo escreve."""
        repo.armar_janela(24, por="joana")

        cliente.post("/fragmentos/janela/limpar", data={})

        assert repo.janela_pedida().estado is EstadoDaJanela.OCIOSO


class TestEmProducao:
    """Armar atravessa o bloqueio de produção; os comandos do catálogo, não."""

    @pytest.fixture
    def producao(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
        monkeypatch.setenv("TRACKING_DB_URL", f"sqlite:///{tmp_path}/painel.db")
        monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAPROD")
        monkeypatch.setenv("PAINEL_SENHA", SENHA)
        monkeypatch.setenv("LOG_FILE", "")
        return Settings()

    def test_arma_em_producao(self, producao: Settings, repo: RepositorioTracking) -> None:
        """A exceção que permanece: produção não barra o armar — só a senha guarda."""
        cliente = TestClient(criar_app(settings=producao, tracking=repo))

        resposta = cliente.post(
            "/fragmentos/janela/armar",
            data={"meses": "24", "solicitante": "joana", "senha": SENHA},
        )

        assert resposta.status_code == 200
        assert repo.janela_pedida().estado is EstadoDaJanela.ARMADO

    def test_em_producao_o_cartao_avisa_que_a_escrita_e_de_verdade(
        self, producao: Settings, repo: RepositorioTracking
    ) -> None:
        """Menos texto não é menos aviso: o que some é explicação, não risco."""
        cliente = TestClient(criar_app(settings=producao, tracking=repo))

        corpo = cliente.get("/fragmentos/janela").text

        assert "escreve de verdade no SAP" in corpo

    def test_os_comandos_do_catalogo_seguem_bloqueados(
        self, producao: Settings, repo: RepositorioTracking
    ) -> None:
        """A dispensa é só do armar, e não pode vazar.

        O worker já roda sozinho em produção, e armar só muda quanto ele alcança
        para trás. "Executar ciclo agora" é o clique que o `RISCOS_PRODUCAO.md`
        barrou, e ele continua barrado — com senha e tudo.
        """
        cliente = TestClient(criar_app(settings=producao, tracking=repo))

        resposta = cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "ciclo", "solicitante": "joana", "senha": SENHA},
        )

        assert "apontado para produção" in resposta.text


class TestSemSenhaNoEnv:
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
        quem não digitasse nada passaria. Como armar não tem o bloqueio de
        produção na frente, a senha é a única guarda — e guarda que aprova
        campo em branco não é guarda.
        """
        cliente = TestClient(criar_app(settings=sem_senha, tracking=repo))

        resposta = cliente.post(
            "/fragmentos/janela/armar",
            data={"meses": "24", "solicitante": "joana", "senha": ""},
        )

        assert "PAINEL_SENHA" in resposta.text
        assert repo.janela_pedida().estado is EstadoDaJanela.OCIOSO

    def test_os_comandos_do_catalogo_continuam_recusando(
        self, sem_senha: Settings, repo: RepositorioTracking
    ) -> None:
        """Sem `PAINEL_SENHA`, `compare_digest("", "")` aprovaria o campo vazio.

        A pré-condição do catálogo é o que fecha essa porta, e ela fica.
        """
        cliente = TestClient(criar_app(settings=sem_senha, tracking=repo))

        resposta = cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "ciclo", "solicitante": "joana", "senha": ""},
        )

        assert "PAINEL_SENHA" in resposta.text


class TestALista:
    def test_a_lista_segue_a_janela_armada(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        """Duas telas sobre o mesmo assunto não podem dar números diferentes."""
        from wbcpython.host.worker import janela_padrao

        antiga = janela_padrao(meses=10)
        repo.registrar_verificacao(
            "00099001", status=StatusIntegracao.SEM_ACAO, data_abertura=antiga
        )

        assert "00099001" not in cliente.get("/fragmentos/oportunidades").text

        repo.armar_janela(24, por="joana")

        assert "00099001" in cliente.get("/fragmentos/oportunidades").text


class TestCartoesCompartilhados:
    """Dois pares de comandos dividem um cartão cada: SAP+HANA e Ambiente+doctor.

    Eram quatro cartões quase idênticos — mesmo selo, mesma demora, uma linha de
    texto cada. Juntos viram dois, e cada metade mantém título, selos e botão
    próprios: são comandos diferentes, não variações de um só.
    """

    PARES = (("check-sap", "check-hana"), ("env", "doctor"))

    @pytest.mark.parametrize(("dono", "outro"), PARES)
    def test_o_cartao_aponta_para_o_alternativo(self, dono: str, outro: str) -> None:
        from wbcpython.dashboard import comandos as cmd

        assert cmd.POR_ID[dono].alternativo == outro
        assert cmd.POR_ID[outro].oculto

    @pytest.mark.parametrize(("dono", "outro"), PARES)
    def test_o_alternativo_nao_ganha_cartao_proprio(
        self, cliente: TestClient, dono: str, outro: str
    ) -> None:
        """Senão a grade volta a ter um cartão por comando."""
        corpo = cliente.get("/fragmentos/comandos").text

        assert f'name="comando" value="{outro}"' not in corpo
        assert corpo.count(f'name="comando" value="{dono}"') == 1

    def test_ha_um_botao_alternativo_por_par(self, cliente: TestClient) -> None:
        corpo = cliente.get("/fragmentos/comandos").text

        assert corpo.count('name="alternativo"') == len(self.PARES)
