"""Testes da aba "Executar" — o catálogo, as guardas e o executor.

O que se testa aqui é sobretudo **o que a tela recusa**. Um botão que dispara
`ciclo` cria e cancela documentos financeiros: o caminho felizmente já é
coberto pelos testes do worker, e o que precisa de rede de segurança é a porta
— senha, auditoria, produção, prévia de peso e execução única.

Nenhum teste toca em SAP ou WBC. O executor é exercitado com a CLI de verdade
numa opção que ela recusa — o que prova processo, arquivo de saída e código de
retorno sem sair da máquina — e, onde o teste precisa de um processo que fique
rodando, com um `Popen` de mentira, para que "o primeiro ainda está rodando"
não dependa de o comando real ser mais lento que a linha seguinte do teste.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from wbcpython.config import Settings
from wbcpython.dashboard import comandos as cmd
from wbcpython.dashboard.web import criar_app
from wbcpython.tracking import RepositorioTracking

SENHA = "segredo-de-teste"


def _ambiente(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **extra: str) -> Settings:
    monkeypatch.setenv("TRACKING_DB_URL", f"sqlite:///{tmp_path}/c.db")
    monkeypatch.setenv("SL_BASE_URL", "https://exemplo:50000/b1s/v1")
    monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAHOMOLOG")
    monkeypatch.setenv("SL_USERNAME", "u")
    monkeypatch.setenv("SL_PASSWORD", "p")
    monkeypatch.setenv("LOG_FILE", "")
    monkeypatch.setenv("PAINEL_SENHA", SENHA)
    for chave, valor in extra.items():
        monkeypatch.setenv(chave, valor)
    return Settings()


@pytest.fixture
def repo(tmp_path: Path) -> RepositorioTracking:
    return RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/c.db")


@pytest.fixture
def executor() -> cmd.Executor:
    return cmd.Executor(arquivo_do_retrato="previsao.json")


@pytest.fixture
def cliente(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo: RepositorioTracking,
    executor: cmd.Executor,
) -> TestClient:
    config = _ambiente(monkeypatch, tmp_path)
    return TestClient(criar_app(settings=config, tracking=repo, executor=executor))


def _sem_disparar(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Substitui o disparo real: registra a chamada e não abre processo."""
    chamadas: list[tuple] = []

    def falso(self, comando, valores, *, solicitante, aplicar_pesos=False):
        chamadas.append((comando.id, valores, solicitante, aplicar_pesos))

    monkeypatch.setattr(cmd.Executor, "iniciar", falso)
    return chamadas


class TestCatalogo:
    def test_todo_comando_que_escreve_esta_marcado(self) -> None:
        """A marca é o que decide senha e bloqueio em produção. Um comando novo
        que escreva e chegue desmarcado passaria por cima das duas guardas."""
        escrevem = {"ciclo", "pesos"}
        for comando in cmd.CATALOGO:
            assert comando.escreve == (comando.id in escrevem), comando.id

    def test_datas_de_abertura_nao_conta_como_escrita_no_sap(self) -> None:
        comando = cmd.POR_ID["datas-de-abertura"]
        assert comando.escreve is False
        assert comando.escreve_tracking is True
        assert comando.protegido is True


class TestMontarArgv:
    def test_traduz_campos_em_opcoes(self) -> None:
        argv = cmd.montar_argv(cmd.POR_ID["ciclo"], {"orcamento": " 00125533 ", "cancelados": "1"})
        assert argv == ["ciclo", "--orcamento", "00125533", "--cancelados"]

    def test_campo_vazio_nao_gera_opcao(self) -> None:
        assert cmd.montar_argv(cmd.POR_ID["ciclo"], {"orcamento": "", "cancelados": ""}) == [
            "ciclo"
        ]

    def test_ignora_campo_que_o_comando_nao_declara(self) -> None:
        """A lista de argumentos sai **do catálogo**, não do formulário. É o que
        impede que um campo inventado na requisição vire opção da CLI."""
        argv = cmd.montar_argv(cmd.POR_ID["env"], {"orcamento": "1", "--rm": "-rf"})
        assert argv == ["env"]

    def test_valor_estranho_vira_argumento_e_nao_comando(self) -> None:
        """Sem shell: o valor viaja como um argumento só, por esquisito que seja."""
        argv = cmd.montar_argv(cmd.POR_ID["ciclo"], {"orcamento": "1; rm -rf /", "cancelados": ""})
        assert argv == ["ciclo", "--orcamento", "1; rm -rf /"]

    def test_exportar_ganha_o_caminho_do_retrato(self) -> None:
        """Na tela `--exportar` é caixa; na CLI é opção com caminho, e o caminho
        tem de ser o mesmo que a aba "Próximo ciclo" lê."""
        argv = cmd._ajustar_exportar(
            cmd.POR_ID["pendentes"], ["pendentes", "--exportar"], "previsao.json"
        )
        assert argv == ["pendentes", "--exportar", "previsao.json"]


