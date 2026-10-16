"""Future-delivery sale (CFOP 5922/6922) is not a closed pedido until the remessa ships.

Numbers are the real ones measured in production on 16/10/2026 (docs/PLANO_ENTREGA_FUTURA.md).
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

import extract_ordens_servico_engenharia as mod
import faturamento_pedido as fat
from faturamento_pedido import Faturamento, situacao_remessa


@pytest.mark.parametrize(("futura", "devolvido", "remetido", "esperado"), [
    (7458.0, 0.0, 0.0, "nenhuma"),                 # 84464: nothing shipped
    (372407.0, 0.0, 169527.0, "parcial"),          # 84407: one of two loads
    (376684.0, 0.0, 376686.0, "completa"),         # 84021: R$ 0,02 over, rounding
    (915568.24, 915568.24, 0.0, "anulada"),        # 83422: credit note cancelled it all
    (738273.0, 244732.0, 503808.0, "completa"),    # 83898: partial annulment + the rest shipped
    (100000.0, 0.0, 99600.0, "completa"),          # 0,4% missing: inside 0,5%
    (100000.0, 0.0, 99000.0, "parcial"),           # 1% missing
    (80.0, 0.0, 79.5, "completa"),                 # small order: R$ 1,00 floor
    (80.0, 0.0, 0.5, "nenhuma"),                   # a few cents are not a remessa
])
def test_situacao_remessa(futura, devolvido, remetido, esperado):
    assert situacao_remessa(futura, devolvido, remetido) == esperado


def test_pendente_so_para_entrega_futura_nao_remetida():
    assert Faturamento("entrega_futura", "nenhuma", None).pendente
    assert Faturamento("entrega_futura", "parcial", None).pendente
    assert not Faturamento("entrega_futura", "completa", None).pendente
    assert not Faturamento("entrega_futura", "anulada", None).pendente
    assert not Faturamento("venda", None, None).pendente


def test_classificar_pedido_com_faturamento():
    pendente = Faturamento("entrega_futura", "nenhuma", "2026-10-07")
    assert mod.classificar_pedido('N', 'C', pendente) == (False, 'Aberto')
    assert mod.classificar_pedido('N', 'C', Faturamento("entrega_futura", "completa", None)) == (False, 'Fechado')
    assert mod.classificar_pedido('N', 'C', Faturamento("venda", None, None)) == (False, 'Fechado')
    assert mod.classificar_pedido('Y', 'C', pendente) == (True, 'Cancelado')
    assert mod.classificar_pedido('N', 'C', None) == (False, 'Fechado')


class _SapFaturamento:
    """Answers the three invoice queries; anything else is a test bug."""

    def __init__(self, notas, devolucoes=None, remessas=None):
        self.notas, self.devolucoes, self.remessas = notas, devolucoes or [], remessas or []
        self.consultas: list[tuple[str, list]] = []

    def execute_query(self, query, params=None):
        self.consultas.append((query, params))
        if '"Devolvido"' in query:
            return pd.DataFrame(self.devolucoes, columns=["Pedido", "Devolvido"])
        if '"Remessa"' in query:
            return pd.DataFrame(self.remessas, columns=["Pedido", "Remessa", "Valor"])
        if '"UltimaNF"' in query:
            return None if self.notas is None else pd.DataFrame(self.notas, columns=["Pedido", "UltimaNF", "Futura"])
        raise AssertionError(f"consulta inesperada: {query}")


def test_ler_faturamento_soma_remessas_por_pedido_sem_contar_duas_vezes():
    sap = _SapFaturamento(
        notas=[(10, datetime(2026, 9, 18), 372407.0),      # entrega futura
               (11, datetime(2026, 10, 9), 0.0),           # venda normal
               (12, datetime(2026, 1, 6), 915568.24)],     # anulada
        devolucoes=[(12, 915568.24)],
        remessas=[(10, 900, 87000.0),
                  (10, 900, 87000.0),       # same remessa via NF ref (13) and pedido ref (17)
                  (10, 901, 82527.0),
                  (10, 902, None)])         # a non-remessa NF referencing the pedido
    r = fat.ler_faturamento(sap, "SBO", [12, 10, 11, 10])
    assert r == {
        10: Faturamento("entrega_futura", "parcial", "2026-09-18"),
        11: Faturamento("venda", None, "2026-10-09"),
        12: Faturamento("entrega_futura", "anulada", "2026-01-06"),
    }
    texto = " ".join(q for q, _ in sap.consultas)
    assert 'IN (10,11,12)' in texto and 'IN (10,12)' in texto       # keys pasted, validated ints
    assert '"RefObjType" = \'13\'' in texto and '"RefObjType" = \'17\'' in texto
    assert all(p == [] for _, p in sap.consultas)


def test_ler_faturamento_sem_entrega_futura_nao_le_devolucao_nem_remessa():
    sap = _SapFaturamento(notas=[(11, datetime(2026, 10, 9), 0.0)])
    assert fat.ler_faturamento(sap, "SBO", [11]) == {11: Faturamento("venda", None, "2026-10-09")}
    assert len(sap.consultas) == 1


def test_ler_faturamento_falha_devolve_none():
    assert fat.ler_faturamento(_SapFaturamento(notas=None), "SBO", [10]) is None

    class _Quebra:
        def execute_query(self, query, params=None):
            raise RuntimeError("HANA caiu")
    assert fat.ler_faturamento(_Quebra(), "SBO", [10]) is None


def test_ler_faturamento_lista_vazia_nao_consulta():
    sap = _SapFaturamento(notas=[])
    assert fat.ler_faturamento(sap, "SBO", []) == {}
    assert sap.consultas == []


# ---------- The readers that answer the PCP app ----------

def _set_sap_env(monkeypatch):
    monkeypatch.setenv('SAP_HOST', 'host')
    monkeypatch.setenv('SAP_USER', 'user')
    monkeypatch.setenv('SAP_PASSWORD', 'pwd')
    monkeypatch.setenv('SAP_SCHEMA', 'SBOALTAMIRAPROD')


def _fake_extractor(pedido_rows, notas, remessas=None, lista_rows=None):
    class _FS:
        def __init__(self, *a, **k):
            self._fat = _SapFaturamento(notas, remessas=remessas)

        def connect(self):
            return True

        def execute_query(self, query, params=None):
            if '"UltimaNF"' in query or '"Devolvido"' in query or '"Remessa"' in query:
                return self._fat.execute_query(query, params)
            if 'OWOR' in query and lista_rows is not None:
                return pd.DataFrame(lista_rows)
            if 'OWOR' in query:
                return pd.DataFrame({'Status': ['R']})
            return pd.DataFrame(pedido_rows)

        def close(self):
            pass
    return _FS


_FECHADO_84464 = [{'DocEntry': 20055, 'CANCELED': 'N', 'DocStatus': 'C', 'CardCode': 'C1',
                   'CardName': 'TAKII DO BRASIL LTDA', 'DocDate': datetime(2026, 10, 1),
                   'DocDueDate': datetime(2026, 11, 17), 'DocTotal': 7458.0, 'DocCur': 'R$'}]


def test_consultar_status_entrega_futura_sem_remessa_e_aberto(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _fake_extractor(
        _FECHADO_84464, notas=[(20055, datetime(2026, 10, 7), 7458.0)]))
    r = mod.consultar_status_pedido(84464)
    assert r['pedido_status'] == 'Aberto' and r['pedido_cancelado'] is False
    assert (r['faturamento'], r['remessa'], r['faturado_em']) == ('entrega_futura', 'nenhuma', '2026-10-07')


def test_consultar_status_remessa_so_pela_referencia_ao_pedido_fecha(monkeypatch):
    """83919: the remessa references only the pedido (RefObjType 17) and still closes it."""
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _fake_extractor(
        _FECHADO_84464, notas=[(20055, datetime(2026, 6, 8), 43856.0)],
        remessas=[(20055, 777, 43856.0)]))
    r = mod.consultar_status_pedido(84464)
    assert r['pedido_status'] == 'Fechado' and r['remessa'] == 'completa'


def test_consultar_status_venda_normal_continua_fechado_com_data(monkeypatch):
    """D1: NF 5101 → still Fechado, now with the date of the last NF."""
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _fake_extractor(
        _FECHADO_84464, notas=[(20055, datetime(2026, 1, 6), 0.0)]))
    r = mod.consultar_status_pedido(83411)
    assert r['pedido_status'] == 'Fechado'
    assert (r['faturamento'], r['remessa'], r['faturado_em']) == ('venda', None, '2026-01-06')


def test_consultar_status_leitura_das_nfs_falha_mantem_fechado(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _fake_extractor(_FECHADO_84464, notas=None))
    r = mod.consultar_status_pedido(84464)
    assert r['pedido_status'] == 'Fechado' and r['faturamento'] is None


def test_diagnosticar_nped_usa_a_mesma_regra(monkeypatch):
    _set_sap_env(monkeypatch)
    monkeypatch.setattr(mod, 'SAPExtractor', _fake_extractor(
        [{'DocEntry': 20055, 'CANCELED': 'N', 'DocStatus': 'C'}],
        notas=[(20055, datetime(2026, 10, 7), 7458.0)]))
    assert mod.diagnosticar_nped(84464)['pedido_status'] == 'Aberto'


def test_listar_pedidos_com_os_usa_a_mesma_regra(monkeypatch):
    _set_sap_env(monkeypatch)
    lista = [
        {'NPED': 84464, 'Cliente': 'TAKII', 'OS': 1, 'Data': None,
         'Canceled': 'N', 'DocStatus': 'C', 'PedidoExiste': 1, 'PedEntry': 20055},
        {'NPED': 83411, 'Cliente': 'CANON', 'OS': 2, 'Data': None,
         'Canceled': 'N', 'DocStatus': 'C', 'PedidoExiste': 1, 'PedEntry': 18000},
    ]
    monkeypatch.setattr(mod, 'SAPExtractor', _fake_extractor(
        [], notas=[(20055, datetime(2026, 10, 7), 7458.0), (18000, datetime(2026, 1, 6), 0.0)],
        lista_rows=lista))
    por = {p['nped']: p for p in mod.listar_pedidos_com_os(30)}
    assert por[84464]['status_pedido'] == 'Aberto' and por[84464]['remessa'] == 'nenhuma'
    assert por[83411]['status_pedido'] == 'Fechado' and por[83411]['faturamento'] == 'venda'


def test_ler_faturamento_falha_na_devolucao_ou_remessa_devolve_none():
    class _Parcial(_SapFaturamento):
        def __init__(self, cair):
            super().__init__(notas=[(10, datetime(2026, 9, 18), 100.0)])
            self.cair = cair

        def execute_query(self, query, params=None):
            if self.cair in query:
                return None
            return super().execute_query(query, params)
    assert fat.ler_faturamento(_Parcial('"Devolvido"'), "SBO", [10]) is None
    assert fat.ler_faturamento(_Parcial('"Remessa"'), "SBO", [10]) is None


def test_situacao_remessa_no_limite_exato_da_tolerancia():
    assert situacao_remessa(100000.0, 0.0, 99500.0) == "completa"     # falta = 0,5% exato
    assert situacao_remessa(100000.0, 0.0, 99499.0) == "parcial"
    # Credit note bigger than the NF, some remessa: nothing is left to ship → not pending.
    assert situacao_remessa(100.0, 150.0, 30.0) == "completa"
    assert situacao_remessa(100.0, 150.0, 0.0) == "anulada"
