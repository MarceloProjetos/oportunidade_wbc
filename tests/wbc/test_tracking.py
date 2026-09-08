"""Testes do banco de acompanhamento (SQLite em memória)."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from wbcpython.tracking import (
    RepositorioTracking,
    StatusExecucao,
    StatusIntegracao,
    TipoEvento,
    TravaNaoObtida,
)
from wbcpython.tracking.modelos import Trava


@pytest.fixture
def repo(tmp_path) -> RepositorioTracking:
    # Arquivo em vez de :memory: — a trava precisa ser visível entre conexões.
    return RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/tracking.db")


class TestAcompanhamento:
    def test_cria_na_primeira_verificacao(self, repo: RepositorioTracking) -> None:
        repo.registrar_verificacao("00123316", cliente="BALTEAU", sitcode_wbc=40)
        registro = repo.obter("00123316")
        assert registro is not None
        assert registro.cliente == "BALTEAU"
        assert registro.sitcode_wbc == 40

    def test_atualiza_na_segunda(self, repo: RepositorioTracking) -> None:
        repo.registrar_verificacao("X", cliente="A", sitcode_wbc=30)
        repo.registrar_verificacao("X", sitcode_wbc=60)
        registro = repo.obter("X")
        assert registro is not None
        assert registro.sitcode_wbc == 60

    def test_campos_nao_informados_sao_preservados(self, repo: RepositorioTracking) -> None:
        """Uma verificação parcial não pode apagar o que já se sabia."""
        repo.registrar_verificacao("X", cliente="BALTEAU", vendedor="043")
        repo.registrar_verificacao("X", sitcode_wbc=60)
        registro = repo.obter("X")
        assert registro is not None
        assert registro.cliente == "BALTEAU"
        assert registro.vendedor == "043"

    def test_marca_a_hora_da_verificacao(self, repo: RepositorioTracking) -> None:
        repo.registrar_verificacao("X")
        registro = repo.obter("X")
        assert registro is not None
        assert registro.ultima_verificacao is not None

    def test_inexistente_devolve_none(self, repo: RepositorioTracking) -> None:
        assert repo.obter("NAO_EXISTE") is None


class TestDocumentos:
    def test_registra_cotacao(self, repo: RepositorioTracking) -> None:
        repo.registrar_documento(
            "X", tipo="cotacao", doc_entry=10, doc_num=900, valor=Decimal("1500.50")
        )
        registro = repo.obter("X")
        assert registro is not None
        assert registro.cotacao_docentry == 10
        assert registro.cotacao_docnum == 900
        assert registro.cotacao_valor == Decimal("1500.50")

    def test_registra_pedido(self, repo: RepositorioTracking) -> None:
        repo.registrar_documento("X", tipo="pedido", doc_entry=20, doc_num=800)
        registro = repo.obter("X")
        assert registro is not None
        assert registro.pedido_docentry == 20

    def test_tipo_desconhecido_falha(self, repo: RepositorioTracking) -> None:
        with pytest.raises(ValueError, match="tipo de documento"):
            repo.registrar_documento("X", tipo="nota_fiscal", doc_entry=1)


class TestErros:
    def test_erro_muda_status_e_gera_evento(self, repo: RepositorioTracking) -> None:
        repo.registrar_erro("X", "SAP recusou: CNPJ duplicado")
        registro = repo.obter("X")
        assert registro is not None
        assert registro.status is StatusIntegracao.ERRO
        assert registro.tem_erro is True
        assert "CNPJ" in registro.ultimo_erro
        assert len(repo.eventos("X")) == 1

    def test_sucesso_posterior_limpa_o_erro(self, repo: RepositorioTracking) -> None:
        repo.registrar_erro("X", "falhou")
        repo.registrar_verificacao("X", status=StatusIntegracao.COTACAO_CRIADA)
        registro = repo.obter("X")
        assert registro is not None
        assert registro.ultimo_erro == ""
        assert registro.status is StatusIntegracao.COTACAO_CRIADA


class TestEventos:
    def test_guarda_historico(self, repo: RepositorioTracking) -> None:
        repo.registrar_evento("X", regra="sem_cotacao_cria", mensagem="criou cotação")
        repo.registrar_evento("X", tipo=TipoEvento.ACAO, mensagem="vinculou")
        assert len(repo.eventos("X")) == 2

    def test_detalhes_viram_json(self, repo: RepositorioTracking) -> None:
        repo.registrar_evento("X", mensagem="m", detalhes={"doc_entry": 5, "valor": Decimal("1.5")})
        evento = repo.eventos("X")[0]
        assert '"doc_entry": 5' in evento.detalhes

    def test_evento_cria_o_acompanhamento_se_faltar(self, repo: RepositorioTracking) -> None:
        repo.registrar_evento("NOVO", mensagem="primeiro contato")
        assert repo.obter("NOVO") is not None


class TestListagemEKpis:
    def test_filtra_por_status(self, repo: RepositorioTracking) -> None:
        repo.registrar_verificacao("A", status=StatusIntegracao.COTACAO_CRIADA)
        repo.registrar_verificacao("B", status=StatusIntegracao.ERRO)
        assert [r.orcnum for r in repo.listar(status=StatusIntegracao.ERRO)] == ["B"]

    def test_busca_por_orcamento_ou_cliente(self, repo: RepositorioTracking) -> None:
        repo.registrar_verificacao("00123316", cliente="BALTEAU")
        repo.registrar_verificacao("00999999", cliente="OUTRO")
        assert [r.orcnum for r in repo.listar(busca="BALTEAU")] == ["00123316"]
        assert [r.orcnum for r in repo.listar(busca="0012")] == ["00123316"]

    def test_contagem_por_status(self, repo: RepositorioTracking) -> None:
        repo.registrar_verificacao("A", status=StatusIntegracao.COTACAO_CRIADA)
        repo.registrar_verificacao("B", status=StatusIntegracao.COTACAO_CRIADA)
        repo.registrar_verificacao("C", status=StatusIntegracao.ERRO)
        contagem = repo.contagem_por_status()
        assert contagem[StatusIntegracao.COTACAO_CRIADA] == 2
        assert contagem[StatusIntegracao.ERRO] == 1


class TestExecucoes:
    def test_ciclo_completo(self, repo: RepositorioTracking) -> None:
        execucao_id = repo.iniciar_execucao()
        repo.finalizar_execucao(execucao_id, processados=10, sucessos=9, erros=1)

        execucao = repo.ultimas_execucoes()[0]
        assert execucao.status is StatusExecucao.CONCLUIDA
        assert execucao.processados == 10
        assert execucao.erros == 1
        assert execucao.duracao_segundos is not None

    def test_execucao_em_andamento_nao_tem_duracao(self, repo: RepositorioTracking) -> None:
        repo.iniciar_execucao()
        assert repo.ultimas_execucoes()[0].duracao_segundos is None

    def test_finalizar_execucao_inexistente_nao_falha(self, repo: RepositorioTracking) -> None:
        repo.finalizar_execucao(9999, processados=0, sucessos=0, erros=0)


class TestTravaDeExecucaoUnica:
    """O legado não tinha trava — duas execuções sobrepostas duplicariam documentos."""

    def test_permite_uma_execucao(self, repo: RepositorioTracking) -> None:
        with repo.trava_de_execucao():
            pass  # sem exceção

    def test_bloqueia_a_segunda_simultanea(self, repo: RepositorioTracking) -> None:
        with repo.trava_de_execucao(), pytest.raises(TravaNaoObtida), repo.trava_de_execucao():
            pass

    def test_libera_ao_sair(self, repo: RepositorioTracking) -> None:
        with repo.trava_de_execucao():
            pass
        with repo.trava_de_execucao():
            pass  # a segunda agora entra

    def test_libera_mesmo_com_excecao(self, repo: RepositorioTracking) -> None:
        with pytest.raises(RuntimeError), repo.trava_de_execucao():
            raise RuntimeError("falha no meio do processamento")
        with repo.trava_de_execucao():
            pass

    def test_trava_expirada_e_assumida(self, repo: RepositorioTracking) -> None:
        """Um worker que morre sem liberar não pode travar a integração para sempre."""
        with repo.sessao() as s, s.begin():
            s.add(
                Trava(
                    nome="worker_integracao",
                    dono="maquina-morta:999",
                    adquirida_em=datetime.now() - timedelta(hours=2),
                    expira_em=datetime.now() - timedelta(hours=1),
                )
            )
        with repo.trava_de_execucao():
            pass  # assumiu a trava expirada

    def test_mensagem_identifica_quem_detem(self, repo: RepositorioTracking) -> None:
        with repo.trava_de_execucao(), pytest.raises(TravaNaoObtida) as exc:
            with repo.trava_de_execucao():
                pass
        assert "execução em andamento" in str(exc.value)

    def test_concorrencia_real_entre_threads(self, repo: RepositorioTracking) -> None:
        """Duas execuções nunca ficam dentro da trava ao mesmo tempo.

        A primeira versão deste teste segurava a trava por `sleep(0.05)` e
        exigia que as outras três fossem recusadas. Isso só vale se as quatro
        threads disputarem dentro dessa janela: sob carga, a vencedora liberava
        antes de as outras chegarem, uma segunda entrava — legitimamente — e o
        teste falhava anunciando um defeito que não existia. Aconteceu de
        verdade, com a máquina rodando quatro processos ao mesmo tempo.

        Agora a vencedora só solta a trava depois que as quatro resolveram, o
        que torna a disputa determinística. E a asserção passou a ser a
        propriedade que importa — exclusão mútua, medida pelo pico de threads
        simultâneas lá dentro —, não a coincidência de tempo.
        """
        entraram: list[int] = []
        recusadas: list[int] = []
        pico = 0
        dentro = 0
        contador = threading.Lock()
        pode_sair = threading.Event()

        def tentar(n: int) -> None:
            nonlocal pico, dentro
            try:
                with repo.trava_de_execucao():
                    with contador:
                        dentro += 1
                        pico = max(pico, dentro)
                    entraram.append(n)
                    # Segura a trava até todo mundo ter tentado: é o que
                    # garante que a disputa aconteça de fato.
                    pode_sair.wait(timeout=10)
                    with contador:
                        dentro -= 1
            except TravaNaoObtida:
                recusadas.append(n)

        threads = [threading.Thread(target=tentar, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()

        limite = time.monotonic() + 10
        while len(entraram) + len(recusadas) < 4 and time.monotonic() < limite:
            time.sleep(0.01)
        pode_sair.set()
        for t in threads:
            t.join(timeout=10)

        assert pico == 1, f"{pico} execuções dentro da trava ao mesmo tempo"
        assert len(entraram) == 1, f"mais de uma execução entrou: {entraram}"
        assert len(recusadas) == 3


class TestJanelaDoAcompanhamento:
    """O acompanhamento nunca esquece — por isso o painel precisa do corte.

    Quando a janela encolheu de 6 para 3 meses, 167 orçamentos de maio
    continuaram aparecendo como se o ciclo ainda os olhasse. A data de abertura
    existe para que painel e worker apliquem exatamente o mesmo critério.
    """

    def test_filtra_pela_mesma_data_que_o_worker(self, repo: RepositorioTracking) -> None:
        from datetime import date

        repo.registrar_verificacao("DENTRO", data_abertura=date(2026, 7, 10))
        repo.registrar_verificacao("FORA", data_abertura=date(2026, 5, 5))

        dentro = repo.listar(aberto_desde=date(2026, 6, 1))
        assert [r.orcnum for r in dentro] == ["DENTRO"]
        assert len(repo.listar()) == 2

    def test_linha_sem_data_nao_some(self, repo: RepositorioTracking) -> None:
        """São as gravadas antes da coluna existir. Sumir por falta de um dado
        que nunca foi gravado seria lido como 'não existe', e não como
        'não sei a data'."""
        from datetime import date

        repo.registrar_verificacao("ANTIGA")
        assert [r.orcnum for r in repo.listar(aberto_desde=date(2026, 6, 1))] == ["ANTIGA"]

    def test_verificacao_sem_data_preserva_a_que_havia(self, repo: RepositorioTracking) -> None:
        from datetime import date

        repo.registrar_verificacao("A", data_abertura=date(2026, 7, 10))
        repo.registrar_verificacao("A", regra="outra")
        assert repo.obter("A").data_abertura == date(2026, 7, 10)


class TestPreencherDatas:
    """Sem o preenchimento retroativo, o corte de janela não pega o histórico.

    Data nula passa pelo filtro de propósito, e quem já saiu da janela nunca
    mais é verificado pelo ciclo — ficaria nulo para sempre.
    """

    def test_preenche_so_o_que_esta_vazio(self, repo: RepositorioTracking) -> None:
        from datetime import date, datetime

        repo.registrar_verificacao("VAZIA")
        repo.registrar_verificacao("JA_TEM", data_abertura=date(2026, 7, 1))

        preenchidas = repo.preencher_datas_de_abertura(
            {"VAZIA": datetime(2026, 5, 5, 0, 0), "JA_TEM": datetime(2020, 1, 1, 0, 0)}
        )

        assert preenchidas == 1
        assert repo.obter("VAZIA").data_abertura == date(2026, 5, 5)
        # A data do ciclo não é sobrescrita pela da varredura.
        assert repo.obter("JA_TEM").data_abertura == date(2026, 7, 1)

    def test_orcamento_que_nao_esta_no_acompanhamento_e_ignorado(
        self, repo: RepositorioTracking
    ) -> None:
        from datetime import datetime

        assert repo.preencher_datas_de_abertura({"NAO_EXISTE": datetime(2026, 5, 5)}) == 0

    def test_depois_de_preencher_o_corte_finalmente_pega(self, repo: RepositorioTracking) -> None:
        """O teste que amarra o motivo do comando existir."""
        from datetime import date, datetime

        repo.registrar_verificacao("DE_MAIO")
        assert len(repo.listar(aberto_desde=date(2026, 6, 1))) == 1  # nula passa

        repo.preencher_datas_de_abertura({"DE_MAIO": datetime(2026, 5, 5)})
        assert repo.listar(aberto_desde=date(2026, 6, 1)) == []


class TestColunaNovaEmBaseExistente:
    def test_add_column_na_base_que_ja_existia(self, tmp_path) -> None:
        """`create_all` cria tabela que falta, mas não altera a que existe: sem
        a migração, uma coluna nova vira 'no such column' na primeira consulta —
        e recriar a base perderia o histórico operacional."""
        from sqlalchemy import create_engine, inspect, text

        url = f"sqlite:///{tmp_path}/velha.db"
        # Uma base "de antes": tudo igual, menos a coluna que o modelo ganhou.
        antiga = RepositorioTracking.a_partir_da_url(url)
        antiga.registrar_verificacao("00000001", cliente="BALTEAU")
        with antiga._engine.begin() as conexao:
            conexao.execute(text("ALTER TABLE acompanhamento DROP COLUMN data_abertura"))
        antiga._engine.dispose()

        engine = create_engine(url)
        assert "data_abertura" not in {
            c["name"] for c in inspect(engine).get_columns("acompanhamento")
        }
        engine.dispose()

        repo = RepositorioTracking.a_partir_da_url(url)

        colunas = {c["name"] for c in inspect(repo._engine).get_columns("acompanhamento")}
        assert "data_abertura" in colunas
        # E o histórico continua lá — é o ponto de migrar em vez de recriar.
        registro = repo.obter("00000001")
        assert registro is not None
        assert registro.cliente == "BALTEAU"
        assert registro.data_abertura is None
