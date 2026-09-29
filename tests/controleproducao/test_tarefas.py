"""Testes da execução em segundo plano e da confirmação de operação irreversível.

Construídos em 22/09/2026 para a camada web, a partir de três decisões do Anderson:
sem autenticação por enquanto, operação irreversível exige confirmação, operação longa
roda em segundo plano com acompanhamento.
"""
import asyncio
from pathlib import Path as _Path

import pytest

from controleproducao.core.confirmacao import ConfirmacaoInvalida, RegistroDePlanos
from controleproducao.core.tarefas import RegistroDeTarefas

# SIS repo root (tests/controleproducao/<file> -> parents[2]): the tests open package files
# from disk without depending on the directory pytest was launched from.
_RAIZ = _Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Tarefas em segundo plano
# ---------------------------------------------------------------------------
def test_tarefa_guarda_resultado_e_acompanhamento():
    async def cenario():
        registro = RegistroDeTarefas()

        async def trabalho(tarefa):
            tarefa.avanca("lendo o pedido", feitos=1, total=2)
            tarefa.avanca("criando as OPs", feitos=2, total=2)
            return {"criadas": 3}

        tarefa = registro.criar("pedidos_wbc", "processar", "pedido 84348", trabalho)
        while not tarefa.terminada:
            await asyncio.sleep(0)

        return tarefa

    tarefa = asyncio.run(cenario())
    assert tarefa.situacao == "concluída"
    assert tarefa.resultado == {"criadas": 3}
    assert tarefa.para_json()["percentual"] == 100
    assert any("criando as OPs" in linha for linha in tarefa.linhas)


def test_falha_vira_resultado_da_tarefa_em_vez_de_derrubar():
    """Uma tarefa que estoura não pode levar o servidor junto; o erro é o resultado dela."""
    async def cenario():
        registro = RegistroDeTarefas()

        async def trabalho(_tarefa):
            raise RuntimeError("custo do item não encontrado")

        tarefa = registro.criar("manutencao_op", "encerrar", "OP 156209", trabalho)
        while not tarefa.terminada:
            await asyncio.sleep(0)
        return tarefa

    tarefa = asyncio.run(cenario())
    assert tarefa.situacao == "erro"
    assert "custo do item não encontrado" in tarefa.erro


def test_dois_processamentos_do_mesmo_modulo_nao_rodam_juntos():
    """Duas execuções simultâneas no mesmo módulo disputariam os mesmos pedidos e OPs —
    é assim que nasce OP duplicada."""
    async def cenario():
        registro = RegistroDeTarefas()
        liberar = asyncio.Event()

        async def trabalho(_tarefa):
            await liberar.wait()

        registro.criar("pedidos_wbc", "processar", "primeiro", trabalho)
        with pytest.raises(RuntimeError) as erro:
            registro.criar("pedidos_wbc", "processar", "segundo", trabalho)
        liberar.set()
        return str(erro.value)

    assert "já tem uma execução em andamento" in asyncio.run(cenario())


def test_modulos_diferentes_rodam_em_paralelo():
    """Serializar tudo faria o usuário esperar sem motivo: os módulos são independentes."""
    async def cenario():
        registro = RegistroDeTarefas()
        liberar = asyncio.Event()

        async def trabalho(_tarefa):
            await liberar.wait()

        registro.criar("pedidos_wbc", "processar", "a", trabalho)
        registro.criar("manutencao_op", "encerrar", "b", trabalho)  # não pode levantar
        liberar.set()

    asyncio.run(cenario())


def test_modulo_libera_quando_a_tarefa_termina():
    async def cenario():
        registro = RegistroDeTarefas()

        async def trabalho(_tarefa):
            return None

        tarefa = registro.criar("pedidos_wbc", "processar", "a", trabalho)
        while not tarefa.terminada:
            await asyncio.sleep(0)
        assert registro.em_execucao("pedidos_wbc") is None
        registro.criar("pedidos_wbc", "processar", "b", trabalho)  # não pode levantar

    asyncio.run(cenario())