class TestGuardaDeSenha:
    def test_sem_senha_recusa_e_nao_dispara(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chamadas = _sem_disparar(monkeypatch)
        resposta = cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "datas-de-abertura", "solicitante": "anderson"},
        )
        assert "Senha incorreta" in resposta.text
        assert chamadas == []

    def test_senha_errada_recusa(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chamadas = _sem_disparar(monkeypatch)
        resposta = cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "datas-de-abertura", "solicitante": "anderson", "senha": "chute"},
        )
        assert "Senha incorreta" in resposta.text
        assert chamadas == []

    def test_sem_nome_recusa_mesmo_com_senha_certa(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Senha autoriza, nome audita. A senha não diz **quem** executou, e
        "quem mandou rodar isso?" é a pergunta que aparece semanas depois."""
        chamadas = _sem_disparar(monkeypatch)
        resposta = cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "datas-de-abertura", "solicitante": "  ", "senha": SENHA},
        )
        assert "auditável" in resposta.text
        assert chamadas == []

    def test_senha_certa_com_nome_dispara(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chamadas = _sem_disparar(monkeypatch)
        resposta = cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "datas-de-abertura", "solicitante": "anderson", "senha": SENHA},
        )
        assert resposta.status_code == 200
        assert chamadas and chamadas[0][0] == "datas-de-abertura"
        assert chamadas[0][2] == "anderson"

    def test_comando_de_leitura_nao_pede_senha(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`env` e `pendentes` não escrevem em lugar nenhum. Exigir senha para
        conferir o ambiente só faria as pessoas pararem de conferir."""
        chamadas = _sem_disparar(monkeypatch)
        cliente.post("/fragmentos/comandos/executar", data={"comando": "env"})
        assert chamadas and chamadas[0][0] == "env"

    def test_a_execucao_protegida_fica_no_historico_do_orcamento(
        self, cliente: TestClient, repo: RepositorioTracking, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """O histórico em memória do painel morre com o processo; este é o rastro
        que dura."""
        _sem_disparar(monkeypatch)
        cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "ciclo", "orcamento": "00125533", "solicitante": "anderson"},
        )
        eventos = repo.eventos("00125533")
        assert any("anderson" in e.mensagem for e in eventos)


class TestGuardaDeSenhaAusente:
    def test_sem_painel_senha_no_env_a_escrita_fica_desabilitada(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: RepositorioTracking
    ) -> None:
        """Falha fechada: o padrão de um campo esquecido não pode ser "qualquer
        um na rede cria pedido no SAP"."""
        config = _ambiente(monkeypatch, tmp_path, PAINEL_SENHA="")
        cliente = TestClient(criar_app(settings=config, tracking=repo))

        assert config.painel_pode_escrever is False
        tela = cliente.get("/fragmentos/comandos").text
        assert "PAINEL_SENHA" in tela

        resposta = cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "pesos", "pedido": "84000", "solicitante": "anderson", "senha": ""},
        )
        assert "PAINEL_SENHA" in resposta.text

    def test_senha_vazia_nao_casa_com_senha_vazia(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: RepositorioTracking
    ) -> None:
        """A armadilha óbvia: sem a guarda de configuração, `"" == ""` liberaria
        tudo justamente na instalação que esqueceu de configurar."""
        config = _ambiente(monkeypatch, tmp_path, PAINEL_SENHA="")
        cliente = TestClient(criar_app(settings=config, tracking=repo))
        resposta = cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "datas-de-abertura", "solicitante": "x", "senha": ""},
        )
        assert "PAINEL_SENHA" in resposta.text


