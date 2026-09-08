"""Testes das rotas do painel — o HTML de verdade, sem navegador.

As agregações têm teste próprio (`test_previsao.py`, `test_dashboard.py`). O que
só aparece aqui é o que quebra na tela e em nenhuma função pura: um template com
variável errada, um fragmento que devolve 500, um filtro que não chega ao
repositório. Com o Streamlit isso exigia subir um runtime inteiro; aqui é uma
requisição HTTP contra um SQLite temporário.

O `importorskip` mantém a suíte verde em quem não instalou o extra `dashboard`.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from wbcpython.application.previsao import LinhaDePrevisao, Previsao, para_json
from wbcpython.config import Settings
from wbcpython.dashboard.web import criar_app
from wbcpython.tracking import RepositorioTracking, StatusIntegracao


@pytest.fixture
def repo(tmp_path: Path) -> RepositorioTracking:
    return RepositorioTracking.a_partir_da_url(f"sqlite:///{tmp_path}/painel.db")


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("TRACKING_DB_URL", f"sqlite:///{tmp_path}/painel.db")
    monkeypatch.setenv("SL_BASE_URL", "https://exemplo:50000/b1s/v1")
    monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAHOMOLOG")
    monkeypatch.setenv("SL_USERNAME", "u")
    monkeypatch.setenv("SL_PASSWORD", "p")
    monkeypatch.setenv("LOG_FILE", "")
    return Settings()


@pytest.fixture
def cliente(config: Settings, repo: RepositorioTracking) -> TestClient:
    return TestClient(criar_app(settings=config, tracking=repo))


def _retrato(tmp_path: Path) -> Path:
    previsao = Previsao(
        gerado_em=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        company_db="SBOALTAMIRAHOMOLOG",
        corte="2026-06-01",
        meses_de_janela=3,
        limite_de_escrita=200,
        linhas=(
            LinhaDePrevisao(orcnum="00000001", cliente="A", sitcode_wbc=40),
            LinhaDePrevisao(
                orcnum="00000002",
                cliente="B",
                sitcode_wbc=60,
                status_oportunidade="Vendida",
                acoes=("atualizar_status_oportunidade",),
            ),
            LinhaDePrevisao(
                orcnum="00000003",
                cliente="C",
                sitcode_wbc=40,
                cotacao_docnum=77829,
                acoes=("criar_cotacao", "vincular_documento_a_oportunidade"),
                valor_do_documento=Decimal("100.00"),
            ),
        ),
    )
    destino = tmp_path / "previsao.json"
    destino.write_text(json.dumps(para_json(previsao)), encoding="utf-8")
    return destino


class TestPagina:
    def test_a_casca_carrega_e_traz_o_htmx_do_proprio_pacote(self, cliente: TestClient) -> None:
        """Nada de CDN: a máquina do worker não tem saída para a internet, e um
        painel que depende de baixar algo abre em branco."""
        resposta = cliente.get("/")
        assert resposta.status_code == 200
        assert "/static/htmx.min.js" in resposta.text
        assert "cdn" not in resposta.text.lower()
        assert cliente.get("/static/htmx.min.js").status_code == 200

    def test_o_ambiente_fica_visivel(self, cliente: TestClient) -> None:
        assert "SBOALTAMIRAHOMOLOG" in cliente.get("/").text

    def test_producao_ganha_tarja(self, tmp_path: Path, monkeypatch, repo) -> None:
        """O fato mais perigoso da tela é o ambiente: em produção o ciclo cria e
        cancela documentos de verdade."""
        monkeypatch.setenv("TRACKING_DB_URL", f"sqlite:///{tmp_path}/p.db")
        monkeypatch.setenv("SL_BASE_URL", "https://exemplo:50000/b1s/v1")
        monkeypatch.setenv("SL_COMPANY_DB", "SBOALTAMIRAPROD")
        monkeypatch.setenv("SL_USERNAME", "u")
        monkeypatch.setenv("SL_PASSWORD", "p")
        cliente = TestClient(criar_app(settings=Settings(), tracking=repo))

        texto = cliente.get("/").text
        assert "tarja-producao" in texto
        assert "não têm volta" in texto

    def test_a_aba_vem_do_endereco(self, cliente: TestClient) -> None:
        """Estado no endereço: o painel pode ficar numa TV numa aba fixa, e um
        endereço colado no chat abre a mesma tela para quem receber."""
        assert "/fragmentos/log" in cliente.get("/?aba=log").text

    def test_aba_desconhecida_cai_no_padrao(self, cliente: TestClient) -> None:
        assert "/fragmentos/oportunidades" in cliente.get("/?aba=inventada").text


class TestKpis:
    def test_conta_avaliados_e_com_acao_separadamente(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        """Contar tudo como integrado anunciava centenas de orçamentos tratados
        quando o ciclo não havia tocado em nenhum."""
        hoje = date.today()
        repo.registrar_verificacao("A", status=StatusIntegracao.COTACAO_CRIADA, data_abertura=hoje)
        repo.registrar_verificacao("B", status=StatusIntegracao.SEM_ACAO, data_abertura=hoje)

        texto = cliente.get("/fragmentos/kpis").text
        assert "Avaliados" in texto
        assert "Com ação" in texto

    def test_erro_tira_o_semaforo_do_verde(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        """Qualquer erro já tira do verde: num fluxo que cria documentos
        financeiros, um erro isolado merece atenção — não é ruído estatístico."""
        hoje = date.today()
        for i in range(9):
            repo.registrar_verificacao(
                f"OK{i}", status=StatusIntegracao.COTACAO_CRIADA, data_abertura=hoje
            )
        repo.registrar_verificacao("RUIM", status=StatusIntegracao.ERRO, data_abertura=hoje)

        texto = cliente.get("/fragmentos/kpis").text
        assert "Integração saudável" not in texto
        assert "erro" in texto

    def test_sem_dados_diz_que_nao_ha_dados(self, cliente: TestClient) -> None:
        assert "Nenhum orçamento processado ainda" in cliente.get("/fragmentos/kpis").text


class TestOportunidades:
    def test_lista_e_filtra_por_busca(self, cliente: TestClient, repo: RepositorioTracking) -> None:
        hoje = date.today()
        repo.registrar_verificacao(
            "00000001", status=StatusIntegracao.SEM_ACAO, cliente="ACME", data_abertura=hoje
        )
        repo.registrar_verificacao(
            "00000002", status=StatusIntegracao.SEM_ACAO, cliente="OUTRO", data_abertura=hoje
        )

        todos = cliente.get("/fragmentos/oportunidades").text
        assert "ACME" in todos and "OUTRO" in todos

        filtrado = cliente.get("/fragmentos/oportunidades", params={"busca": "ACME"}).text
        assert "ACME" in filtrado
        assert "OUTRO" not in filtrado

    def test_filtro_vazio_nao_mente_dizendo_que_nao_ha_nada(self, cliente: TestClient) -> None:
        assert (
            "Nenhum orçamento encontrado"
            in cliente.get("/fragmentos/oportunidades", params={"busca": "inexistente"}).text
        )

    def test_fora_da_janela_so_aparece_quando_pedido(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        """O corte é o mesmo do worker. Quando a janela encolheu de 6 para 3
        meses, 167 orçamentos antigos continuaram no painel como se o ciclo
        ainda os avaliasse."""
        repo.registrar_verificacao(
            "ANTIGO",
            status=StatusIntegracao.SEM_ACAO,
            cliente="VELHO",
            data_abertura=date(2020, 1, 1),
        )

        assert "VELHO" not in cliente.get("/fragmentos/oportunidades").text
        assert "VELHO" in cliente.get("/fragmentos/oportunidades", params={"tudo": 1}).text


class TestProximoCiclo:
    def test_renderiza_os_numeros_do_retrato(self, cliente: TestClient, tmp_path: Path) -> None:
        texto = cliente.get("/fragmentos/ciclo", params={"arquivo": str(_retrato(tmp_path))}).text
        assert "Na janela" in texto
        assert "O ciclo agiria" in texto
        assert "Criar cotação" in texto

    def test_mostra_a_procedencia_do_retrato(self, cliente: TestClient, tmp_path: Path) -> None:
        """Um retrato de produção aberto achando que é homologação é o erro mais
        caro que esta tela poderia induzir."""
        texto = cliente.get("/fragmentos/ciclo", params={"arquivo": str(_retrato(tmp_path))}).text
        assert "SBOALTAMIRAHOMOLOG" in texto
        assert "01/09/2026" in texto

    def test_sem_retrato_ensina_a_gerar_um(self, cliente: TestClient, tmp_path: Path) -> None:
        """ "Nada a fazer" e "ninguém gerou o retrato" não podem se parecer."""
        texto = cliente.get(
            "/fragmentos/ciclo", params={"arquivo": str(tmp_path / "nao_existe.json")}
        ).text
        assert "--exportar" in texto
        assert "Na janela" not in texto

    def test_retrato_corrompido_diz_o_que_houve(self, cliente: TestClient, tmp_path: Path) -> None:
        ruim = tmp_path / "ruim.json"
        ruim.write_text("{isso não é json", encoding="utf-8")
        resposta = cliente.get("/fragmentos/ciclo", params={"arquivo": str(ruim)})
        assert resposta.status_code == 200
        assert "Não foi possível ler o retrato" in resposta.text

    def test_filtra_por_sitcode(self, cliente: TestClient, tmp_path: Path) -> None:
        texto = cliente.get(
            "/fragmentos/ciclo",
            params={"arquivo": str(_retrato(tmp_path)), "sitcode": "60"},
        ).text
        assert "00000002" in texto
        assert "00000003" not in texto

    def test_dois_graficos_nao_repetem_os_mesmos_dados(
        self, cliente: TestClient, tmp_path: Path
    ) -> None:
        """A macro existe porque `{% with %}` não propaga escopo para dentro de
        um `include`: os dois gráficos saíam com os dados do primeiro."""
        texto = cliente.get("/fragmentos/ciclo", params={"arquivo": str(_retrato(tmp_path))}).text
        assert "Criar cotação" in texto  # gráfico de ações
        assert "Vendida" in texto  # gráfico de status da oportunidade


class TestDetalhe:
    def test_sem_orcamento_pede_um(self, cliente: TestClient) -> None:
        assert "Informe um número de orçamento" in cliente.get("/fragmentos/detalhe").text

    def test_orcamento_inexistente_nao_finge_existir(self, cliente: TestClient) -> None:
        texto = cliente.get("/fragmentos/detalhe", params={"orcnum": "00000009"}).text
        assert "não encontrado" in texto

    def test_mostra_historico(self, cliente: TestClient, repo: RepositorioTracking) -> None:
        repo.registrar_verificacao(
            "00000001", status=StatusIntegracao.COTACAO_CRIADA, cliente="ACME"
        )
        texto = cliente.get("/fragmentos/detalhe", params={"orcnum": "00000001"}).text
        assert "ACME" in texto
        assert "Histórico" in texto

    def test_erro_ganha_selo_vermelho(self, cliente: TestClient, repo: RepositorioTracking) -> None:
        """Um selo cinza sobre um orçamento que falhou faz o erro passar
        despercebido em quem varre a tela."""
        repo.registrar_erro("00000002", "Service Layer recusou.")
        texto = cliente.get("/fragmentos/detalhe", params={"orcnum": "00000002"}).text
        assert 'class="selo critico"' in texto

    def test_regras_emendadas_viram_etiquetas_separadas(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        """A regra é uma lista emendada com "+", e o nome de cada uma é longo.

        Como texto corrido, `cancela_cotacao_no_encerramento+marca_perdida` é
        uma palavra só para o navegador: não quebra, e atravessa a coluna
        vizinha da grade — o valor aparece por cima do rótulo do campo ao lado.
        """
        repo.registrar_verificacao(
            "00000001",
            status=StatusIntegracao.ENCERRADA,
            regra="cancela_cotacao_no_encerramento+marca_perdida",
        )
        texto = cliente.get("/fragmentos/detalhe", params={"orcnum": "00000001"}).text

        assert "cancela_<wbr />cotacao_<wbr />no_<wbr />encerramento" in texto
        assert "marca_<wbr />perdida" in texto
        # O "+" some da tela: era emenda de armazenamento, não de leitura.
        assert "cancela_cotacao_no_encerramento+marca_perdida" not in texto

    def test_regra_unica_tambem_vira_etiqueta(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        repo.registrar_verificacao("00000001", status=StatusIntegracao.SEM_ACAO, regra="sem_acao")
        texto = cliente.get("/fragmentos/detalhe", params={"orcnum": "00000001"}).text
        assert "sem_<wbr />acao" in texto

    def test_sem_regra_nao_mostra_etiqueta_vazia(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        """Uma etiqueta em branco lê como regra sem nome, e não como ausência."""
        repo.registrar_verificacao("00000001", status=StatusIntegracao.SEM_ACAO)
        texto = cliente.get("/fragmentos/detalhe", params={"orcnum": "00000001"}).text
        assert '<span class="regra">' not in texto


class TestFocoDaBusca:
    """A busca troca a **lista**, e não o bloco que contém o campo.

    Com `hx-target="#conteudo"`, cada busca destruía e recriava o próprio
    formulário: o campo perdia o foco e o cursor voltava ao começo, então a
    primeira tecla era a única que entrava. Verificado em navegador — digitando
    "00125607" no código antigo, o valor parava em "0" e `focado` virava
    `false` já na segunda tecla.

    Os testes abaixo fixam a estrutura que sustenta o conserto. Nenhum deles
    enxerga foco: isso é posição na tela, e quem verifica é o navegador.
    """

    def test_a_busca_nao_troca_o_bloco_inteiro(self, cliente: TestClient) -> None:
        texto = cliente.get("/fragmentos/oportunidades").text
        formulario = texto[texto.index('class="filtros"') :][:600]

        assert 'hx-target="#lista-oportunidades"' in formulario
        assert 'hx-target="#conteudo"' not in formulario

    def test_a_lista_tem_alvo_estavel(self, cliente: TestClient) -> None:
        assert 'id="lista-oportunidades"' in cliente.get("/fragmentos/oportunidades").text

    def test_o_alvo_existe_mesmo_sem_resultado(self, cliente: TestClient) -> None:
        """Sem o invólucro, uma busca sem resultado tiraria o alvo da página e a
        tecla seguinte não filtraria mais nada."""
        texto = cliente.get("/fragmentos/oportunidades", params={"busca": "nao-existe-nenhum"}).text

        assert 'id="lista-oportunidades"' in texto
        assert "Nenhum orçamento encontrado" in texto

    def test_a_contagem_acompanha_o_filtro(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        """A contagem viaja por `hx-select-oob` porque mora fora do alvo. Sem
        isso, a lista filtraria e o "N orçamento(s)" ficaria no número antigo —
        um painel que se contradiz na mesma tela."""
        repo.registrar_verificacao("00000001", status=StatusIntegracao.SEM_ACAO)
        repo.registrar_verificacao("00000002", status=StatusIntegracao.SEM_ACAO)

        texto = cliente.get("/fragmentos/oportunidades", params={"busca": "00000001"}).text
        assert 'id="contagem-oportunidades"' in texto
        assert "1 orçamento(s)" in texto

        formulario = texto[texto.index('class="filtros"') :][:600]
        assert 'hx-select-oob="#contagem-oportunidades"' in formulario


class TestReprocessamento:
    def test_registra_quem_pediu(self, cliente: TestClient, repo: RepositorioTracking) -> None:
        """Uma ação manual que termina em documento criado no SAP precisa dizer
        quem pediu: é a pergunta que aparece semanas depois."""
        repo.registrar_verificacao("00000001", status=StatusIntegracao.SEM_ACAO)

        resposta = cliente.post(
            "/fragmentos/reprocessar",
            data={"orcnum": "00000001", "solicitante": "anderson"},
        )
        assert resposta.status_code == 200
        assert "anderson" in resposta.text

        eventos = repo.eventos("00000001")
        assert any("anderson" in e.mensagem for e in eventos)

    def test_sem_solicitante_recusa(self, cliente: TestClient, repo: RepositorioTracking) -> None:
        repo.registrar_verificacao("00000001", status=StatusIntegracao.SEM_ACAO)

        resposta = cliente.post(
            "/fragmentos/reprocessar", data={"orcnum": "00000001", "solicitante": "   "}
        )
        assert "auditável" in resposta.text
        assert repo.eventos("00000001") == []


class TestExecucoes:
    def test_sem_execucao_diz_isso(self, cliente: TestClient) -> None:
        assert "ainda não executou" in cliente.get("/fragmentos/execucoes").text


class TestLog:
    """A aba que faz do painel um monitor: mostra o log do worker."""

    def test_mostra_as_linhas_do_arquivo(self, cliente: TestClient, tmp_path: Path) -> None:
        log = tmp_path / "wbcpython.log"
        log.write_text(
            "2026-09-01 10:00:00 | INFO     | wbcpython.host.worker | ciclo concluído\n",
            encoding="utf-8",
        )
        texto = cliente.get("/fragmentos/log", params={"arquivo": str(log)}).text
        assert "ciclo concluído" in texto
        assert "1 linha(s)" in texto

    def test_erro_no_log_vira_alerta(self, cliente: TestClient, tmp_path: Path) -> None:
        """Num monitor, um ERROR no meio de mil linhas não pode depender de
        alguém ler a tabela inteira."""
        log = tmp_path / "wbcpython.log"
        log.write_text(
            "2026-09-01 10:00:00 | ERROR    | wbcpython.host.worker | estourou\n",
            encoding="utf-8",
        )
        texto = cliente.get("/fragmentos/log", params={"arquivo": str(log)}).text
        assert "1 linha(s) de erro" in texto
        assert 'class="log-linha ERROR"' in texto

    def test_filtra_por_nivel(self, cliente: TestClient, tmp_path: Path) -> None:
        log = tmp_path / "wbcpython.log"
        log.write_text(
            "2026-09-01 10:00:00 | INFO     | w | rotina\n"
            "2026-09-01 10:00:01 | ERROR    | w | estourou\n",
            encoding="utf-8",
        )
        texto = cliente.get("/fragmentos/log", params={"arquivo": str(log), "nivel": "ERROR"}).text
        assert "estourou" in texto
        assert "rotina" not in texto

    def test_sem_log_configurado_explica_como_ligar(self, cliente: TestClient) -> None:
        assert "LOG_FILE" in cliente.get("/fragmentos/log").text

    def test_a_lista_se_repinta_sozinha_e_o_formulario_nao(
        self, cliente: TestClient, tmp_path: Path
    ) -> None:
        """Se o bloco inteiro se repintasse, o campo de filtro seria recriado
        embaixo de quem estivesse digitando."""
        log = tmp_path / "wbcpython.log"
        log.write_text("2026-09-01 10:00:00 | INFO | w | ok\n", encoding="utf-8")
        texto = cliente.get("/fragmentos/log", params={"arquivo": str(log)}).text
        assert 'hx-select="#log-lista"' in texto
        assert 'id="log-filtros"' in texto


class TestRecortes:
    """Os indicadores do topo são os filtros da lista.

    O que estes testes protegem é a **coerência**: o indicador que diz 8 tem de
    abrir uma lista de 8. Um número que não bate com a lista que ele abre é pior
    do que não ter o filtro — quem olha conclui que o painel está mentindo, e
    não tem como saber qual dos dois está errado.
    """

    @pytest.fixture
    def povoado(self, repo: RepositorioTracking) -> RepositorioTracking:
        hoje = date.today()
        casos = [
            ("CRIADA", StatusIntegracao.COTACAO_CRIADA),
            ("ATUALIZADA", StatusIntegracao.COTACAO_ATUALIZADA),
            ("PEDIDO", StatusIntegracao.PEDIDO_CRIADO),
            ("ENCERRADA", StatusIntegracao.ENCERRADA),
            ("PARADA1", StatusIntegracao.SEM_ACAO),
            ("PARADA2", StatusIntegracao.SEM_ACAO),
            ("RUIM", StatusIntegracao.ERRO),
        ]
        for orcnum, status in casos:
            repo.registrar_verificacao(orcnum, status=status, data_abertura=hoje)
        repo.registrar_documento("CRIADA", tipo="cotacao", doc_entry=1, doc_num=101)
        repo.registrar_documento("PEDIDO", tipo="cotacao", doc_entry=2, doc_num=102)
        repo.registrar_documento(
            "PEDIDO", tipo="pedido", doc_entry=3, doc_num=201, valor=Decimal("1000.00")
        )
        return repo

    def _quantos(self, cliente: TestClient, recorte: str) -> int:
        texto = cliente.get("/fragmentos/oportunidades", params={"recorte": recorte}).text
        return texto.count("<tr>") - 1 if "<tbody>" in texto else 0

    @pytest.mark.parametrize(
        ("recorte", "esperado"),
        [
            ("todos", 7),
            ("com_acao", 4),  # criada, atualizada, pedido, encerrada
            ("sem_acao", 2),
            ("encerradas", 1),
            ("com_erro", 1),
            ("com_cotacao", 2),
            ("com_pedido", 1),
        ],
    )
    def test_o_indicador_e_a_lista_dizem_o_mesmo_numero(
        self, cliente: TestClient, povoado: RepositorioTracking, recorte: str, esperado: int
    ) -> None:
        assert self._quantos(cliente, recorte) == esperado

    def test_os_numeros_do_topo_batem_com_os_recortes(
        self, cliente: TestClient, povoado: RepositorioTracking
    ) -> None:
        """A prova de que os dois lados usam a mesma definição: o valor exibido
        no indicador e o tamanho da lista que ele abre."""
        from wbcpython.dashboard.dados import calcular_kpis

        kpis = calcular_kpis(povoado.listar(limite=5000))
        assert self._quantos(cliente, "com_acao") == kpis.com_acao
        assert self._quantos(cliente, "com_erro") == kpis.com_erro
        assert self._quantos(cliente, "sem_acao") == kpis.sem_acao
        assert self._quantos(cliente, "encerradas") == kpis.encerradas
        assert self._quantos(cliente, "com_cotacao") == kpis.com_cotacao
        assert self._quantos(cliente, "com_pedido") == kpis.com_pedido

    def test_com_erro_lista_so_o_que_deu_erro(
        self, cliente: TestClient, povoado: RepositorioTracking
    ) -> None:
        texto = cliente.get("/fragmentos/oportunidades", params={"recorte": "com_erro"}).text
        assert "RUIM" in texto
        assert "PARADA1" not in texto

    def test_o_recorte_ativo_fica_visivel_e_removivel(
        self, cliente: TestClient, povoado: RepositorioTracking
    ) -> None:
        """Uma lista filtrada que não diz que está filtrada é a forma mais fácil
        de alguém concluir que faltam orçamentos no painel."""
        texto = cliente.get("/fragmentos/oportunidades", params={"recorte": "com_erro"}).text
        assert 'class="recorte"' in texto
        assert "Com erro" in texto
        assert "recorte=todos" in texto

    def test_sem_recorte_nao_aparece_etiqueta(
        self, cliente: TestClient, povoado: RepositorioTracking
    ) -> None:
        assert 'class="recorte"' not in cliente.get("/fragmentos/oportunidades").text

    def test_recorte_desconhecido_mostra_tudo_em_vez_de_estourar(
        self, cliente: TestClient, povoado: RepositorioTracking
    ) -> None:
        """O identificador vem do endereço, que pode ter sido colado, editado à
        mão ou guardado num favorito de uma versão anterior."""
        resposta = cliente.get("/fragmentos/oportunidades", params={"recorte": "inventado"})
        assert resposta.status_code == 200
        assert self._quantos(cliente, "inventado") == 7

    def test_recorte_e_busca_se_somam(
        self, cliente: TestClient, povoado: RepositorioTracking
    ) -> None:
        texto = cliente.get(
            "/fragmentos/oportunidades", params={"recorte": "sem_acao", "busca": "PARADA1"}
        ).text
        assert "PARADA1" in texto
        assert "PARADA2" not in texto

    def test_os_indicadores_sao_botoes_com_o_filtro(self, cliente: TestClient) -> None:
        """Botão, e não `div` clicável: precisa ser alcançável pelo teclado e
        anunciado como botão."""
        texto = cliente.get("/fragmentos/kpis").text
        assert "<button" in texto
        assert "recorte=com_erro" in texto
        assert "aria-pressed" in texto

    def test_o_indicador_ativo_vem_marcado(self, cliente: TestClient) -> None:
        """O topo repinta a cada 30 s; sem isso a marca sumiria sozinha pouco
        depois do clique."""
        texto = cliente.get("/fragmentos/kpis", params={"recorte": "com_erro"}).text
        assert 'aria-pressed="true"' in texto

    def test_o_recorte_vive_no_endereco(self, cliente: TestClient) -> None:
        """`/?recorte=com_erro` abre o painel já filtrado, e o endereço pode ser
        colado no chat."""
        texto = cliente.get("/?aba=oportunidades&recorte=com_erro").text
        assert "recorte=com_erro" in texto

    def test_a_janela_continua_valendo_dentro_do_recorte(
        self, cliente: TestClient, repo: RepositorioTracking
    ) -> None:
        """O recorte filtra dentro do que a janela trouxe, não por cima dela."""
        repo.registrar_verificacao(
            "ANTIGO", status=StatusIntegracao.ERRO, data_abertura=date(2020, 1, 1)
        )
        repo.registrar_verificacao("NOVO", status=StatusIntegracao.ERRO, data_abertura=date.today())

        dentro = cliente.get("/fragmentos/oportunidades", params={"recorte": "com_erro"}).text
        assert "NOVO" in dentro
        assert "ANTIGO" not in dentro

        tudo = cliente.get(
            "/fragmentos/oportunidades", params={"recorte": "com_erro", "tudo": 1}
        ).text
        assert "ANTIGO" in tudo