def test_historico_nao_cresce_para_sempre():
    """Sem limite, um processo de longa duração acumularia resultados indefinidamente."""
    from controleproducao.core import tarefas as mod

    async def cenario():
        registro = RegistroDeTarefas()

        async def trabalho(_tarefa):
            return None

        for i in range(mod.MAX_TAREFAS + 10):
            t = registro.criar(f"modulo{i}", "x", "y", trabalho)
            while not t.terminada:
                await asyncio.sleep(0)
        return registro

    registro = asyncio.run(cenario())
    assert len(registro.listar(limite=10_000)) <= mod.MAX_TAREFAS


# ---------------------------------------------------------------------------
# Confirmação de operação irreversível
# ---------------------------------------------------------------------------
def test_token_so_vale_uma_vez():
    """Um POST pode ser repetido por F5, duplo clique ou retry do navegador — coisa que
    na CLI não acontece. O token de uso único é o que impede a reexecução."""
    planos = RegistroDePlanos()
    plano = planos.criar("encerrar", {"ops": 1}, [{"doc_num": 156209}])

    planos.consumir(plano.token)
    with pytest.raises(ConfirmacaoInvalida):
        planos.consumir(plano.token)


def test_token_inexistente_e_recusado():
    with pytest.raises(ConfirmacaoInvalida):
        RegistroDePlanos().consumir("inventado")


def test_token_vencido_e_recusado():
    """O plano foi calculado sobre o estado do SAP de então; estado velho é o que faz
    alguém cancelar a OP errada."""
    from datetime import datetime, timedelta

    from controleproducao.core import confirmacao as mod

    planos = RegistroDePlanos()
    plano = planos.criar("encerrar", {}, [])
    plano.criado_em = datetime.now() - mod.VALIDADE - timedelta(seconds=1)

    with pytest.raises(ConfirmacaoInvalida) as erro:
        planos.consumir(plano.token)
    assert "venceu" in str(erro.value)


def test_plano_nao_pede_mais_texto_de_producao():
    """A digitação da company DB saiu em 22/09/2026 junto com a trava de produção.

    O que sobrou — plano calculado, mostrado e confirmado com token de uso único — nunca
    foi a trava: vale igual em homologação, porque o motivo é a operação ser irreversível,
    não o ambiente ser arriscado.
    """
    planos = RegistroDePlanos()
    plano = planos.criar("encerrar", {}, [])

    assert not hasattr(plano, "exige_texto")
    assert "exige_texto" not in plano.para_json()
    # Um único argumento: o token. Sem segundo fator, sem digitação.
    assert planos.consumir(plano.token).operacao == "encerrar"


def test_o_plano_confirmado_e_o_que_foi_mostrado():
    """O ponto do mecanismo: o usuário confirma sobre o que foi calculado e exibido, não
    sobre o que ele imagina que vai acontecer."""
    planos = RegistroDePlanos()
    itens = [{"doc_num": 156209, "item": "ESTFEC"}, {"doc_num": 156210, "item": "ESTCAN"}]
    plano = planos.criar("encerrar", {"total": 2}, itens)

    confirmado = planos.consumir(plano.token)
    assert confirmado.itens == itens


