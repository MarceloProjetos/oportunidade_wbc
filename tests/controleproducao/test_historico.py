"""Execuções history in Supabase (29/09/2026): write + prune to 30, read, and the screen.

The Supabase client is replaced by `_BancoFalso`, an in-memory table that understands the
exact PostgREST builder chain `core/historico.py` uses. The real client is blocked by the
root conftest, so a test that forgot the fake fails instead of reaching production.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from controleproducao.core import historico as mod
from controleproducao.core.historico import ExecucaoGuardada, HistoricoSupabase, registro_de
from controleproducao.core.tarefas import MAX_NA_TELA, TAREFAS, RegistroDeTarefas, Tarefa

_RAIZ = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# In-memory stand-in for the PostgREST table
# ---------------------------------------------------------------------------
class _Resposta:
    def __init__(self, data):
        self.data = data


class _Consulta:
    def __init__(self, banco: _BancoFalso) -> None:
        self.banco = banco
        self.op: tuple = ()
        self.colunas = "*"
        self.filtros: list = []
        self.ordem: tuple[str, bool] | None = None
        self.faixa: tuple[int, int] | None = None
        self.limite: int | None = None

    def upsert(self, registro, on_conflict):
        self.op = ("upsert", registro, on_conflict)
        return self

    def select(self, colunas):
        self.op, self.colunas = ("select",), colunas
        return self

    def delete(self):
        self.op = ("delete",)
        return self

    def order(self, coluna, desc=False):
        self.ordem = (coluna, desc)
        return self

    def range(self, inicio, fim):
        self.faixa = (inicio, fim)
        return self

    def limit(self, n):
        self.limite = n
        return self

    def eq(self, coluna, valor):
        self.filtros.append(lambda r: r.get(coluna) == valor)
        return self

    def in_(self, coluna, valores):
        self.filtros.append(lambda r: r.get(coluna) in set(valores))
        return self

    def execute(self):
        b = self.banco
        tipo = self.op[0]
        b.chamadas.append(tipo)
        if b.falhas.get(tipo, 0) > 0:
            b.falhas[tipo] -= 1
            raise RuntimeError(f"supabase fora ({tipo})")
        linhas = [r for r in b.linhas if all(f(r) for f in self.filtros)]
        if tipo == "upsert":
            _, registro, chave = self.op
            existente = next((r for r in b.linhas if r[chave] == registro[chave]), None)
            if existente:
                existente.update(registro)
            else:
                b.proximo_id += 1
                b.linhas.append({"id": b.proximo_id, **registro})
            return _Resposta([registro])
        if tipo == "delete":
            b.linhas = [r for r in b.linhas if r not in linhas]
            return _Resposta(linhas)
        if self.ordem:
            coluna, desc = self.ordem
            linhas.sort(key=lambda r: r[coluna], reverse=desc)
        if self.faixa:
            linhas = linhas[self.faixa[0]:self.faixa[1] + 1]
        if self.limite is not None:
            linhas = linhas[:self.limite]
        if self.colunas != "*":
            nomes = self.colunas.split(",")
            linhas = [{k: r[k] for k in nomes if k in r} for r in linhas]
        return _Resposta([dict(r) for r in linhas])


class _BancoFalso:
    def __init__(self) -> None:
        self.linhas: list[dict] = []
        self.proximo_id = 0
        self.chamadas: list[str] = []
        self.falhas: dict[str, int] = {}
        self.tabelas: list[str] = []

    def table(self, nome):
        self.tabelas.append(nome)
        return _Consulta(self)


def _historico(banco: _BancoFalso | None = None) -> tuple[HistoricoSupabase, _BancoFalso]:
    banco = banco or _BancoFalso()
    hist = HistoricoSupabase("http://supabase.invalido", "chave-falsa", pausas_s=(0.0, 0.0))
    hist._cliente = banco
    return hist, banco


_ID = iter(range(1, 10_000))


def _terminada(nome="Processar novos", situacao="concluída", resultado=None, minutos_atras=0) -> Tarefa:
    inicio = datetime.now().replace(microsecond=0) - timedelta(minutes=minutos_atras)
    tarefa = Tarefa(
        id=f"{next(_ID):012x}", nome=nome, descricao="pedido 84433", criada_em=inicio,
        modulo="pedidos_wbc", situacao=situacao, iniciada_em=inicio,
        terminada_em=inicio + timedelta(seconds=7), passo="fim", passos_feitos=2, passos_total=2,
        resultado=resultado,
    )
    tarefa.linhas.extend(["10:00:00  lendo o pedido", "10:00:07  OP criada: DocEntry=156292"])
    return tarefa


@pytest.fixture(autouse=True)
def _registro_limpo():
    def _zera():
        for job in list(TAREFAS._jobs.values()):
            job.cancel()
        TAREFAS._jobs.clear()
        TAREFAS._tarefas.clear()
        TAREFAS._ordem.clear()
        TAREFAS._em_execucao.clear()

    _zera()
    yield
    _zera()


# ---------------------------------------------------------------------------
# Write and prune
# ---------------------------------------------------------------------------
def test_guarda_a_execucao_com_o_log_e_o_resultado():
    hist, banco = _historico()
    resultado = {"criadas": 3, "custo": Decimal("1.50"), "em": datetime(2026, 9, 29, 10, 0)}
    tarefa = _terminada(resultado=resultado)

    assert hist.guardar(tarefa) is True

    assert banco.tabelas and set(banco.tabelas) == {"controle_producao_execucoes"}
    linha = banco.linhas[0]
    assert linha["tarefa_id"] == tarefa.id
    assert linha["modulo"] == "pedidos_wbc"
    assert linha["situacao"] == "concluída"
    assert linha["com_falhas"] is False
    assert linha["linhas"] == list(tarefa.linhas)
    assert linha["duracao_segundos"] == 7.0
    # Decimal / datetime would break the JSON body of the request: stored as text.
    assert linha["resultado"] == {"criadas": 3, "custo": "1.50", "em": "2026-09-29 10:00:00"}
    # timestamptz needs the offset: a naive local time would be read as UTC (3 h off).
    assert re.search(r"[+-]\d\d:\d\d$", linha["criada_em"])


def test_a_31a_gravacao_apaga_a_mais_antiga():
    hist, banco = _historico()
    tarefas = [_terminada(minutos_atras=100 - i) for i in range(MAX_NA_TELA + 1)]
    for tarefa in tarefas:
        assert hist.guardar(tarefa)

    guardadas = {r["tarefa_id"] for r in banco.linhas}
    assert len(guardadas) == MAX_NA_TELA == 30
    assert tarefas[0].id not in guardadas          # the oldest went
    assert tarefas[-1].id in guardadas and tarefas[1].id in guardadas


def test_supabase_fora_nao_levanta_e_avisa_no_log(caplog):
    """The SAP writes already happened: a history outage cannot turn them into a failure."""
    hist, banco = _historico()
    banco.falhas["upsert"] = 99

    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        assert hist.guardar(_terminada()) is False

    assert banco.chamadas.count("upsert") == 3      # 3 attempts, then gives up
    assert banco.linhas == []
    assert "não foi guardada no Supabase após 3 tentativa(s)" in caplog.text


def test_nova_tentativa_nao_duplica_a_linha():
    """Upsert on tarefa_id: a retry after a timeout that did reach the server stays one row."""
    hist, banco = _historico()
    tarefa = _terminada()
    banco.falhas["upsert"] = 1
    assert hist.guardar(tarefa) is True
    assert hist.guardar(tarefa) is True             # same execution again (defensive)
    assert [r["tarefa_id"] for r in banco.linhas] == [tarefa.id]


def test_poda_que_falha_nao_desfaz_a_gravacao(caplog):
    hist, banco = _historico()
    banco.falhas["select"] = 1
    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        assert hist.guardar(_terminada()) is True
    assert len(banco.linhas) == 1
    assert "a poda de 'controle_producao_execucoes' falhou" in caplog.text


def test_execucao_em_andamento_nao_e_guardada():
    hist, banco = _historico()
    tarefa = _terminada(situacao="executando")
    assert hist.guardar(tarefa) is False
    assert banco.chamadas == []


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def test_lista_so_o_resumo_da_mais_recente_para_a_mais_antiga():
    hist, _ = _historico()
    velha = _terminada(nome="Encerrar OPs", minutos_atras=30)
    nova = _terminada(nome="Processar novos", resultado={"com_erro": [{"pedido": 84433}]})
    for tarefa in (nova, velha):
        hist.guardar(tarefa)

    lista = hist.listar()

    assert [g.id for g in lista] == [nova.id, velha.id]
    primeira = lista[0]
    assert isinstance(primeira, ExecucaoGuardada)
    assert primeira.com_falhas is True and primeira.terminada and primeira.guardada
    assert primeira.criada_em == nova.criada_em   # back to naive local time
    assert primeira.duracao_segundos == 7.0
    assert not hasattr(primeira, "linhas")         # the list does not pull the logs


def test_obter_devolve_a_execucao_inteira_so_para_leitura():
    hist, _ = _historico()
    original = _terminada(situacao="erro", resultado={"com_erro": [1]})
    original.erro = "custo do item não encontrado"
    hist.guardar(original)

    tarefa = hist.obter(original.id)

    assert tarefa is not None and tarefa.guardada and tarefa.terminada
    assert tarefa.situacao == "erro" and tarefa.erro == original.erro
    assert list(tarefa.linhas) == list(original.linhas)
    assert tarefa.duracao_segundos == original.duracao_segundos
    assert tarefa.para_json()["percentual"] == 100
    assert tarefa.com_falhas is True


def test_id_fora_do_formato_nao_vai_ao_supabase():
    hist, banco = _historico()
    assert hist.obter("../../etc") is None
    assert hist.obter("") is None
    assert banco.chamadas == []


def test_leitura_com_supabase_fora_levanta_para_quem_chamou():
    """Reads fail loud: "could not look" must not come back as an empty list."""
    hist, banco = _historico()
    banco.falhas["select"] = 1
    with pytest.raises(RuntimeError):
        hist.listar()


# ---------------------------------------------------------------------------
# Which machine keeps a history
# ---------------------------------------------------------------------------
def test_fora_da_11_nao_ha_historico(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "falsa")
    assert mod.da_maquina() is None


def test_na_11_sem_chave_fica_so_na_memoria(como_a_11, caplog):
    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        assert mod.da_maquina() is None
    assert "SUPABASE_SERVICE_ROLE_KEY" in caplog.text


def test_na_11_com_chave_liga_sem_conectar_na_subida(como_a_11, monkeypatch):
    """Lazy client: the service must come up even with Supabase down."""
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "falsa")
    hist = mod.da_maquina()
    assert isinstance(hist, HistoricoSupabase)
    assert hist._cliente is None and hist.maximo == 30


# ---------------------------------------------------------------------------
# Registry: when the write happens and what it must not hold up
# ---------------------------------------------------------------------------
class _HistoricoDeTeste:
    def __init__(self, bloqueio: threading.Event | None = None) -> None:
        self.guardadas: list[Tarefa] = []
        self.bloqueio = bloqueio

    def guardar(self, tarefa):
        if self.bloqueio:
            self.bloqueio.wait(5)
        self.guardadas.append(tarefa)
        return True


def test_execucao_terminada_vai_para_o_historico_com_o_modulo():
    async def cenario():
        hist = _HistoricoDeTeste()
        registro = RegistroDeTarefas(historico=hist)

        async def trabalho(tarefa):
            tarefa.avanca("criando as OPs", feitos=1, total=1)
            return {"criadas": 1}

        tarefa = registro.criar("manutencao_op", "Encerrar OPs", "pedido 84433", trabalho)
        while not tarefa.terminada:
            await asyncio.sleep(0)
        await registro.aguardar_gravacoes()
        return hist, tarefa

    hist, tarefa = asyncio.run(cenario())
    assert hist.guardadas == [tarefa]
    assert tarefa.modulo == "manutencao_op" and tarefa.situacao == "concluída"


def test_gravacao_lenta_nao_segura_o_modulo():
    """The module lock is released when the task ends, not when Supabase answers."""
    async def cenario():
        liberar = threading.Event()
        hist = _HistoricoDeTeste(bloqueio=liberar)
        registro = RegistroDeTarefas(historico=hist)

        async def trabalho(_tarefa):
            return None

        tarefa = registro.criar("pedidos_wbc", "processar", "a", trabalho)
        while not tarefa.terminada:
            await asyncio.sleep(0)
        livre = registro.em_execucao("pedidos_wbc") is None
        ainda_gravando = hist.guardadas == []
        liberar.set()
        await registro.aguardar_gravacoes()
        return livre, ainda_gravando, hist

    livre, ainda_gravando, hist = asyncio.run(cenario())
    assert livre and ainda_gravando
    assert len(hist.guardadas) == 1


def test_lista_da_tela_junta_memoria_e_historico_sem_repetir():
    hist, _ = _historico()
    antiga = _terminada(nome="Encerrar OPs", minutos_atras=60)
    hist.guardar(antiga)

    async def cenario():
        registro = RegistroDeTarefas(historico=hist)
        parar = asyncio.Event()

        async def rapido(_tarefa):
            return None

        async def demorado(_tarefa):
            await parar.wait()

        feita = registro.criar("pedidos_wbc", "Processar novos", "b", rapido)
        while not feita.terminada:
            await asyncio.sleep(0)
        await registro.aguardar_gravacoes()          # now in memory AND in the history
        rodando = registro.criar("manutencao_op", "Liberar OPs", "c", demorado)
        await asyncio.sleep(0)
        itens, aviso = await registro.recentes()
        parar.set()
        return itens, aviso, feita, rodando

    itens, aviso, feita, rodando = asyncio.run(cenario())
    assert aviso is None
    assert [i.id for i in itens] == [rodando.id, feita.id, antiga.id]


def test_lista_da_tela_avisa_quando_o_historico_nao_responde():
    hist, banco = _historico()
    banco.falhas["select"] = 99   # the finished task's own prune also selects

    async def cenario():
        registro = RegistroDeTarefas(historico=hist)

        async def rapido(_tarefa):
            return None

        feita = registro.criar("pedidos_wbc", "Processar novos", "b", rapido)
        while not feita.terminada:
            await asyncio.sleep(0)
        return await registro.recentes(), feita

    (itens, aviso), feita = asyncio.run(cenario())
    assert [i.id for i in itens] == [feita.id]
    assert aviso and "só as execuções desde o último reinício" in aviso


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------
@pytest.fixture
def cliente():
    from controleproducao.main import app

    return TestClient(app)


def test_tela_lista_as_guardadas_depois_de_um_reinicio(cliente):
    hist, _ = _historico()
    guardada = _terminada(nome="Encerrar OPs", resultado={"com_erro": [1]})
    hist.guardar(guardada)
    TAREFAS.historico = hist                        # a fresh process: nothing in memory

    html = cliente.get("/tarefas").text

    assert "As 30 execuções mais recentes, guardadas no Supabase" in html
    assert "Encerrar OPs" in html and f"/tarefas/{guardada.id}" in html
    assert "concluída com falhas" in html


def test_tela_sem_historico_explica_que_e_so_memoria(cliente):
    html = cliente.get("/tarefas").text
    assert "Histórico em memória do processo" in html
    assert "Nenhuma execução registrada neste processo." in html


def test_tela_avisa_quando_o_supabase_nao_responde(cliente):
    hist, banco = _historico()
    banco.falhas["select"] = 1
    TAREFAS.historico = hist

    resposta = cliente.get("/tarefas")

    assert resposta.status_code == 200
    assert "Não foi possível ler o histórico guardado no Supabase agora" in resposta.text


def test_detalhe_de_execucao_guardada_abre_so_para_leitura(cliente):
    hist, _ = _historico()
    guardada = _terminada()
    hist.guardar(guardada)
    TAREFAS.historico = hist

    html = cliente.get(f"/tarefas/{guardada.id}").text
    estado = cliente.get(f"/tarefas/{guardada.id}/estado").json()

    assert "guardada no Supabase (só leitura)" in html
    assert re.search(r'<div id="acoes" class="ov-acoes-lote" hidden>', html)
    assert "OP criada: DocEntry=156292" in html
    assert estado["terminada"] is True and estado["linhas"] == list(guardada.linhas)


def test_detalhe_inexistente_mostra_a_pagina_de_erro_e_nao_json(cliente):
    resposta = cliente.get("/tarefas/0123456789ab")
    assert resposta.status_code == 404
    assert "text/html" in resposta.headers["content-type"]
    assert "Execução não encontrada" in resposta.text
    assert 'class="item ativo" href="/tarefas"' in resposta.text   # menu still on Execuções
    assert cliente.get("/tarefas/0123456789ab/estado").status_code == 404


def test_detalhe_com_supabase_fora_e_503_e_nao_404(cliente):
    hist, banco = _historico()
    banco.falhas["select"] = 2
    TAREFAS.historico = hist

    pagina = cliente.get("/tarefas/0123456789ab")
    estado = cliente.get("/tarefas/0123456789ab/estado")

    assert pagina.status_code == 503 and "Histórico indisponível" in pagina.text
    assert estado.status_code == 503


def test_health_diz_onde_fica_o_historico(cliente):
    assert cliente.get("/health").json()["historico"] == "memoria"
    TAREFAS.historico, _ = _historico()
    assert cliente.get("/health").json()["historico"] == "supabase"


def test_subida_do_app_decide_o_historico_pela_maquina():
    """Off the .11 the startup leaves no history attached (memory only)."""
    from controleproducao.main import app

    TAREFAS.historico = object()
    with TestClient(app):
        assert TAREFAS.historico is None


# ---------------------------------------------------------------------------
# DDL x code
# ---------------------------------------------------------------------------
def test_o_sql_tem_toda_coluna_que_o_codigo_grava():
    """A column the code sends but the table lacks fails the whole insert (PGRST204)."""
    ddl = (_RAIZ / "sql" / "controle_producao_execucoes.sql").read_text(encoding="utf-8")
    corpo = ddl.split("create table if not exists public.controle_producao_execucoes (")[1].split(");")[0]
    colunas = {linha.split()[0] for linha in corpo.splitlines() if re.match(r"^\s+[a-z_]+\s", linha)}

    enviadas = set(registro_de(_terminada()))
    assert enviadas <= colunas, enviadas - colunas
    assert set(mod._COLUNAS_DA_LISTA.split(",")) <= colunas


def test_o_sql_fecha_a_tabela_para_a_anon():
    ddl = (_RAIZ / "sql" / "controle_producao_execucoes.sql").read_text(encoding="utf-8")
    codigo = "\n".join(linha for linha in ddl.splitlines() if not linha.lstrip().startswith("--"))
    assert "enable row level security" in codigo and "force  row level security" in codigo
    assert "create policy" not in codigo
    assert "tarefa_id         text        not null unique" in codigo
    # The CHECK lists exactly the finished states of a task.
    for situacao in ("concluída", "erro", "cancelada"):
        assert f"'{situacao}'" in codigo
        assert Tarefa(id="x", nome="n", descricao="d", criada_em=datetime.now(), situacao=situacao).terminada


def test_hora_volta_igual_quando_o_supabase_devolve_em_utc():
    """PostgREST answers timestamptz in UTC (+00:00); the fake above echoed the local offset,
    so the UTC → local path was never exercised (30/09/2026)."""

    local = datetime(2026, 9, 29, 15, 36, 5)
    como_o_supabase_devolve = datetime.fromisoformat(mod._para_iso(local)).astimezone(UTC).isoformat()
    assert como_o_supabase_devolve.endswith("+00:00")
    assert mod._de_iso(como_o_supabase_devolve) == local


def test_historico_e_procurado_de_novo_se_a_11_ainda_nao_tinha_o_ip(monkeypatch):
    """The service starts at boot; the IP may not be bound yet. Re-asked, not frozen."""
    from controleproducao.core import tarefas as mod_tarefas

    respostas = [None, "historico-da-11"]
    registro = RegistroDeTarefas()
    relogio = [1000.0]
    monkeypatch.setattr(mod_tarefas.time, "monotonic", lambda: relogio[0])

    registro.usar_historico(lambda: respostas.pop(0))
    assert registro.historico is None                         # at startup: not the .11 yet
    relogio[0] += mod_tarefas.REVER_HISTORICO_S - 1
    assert registro.historico is None and len(respostas) == 1  # not asked again too soon
    relogio[0] += 2
    assert registro.historico == "historico-da-11"            # found, and kept
    registro.historico = None                                 # explicit value stops re-checks
    relogio[0] += 10 * mod_tarefas.REVER_HISTORICO_S
    assert registro.historico is None
