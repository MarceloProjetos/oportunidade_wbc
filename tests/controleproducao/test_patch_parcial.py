"""Testes da verificação do PATCH parcial de `DocumentLines` (21/09/2026).

A pendência da seção 7.11 do guia: ao mandar `DocumentLines` com só algumas linhas, a
Service Layer preserva as demais ou substitui a coleção? Estes testes cobrem a
FERRAMENTA de verificação — quem responde a pergunta é rodá-la contra o ambiente real.

O que precisa estar certo aqui é a detecção: uma ferramenta de verificação que deixa
passar a falha é pior que não ter ferramenta, porque fecha a pendência sem base.
"""
import asyncio
from unittest.mock import AsyncMock

from controleproducao.modules.pedidos_wbc import service as svc


def _linha(line_num, item="A", qtd=2, preco=10, op=0):
    return {
        "LineNum": line_num, "ItemCode": item, "Quantity": qtd, "Price": preco,
        "LineTotal": qtd * preco, "WhsCode": "01", "U_INO_OP": op, "U_INO_ORCITM": "1",
    }


def test_detecta_linha_que_sumiu():
    """O cenário que motivou a pendência: a Service Layer troca a coleção inteira pelo
    que foi enviado, e as linhas não citadas somem."""
    antes = [_linha(0), _linha(1), _linha(2)]
    depois = [_linha(0)]

    diff = svc.compara_fotos(antes, depois)

    assert diff["sumiram"] == [1, 2]


def test_detecta_campo_alterado_sem_ter_sido_enviado():
    """O outro modo de falha, mais silencioso: a linha continua lá, mas um campo que não
    foi enviado voltou zerado. Foi assim que o `U_B1SYS_RevenueInd2` apareceu (seção 7.11)."""
    antes = [_linha(0, preco=10)]
    depois = [_linha(0, preco=0)]

    diff = svc.compara_fotos(antes, depois)

    assert diff["alteradas"][0]["LineNum"] == 0
    assert diff["alteradas"][0]["campos"]["Price"] == (10, 0)
    assert diff["alteradas"][0]["campos"]["LineTotal"] == (20, 0)


def test_sem_diferenca_nao_reporta_nada():
    """O resultado bom. Se isto der falso positivo, a ferramenta reprova um mecanismo que
    funciona e o módulo 2 fica travado por engano."""
    fotos = [_linha(0), _linha(1)]
    diff = svc.compara_fotos(fotos, [dict(linha) for linha in fotos])

    assert diff == {"sumiram": [], "surgiram": [], "alteradas": []}


def test_comparacao_nao_se_confunde_com_tipo_do_banco():
    """`Decimal('2.000000')` e `2` são o mesmo valor vindo do HANA em momentos diferentes;
    acusar isso como alteração encheria o relatório de ruído."""
    from decimal import Decimal

    antes = [{"LineNum": 0, "Quantity": Decimal("2"), "U_INO_OP": 0}]
    depois = [{"LineNum": 0, "Quantity": 2, "U_INO_OP": 0}]

    assert svc.compara_fotos(antes, depois)["alteradas"] == []


def test_detecta_linha_nova():
    diff = svc.compara_fotos([_linha(0)], [_linha(0), _linha(1)])
    assert diff["surgiram"] == [1]


def test_patch_de_prova_manda_o_mesmo_formato_do_codigo_de_producao():
    """Se o corpo do teste não for o mesmo de `marca_op_nas_linhas` — `DocumentLines` com
    só `LineNum` + um campo —, o experimento não prova nada sobre o código real."""
    sl = AsyncMock()
    asyncio.run(svc.patch_parcial_de_prova(sl, doc_entry=19466, line_num=3, valor_atual=157832))

    entidade, chave, corpo = sl.update_entity.await_args.args
    assert (entidade, chave) == ("Orders", 19466)
    assert corpo == {"DocumentLines": [{"LineNum": 3, "U_INO_OP": 157832}]}


def test_patch_de_prova_nao_altera_valor():
    """É um no-op deliberado: reenvia o valor atual. Assim, qualquer diferença observada
    depois é efeito colateral do mecanismo, não da alteração pedida."""
    sl = AsyncMock()
    asyncio.run(svc.patch_parcial_de_prova(sl, 19466, 0, 42))

    assert sl.update_entity.await_args.args[2]["DocumentLines"][0]["U_INO_OP"] == 42


# ---------------------------------------------------------------------------
# Identificação do pedido (21/09/2026)
# ---------------------------------------------------------------------------
def test_query_por_docentry_nao_confunde_com_docnum():
    """`DocNum` e `DocEntry` são numerações distintas do B1. O incidente que originou esta
    verificação foi registrado no guia como "84263/19466", mas o DocNum 84263 tem
    DocEntry 19253 — o par estava errado e a auditoria teria olhado o documento errado.
    Daí existir uma entrada por DocEntry."""
    from controleproducao.modules.pedidos_wbc import queries as q

    sql = q.PEDIDO_POR_DOCENTRY.format(doc_entry=19466)
    assert '"DocEntry" = 19466' in sql
    assert "DocNum" in sql, "a projeção precisa devolver o DocNum para exibição"


def test_comando_recusa_docnum_e_docentry_juntos():
    from typer.testing import CliRunner

    from controleproducao.cli import app

    resultado = CliRunner().invoke(app, ["diag", "patch-parcial", "84263", "--docentry", "19466"])
    assert resultado.exit_code == 1
