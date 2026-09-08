"""Testes do ponto de entrada de linha de comando."""

from __future__ import annotations

import pytest

from wbcpython.cli import main
from wbcpython.config import get_settings


@pytest.fixture(autouse=True)
def _config_limpa(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
    """Roda cada teste num diretório sem .env, com a config memoizada limpa."""
    monkeypatch.chdir(tmp_path)
    for var in (
        "WBC_BLOCK_PRODUCTION_WRITES",
        "WBC_PRODUCTION_COMPANY_DB",
        "SL_COMPANY_DB",
        "SL_USERNAME",
        "SL_PASSWORD",
        "LOG_FILE",
    ):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestComandoEnv:
    def test_ambiente_de_homologacao_retorna_sucesso(self, capsys: pytest.CaptureFixture) -> None:
        assert main(["env"]) == 0
        assert "homologação" in capsys.readouterr().out

    def test_producao_com_trava_ativa_avisa_mas_nao_falha(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAPROD")
        get_settings.cache_clear()
        assert main(["env"]) == 0
        saida = capsys.readouterr().out
        assert "PRODUÇÃO" in saida
        assert "ATIVA" in saida

    def test_producao_com_trava_desativada_avisa_sem_falhar(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Este teste já afirmou que a combinação **falhava**.

        Até a virada para produção ela era proibida pela regra do projeto. Agora
        é o estado normal: sair com erro faria o `doctor` e a aba "Executar" do
        painel marcarem falha em toda execução, e um alarme que toca sempre
        deixa de ser lido.

        O que continua obrigatório é dizer, em voz alta, que ali se escreve.
        """
        monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAPROD")
        monkeypatch.setenv("WBC_BLOCK_PRODUCTION_WRITES", "false")
        get_settings.cache_clear()
        assert main(["env"]) == 0
        saida = capsys.readouterr().out
        assert "DESATIVADA" in saida
        assert "ESCREVE em" in saida
        assert "não se desfazem" in saida

    def test_nao_vaza_credenciais(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("SL_USERNAME", "usuario_secreto")
        monkeypatch.setenv("SL_PASSWORD", "senha_secreta")
        get_settings.cache_clear()
        main(["env"])
        saida = capsys.readouterr().out
        assert "senha_secreta" not in saida
        assert "usuario_secreto" not in saida


class TestComandoDoctor:
    def test_sem_credenciais_reporta_problema(self, capsys: pytest.CaptureFixture) -> None:
        assert main(["doctor"]) == 1
        saida = capsys.readouterr().out
        assert "SL_USERNAME" in saida
        assert "SL_PASSWORD" in saida

    def test_com_credenciais_passa(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("SL_USERNAME", "usr")
        monkeypatch.setenv("SL_PASSWORD", "pwd")
        get_settings.cache_clear()
        assert main(["doctor"]) == 0
        assert "nenhum problema impeditivo" in capsys.readouterr().out

    def test_nao_vaza_senha(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("SL_USERNAME", "usr")
        monkeypatch.setenv("SL_PASSWORD", "senha_secreta")
        get_settings.cache_clear()
        main(["doctor"])
        assert "senha_secreta" not in capsys.readouterr().out


class TestComandoCheckSap:
    """O check-sap precisa falhar com mensagem legível, nunca com traceback."""

    def _forcar_erro(self, monkeypatch: pytest.MonkeyPatch, excecao: Exception) -> None:
        from wbcpython.infrastructure.service_layer import client as modulo

        def explode(self) -> None:
            raise excecao

        monkeypatch.setattr(modulo.ServiceLayerClient, "testar_conexao", explode)

    def test_falha_de_rede_nao_vira_traceback(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        # Regressão: httpx.ConnectError não é OSError e escapava do tratamento,
        # deixando o traceback vazar para quem rodasse fora da rede.
        import httpx

        monkeypatch.setenv("SL_USERNAME", "usr")
        monkeypatch.setenv("SL_PASSWORD", "pwd")
        get_settings.cache_clear()
        self._forcar_erro(monkeypatch, httpx.ConnectError("Connection reset by peer"))

        assert main(["check-sap"]) == 1
        saida = capsys.readouterr().out
        assert "Não foi possível alcançar o servidor" in saida
        assert "rede interna" in saida

    def test_erro_do_sap_vira_orientacao(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        from wbcpython.infrastructure.service_layer import ServiceLayerError

        monkeypatch.setenv("SL_USERNAME", "usr")
        monkeypatch.setenv("SL_PASSWORD", "pwd")
        get_settings.cache_clear()
        self._forcar_erro(
            monkeypatch,
            ServiceLayerError("Invalid user or password", status_code=401, sap_code=-304),
        )

        assert main(["check-sap"]) == 1
        saida = capsys.readouterr().out
        assert "Invalid user or password" in saida
        assert "Verifique, nesta ordem" in saida

    def test_sucesso_retorna_zero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        from wbcpython.infrastructure.service_layer import client as modulo

        monkeypatch.setenv("SL_USERNAME", "usr")
        monkeypatch.setenv("SL_PASSWORD", "pwd")
        get_settings.cache_clear()
        monkeypatch.setattr(
            modulo.ServiceLayerClient, "testar_conexao", lambda self: "Conexão OK com o SAP."
        )
        monkeypatch.setattr(modulo.ServiceLayerClient, "close", lambda self: None)

        assert main(["check-sap"]) == 0
        assert "Conexão OK" in capsys.readouterr().out


class TestComandoDashboard:
    def test_sem_fastapi_orienta_a_instalar(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        from wbcpython import cli as modulo

        monkeypatch.setattr(modulo, "_tem_modulo", lambda nome: False)
        assert main(["dashboard"]) == 1
        assert "requirements.txt" in capsys.readouterr().out

    def test_escuta_so_na_propria_maquina_por_padrao(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """O painel não tem autenticação: expor na rede precisa ser um ato
        deliberado, não o que acontece quando ninguém passa `--host`."""
        pytest.importorskip("uvicorn")
        from wbcpython import cli as modulo

        chamadas: list[dict] = []
        monkeypatch.setattr(modulo, "_preparar_log", lambda settings: None)
        monkeypatch.setattr(
            "uvicorn.run",
            lambda app, **kw: chamadas.append(kw),
        )
        monkeypatch.setattr(
            "wbcpython.dashboard.web.criar_app",
            lambda **kw: object(),
        )

        assert main(["dashboard"]) == 0
        assert chamadas[0]["host"] == "127.0.0.1"
        assert "não tem autenticação" not in capsys.readouterr().out

    def test_expor_na_rede_avisa_que_nao_ha_autenticacao(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        pytest.importorskip("uvicorn")
        from wbcpython import cli as modulo

        monkeypatch.setattr(modulo, "_preparar_log", lambda settings: None)
        monkeypatch.setattr("uvicorn.run", lambda app, **kw: None)
        monkeypatch.setattr("wbcpython.dashboard.web.criar_app", lambda **kw: object())

        assert main(["dashboard", "--host", "0.0.0.0", "--porta", "9000"]) == 0
        assert "não tem autenticação" in capsys.readouterr().out


class TestComandoCiclo:
    """`ciclo` roda uma vez e sai — `worker` roda continuamente.

    Estes testes usam um dublê do worker: iniciar o worker de verdade aqui
    penduraria a suíte (foi o que aconteceu quando o comando passou a existir e
    o teste antigo ainda o tratava como "não implementado").
    """

    def _dublar_worker(self, monkeypatch: pytest.MonkeyPatch, resultado) -> list:
        from wbcpython.host import worker as modulo

        chamadas = []

        class WorkerFalso:
            def __init__(self, *a, **kw) -> None:
                pass

            def executar_ciclo(self, *, orcamento=None, apenas_sitcode=None, somente_leitura=False):
                # `somente_leitura` é aceito e ignorado: quem o testa é
                # TestCicloSimulado. Este dublê existe para as outras opções, e
                # a assinatura estrita é de propósito — foi ela que apontou o
                # elo novo em vez de deixá-lo passar em silêncio.
                chamadas.append(("ciclo", orcamento, apenas_sitcode))
                return resultado

            def rodar_continuamente(self):
                chamadas.append(("continuo", None, None))

        monkeypatch.setattr(modulo, "WorkerIntegracao", WorkerFalso)
        return chamadas

    def test_ciclo_executa_uma_vez(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        from wbcpython.host.worker import ResultadoExecucao

        chamadas = self._dublar_worker(monkeypatch, ResultadoExecucao(3, 3, 0))
        assert main(["ciclo"]) == 0
        assert chamadas == [("ciclo", None, None)]
        assert "3 orçamento(s)" in capsys.readouterr().out

    def test_ciclo_com_orcamento_especifico(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from wbcpython.host.worker import ResultadoExecucao

        chamadas = self._dublar_worker(monkeypatch, ResultadoExecucao())
        main(["ciclo", "--orcamento", "00123316"])
        assert chamadas == [("ciclo", "00123316", None)]

    def test_ciclo_com_erros_retorna_codigo_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from wbcpython.host.worker import ResultadoExecucao

        self._dublar_worker(monkeypatch, ResultadoExecucao(2, 1, 1))
        assert main(["ciclo"]) == 1

    def test_cancelados_restringe_ao_sitcode_99(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Isola a leva dos cancelamentos: a ação deles não se desfaz."""
        from wbcpython.host.worker import ResultadoExecucao

        chamadas = self._dublar_worker(monkeypatch, ResultadoExecucao())
        assert main(["ciclo", "--cancelados"]) == 0
        assert chamadas == [("ciclo", None, 99)]

    def test_sem_a_opcao_o_ciclo_nao_filtra(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from wbcpython.host.worker import ResultadoExecucao

        chamadas = self._dublar_worker(monkeypatch, ResultadoExecucao())
        assert main(["ciclo"]) == 0
        assert chamadas == [("ciclo", None, None)]

    def test_worker_roda_continuamente(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from wbcpython.host.worker import ResultadoExecucao

        chamadas = self._dublar_worker(monkeypatch, ResultadoExecucao())
        assert main(["worker"]) == 0
        assert chamadas == [("continuo", None, None)]

    def test_producao_com_trava_desligada_roda_e_avisa(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Este teste já afirmou o contrário.

        A recusa existia como segunda defesa, contra desligar a trava e
        esquecer ligada. Na virada para produção, decidida pelo usuário, ela
        deixaria a integração sem poder rodar — e foi removida.

        O que ficou no lugar é visibilidade, e é isso que se afirma aqui:
        nenhum ciclo em produção começa sem dizer, na tela e no log, que vai
        escrever em documento de verdade.
        """
        monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAPROD")
        monkeypatch.setenv("WBC_BLOCK_PRODUCTION_WRITES", "false")
        get_settings.cache_clear()
        from wbcpython.host.worker import ResultadoExecucao

        chamadas = self._dublar_worker(monkeypatch, ResultadoExecucao())
        assert main(["ciclo"]) == 0
        assert chamadas == [("ciclo", None, None)]

        saida = capsys.readouterr().out
        assert "ESCRITA EM PRODUÇÃO HABILITADA" in saida
        assert "SBOALTAMIRAPROD" in saida
        assert "não se desfazem" in saida

    def test_em_homologacao_nao_ha_aviso_de_producao(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """O aviso tem de significar alguma coisa: se aparecesse sempre, viraria
        ruído e ninguém o leria no dia em que importa."""
        monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAHOMOLOG")
        get_settings.cache_clear()
        from wbcpython.host.worker import ResultadoExecucao

        self._dublar_worker(monkeypatch, ResultadoExecucao())
        assert main(["ciclo"]) == 0
        assert "ESCRITA EM PRODUÇÃO" not in capsys.readouterr().out


class TestSemArgumentos:
    def test_mostra_ajuda(self, capsys: pytest.CaptureFixture) -> None:
        assert main([]) == 0
        assert "doctor" in capsys.readouterr().out


class TestComandoPendentes:
    """`pendentes` é a prévia: mostra o que o ciclo faria e **não escreve nada**.

    Esta é a rede de segurança para testar em homologação, então o que importa
    aqui não é o texto impresso e sim a garantia negativa: nenhuma chamada que
    não seja de leitura, e o processador — que é quem escreve — nunca criado.
    """

    def _montar_ambiente(self, monkeypatch: pytest.MonkeyPatch, oportunidades_):
        from decimal import Decimal

        from wbcpython.infrastructure.hana import oportunidades as mod_hana
        from wbcpython.infrastructure.service_layer import client as mod_client
        from wbcpython.infrastructure.service_layer import documentos as mod_doc
        from wbcpython.infrastructure.service_layer import grupo_produtos as mod_grp
        from wbcpython.infrastructure.wbc_sql import repository as mod_wbc
        from wbcpython.infrastructure.wbc_sql.models import ItemOrcamentoWbc, OrcamentoWbc

        registro: dict[str, list] = {"http": [], "processadores": []}

        class ClienteFalso:
            def __init__(self, *a, **kw) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a) -> None:
                return None

            def request(self, metodo, *a, **kw):
                registro["http"].append(metodo)
                return {}

        class OportunidadesHanaFalso:
            """A prévia lê do HANA, como o ciclo — sem teto e sem paginação."""

            def __init__(self, settings, *, company_db) -> None:
                registro["company_db"] = company_db

            def pendentes_de_integracao(self, *, desde, orcamento=None):
                if orcamento:
                    return [o for o in oportunidades_ if o.get("U_ORCNUM_WBC") == orcamento]
                return list(oportunidades_)

            def close(self) -> None:
                return None

        class DocumentosFalso:
            def __init__(self, cliente) -> None:
                pass

            def existe(self, tipo, orcnum) -> bool:
                return False

            def revisao_aplicada(self, tipo, orcnum):
                return None

            def parceiro_aplicado(self, tipo, orcnum):
                return ""

            def esta_fechado(self, tipo, orcnum) -> bool:
                return False

        class GruposFalso:
            def __init__(self, cliente) -> None:
                pass

            def carregar(self):
                from wbcpython.infrastructure.service_layer.grupo_produtos import (
                    GrupoDeProduto,
                )

                return {"2": GrupoDeProduto("2", "Porta-Paletes", "I000003")}

        class WbcFalso:
            @classmethod
            def a_partir_de(cls, settings):
                return cls()

            def situacoes_atuais(self, orcnums):
                """Situação lida em lote — como no ciclo.

                `00000005` tem SitCode abaixo do mínimo: é o orçamento sem ação,
                usado para verificar o filtro `--com-acao`.
                """
                return {
                    orcnum: (5 if orcnum == "00000005" else 30, "A")
                    for orcnum in orcnums
                    if orcnum != "99999999"
                }

            def buscar_orcamento(self, orcnum):
                if orcnum == "99999999":
                    return None
                if orcnum == "00000000":
                    # Orçamento ainda sem itens no WBC — situação normal, e o
                    # que o SAP recusaria como documento sem valor.
                    return OrcamentoWbc(
                        orcnum=orcnum,
                        revisao="A",
                        sitcode=30,
                        cliente_nome="BALTEAU",
                        representante="043",
                        municipio="ITAJUBA",
                        uf="MG",
                        itens=(),
                    )
                return OrcamentoWbc(
                    orcnum=orcnum,
                    revisao="A",
                    sitcode=30,
                    cliente_nome="BALTEAU",
                    representante="043",
                    municipio="ITAJUBA",
                    uf="MG",
                    itens=(
                        ItemOrcamentoWbc(
                            orcitm=1, produto="P", quantidade=Decimal(1), valor=Decimal(10)
                        ),
                    ),
                )

        class ProcessadorEspiao:
            def __init__(self, *a, **kw) -> None:
                registro["processadores"].append(kw)
                raise AssertionError("a prévia não pode instanciar o processador")

        from wbcpython.application import processar as mod_proc

        monkeypatch.setattr(mod_client, "ServiceLayerClient", ClienteFalso)
        monkeypatch.setattr(mod_hana, "RepositorioOportunidadesHana", OportunidadesHanaFalso)
        monkeypatch.setattr(mod_doc, "RepositorioDocumentosVendaServiceLayer", DocumentosFalso)
        monkeypatch.setattr(mod_grp, "RepositorioGrupoProdutosServiceLayer", GruposFalso)
        monkeypatch.setattr(mod_wbc, "RepositorioOrcamentosWbcSql", WbcFalso)
        monkeypatch.setattr(mod_proc, "ProcessadorDeOrcamento", ProcessadorEspiao)
        return registro

    def test_lista_decisao_sem_executar_nada(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        registro = self._montar_ambiente(
            monkeypatch,
            [{"SequentialNo": 1, "U_ORCNUM_WBC": "00123316", "U_INO_StatusWBC": "0"}],
        )
        assert main(["pendentes"]) == 0

        saida = capsys.readouterr().out
        assert "00123316" in saida
        assert "regra:" in saida
        assert "somente leitura" in saida
        assert registro["http"] == []
        assert registro["processadores"] == []

    def test_orcamento_ausente_no_wbc_e_relatado_nao_estourado(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        self._montar_ambiente(monkeypatch, [{"SequentialNo": 2, "U_ORCNUM_WBC": "99999999"}])
        assert main(["pendentes"]) == 0
        assert "não encontrado no WBC" in capsys.readouterr().out

    def test_oportunidade_sem_numero_de_orcamento_e_avisada(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        self._montar_ambiente(monkeypatch, [{"SequentialNo": 3, "U_ORCNUM_WBC": ""}])
        assert main(["pendentes"]) == 0
        assert "sem U_ORCNUM_WBC" in capsys.readouterr().out

    def test_filtra_por_orcamento(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        self._montar_ambiente(
            monkeypatch,
            [
                {"SequentialNo": 1, "U_ORCNUM_WBC": "00123316"},
                {"SequentialNo": 2, "U_ORCNUM_WBC": "00123317"},
            ],
        )
        assert main(["pendentes", "--orcamento", "00123316"]) == 0
        saida = capsys.readouterr().out
        assert "00123316" in saida
        assert "00123317" not in saida

    def test_com_acao_esconde_quem_nao_tem_acao(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Numa janela de ~950, as com ação são dezenas — o resto é ruído."""
        self._montar_ambiente(
            monkeypatch,
            [
                {"SequentialNo": 1, "U_ORCNUM_WBC": "00123316"},
                {"SequentialNo": 2, "U_ORCNUM_WBC": "00000005"},
            ],
        )
        assert main(["pendentes", "--com-acao"]) == 0
        saida = capsys.readouterr().out
        assert "00123316" in saida
        assert "00000005" not in saida

    def test_com_acao_nao_muda_a_contagem_do_resumo(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Filtrar a exibição não pode dar a impressão de uma janela menor.

        O filtro é de saída, não de avaliação: os dois orçamentos continuam
        sendo lidos e decididos, e o resumo tem de dizer isso.
        """
        self._montar_ambiente(
            monkeypatch,
            [
                {"SequentialNo": 1, "U_ORCNUM_WBC": "00123316"},
                {"SequentialNo": 2, "U_ORCNUM_WBC": "00000005"},
            ],
        )
        assert main(["pendentes", "--com-acao"]) == 0
        assert "2 avaliada(s); 1 resultaria" in capsys.readouterr().out

    def test_sem_o_filtro_lista_todos(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        self._montar_ambiente(
            monkeypatch,
            [
                {"SequentialNo": 1, "U_ORCNUM_WBC": "00123316"},
                {"SequentialNo": 2, "U_ORCNUM_WBC": "00000005"},
            ],
        )
        assert main(["pendentes"]) == 0
        saida = capsys.readouterr().out
        assert "00123316" in saida
        assert "00000005" in saida

    def test_com_acao_ainda_mostra_problemas_de_dado(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Orçamento ausente no WBC e oportunidade sem número não geram ação,
        mas são exatamente o que a prévia existe para revelar."""
        self._montar_ambiente(
            monkeypatch,
            [
                {"SequentialNo": 2, "U_ORCNUM_WBC": "99999999"},
                {"SequentialNo": 3, "U_ORCNUM_WBC": ""},
            ],
        )
        assert main(["pendentes", "--com-acao"]) == 0
        saida = capsys.readouterr().out
        assert "não encontrado no WBC" in saida
        assert "sem U_ORCNUM_WBC" in saida

    def test_avisa_quando_o_documento_nao_teria_valor(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """A prévia tem de mostrar o que o ciclo faria — e o ciclo não envia
        documento sem valor. Sem este aviso, o orçamento aparece como escrita
        prevista e depois nada acontece, sem explicação."""
        self._montar_ambiente(monkeypatch, [{"SequentialNo": 1, "U_ORCNUM_WBC": "00000000"}])
        assert main(["pendentes"]) == 0
        saida = capsys.readouterr().out
        assert "NÃO seria criado" in saida
        assert "recusa documento sem valor" in saida

    def test_exportar_grava_o_retrato_que_o_painel_le(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture
    ) -> None:
        """O painel não consulta SAP nem WBC: ele lê este arquivo.

        Por isso o contrato é testado aqui e não só na tela — se o comando
        parar de gravar, o painel some sem nenhum erro visível.
        """
        import json

        from wbcpython.dashboard.previsao import carregar

        self._montar_ambiente(monkeypatch, [{"SequentialNo": 1, "U_ORCNUM_WBC": "00123316"}])
        destino = tmp_path / "previsao.json"
        assert main(["pendentes", "--exportar", str(destino)]) == 0

        gravado = json.loads(destino.read_text(encoding="utf-8"))
        assert gravado["meta"]["company_db"] == "SBOALTAMIRAHOMOLOG"
        assert gravado["linhas"][0]["orcnum"] == "00123316"
        # E o painel consegue ler de volta o que foi gravado.
        assert carregar(destino).linhas[0].orcnum == "00123316"
        assert "Retrato salvo em" in capsys.readouterr().out

    def test_o_relatorio_tambem_vai_para_o_arquivo_de_log(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture
    ) -> None:
        """A promessa das duas telas: o que a pessoa lê no terminal é o que o
        painel mostra depois. Se o comando voltar a usar `print`, a tela
        continua certa e o painel fica em branco — sem erro nenhum."""
        from wbcpython.logs import ler

        log = tmp_path / "wbcpython.log"
        monkeypatch.setenv("LOG_FILE", str(log))
        get_settings.cache_clear()
        self._montar_ambiente(monkeypatch, [{"SequentialNo": 1, "U_ORCNUM_WBC": "00123316"}])

        assert main(["pendentes"]) == 0
        na_tela = capsys.readouterr().out
        no_arquivo = "\n".join(linha.mensagem for linha in ler(log))

        assert "00123316" in na_tela
        assert "00123316" in no_arquivo
        assert "Resumo:" in no_arquivo

    def test_falha_de_rede_vira_mensagem_e_codigo_1(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        from wbcpython.infrastructure.service_layer import client as mod_client

        class ClienteQueFalha:
            def __init__(self, *a, **kw) -> None:
                raise ConnectionError("sem rota para o host")

        monkeypatch.setattr(mod_client, "ServiceLayerClient", ClienteQueFalha)
        assert main(["pendentes"]) == 1
        assert "ConnectionError" in capsys.readouterr().out


class TestAvisoDeProducaoDoPendentes:
    """O aviso final do `pendentes` tem de descrever a trava que existe hoje.

    O texto antigo — "A trava impedirá as escritas" — sobreviveu à virada para
    produção e passou a mentir: prometia uma proteção desligada. Quem lê a
    prévia conclui que o ciclo seguinte é ensaio, e é exatamente esse leitor
    que decide rodar o ciclo.
    """

    def _preparar(self, monkeypatch: pytest.MonkeyPatch, *, trava: str) -> None:
        TestComandoPendentes()._montar_ambiente(
            monkeypatch,
            [{"SequentialNo": 1, "U_ORCNUM_WBC": "00123316", "U_INO_StatusWBC": "0"}],
        )
        monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAPROD")
        monkeypatch.setenv("HANA_SCHEMA", "SBOALTAMIRAPROD")
        monkeypatch.setenv("WBC_BLOCK_PRODUCTION_WRITES", trava)
        get_settings.cache_clear()

    def test_com_a_trava_desligada_diz_que_as_escritas_sao_de_verdade(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        self._preparar(monkeypatch, trava="false")
        assert main(["pendentes"]) == 0
        saida = capsys.readouterr().out
        assert "DESATIVADA" in saida
        assert "de verdade" in saida
        assert "não se desfazem" in saida

    def test_com_a_trava_desligada_nunca_promete_protecao(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """O teste que existe por causa do defeito: nada de "impedirá"."""
        self._preparar(monkeypatch, trava="false")
        assert main(["pendentes"]) == 0
        saida = capsys.readouterr().out
        assert "impedirá" not in saida
        assert "trava de escrita está ATIVA" not in saida

    def test_com_a_trava_ligada_diz_que_nada_seria_gravado(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        self._preparar(monkeypatch, trava="true")
        assert main(["pendentes"]) == 0
        saida = capsys.readouterr().out
        assert "ATIVA" in saida
        assert "não gravaria nada" in saida

    def test_o_aviso_nomeia_a_base_alvo(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Metade dos sustos vem de não saber qual base está no `.env`."""
        self._preparar(monkeypatch, trava="false")
        assert main(["pendentes"]) == 0
        assert "SBOALTAMIRAPROD" in capsys.readouterr().out


class TestCicloSimulado:
    """`ciclo --simular`: enche o painel sem tocar no SAP."""

    def test_a_bandeira_existe(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit):
            main(["ciclo", "--help"])
        assert "--simular" in capsys.readouterr().out

    def test_chega_ao_worker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A bandeira tem de atravessar CLI e worker até o processador. Um elo
        solto aqui não falha: executa o ciclo de verdade em silêncio."""
        recebido: dict[str, object] = {}

        class WorkerFalso:
            def __init__(self, *a, **kw) -> None: ...

            def executar_ciclo(self, **kw):
                recebido.update(kw)
                from wbcpython.host.worker import ResultadoExecucao

                return ResultadoExecucao()

        from wbcpython.host import worker as mod_worker

        monkeypatch.setattr(mod_worker, "WorkerIntegracao", WorkerFalso)
        monkeypatch.setenv("LOG_FILE", "")
        get_settings.cache_clear()

        assert main(["ciclo", "--simular"]) == 0
        assert recebido["somente_leitura"] is True

    def test_sem_a_bandeira_o_ciclo_e_de_verdade(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recebido: dict[str, object] = {}

        class WorkerFalso:
            def __init__(self, *a, **kw) -> None: ...

            def executar_ciclo(self, **kw):
                recebido.update(kw)
                from wbcpython.host.worker import ResultadoExecucao

                return ResultadoExecucao()

        from wbcpython.host import worker as mod_worker

        monkeypatch.setattr(mod_worker, "WorkerIntegracao", WorkerFalso)
        monkeypatch.setenv("LOG_FILE", "")
        get_settings.cache_clear()

        assert main(["ciclo"]) == 0
        assert recebido["somente_leitura"] is False

    def test_o_aviso_de_producao_da_lugar_ao_da_simulacao(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Dizer "escreve de verdade" numa execução que não escreve nada ensina
        a ignorar o aviso justamente quando ele importa."""

        class WorkerFalso:
            def __init__(self, *a, **kw) -> None: ...

            def executar_ciclo(self, **kw):
                from wbcpython.host.worker import ResultadoExecucao

                return ResultadoExecucao()

        from wbcpython.host import worker as mod_worker

        monkeypatch.setattr(mod_worker, "WorkerIntegracao", WorkerFalso)
        monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAPROD")
        monkeypatch.setenv("WBC_BLOCK_PRODUCTION_WRITES", "false")
        monkeypatch.setenv("LOG_FILE", "")
        get_settings.cache_clear()

        assert main(["ciclo", "--simular"]) == 0
        saida = capsys.readouterr().out
        assert "SIMULAÇÃO" in saida
        assert "ESCRITA EM PRODUÇÃO HABILITADA" not in saida
        assert "nenhum documento foi criado" in saida


class TestHostDoPainel:
    """De onde vem o endereço de escuta, e o que ele avisa.

    O padrão fechado é o que importa aqui: o painel não tem autenticação, e
    quem alcança a porta registra reprocessamento em nome de quem quiser e
    dispara a simulação de ciclo. Expor tem de ser escolha escrita.
    """

    def _executar(self, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> dict:
        recebido: dict = {}

        class UvicornFalso:
            @staticmethod
            def run(app, host, port, log_level="warning"):
                recebido.update(host=host, porta=port)

        import uvicorn

        monkeypatch.setattr(uvicorn, "run", UvicornFalso.run)
        monkeypatch.setenv("LOG_FILE", "")
        get_settings.cache_clear()
        assert main(argv) == 0
        return recebido

    def test_padrao_e_so_a_propria_maquina(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._executar(monkeypatch, ["dashboard"])["host"] == "127.0.0.1"

    def test_o_env_muda_o_padrao(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`PAINEL_HOST` existe porque o painel roda como serviço: uma escolha
        que só vive no comando se perde no primeiro reinício."""
        monkeypatch.setenv("PAINEL_HOST", "0.0.0.0")
        monkeypatch.setenv("PAINEL_PORTA", "8501")
        recebido = self._executar(monkeypatch, ["dashboard"])
        assert recebido == {"host": "0.0.0.0", "porta": 8501}

    def test_a_linha_de_comando_ganha_do_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Para um teste pontual sem editar o `.env`."""
        monkeypatch.setenv("PAINEL_HOST", "0.0.0.0")
        recebido = self._executar(monkeypatch, ["dashboard", "--host", "127.0.0.1"])
        assert recebido["host"] == "127.0.0.1"

    def test_avisa_ao_expor(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("PAINEL_HOST", "0.0.0.0")
        self._executar(monkeypatch, ["dashboard"])
        saida = capsys.readouterr().out
        assert "não tem autenticação" in saida

    def test_nao_avisa_em_loopback(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Aviso que aparece sempre deixa de ser lido."""
        self._executar(monkeypatch, ["dashboard"])
        assert "não tem autenticação" not in capsys.readouterr().out

    def test_um_ip_fixo_da_rede_tambem_expoe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`0.0.0.0` é o caso comum, não a definição. Fixar o IP da máquina na
        rede expõe igual, e um teste de igualdade com "0.0.0.0" deixaria
        passar calado."""
        from wbcpython.config import Settings

        monkeypatch.setenv("PAINEL_HOST", "192.168.1.70")
        get_settings.cache_clear()
        assert Settings().painel_exposto

    def test_loopback_nao_conta_como_exposto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from wbcpython.config import Settings

        for endereco in ("127.0.0.1", "localhost", "::1"):
            monkeypatch.setenv("PAINEL_HOST", endereco)
            get_settings.cache_clear()
            assert not Settings().painel_exposto


class TestJanelaDaPreviaComOrcamento:
    """A prévia tem de olhar a **mesma** janela que o ciclo olharia.

    Uma prévia com janela menor diria "nada a fazer" sobre um orçamento que o
    ciclo seguinte vai atualizar — e é justamente a prévia que autoriza rodá-lo.
    """

    def _cortes(self, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> list:
        vistos: list = []
        TestComandoPendentes()._montar_ambiente(monkeypatch, [])

        from wbcpython.infrastructure.hana import oportunidades as mod_hana

        class HanaEspiao:
            def __init__(self, *a, **kw) -> None: ...

            def pendentes_de_integracao(self, *, desde, orcamento=None):
                vistos.append((desde, orcamento))
                return []

            def close(self) -> None: ...

        monkeypatch.setattr(mod_hana, "RepositorioOportunidadesHana", HanaEspiao)
        monkeypatch.setenv("MESES_DE_JANELA", "6")
        monkeypatch.setenv("MESES_DE_JANELA_DIRIGIDA", "12")
        monkeypatch.setenv("LOG_FILE", "")
        get_settings.cache_clear()
        assert main(argv) == 0
        return vistos

    def test_com_orcamento_usa_a_janela_dirigida(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from wbcpython.host.worker import janela_padrao

        vistos = self._cortes(monkeypatch, ["pendentes", "--orcamento", "00124045"])
        assert vistos[0][0] == janela_padrao(meses=12)

    def test_sem_orcamento_usa_a_janela_do_ciclo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from wbcpython.host.worker import janela_padrao

        vistos = self._cortes(monkeypatch, ["pendentes"])
        assert vistos[0][0] == janela_padrao(meses=6)

    def test_nada_encontrado_com_orcamento_avisa(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        self._cortes(monkeypatch, ["pendentes", "--orcamento", "00100000"])
        saida = capsys.readouterr().out
        assert "00100000" in saida
        assert "não encontrado" in saida

    def test_nada_encontrado_sem_orcamento_nao_avisa(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        self._cortes(monkeypatch, ["pendentes"])
        assert "não encontrado" not in capsys.readouterr().out


class TestComandoPesos:
    """`wbcpython pesos` — corrige o `Weight1` de um pedido já criado.

    O que precisa ser garantido aqui é o que distingue este comando de um
    reprocessamento: ele grava **só** o peso, casa as linhas pelo `U_INO_ORCITM`
    (não pela ordem) e, com `--simular`, não escreve nada.
    """

    def _montar_ambiente(self, monkeypatch, *, doc, pesos):
        from decimal import Decimal

        from wbcpython.infrastructure.service_layer import client as mod_client
        from wbcpython.infrastructure.service_layer import documentos as mod_doc
        from wbcpython.infrastructure.wbc_sql import repository as mod_wbc

        registro: dict[str, list] = {"pesos_gravados": [], "http": []}

        class ClienteFalso:
            def __init__(self, *a, **kw) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a) -> None:
                return None

            def request(self, metodo, *a, **kw):
                registro["http"].append(metodo)
                return {}

        class DocumentosFalso:
            def __init__(self, cliente) -> None:
                pass

            def buscar_por_docnum(self, tipo, docnum):
                return doc

            def buscar(self, tipo, orcamento):
                return doc

            def atualizar_pesos(self, tipo, doc_entry, pesos_por_linha):
                registro["pesos_gravados"].append((doc_entry, dict(pesos_por_linha)))

        class WbcFalso:
            @classmethod
            def a_partir_de(cls, settings):
                return cls()

            def pesos_por_item(self, orcnum):
                return {k: Decimal(str(v)) for k, v in pesos.items()}

        monkeypatch.setattr(mod_client, "ServiceLayerClient", ClienteFalso)
        monkeypatch.setattr(mod_doc, "RepositorioDocumentosVendaServiceLayer", DocumentosFalso)
        monkeypatch.setattr(mod_wbc, "RepositorioOrcamentosWbcSql", WbcFalso)
        return registro

    def _pedido(self, *linhas):
        return {
            "DocEntry": 19489,
            "DocNum": 84315,
            "Cancelled": "tNO",
            "U_INO_COTWBC": "00124853",
            "DocumentLines": list(linhas),
        }

    def _linha(self, line_num, orcitm, *, peso=0.0, quantidade=1.0):
        return {
            "LineNum": line_num,
            "U_INO_ORCITM": str(orcitm),
            "Weight1": peso,
            "Quantity": quantidade,
        }

    def test_grava_o_peso_das_linhas_desatualizadas(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        registro = self._montar_ambiente(
            monkeypatch,
            doc=self._pedido(self._linha(0, 1, peso=1.0), self._linha(1, 5, peso=1.0)),
            pesos={1: "760.65", 5: "45.13"},
        )

        assert main(["pesos", "--pedido", "84315"]) == 0
        # floor(760,65 × 1,1) = 836 — o mesmo valor do pedido 84112 da produção.
        assert registro["pesos_gravados"] == [(19489, {0: 836.0, 1: 49.0})]
        assert "1 -> 836 kg" in capsys.readouterr().out

    def test_casa_por_orcitm_e_nao_por_ordem(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Uma linha a mais ou fora de ordem não pode deslocar o peso das outras."""
        registro = self._montar_ambiente(
            monkeypatch,
            doc=self._pedido(self._linha(0, 5), self._linha(1, 1)),
            pesos={1: "760.65", 5: "45.13"},
        )

        assert main(["pesos", "--pedido", "84315"]) == 0
        assert registro["pesos_gravados"] == [(19489, {0: 49.0, 1: 836.0})]

    def test_peso_e_dividido_pela_quantidade(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        registro = self._montar_ambiente(
            monkeypatch,
            doc=self._pedido(self._linha(0, 1, quantidade=4.0)),
            pesos={1: "760.60"},
        )

        assert main(["pesos", "--pedido", "84315"]) == 0
        # floor((760,60 / 4) × 1,1) = floor(209,165) = 209
        assert registro["pesos_gravados"] == [(19489, {0: 209.0})]

    def test_simular_nao_escreve(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        registro = self._montar_ambiente(
            monkeypatch,
            doc=self._pedido(self._linha(0, 1)),
            pesos={1: "760.65"},
        )

        assert main(["pesos", "--pedido", "84315", "--simular"]) == 0
        assert registro["pesos_gravados"] == []
        saida = capsys.readouterr().out
        assert "Simulação" in saida
        assert "mudariam" in saida

    def test_linha_ja_correta_nao_e_reescrita(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Escrita à toa é ida à rede e ruído no histórico do documento."""
        registro = self._montar_ambiente(
            monkeypatch,
            doc=self._pedido(self._linha(0, 1, peso=836.0)),
            pesos={1: "760.65"},
        )

        assert main(["pesos", "--pedido", "84315"]) == 0
        assert registro["pesos_gravados"] == []
        assert "Nada a alterar" in capsys.readouterr().out

    def test_linha_sem_peso_na_arvore_e_relatada_e_mantida(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        registro = self._montar_ambiente(
            monkeypatch,
            doc=self._pedido(self._linha(0, 1), self._linha(1, 9, peso=1.0)),
            pesos={1: "760.65"},
        )

        assert main(["pesos", "--pedido", "84315"]) == 0
        assert registro["pesos_gravados"] == [(19489, {0: 836.0})]
        assert "sem peso na árvore" in capsys.readouterr().out

    def test_peso_que_trunca_para_zero_e_mantido(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Gravar 0 kg some do somatório da expedição — pior que o 1 do cadastro."""
        registro = self._montar_ambiente(
            monkeypatch,
            doc=self._pedido(self._linha(0, 1, peso=1.0)),
            pesos={1: "0.5"},
        )

        assert main(["pesos", "--pedido", "84315"]) == 0
        assert registro["pesos_gravados"] == []
        assert "trunca para zero" in capsys.readouterr().out

    def test_pedido_inexistente_vira_erro(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        self._montar_ambiente(monkeypatch, doc=None, pesos={})
        assert main(["pesos", "--pedido", "99999"]) == 1
        assert "Nenhum pedido encontrado" in capsys.readouterr().out

    def test_orcamento_sem_arvore_nao_e_erro(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Não ter árvore é comum e não é falha: o pedido fica como está."""
        registro = self._montar_ambiente(monkeypatch, doc=self._pedido(self._linha(0, 1)), pesos={})
        assert main(["pesos", "--orcamento", "00124853"]) == 0
        assert registro["pesos_gravados"] == []
        assert "não tem árvore" in capsys.readouterr().out

    def test_exige_pedido_ou_orcamento(self) -> None:
        with pytest.raises(SystemExit):
            main(["pesos"])


class TestDoctorEmProducao:
    """Depois da virada, produção com a trava desligada é o estado normal.

    Antes era problema impeditivo. Contá-lo agora faria o `doctor` sair com erro
    em toda execução — e um diagnóstico que nunca fica verde para de ser
    diagnóstico, porque ninguém distingue mais o dia em que algo quebrou.
    """

    def test_nao_conta_como_problema_impeditivo(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAPROD")
        monkeypatch.setenv("WBC_BLOCK_PRODUCTION_WRITES", "false")
        monkeypatch.setenv("SL_USERNAME", "usr")
        monkeypatch.setenv("SL_PASSWORD", "pwd")
        get_settings.cache_clear()

        assert main(["doctor"]) == 0
        assert "nenhum problema impeditivo" in capsys.readouterr().out

    def test_mas_diz_em_voz_alta_que_escreve(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAPROD")
        monkeypatch.setenv("WBC_BLOCK_PRODUCTION_WRITES", "false")
        monkeypatch.setenv("SL_USERNAME", "usr")
        monkeypatch.setenv("SL_PASSWORD", "pwd")
        get_settings.cache_clear()

        main(["doctor"])
        saida = capsys.readouterr().out
        assert "ESCREVE em" in saida
        assert "SBOALTAMIRAPROD" in saida