class TestGuardaDeProducao:
    def test_em_producao_nenhuma_escrita_passa(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: RepositorioTracking
    ) -> None:
        """Ver RISCOS_PRODUCAO.md: o primeiro ciclo em produção cancelaria 27
        cotações e criaria 4 pedidos (R$ 170.973,41). Isso não pode depender de
        um clique numa tela sem autenticação."""
        config = _ambiente(monkeypatch, tmp_path, SL_COMPANY_DB="SBOALTAMIRAPROD")
        cliente = TestClient(criar_app(settings=config, tracking=repo))

        assert config.painel_pode_escrever is False
        resposta = cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "pesos", "pedido": "84000", "solicitante": "anderson", "senha": SENHA},
        )
        assert "produção" in resposta.text
        assert "terminal" in resposta.text

    def test_em_producao_a_leitura_continua_passando(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: RepositorioTracking
    ) -> None:
        """Bloquear escrita não é bloquear a tela: conferir o ambiente e rodar a
        prévia em produção é justamente o que se quer poder fazer."""
        config = _ambiente(monkeypatch, tmp_path, SL_COMPANY_DB="SBOALTAMIRAPROD")
        cliente = TestClient(criar_app(settings=config, tracking=repo))
        chamadas = _sem_disparar(monkeypatch)

        cliente.post("/fragmentos/comandos/executar", data={"comando": "pendentes"})
        assert chamadas and chamadas[0][0] == "pendentes"

    def test_a_tela_explica_qual_guarda_fechou(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: RepositorioTracking
    ) -> None:
        """As duas causas têm remédios opostos: uma se resolve preenchendo o
        `.env`, a outra não deve ser resolvida."""
        config = _ambiente(monkeypatch, tmp_path, SL_COMPANY_DB="SBOALTAMIRAPROD")
        tela = (
            TestClient(criar_app(settings=config, tracking=repo)).get("/fragmentos/comandos").text
        )
        assert "SBOALTAMIRAPROD" in tela
        assert "PAINEL_SENHA" not in tela


class TestAlvoDoPeso:
    def test_pedido_e_orcamento_juntos_sao_recusados(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chamadas = _sem_disparar(monkeypatch)
        resposta = cliente.post(
            "/fragmentos/comandos/executar",
            data={
                "comando": "pesos",
                "pedido": "84112",
                "orcamento": "00124853",
                "solicitante": "anderson",
                "senha": SENHA,
            },
        )
        assert "não os dois" in resposta.text
        assert chamadas == []

    def test_sem_alvo_nenhum_e_recusado(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chamadas = _sem_disparar(monkeypatch)
        resposta = cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "pesos", "solicitante": "anderson", "senha": SENHA},
        )
        assert "Informe o pedido ou o orçamento" in resposta.text
        assert chamadas == []


class TestPreviaObrigatoriaDePeso:
    def test_aplicar_sem_previa_e_recusado(self, executor: cmd.Executor) -> None:
        """O comando altera peso de documento já criado, e a diferença entre o
        peso líquido da árvore e o de embarque (×1,10) é o tipo de coisa que se
        confere olhando."""
        with pytest.raises(cmd.PreviaObrigatoria):
            executor.iniciar(
                cmd.POR_ID["pesos"],
                {"pedido": "84112", "orcamento": ""},
                solicitante="anderson",
                aplicar_pesos=True,
            )

    def test_a_previa_forca_simular(self, executor: cmd.Executor) -> None:
        argv = executor._pesos(["pesos", "--pedido", "84112"], {"pedido": "84112"}, aplicar=False)
        assert "--simular" in argv

    def test_previa_de_outro_pedido_nao_autoriza(self, executor: cmd.Executor) -> None:
        """Autorizar por "já simulou alguma coisa" deixaria aplicar no pedido
        errado depois de conferir o certo."""
        executor._previa_de_peso = executor._alvo_de_peso({"pedido": "99999"})
        with pytest.raises(cmd.PreviaObrigatoria):
            executor._pesos(["pesos", "--pedido", "84112"], {"pedido": "84112"}, aplicar=True)

    def test_previa_do_mesmo_pedido_autoriza(self, executor: cmd.Executor) -> None:
        executor._previa_de_peso = executor._alvo_de_peso({"pedido": "84112"})
        argv = executor._pesos(["pesos", "--pedido", "84112"], {"pedido": "84112"}, aplicar=True)
        assert "--simular" not in argv


