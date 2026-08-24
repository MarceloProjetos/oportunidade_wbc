"""O freio da D1: o nucleo portado nao pode divergir do original do V117.

O plano (``docs/PLANO_SITUACAO_PEDIDOS_MCP.md``) escolheu **portar** o modulo puro em vez
de reimplementar ou chamar o `.90`. Sem este teste, essa decisao vira "reimplementar" por
omissao em tres meses: alguem corrige um caso no V117, ninguem lembra da copia, e a .11
passa a responder diferente da tela **sem erro nenhum aparecer**.

Como funciona: compara o CODIGO-FONTE, funcao por funcao, com
``web_orcaview_V117/backend/services/situacao_pedidos_service.py``. Roda so onde os dois
repos estao lado a lado (maquina de dev); na .11, que so tem este repo, faz ``skip``.

Se falhar, a pergunta certa **nao** e' "como calo o teste?" e sim "qual dos dois lados
mudou, e o outro precisa da mesma mudanca?".
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import re
import sys

import pytest

import sap_montagem_labels
import situacao_pedidos

#: Funcoes que TEM de ser identicas nos dois lados. Sao as puras -- as que decidem o que
#: a tela e o MCP respondem.
FUNCOES_NUCLEO = (
    "_flag",
    "_status",
    "prazo_fim",
    "_dias_atraso",
    "_dias_desde",
    "_montagem",
    "_pedido",
    "normalizar",
    "montar_dashboard",
    "montadores_do_recorte",
    "filtrar",
)

#: Constantes que valem contrato (mudar uma delas de um lado so muda a resposta).
CONSTANTES_NUCLEO = (
    "KPI_FILTROS",
    "STATUS_CHIPS",
    "MONTADOR_SEM",
    "FIN_LIBERACAO_LIMITE_DIAS",
    "_MAX_LINHAS",
)

#: FORA da comparacao, de proposito -- e cada um por um motivo diferente:
#:
#: - ``fetch_pedidos``/``limpar_cache``/``_fetch_sync``: I/O e cache, fase F2, cliente
#:   HANA diferente dos dois lados;
#: - ``ValidationError``/``now_br``: as duas dependencias que o porte teve de trocar
#:   (no V117 vem de ``exceptions`` e ``utils``);
#: - ``alerta_liberacao``/``com_alerta``/``filtrar_bloqueio``/``resumir``/
#:   ``BLOQUEIO_FILTROS``/``CAMPOS_RESUMO``: exclusivos da .11, nao existem no V117.
FORA_DA_COMPARACAO = frozenset({
    "fetch_pedidos", "limpar_cache", "_fetch_sync",
    "ValidationError", "now_br",
    "alerta_liberacao", "com_alerta", "filtrar_bloqueio", "resumir",
    "BLOQUEIO_FILTROS", "CAMPOS_RESUMO",
})

_IMPORT_RE = re.compile(r"^[ 	]*(?:import|from)\s+.*$", re.MULTILINE)


def _caminho_v117() -> str | None:
    """O ``situacao_pedidos_service.py`` do V117, se este repo estiver ao lado dele.

    Layout de dev: ``D:\\ProjetoAltamira\\MCPs\\ServidorIntegracaoSAP`` e
    ``D:\\ProjetoAltamira\\web_orcaview_V117``. Um override por ambiente existe para
    quem clona em outro lugar.
    """
    override = os.environ.get("V117_BACKEND_DIR")
    candidatos = []
    if override:
        candidatos.append(os.path.join(override, "services", "situacao_pedidos_service.py"))
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidatos.append(os.path.join(
        raiz, "..", "..", "web_orcaview_V117", "backend", "services",
        "situacao_pedidos_service.py"))
    for c in candidatos:
        if os.path.isfile(c):
            return os.path.normpath(c)
    return None


def _carregar_v117():
    """Importa o modulo do V117 isolado, sem exigir os pacotes ``services``/``utils``.

    O import de topo dele e' ``from exceptions import ValidationError`` -- que aqui nao
    existe. Stubamos os tres modulos ausentes no ``sys.modules`` durante o import; os
    imports que ficam DENTRO dos corpos (``utils.now_br``,
    ``services.sap_montagem_labels``) nunca sao executados, porque este teste so le o
    fonte, nao chama as funcoes.
    """
    caminho = _caminho_v117()
    if not caminho:
        pytest.skip("repo do web_orcaview_V117 não está ao lado — teste só roda em dev")

    import types

    stubs = {}
    if "exceptions" not in sys.modules:
        mod = types.ModuleType("exceptions")
        mod.ValidationError = type("ValidationError", (ValueError,), {})
        stubs["exceptions"] = mod
    for nome, atributos in (("utils", {"now_br": lambda: None}),
                            ("services", {})):
        if nome not in sys.modules:
            mod = types.ModuleType(nome)
            for k, v in atributos.items():
                setattr(mod, k, v)
            stubs[nome] = mod
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("_v117_situacao_pedidos", caminho)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for nome in stubs:
            sys.modules.pop(nome, None)


def _normalizar_fonte(texto: str) -> str:
    """Fonte comparavel: sem linhas de import e sem espaco no fim das linhas.

    As linhas de import sao a UNICA divergencia legitima -- o V117 importa
    ``services.sap_montagem_labels`` e ``utils.now_br``, que aqui tem outro caminho.
    Nada mais e' perdoado: docstring, comentario e ordem de campo contam.
    """
    sem_import = _IMPORT_RE.sub("", texto)
    return "\n".join(l.rstrip() for l in sem_import.splitlines() if l.strip())


@pytest.fixture(scope="module")
def v117():
    return _carregar_v117()


@pytest.mark.parametrize("nome", FUNCOES_NUCLEO)
def test_funcao_do_nucleo_e_identica_ao_v117(nome, v117):
    aqui = _normalizar_fonte(inspect.getsource(getattr(situacao_pedidos, nome)))
    la = _normalizar_fonte(inspect.getsource(getattr(v117, nome)))
    assert aqui == la, (
        f"{nome}() divergiu do V117.\n"
        "Um dos dois lados mudou e o outro ficou para trás — replique a mudança "
        "em vez de afrouxar este teste.\n"
        f"V117: web_orcaview_V117/backend/services/situacao_pedidos_service.py"
    )


@pytest.mark.parametrize("nome", CONSTANTES_NUCLEO)
def test_constante_do_nucleo_e_identica_ao_v117(nome, v117):
    assert getattr(situacao_pedidos, nome) == getattr(v117, nome), (
        f"{nome} divergiu do V117 — é contrato, muda nos dois ou em nenhum."
    )


def test_nenhuma_funcao_publica_do_v117_ficou_de_fora(v117):
    """Se o V117 ganhar uma funcao nova, este teste avisa -- senao o porte envelhece.

    E' o unico ponto que percebe uma AUSENCIA; os testes acima so comparam o que ja
    existe dos dois lados.
    """
    la = {n for n in v117.__all__ if not n.startswith("_")}
    aqui = set(dir(situacao_pedidos))
    faltando = {n for n in la if n not in aqui and n not in FORA_DA_COMPARACAO}
    assert not faltando, (
        f"o V117 exporta {sorted(faltando)} e a .11 não — porte ou adicione a "
        f"FORA_DA_COMPARACAO com o motivo."
    )


def test_rotulo_de_montagem_e_identico_ao_v117():
    """``rotulo()`` decide o texto do tipo de montagem — tem de ser o mesmo dos dois lados.

    O resto do ``sap_montagem_labels`` diverge de propósito (aqui a busca no SAP é um
    gancho da F2); ``rotulo`` e o mapa de fallback, não.
    """
    caminho = _caminho_v117()
    if not caminho:
        pytest.skip("repo do web_orcaview_V117 não está ao lado — teste só roda em dev")
    origem = os.path.join(os.path.dirname(caminho), "sap_montagem_labels.py")
    if not os.path.isfile(origem):
        pytest.skip("sap_montagem_labels.py não encontrado no V117")

    fonte_v117 = open(origem, encoding="utf-8").read()
    ini = fonte_v117.index("def rotulo(")
    aqui = _normalizar_fonte(inspect.getsource(sap_montagem_labels.rotulo))
    la = _normalizar_fonte(fonte_v117[ini:])
    assert aqui == la, "rotulo() divergiu do V117 — replique a mudança nos dois lados."


def test_fallback_de_montagem_e_identico_ao_v117():
    caminho = _caminho_v117()
    if not caminho:
        pytest.skip("repo do web_orcaview_V117 não está ao lado — teste só roda em dev")
    origem = os.path.join(os.path.dirname(caminho), "sap_montagem_labels.py")
    if not os.path.isfile(origem):
        pytest.skip("sap_montagem_labels.py não encontrado no V117")

    espaco: dict = {}
    fonte = open(origem, encoding="utf-8").read()
    ini = fonte.index("FALLBACK_LABELS: dict[str, str] = {")
    fim = fonte.index("}", ini) + 1
    exec(fonte[ini:fim], {"dict": dict, "str": str}, espaco)  # noqa: S102 - literal do próprio repo
    assert sap_montagem_labels.FALLBACK_LABELS == espaco["FALLBACK_LABELS"]
