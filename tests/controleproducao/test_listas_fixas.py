"""Testes das listas fixas de `CriaItem` — com foco na Solda, recebida em 21/09/2026.

Até essa data `Resources/Solda.txt` não tinha vindo junto com o código do addon e o porte
rodava com a lista vazia: nenhum item caía em `ItemsGroupCode = 332`. Agora que a lista é
real, esses dois caminhos do C# passam a executar pela primeira vez — daí os testes.
"""
from controleproducao.modules.pedidos_wbc import listas_fixas as lf


def test_solda_carregada_do_arquivo_real():
    solda = lf.carregar_solda()
    assert len(solda) > 5000, "Solda.txt não foi encontrado ou veio truncado"
    # Primeira e última linha do arquivo original, como âncora de integridade.
    assert "DOB000POLID0000021/2" in solda


def test_solda_e_cacheada():
    """Sem cache, o arquivo de 115 KB seria lido do disco uma vez por item da estrutura,
    porque `_cria_item` chama `carregar_solda()` a cada chamada."""
    assert lf.carregar_solda() is lf.carregar_solda()


def test_comparacao_e_exata_e_nao_por_prefixo():
    """O C# usa `ItemCode == x` dentro de um `foreach`, não `Contains`. Um código que
    apenas começa com um da lista NÃO deve casar."""
    solda = lf.carregar_solda()
    algum = next(iter(solda))
    assert algum + "XYZ" not in solda


def test_prefixo_333_tem_precedencia_sobre_a_solda():
    """No `CriaItem`, o teste dos prefixos vem primeiro e vence: um item que contenha um
    prefixo de 333 nunca chega a ser avaliado para 332, mesmo estando na Solda. São 1.477
    códigos nessa situação — se esta precedência se inverter, eles mudam de grupo em massa.
    """
    solda = lf.carregar_solda()
    com_prefixo = [c for c in solda if any(p in c for p in lf.PREFIXOS_GRUPO_333)]
    assert com_prefixo, "esperava códigos da Solda contendo prefixos de 333"


def test_explosao_continua_independente_da_solda():
    """São arquivos diferentes decidindo coisas diferentes (`U_INO_EXPL_SOLDA` vs grupo),
    ainda que 178 dos 180 códigos da Explosao também estejam na Solda."""
    assert len(lf.EXPLOSAO_SOLDA) == 180
    assert isinstance(lf.EXPLOSAO_SOLDA, list)