class _ProcessoFalso:
    """Um processo que só termina quando mandarem.

    Existe para os testes de concorrência e de interrupção serem
    determinísticos: com um comando de verdade, "o primeiro ainda está rodando"
    depende de ele ser mais lento que a linha seguinte do teste.
    """

    def __init__(self) -> None:
        self.codigo: int | None = None
        self.terminado = False

    def poll(self) -> int | None:
        return self.codigo

    def terminate(self) -> None:
        self.terminado = True
        self.codigo = -15


class TestExecutor:
    def test_captura_a_saida_e_o_codigo(self, executor: cmd.Executor) -> None:
        """Roda a CLI de verdade — com uma opção que ela recusa, para não tocar
        em SAP nem WBC. O que se verifica é o executor: processo, arquivo de
        saída e código de retorno."""
        execucao = executor.iniciar(
            cmd.Comando(id="f", rotulo="F", resumo="", argv=("--opcao-inexistente",)),
            {},
            solicitante="teste",
        )
        for _ in range(200):
            if not executor.atual():
                break
            time.sleep(0.05)

        assert execucao.fim is not None
        assert execucao.codigo != 0
        assert executor.saida(execucao)  # a CLI escreveu a recusa
        assert executor.historico()[0].id == execucao.id
        assert executor.historico()[0].situacao == "falhou"

    def test_uma_execucao_por_vez(
        self, executor: cmd.Executor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dois cliques não podem virar dois ciclos. A garantia entre processos
        é a trava no banco de acompanhamento; esta é a da tela."""
        monkeypatch.setattr(cmd.subprocess, "Popen", lambda *a, **k: _ProcessoFalso())
        executor.iniciar(cmd.POR_ID["ciclo"], {"orcamento": "00000001"}, solicitante="a")

        with pytest.raises(cmd.JaEmExecucao) as erro:
            executor.iniciar(cmd.POR_ID["ciclo"], {"orcamento": "00000001"}, solicitante="b")
        assert "ainda está rodando" in str(erro.value)
        assert "pedido por a" in str(erro.value)

    def test_interromper_o_ciclo_pede_parada_e_nao_mata(
        self, executor: cmd.Executor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """01/10/2026 review: `terminate` on Windows is TerminateProcess — no handler runs, the
        lock stays held for 30 min and a kill between POST and link leaves a quotation
        unlinked. The cycle is asked through its own stop file and stops between quotes."""
        falso = _ProcessoFalso()
        ambientes: list[dict[str, str]] = []

        def popen(*_a: object, **k: object) -> _ProcessoFalso:
            ambientes.append(k["env"])  # type: ignore[arg-type]
            return falso

        monkeypatch.setattr(cmd.subprocess, "Popen", popen)
        execucao = executor.iniciar(
            cmd.POR_ID["ciclo"], {"orcamento": "00000001"}, solicitante="a"
        )
        assert ambientes[0]["WORKER_ARQUIVO_DE_PARADA"] == str(execucao.parada)

        alvo = executor.interromper()
        assert alvo is not None and alvo.interrompida and alvo.parada_pedida
        assert falso.terminado is False
        assert execucao.parada is not None and execucao.parada.exists()
        assert executor.atual() is execucao, "keeps running until the cycle stops by itself"

        falso.codigo = 1  # the cycle saw the file and left
        assert executor.atual() is None
        assert executor.historico()[0].situacao == "interrompida"
        assert not execucao.parada.exists(), "the stop file is cleaned when the process ends"

    def test_interromper_leitura_encerra_na_hora(
        self, executor: cmd.Executor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        falso = _ProcessoFalso()
        monkeypatch.setattr(cmd.subprocess, "Popen", lambda *a, **k: falso)
        executor.iniciar(cmd.POR_ID["pendentes"], {"orcamento": "00000001"}, solicitante="a")

        alvo = executor.interromper()
        assert alvo is not None and alvo.interrompida is True
        assert falso.terminado is True
        assert executor.atual() is None
        assert executor.historico()[0].situacao == "interrompida"

    def test_interromper_sem_nada_rodando_nao_estoura(self, executor: cmd.Executor) -> None:
        assert executor.interromper() is None

    def test_terminado_libera_a_vez(
        self, executor: cmd.Executor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        falso = _ProcessoFalso()
        monkeypatch.setattr(cmd.subprocess, "Popen", lambda *a, **k: falso)
        executor.iniciar(cmd.POR_ID["ciclo"], {"orcamento": "00000001"}, solicitante="a")
        falso.codigo = 0

        assert executor.atual() is None
        executor.iniciar(cmd.POR_ID["ciclo"], {"orcamento": "00000001"}, solicitante="b")

    def test_a_senha_nao_vai_para_o_subprocesso(
        self, executor: cmd.Executor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A senha autoriza o clique; dentro do comando não tem nada a fazer, e
        deixá-la no ambiente do filho é vazamento gratuito."""
        monkeypatch.setenv("PAINEL_SENHA", SENHA)
        assert "PAINEL_SENHA" not in executor._ambiente()

    def test_a_linha_exibida_e_o_comando_e_nada_mais(
        self, executor: cmd.Executor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cmd.subprocess, "Popen", lambda *a, **k: _ProcessoFalso())
        execucao = executor.iniciar(
            cmd.POR_ID["ciclo"], {"orcamento": "00125533"}, solicitante="teste"
        )
        assert execucao.linha == "wbcpython ciclo --orcamento 00125533"
        assert SENHA not in execucao.linha


class TestTelaDeComandos:
    def test_separa_leitura_de_escrita(self, cliente: TestClient) -> None:
        """Misturar "testar a conexão" com "criar pedidos de verdade" na mesma
        lista é convite a clicar no errado."""
        tela = cliente.get("/fragmentos/comandos").text
        assert "Leitura e diagnóstico" in tela
        assert "escreve no SAP" in tela
        assert "só leitura" in tela

    def test_o_ciclo_avisa_que_nao_se_desfaz(self, cliente: TestClient) -> None:
        assert "não se desfazem" in cliente.get("/fragmentos/comandos").text

    def test_comando_desconhecido_e_recusado(self, cliente: TestClient) -> None:
        resposta = cliente.post("/fragmentos/comandos/executar", data={"comando": "rm"})
        assert "desconhecido" in resposta.text

    def test_o_console_carrega_vazio(self, cliente: TestClient) -> None:
        assert cliente.get("/fragmentos/execucao").status_code == 200

    def test_saida_que_saiu_da_memoria_diz_isso(self, cliente: TestClient) -> None:
        resposta = cliente.get("/fragmentos/execucao/999")
        assert "não está mais na memória" in resposta.text


class TestCicloDeUmOrcamento:
    """The painel's "Ciclo de integração" (owner, 01/10/2026): one quote typed by the operator,
    no password, also in production — and NEVER the whole window. Skipping the password is only
    safe because the quote is mandatory; these tests nail that side of the bargain."""

    def test_e_dirigido_e_nao_protegido(self) -> None:
        ciclo = cmd.POR_ID["ciclo"]
        assert ciclo.escreve and ciclo.dirigido and not ciclo.protegido
        assert next(c for c in ciclo.campos if c.nome == "orcamento").obrigatorio

    def test_o_simulado_saiu_do_painel(self) -> None:
        assert "ciclo-simulado" not in cmd.POR_ID
        assert all("--simular" not in c.argv for c in cmd.CATALOGO)

    def test_o_cartao_diz_a_janela_e_a_espera(self, cliente: TestClient) -> None:
        """24 months since 01/10/2026 (owner); the wait for the worker's lock is new too."""
        tela = cliente.get("/fragmentos/comandos").text
        assert "<strong>24 meses</strong>" in tela
        assert "espera terminar" in tela

    def test_so_o_ciclo_e_dirigido(self) -> None:
        """The exception does not leak: the weights keep the password and the production block."""
        assert [c.id for c in cmd.CATALOGO if c.dirigido] == ["ciclo"]
        assert cmd.POR_ID["pesos"].protegido and cmd.POR_ID["datas-de-abertura"].protegido

    def test_sem_orcamento_recusa_e_nao_dispara(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chamadas = _sem_disparar(monkeypatch)
        resposta = cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "ciclo", "orcamento": "  ", "solicitante": "anderson"},
        )
        assert "age só sobre ele" in resposta.text
        assert chamadas == []

    def test_sem_nome_recusa(self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        chamadas = _sem_disparar(monkeypatch)
        resposta = cliente.post(
            "/fragmentos/comandos/executar", data={"comando": "ciclo", "orcamento": "00123566"}
        )
        assert "auditável" in resposta.text
        assert chamadas == []

    @pytest.mark.parametrize("ruim", ["1234567890", "0012356a", "123; rm -rf /"])
    def test_orcamento_que_nao_e_numero_recusa(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch, ruim: str
    ) -> None:
        chamadas = _sem_disparar(monkeypatch)
        resposta = cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "ciclo", "orcamento": ruim, "solicitante": "anderson"},
        )
        assert "Orçamento inválido" in resposta.text
        assert chamadas == []

    def test_sem_senha_dispara_so_o_orcamento_e_completa_os_zeros(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chamadas = _sem_disparar(monkeypatch)
        cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "ciclo", "orcamento": "123566", "solicitante": "anderson"},
        )
        assert chamadas and chamadas[0][0] == "ciclo"
        assert chamadas[0][1]["orcamento"] == "00123566"
        assert cmd.montar_argv(cmd.POR_ID["ciclo"], chamadas[0][1]) == [
            "ciclo", "--orcamento", "00123566"]

    def test_roda_em_producao_sem_senha(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: RepositorioTracking
    ) -> None:
        config = _ambiente(monkeypatch, tmp_path, SL_COMPANY_DB="SBOALTAMIRAPROD", PAINEL_SENHA="")
        cliente = TestClient(criar_app(settings=config, tracking=repo))
        chamadas = _sem_disparar(monkeypatch)
        cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "ciclo", "orcamento": "00123566", "solicitante": "anderson"},
        )
        assert chamadas and chamadas[0][1]["orcamento"] == "00123566"
        tela = cliente.get("/fragmentos/comandos").text
        assert "Processar um orçamento" in tela and "Processar este orçamento" in tela
        assert "Simular um ciclo" not in tela

    def test_o_executor_tambem_recusa_sem_orcamento(self, executor: cmd.Executor) -> None:
        """Second lock: even called directly, the per-quote cycle never runs the window."""
        with pytest.raises(ValueError, match="só roda com o orçamento"):
            executor.iniciar(cmd.POR_ID["ciclo"], {"orcamento": ""}, solicitante="x")


class TestCartaoCompartilhado:
    """SAP e HANA dividem um cartao; cada metade tem o seu botao.

    O que so este teste pega: o segundo botao precisa disparar o OUTRO comando.
    Um `name` errado no HTML faria os dois botoes rodarem `check-sap`, e a tela
    continuaria parecendo certa -- dois titulos, dois botoes, e o HANA nunca
    testado.
    """

    def test_o_botao_de_baixo_dispara_o_alternativo(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chamadas = _sem_disparar(monkeypatch)

        cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "check-sap", "alternativo": "1"},
        )

        assert chamadas[0][0] == "check-hana"

    def test_o_botao_de_cima_dispara_o_proprio(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chamadas = _sem_disparar(monkeypatch)

        cliente.post("/fragmentos/comandos/executar", data={"comando": "check-sap"})

        assert chamadas[0][0] == "check-sap"

    def test_alternativo_em_comando_que_nao_tem_e_ignorado(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Um POST a mao com `alternativo` nao pode trocar comando nenhum."""
        chamadas = _sem_disparar(monkeypatch)

        cliente.post(
            "/fragmentos/comandos/executar",
            data={"comando": "pendentes", "alternativo": "1"},
        )

        assert chamadas[0][0] == "pendentes"