# ---------------------------------------------------------------------------
# Ponte de log -> acompanhamento (22/09/2026)
# ---------------------------------------------------------------------------
def test_ponte_de_log_leva_o_progresso_do_servico_para_a_tarefa():
    """Os serviços já narram o que fazem com `logger.info` ("OP criada: DocEntry=…").

    A web ouve essa narrativa em vez de manter uma segunda — duas descrições do mesmo
    processo divergem assim que alguém mexer numa delas.
    """
    import logging
    from datetime import datetime

    from controleproducao.core.tarefas import Tarefa, acompanha_log

    tarefa = Tarefa(id="t", nome="n", descricao="d", criada_em=datetime.now())
    logger = logging.getLogger("teste.ponte")

    logger.info("antes da ponte")
    with acompanha_log(tarefa, "teste.ponte"):
        logger.info("  OP criada: DocEntry=%s, item %s, %d linha(s).", 156292, "PAR000", 3)
        logger.debug("detalhe abaixo do nível")
    logger.info("depois da ponte")

    texto = "\n".join(tarefa.linhas)
    assert "OP criada: DocEntry=156292" in texto
    assert "antes da ponte" not in texto
    assert "depois da ponte" not in texto
    assert "detalhe abaixo" not in texto


def test_ponte_devolve_o_logger_ao_estado_anterior():
    """Mexer no nível do logger do módulo afetaria o processo inteiro, inclusive outra
    tarefa rodando em paralelo em outro módulo."""
    import logging
    from datetime import datetime

    from controleproducao.core.tarefas import Tarefa, acompanha_log

    tarefa = Tarefa(id="t", nome="n", descricao="d", criada_em=datetime.now())
    logger = logging.getLogger("teste.restaura")
    logger.setLevel(logging.WARNING)
    quantos = len(logger.handlers)

    with acompanha_log(tarefa, "teste.restaura"):
        assert logger.level == logging.INFO
    assert logger.level == logging.WARNING
    assert len(logger.handlers) == quantos


# ---------------------------------------------------------------------------
# Desfecho na tela (22/09/2026)
# ---------------------------------------------------------------------------
def test_painel_de_erro_fica_escondido_quando_nao_houve_erro():
    """`[hidden]` perde para qualquer classe do autor que defina `display`.

    `.ov-alerta{display:flex}` reacendia o painel de erro VAZIO: uma barra vermelha em
    cima de uma execução bem-sucedida, dizendo "deu ruim" quando não tinha dado. A folha
    precisa ter a regra que devolve a vitória ao atributo.
    """
    css = open(_RAIZ / "controleproducao/static/style.css", encoding="utf-8").read()
    assert "[hidden] { display: none !important; }" in css


def test_barra_carrega_o_desfecho_e_nao_so_o_andamento():
    """Coral no fim não diz nada: a barra é o elemento mais visível da tela.

    Verde = concluída limpa, âmbar = concluída com falhas, vermelho = erro ou cancelada.
    Cores semânticas — não um segundo acento (§4.3 do guia de estilo).
    """
    css = open(_RAIZ / "controleproducao/static/style.css", encoding="utf-8").read()
    for classe, token in [("is-ok", "--color-success"), ("is-warn", "--color-warning"),
                          ("is-erro", "--color-danger")]:
        assert f".ov-barra.{classe}   > i {{ --ov-barra-cor: var({token}); }}" in css \
            or f".ov-barra.{classe} > i {{ --ov-barra-cor: var({token}); }}" in css, classe

    # A pílula e a barra leem a MESMA decisão: duas leituras independentes do mesmo
    # estado divergiriam na primeira vez que alguém mexesse numa delas. Since 30/09/2026 the
    # decision is `Tarefa.desfecho` (server) and the class maps are the templates' globals.
    html = open(_RAIZ / "controleproducao/templates/tarefa.html", encoding="utf-8").read()
    assert "const fim = d.desfecho;" in html
    assert "CLASSE_DA_PILULA|tojson" in html and "CLASSE_DA_BARRA|tojson" in html
    assert "CLASSE_PILULA[fim]" in html and "CLASSE_BARRA[fim]" in html


