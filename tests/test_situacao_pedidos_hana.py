"""Testes da leitura HANA da Situacao dos Pedidos (F2) -- sem rede.

Um HANA de mentira (:class:`_ConexaoFalsa`) responde as duas consultas pelo texto do SQL.
E' o suficiente para cravar o que importa nesta fase: o cache, a guarda de volume, a
conversao de tipos e -- principalmente -- que HANA fora do ar vira **mensagem legivel**,
nunca um 500 cru.

Plano: ``docs/PLANO_SITUACAO_PEDIDOS_MCP.md``.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

import config
import sap_montagem_labels
import situacao_pedidos_hana as hana
from situacao_pedidos import ValidationError


class _Cursor:
    def __init__(self, conexao: "_ConexaoFalsa") -> None:
        self._c = conexao
        self.description: list[tuple] | None = None
        self._linhas: list[tuple] = []

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._c.sqls.append(sql)
        self._c.params.append(params)
        if self._c.erro_em and self._c.erro_em in sql:
            raise RuntimeError("connection closed by peer")
        if 'COUNT(*)' in sql:
            cols, linhas = ["N"], [(self._c.total,)]
        elif "CUFD" in sql:
            cols, linhas = ["FldValue", "Descr"], self._c.udf
        elif '"OCNT"' in sql:
            cols, linhas = ["AbsId", "Name"], self._c.ocnt
        else:
            cols, linhas = self._c.colunas, self._c.linhas
        self.description = [(c,) for c in cols]
        self._linhas = linhas

    def fetchall(self) -> list[tuple]:
        return self._linhas

    def close(self) -> None:
        self._c.cursores_fechados += 1


class _ConexaoFalsa:
    """HANA de mentira. Guarda os SQLs recebidos para as asserções de contrato."""

    def __init__(self, *, colunas=None, linhas=None, total=None, udf=None, erro_em=None,
                 ocnt=None):
        self.colunas = colunas or []
        self.linhas = linhas or []
        self.total = total if total is not None else len(self.linhas)
        self.udf = udf or []
        self.ocnt = ocnt or []
        self.erro_em = erro_em
        self.sqls: list[str] = []
        self.params: list[tuple] = []
        self.cursores_fechados = 0
        self.fechada = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def close(self) -> None:
        self.fechada = True


#: Como o alias da view aparece no SELECT pesado — serve para distingui-lo do COUNT.
SELECT_DA_VIEW = 'VW_STATUS_PEDIDO_DDP" v'

COLUNAS = ["DocEntry", "DocNum", "Data_Pedido", "CardName", "Financeiro", "Producao",
           "Entrega", "Peso", "MontagemValor", "DocTotal", "Data_Entrega",
           "Data_Pagto", "Data_Lib_Fin", "Data_Lib_Prod", "MontadorCnpj"]


def _linha(**over: Any) -> tuple:
    base = {
        "DocEntry": 15118, "DocNum": 84260, "Data_Pedido": dt.date(2026, 8, 12),
        # CHAR do HANA vem com espaco a direita -- de proposito no fixture.
        "CardName": "FLOW X INTERNATIONAL BRASIL LTDA      ",
        "Financeiro": "Bloqueado", "Producao": "Bloqueada", "Entrega": "Bloqueada",
        "Peso": Decimal("1250.50"), "MontagemValor": Decimal("9700.00"),
        "DocTotal": Decimal("41250.00"),
        "Data_Entrega": dt.datetime(2026, 9, 23, 14, 30),
        "Data_Pagto": None, "Data_Lib_Fin": None, "Data_Lib_Prod": None,
        "MontadorCnpj": "67.133.900/0001-88",
    }
    base.update(over)
    return tuple(base[c] for c in COLUNAS)


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    """SAP configurado e cache limpo em todo teste -- e nada de rede de verdade."""
    for k, v in (("SAP_HOST", "hana.teste"), ("SAP_PORT", "30015"),
                 ("SAP_USER", "u"), ("SAP_PASSWORD", "p"),
                 ("SAP_SCHEMA", "SBOALTAMIRAPROD")):
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SAP_DATABASE", raising=False)
    config.reset_settings()
    hana.limpar_cache()
    sap_montagem_labels.registrar_fonte(None)
    yield
    config.reset_settings()
    hana.limpar_cache()
    sap_montagem_labels.registrar_fonte(None)


def _ligar(monkeypatch, conexao, contador=None):
    """Faz o ``_conectar`` devolver a conexão de mentira (e contar as idas)."""
    def _fake(*_a, **_k):
        if contador is not None:
            contador.append(1)
        return conexao
    monkeypatch.setattr(hana, "connect_sap_hana", _fake)
    return conexao


# --- consulta e conversão de tipos ------------------------------------------

def test_le_a_view_e_converte_os_tipos(monkeypatch):
    _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS, linhas=[_linha()]))
    linha = hana.fetch_status_pedidos()[0]

    assert linha["Peso"] == 1250.50 and isinstance(linha["Peso"], float)
    assert linha["DocTotal"] == 41250.00 and isinstance(linha["DocTotal"], float)
    assert linha["Data_Pedido"] == "2026-08-12"
    assert linha["Data_Entrega"] == "2026-09-23"   # timestamp perde a hora
    assert linha["Data_Pagto"] is None             # NULL continua None, nunca NaN
    assert linha["DocNum"] == 84260 and isinstance(linha["DocNum"], int)


def test_char_do_hana_vem_sem_o_espaco_a_direita(monkeypatch):
    """Sem o rstrip, o nome do cliente divergiria da tela por padding invisível."""
    _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS, linhas=[_linha()]))
    assert hana.fetch_status_pedidos()[0]["CardName"] == "FLOW X INTERNATIONAL BRASIL LTDA"


def test_o_sql_usa_left_join_e_a_ordenacao_do_power_bi(monkeypatch):
    """Contrato com o V117: INNER JOIN sumiria com pedido sem montador da resposta.

    São 4 desde 2026-09-10 (a RDR12 do endereço entrou). Vale o mesmo argumento: em
    10/09 nenhum dos 266 pedidos do recorte estava sem linha na RDR12, mas basta um
    para o INNER apagar um pedido da lista sem erro nenhum.
    """
    c = _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS, linhas=[_linha()]))
    hana.fetch_status_pedidos()
    select = next(s for s in c.sqls if SELECT_DA_VIEW in s)

    assert select.count("LEFT JOIN") == 4
    assert "INNER JOIN" not in select
    assert 'ORDER BY v."Producao", v."Data_Pedido"' in select
    assert "SBOALTAMIRAPROD" in select


def test_a_rdr12_entra_por_docentry_e_a_ocnt_fica_de_fora(monkeypatch):
    """A OCNT no JOIN **mata a consulta**: o município é um código e há linha
    histórica com o campo vazio; no plano de execução o HANA avalia a conversão em
    linhas que o filtro descartaria e estoura "invalid number". Quem resolve o nome é
    um SELECT à parte."""
    c = _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS, linhas=[_linha()]))
    hana.fetch_status_pedidos()
    select = next(s for s in c.sqls if SELECT_DA_VIEW in s)

    assert 'LEFT JOIN "SBOALTAMIRAPROD"."RDR12" a ON a."DocEntry" = v."DocEntry"' in select
    assert "OCNT" not in select


def test_as_colunas_de_endereco_vao_com_a_grafia_irregular_do_sap(monkeypatch):
    """`StrNoDlvrP` não tem o "y", e `BldDlvryP` convive com `BuildingS`. Um loop de
    sufixo produziria nome errado — foi a armadilha de 13/08 no OrçaView. Este teste
    existe para o dia em que alguém "arrumar" a lista."""
    c = _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS, linhas=[_linha()]))
    hana.fetch_status_pedidos()
    select = next(s for s in c.sqls if SELECT_DA_VIEW in s)

    for grupo in hana.ENDERECO_COLS.values():
        for coluna in grupo.values():
            assert f'a."{coluna}"' in select, f'{coluna} sumiu do SELECT'
    assert 'a."StrNoDlvrP"' in select      # sem o "y", como o SAP escreve
    assert 'a."StrNoDlvryP"' not in select  # a grafia "certa" NÃO existe na RDR12


def test_o_sql_nao_le_a_udf_de_vendedor_que_nao_existe_em_producao(monkeypatch):
    """``OSLP.U_INO_Vendedor`` não existe em PROD e já quebrou um sync."""
    c = _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS, linhas=[_linha()]))
    hana.fetch_status_pedidos()
    select = next(s for s in c.sqls if SELECT_DA_VIEW in s)
    assert "U_INO_Vendedor" not in select
    assert 's."SlpName" AS "Vendedor"' in select


def test_fecha_conexao_e_cursor(monkeypatch):
    """Uma conexão por leitura — se vazar, a .11 esgota o teto de sessões do HANA."""
    c = _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS, linhas=[_linha()]))
    hana.fetch_status_pedidos()
    assert c.fechada is True
    assert c.cursores_fechados == 2  # o COUNT e o SELECT


# --- cache ------------------------------------------------------------------

def test_segunda_chamada_nao_vai_ao_hana(monkeypatch):
    idas: list[int] = []
    _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS, linhas=[_linha()]), idas)
    hana.fetch_status_pedidos()
    hana.fetch_status_pedidos()
    hana.fetch_status_pedidos()
    assert len(idas) == 1


def test_recarregar_ignora_o_cache(monkeypatch):
    idas: list[int] = []
    _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS, linhas=[_linha()]), idas)
    hana.fetch_status_pedidos()
    hana.fetch_status_pedidos(recarregar=True)
    assert len(idas) == 2


def test_cache_expira_no_ttl(monkeypatch):
    idas: list[int] = []
    _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS, linhas=[_linha()]), idas)
    relogio = [1000.0]
    monkeypatch.setattr(hana.time, "monotonic", lambda: relogio[0])

    hana.fetch_status_pedidos()                       # grava o retrato em t=1000
    relogio[0] = 1000.0 + hana.CACHE_TTL_SEGUNDOS - 1
    hana.fetch_status_pedidos()                       # ainda dentro da janela
    assert len(idas) == 1

    relogio[0] = 1000.0 + hana.CACHE_TTL_SEGUNDOS + 1
    hana.fetch_status_pedidos()                       # expirou
    assert len(idas) == 2


def test_a_lista_devolvida_nao_envenena_o_cache(monkeypatch):
    """Quem chamou pode ordenar/cortar a lista sem estragar o retrato dos outros."""
    _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS,
                                      linhas=[_linha(), _linha(DocNum=1)]))
    primeira = hana.fetch_status_pedidos()
    primeira.clear()
    assert len(hana.fetch_status_pedidos()) == 2


def test_idade_do_cache(monkeypatch):
    """Quem lê "bloqueado" precisa saber se o retrato é de agora ou de 2 min atrás."""
    assert hana.idade_do_cache_s() is None
    _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS, linhas=[_linha()]))
    hana.fetch_status_pedidos()
    idade = hana.idade_do_cache_s()
    assert idade is not None and idade >= 0


# --- guardas e falhas -------------------------------------------------------

def test_guarda_de_volume_recusa_view_que_mudou_de_natureza(monkeypatch):
    """Acima do teto é erro explícito, não 200 mil linhas na resposta."""
    c = _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS, linhas=[_linha()],
                                          total=250_000))
    with pytest.raises(ValidationError, match="mudou de natureza"):
        hana.fetch_status_pedidos()
    # o SELECT pesado nem chegou a rodar
    assert not any(SELECT_DA_VIEW in s for s in c.sqls)
    assert c.fechada is True


def test_hana_fora_do_ar_vira_mensagem_legivel(monkeypatch):
    def _cai(*_a, **_k):
        raise OSError("connection refused")
    monkeypatch.setattr(hana, "connect_sap_hana", _cai)

    with pytest.raises(hana.SAPIndisponivel, match="SAP HANA"):
        hana.fetch_status_pedidos()


def test_consulta_que_estoura_no_meio_vira_sap_indisponivel(monkeypatch):
    c = _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS, linhas=[_linha()],
                                          erro_em=SELECT_DA_VIEW))
    with pytest.raises(hana.SAPIndisponivel):
        hana.fetch_status_pedidos()
    assert c.fechada is True  # a conexão fecha mesmo com a consulta falhando


def test_falha_nao_deixa_cache_velho_para_tras(monkeypatch):
    """Erro não pode gravar cache: a próxima chamada tem de tentar de novo."""
    def _cai(*_a, **_k):
        raise OSError("connection refused")
    monkeypatch.setattr(hana, "connect_sap_hana", _cai)
    with pytest.raises(hana.SAPIndisponivel):
        hana.fetch_status_pedidos()
    assert hana.idade_do_cache_s() is None


def test_sem_credencial_o_erro_diz_o_que_falta(monkeypatch):
    monkeypatch.delenv("SAP_HOST", raising=False)
    config.reset_settings()
    with pytest.raises(hana.SAPIndisponivel, match="SAP_HOST"):
        hana.fetch_status_pedidos()


def test_sem_schema_o_erro_diz_o_que_falta(monkeypatch):
    monkeypatch.delenv("SAP_SCHEMA", raising=False)
    config.reset_settings()
    with pytest.raises(hana.SAPIndisponivel, match="SAP_SCHEMA"):
        hana.fetch_status_pedidos()


# --- rótulos de montagem (o gancho que a F1 deixou) -------------------------

def test_udf_le_a_lista_do_sap(monkeypatch):
    c = _ligar(monkeypatch, _ConexaoFalsa(udf=[("3   ", "MONTAGEM POR CONTA DE TERCEIROS"),
                                               ("", "linha sem código")]))
    valores = hana.fetch_udf_valid_values("ORDR", "INO_TPO_MONTAGEM")

    assert valores == [{"value": "3", "descr": "MONTAGEM POR CONTA DE TERCEIROS"}]
    assert c.params[0] == ("ORDR", "INO_TPO_MONTAGEM")  # parametrizado, não interpolado


def test_udf_nunca_levanta_quando_o_hana_cai(monkeypatch):
    """Quem chama tem fallback; ficar sem rótulo é pior que o rótulo de ontem."""
    def _cai(*_a, **_k):
        raise OSError("connection refused")
    monkeypatch.setattr(hana, "connect_sap_hana", _cai)
    assert hana.fetch_udf_valid_values("ORDR", "INO_TPO_MONTAGEM") == []


def test_ligar_rotulos_faz_o_rotulo_vir_do_sap(monkeypatch):
    _ligar(monkeypatch, _ConexaoFalsa(udf=[("3", "ROTULO QUE VEIO DO SAP")]))
    hana.ligar_rotulos_do_sap()
    assert sap_montagem_labels.rotulo("3") == "ROTULO QUE VEIO DO SAP"


def test_sem_ligar_o_rotulo_sai_do_fallback():
    """Estado da F1 — e o que vale se alguém esquecer de chamar ``ligar_rotulos``."""
    assert sap_montagem_labels.rotulo("3") == "MONTAGEM POR CONTA DE TERCEIROS"


# --- contrato entre o SQL e o núcleo ----------------------------------------

def test_o_select_entrega_todas_as_colunas_que_o_nucleo_le():
    """Um alias com erro de digitação só apareceria em produção, como campo vazio.

    A conexão de mentira não pega isso — ela devolve as colunas que o *teste* declara.
    Então aqui se compara o texto do SELECT com as chaves que o núcleo portado
    realmente lê (``r.get("...")`` em ``_pedido`` e ``_montagem``).
    """
    import inspect
    import re

    import situacao_pedidos as sp

    no_select = set(re.findall(r'"([A-Za-z_@][\w]*)"',
                               hana.STATUS_PEDIDO_COLS.replace('v."', '"')))
    lidas = set(re.findall(
        r'r\.get\(\s*"([^"]+)"',
        inspect.getsource(sp._pedido) + inspect.getsource(sp._montagem)))

    faltando = lidas - no_select
    assert not faltando, (
        f"o núcleo lê {sorted(faltando)} e o SELECT não traz — alias errado ou coluna "
        f"nova no V117 que não foi replicada aqui."
    )


# --- municipio pela OCNT (B2) -----------------------------------------------
# A OCNT NAO entra no JOIN: `County` vazio faz o HANA estourar "invalid number" no plano
# de execucao. O nome vem de um SELECT a parte, na MESMA conexao e nas MESMAS linhas --
# que ja tem o cache de 120 s do recorte. Sem tabela nova, sem cache proprio.

COLUNAS_END = COLUNAS + ["CntyDlvryP", "CityDlvryP", "CountyS", "CityS"]


def _linha_end(**over: Any) -> tuple:
    """Uma linha com os códigos de município dos dois lados do endereço."""
    base = dict(zip(COLUNAS, _linha()))
    base.update({"CntyDlvryP": "1763", "CityDlvryP": "JUIZ DE FORA",
                 "CountyS": "1410", "CityS": "BELO HORIZONTE"})
    base.update(over)
    return tuple(base[c] for c in COLUNAS_END)


def _com_ocnt(monkeypatch, linhas, ocnt=((1763, "Juiz de Fora"), (1410, "Belo Horizonte"))):
    return _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS_END, linhas=linhas,
                                             ocnt=list(ocnt)))


def test_o_municipio_vem_da_ocnt_num_select_a_parte(monkeypatch):
    c = _com_ocnt(monkeypatch, [_linha_end()])
    linha = hana.fetch_status_pedidos()[0]

    assert linha["_MunicipioLocal"] == "Juiz de Fora"
    assert linha["_MunicipioPonto"] == "Belo Horizonte"
    ocnt = [s for s in c.sqls if '"OCNT"' in s]
    assert len(ocnt) == 1, "a OCNT tem de ser UMA consulta, não uma por pedido"
    assert "JOIN" not in ocnt[0], "a OCNT no JOIN mata a consulta (invalid number)"
    assert "IN (1410,1763)" in ocnt[0], "códigos entram como número, ordenados"


def test_um_select_de_ocnt_para_o_recorte_inteiro(monkeypatch):
    """Reuso, não N+1: 3 pedidos com 2 municípios repetidos = 1 consulta, 2 códigos."""
    linhas = [_linha_end(), _linha_end(DocNum=84261), _linha_end(DocNum=84262)]
    c = _com_ocnt(monkeypatch, linhas)
    assert all(r["_MunicipioLocal"] == "Juiz de Fora" for r in hana.fetch_status_pedidos())
    assert len([s for s in c.sqls if '"OCNT"' in s]) == 1


def test_codigo_vazio_ou_lixo_nao_vai_para_a_ocnt(monkeypatch):
    """É por causa dessas linhas que a OCNT não pode entrar no JOIN."""
    c = _com_ocnt(monkeypatch, [_linha_end(CntyDlvryP="", CountyS="  ")], ocnt=[])
    linha = hana.fetch_status_pedidos()[0]

    assert linha["_MunicipioLocal"] is None and linha["_MunicipioPonto"] is None
    assert not [s for s in c.sqls if '"OCNT"' in s], "sem código, sem consulta"


def test_a_chave_do_municipio_existe_mesmo_sem_resposta(monkeypatch):
    """`None` = "não foi possível saber". Chave ausente viraria KeyError em quem lê."""
    c = _com_ocnt(monkeypatch, [_linha_end(CntyDlvryP="9999")], ocnt=[(1410, "Belo Horizonte")])
    linha = hana.fetch_status_pedidos()[0]

    assert "_MunicipioLocal" in linha and linha["_MunicipioLocal"] is None
    assert linha["_MunicipioPonto"] == "Belo Horizonte"
    assert c.sqls  # a consulta aconteceu; o código é que não casou


def test_ocnt_fora_do_ar_nao_derruba_o_recorte(monkeypatch):
    """O nome de uma cidade não vale derrubar a Situação dos Pedidos inteira."""
    c = _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS_END, linhas=[_linha_end()],
                                          erro_em='"OCNT"'))
    linhas = hana.fetch_status_pedidos()

    assert len(linhas) == 1 and linhas[0]["DocNum"] == 84260   # o recorte veio
    assert linhas[0]["_MunicipioLocal"] is None                # só o nome faltou
    assert c.fechada, "a conexão tem de fechar mesmo com a OCNT falhando"


def test_a_ocnt_roda_antes_de_fechar_a_conexao(monkeypatch):
    """Uma conexão por leitura é regra do módulo: nada de abrir outra só para a OCNT."""
    idas: list[int] = []
    _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS_END, linhas=[_linha_end()],
                                      ocnt=[(1763, "Juiz de Fora")]), contador=idas)
    hana.fetch_status_pedidos()
    assert len(idas) == 1


def test_o_municipio_entra_no_cache_do_recorte(monkeypatch):
    """Sem cache próprio: o nome viaja nas linhas, que já são cacheadas por 120 s."""
    idas: list[int] = []
    _ligar(monkeypatch, _ConexaoFalsa(colunas=COLUNAS_END, linhas=[_linha_end()],
                                      ocnt=[(1763, "Juiz de Fora")]), contador=idas)
    hana.fetch_status_pedidos()
    segunda = hana.fetch_status_pedidos()
    assert len(idas) == 1, "a 2ª leitura não pode ir ao HANA de novo"
    assert segunda[0]["_MunicipioLocal"] == "Juiz de Fora"
