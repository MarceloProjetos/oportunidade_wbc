"""Read-only HANA lists of `wbcpython quantidades`: the date cut that keeps the command's own undone write out."""

from __future__ import annotations

from types import SimpleNamespace

from wbcpython.infrastructure.hana import quantidades


class _Conexao:
    def __init__(self, linhas):
        self.linhas, self.executado, self.fechada = linhas, [], False

    def cursor(self):
        conexao = self

        class _Cursor:
            def execute(self, sql, params=()):
                conexao.executado.append((sql, params))

            def fetchall(self):
                return conexao.linhas

            def close(self):
                pass

        return _Cursor()

    def close(self):
        self.fechada = True


_SETTINGS = SimpleNamespace(service_layer=SimpleNamespace(company_db="SBOALTAMIRAPROD"), hana=None)


def test_so_conta_versao_maior_salva_antes_da_regra() -> None:
    """84603, 18/11/2026: the command's write of 88, undone the same day, must not look like a person's choice."""
    conexao = _Conexao([(84580, 0), (84580, 1), (84420, 0)])
    mudadas = quantidades.linhas_ja_maiores_que_um(_SETTINGS, fabrica_de_conexao=lambda: conexao)
    assert mudadas == {84580: {0, 1}, 84420: {0}}
    sql, params = conexao.executado[0]
    assert '"UpdateDate" < ?' in sql and params == (quantidades.CRIADOS_ANTES_DE,)
    assert conexao.fechada


def test_lista_so_pedidos_criados_antes_da_regra() -> None:
    conexao = _Conexao([(84603,), (84604,)])
    assert quantidades.pedidos_com_linha_em_um(_SETTINGS, fabrica_de_conexao=lambda: conexao) == [84603, 84604]
    sql, params = conexao.executado[0]
    assert '"CreateDate" < ?' in sql and params == ("2026-11-18",)
