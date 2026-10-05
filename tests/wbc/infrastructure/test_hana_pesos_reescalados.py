"""Reading integration order lines and their change log from HANA — offline, fake connection."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import SecretStr

from wbcpython.config import HanaSettings
from wbcpython.infrastructure.hana.pesos_reescalados import RepositorioPesosReescaladosHana


class Conexao:
    """Answers each query with the next (columns, rows) of the script."""

    def __init__(self, *respostas: tuple[tuple[str, ...], list[tuple]]) -> None:
        self.respostas = list(respostas)
        self.executados: list[tuple[str, tuple]] = []

    def cursor(self):
        conexao = self

        class Cursor:
            def execute(self, sql, params):
                conexao.executados.append((sql, params))
                self.colunas, self.linhas = conexao.respostas.pop(0)
                self.description = [(c,) for c in self.colunas]

            def fetchall(self):
                return self.linhas

            def close(self):
                pass

        return Cursor()

    def close(self):
        pass


def _settings() -> HanaSettings:
    return HanaSettings(_env_file=None, host="h", port=30015, username="u", password=SecretStr("s"))  # type: ignore[call-arg]


def _repo(conexao):
    return RepositorioPesosReescaladosHana(
        _settings(), company_db="SBOALTAMIRAPROD", usuarios_da_integracao={" OrcaView "},
        fabrica_de_conexao=lambda: conexao,
    )


def _pedidos(primeira=1):
    return (("DocEntry", "DocNum", "U_INO_COTWBC", "PrimeiraVersao"), [(20317, 84457, "00125299 ", primeira)])


COLUNAS = ("DocEntry", "LineNum", "LogInstanc", "Quantity", "Weight1", "LineTotal", "UpdateDate", "UpdateTS",
           "USER_CODE", "U_NAME", "ItemCode", "QtdAtual", "PesoAtual", "TotalAtual")


def _versoes(criada=1, reescalada=2):
    """Line 0 created by the integration, then rescaled by a person; the current RDR1 on each row."""
    atual = ("I000003", 30, 4468.2, 4127.4)
    return (COLUNAS, [
        (20317, 0, criada, 1, 148.94, 4127.4, date(2026, 10, 1), 172016, "orcaview", "orcaview", *atual),
        (20317, 0, reescalada, 30, 4468.2, 4127.4, date(2026, 10, 2), 93412, "vendas01", "Vendas", *atual),
    ])


def test_monta_a_linha_com_as_versoes_em_duas_consultas():
    conexao = Conexao(_pedidos(), _versoes())
    [linha] = _repo(conexao).linhas(desde=date(2026, 10, 2))

    assert (linha.doc_num, linha.orcamento, linha.line_num, linha.item) == (84457, "00125299", 0, "I000003")
    assert (linha.atual.quantidade, linha.atual.peso, linha.atual.total) == (30, 4468.2, 4127.4)
    assert [v.pela_integracao for v in linha.versoes] == [True, False]
    assert linha.versoes[1].momento == datetime(2026, 10, 2, 9, 34, 12)
    assert not linha.historico_cortado and len(conexao.executados) == 2
    sql, params = conexao.executados[0]
    assert '"SBOALTAMIRAPROD"."ORDR"' in sql and "?" in sql
    assert params == (None, None, "orcaview", date(2026, 10, 2), date(2026, 10, 2), "orcaview")
    assert '"RDR1" C' in conexao.executados[1][0] and "\"LineStatus\" = 'O'" in conexao.executados[1][0]


def test_um_pedido_de_qualquer_data_e_sem_pedido_uma_consulta_so():
    conexao = Conexao((_pedidos()[0], []))
    assert _repo(conexao).linhas(desde=None, pedido=84457) == []
    assert conexao.executados[0][1][:2] == (84457, 84457) and len(conexao.executados) == 1


def test_historico_cortado_e_da_linha_que_comeca_na_versao_mais_antiga_que_sobrou():
    [cortada] = _repo(Conexao(_pedidos(primeira=5), _versoes(5, 6))).linhas(desde=None)
    assert cortada.historico_cortado
    # Same order, but this line was added after the oldest surviving version: its author is known.
    [incluida] = _repo(Conexao(_pedidos(primeira=5), _versoes(8, 9))).linhas(desde=None)
    assert not incluida.historico_cortado


def test_sem_usuario_da_integracao_recusa():
    with pytest.raises(ValueError, match="SL_USERNAME"):
        RepositorioPesosReescaladosHana(_settings(), company_db="X", usuarios_da_integracao={""})