@pytest.mark.parametrize("situacao,resultado,esperado", [
    ("concluída", {"com_erro": []}, "ok"),
    ("concluída", {"com_erro": [1]}, "falhas"),
    ("cancelada", None, "cancelada"),
    ("erro", {"com_erro": [1]}, "erro"),
    ("executando", None, "rodando"),
    ("na fila", None, "fila"),
])
def test_um_so_desfecho_para_lista_detalhe_e_barra(situacao, resultado, esperado):
    """29/09/2026: the list painted a cancelled run GREEN while the detail showed it amber."""
    from datetime import datetime

    from controleproducao.core.tarefas import CLASSE_DA_BARRA, CLASSE_DA_PILULA, Tarefa

    tarefa = Tarefa(id="t", nome="n", descricao="d", criada_em=datetime.now(),
                    situacao=situacao, resultado=resultado)
    assert tarefa.desfecho == esperado == tarefa.para_json()["desfecho"]
    assert esperado in CLASSE_DA_PILULA and esperado in CLASSE_DA_BARRA
    assert CLASSE_DA_PILULA["cancelada"] != "is-ok"

    lista = open(_RAIZ / "controleproducao/templates/tarefas.html", encoding="utf-8").read()
    assert "CLASSE_DA_PILULA[t.desfecho]" in lista


def test_origem_fora_da_lista_e_recusada_antes_de_criar():
    """The history table CHECKs ``origem in ('tela', 'api')``: a typo here would lose the row."""
    registro = RegistroDeTarefas()

    async def trabalho(_tarefa):
        return None

    with pytest.raises(ValueError, match="origem"):
        registro.criar("manutencao_op", "Liberar OPs", "9001", trabalho, origem="API")
    assert registro.listar() == []


def test_quem_pediu_vai_para_o_json_do_acompanhamento():
    async def cenario():
        registro = RegistroDeTarefas()

        async def trabalho(_tarefa):
            return None

        tarefa = registro.criar("manutencao_op", "Liberar OPs", "9001", trabalho,
                                solicitante="joao", origem="api")
        while not tarefa.terminada:
            await asyncio.sleep(0)
        return tarefa

    tarefa = asyncio.run(cenario())
    assert (tarefa.para_json()["solicitante"], tarefa.para_json()["origem"]) == ("joao", "api")


def test_parada_combinada_nao_corta_a_execucao_e_termina_cancelada():
    """D5 (29/09/2026): a task created with `parada_combinada` is asked to stop, not cut —
    its body decides where (the closing of OPs: before the next OP) and keeps its result."""
    async def cenario():
        registro = RegistroDeTarefas()
        passos = []

        async def trabalho(tarefa):
            for passo in range(1, 500):
                if tarefa.parada_pedida:
                    break
                passos.append(passo)
                await asyncio.sleep(0.001)
            return {"feitos": list(passos), "com_erro": []}

        tarefa = registro.criar("manutencao_op", "Encerrar OPs", "3 OP(s)", trabalho,
                                parada_combinada=True)
        await asyncio.sleep(0.005)
        pediu = await registro.cancelar(tarefa.id)
        de_novo = await registro.cancelar(tarefa.id)          # asking twice is harmless
        while not tarefa.terminada:
            await asyncio.sleep(0.001)
        return tarefa, pediu, de_novo

    tarefa, pediu, de_novo = asyncio.run(cenario())
    assert pediu and de_novo
    assert tarefa.situacao == "cancelada" and tarefa.desfecho == "cancelada"
    assert tarefa.resultado["feitos"]                          # the result survived the stop
    assert sum("Interrupção pedida" in linha for linha in tarefa.linhas) == 1
    assert tarefa.para_json()["parada_pedida"] is True


def test_sem_parada_combinada_o_cancelar_corta_como_antes():
    async def cenario():
        registro = RegistroDeTarefas()

        async def trabalho(_tarefa):
            await asyncio.sleep(10)

        tarefa = registro.criar("manutencao_op", "Liberar OPs", "9001", trabalho)
        await asyncio.sleep(0)
        await registro.cancelar(tarefa.id)
        while not tarefa.terminada:
            await asyncio.sleep(0.001)
        return tarefa

    tarefa = asyncio.run(cenario())
    assert tarefa.situacao == "cancelada" and tarefa.resultado is None
