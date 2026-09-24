"""Espelho da VW_EVOL_ORCAMENTO_ALT — o que a carga não pode errar.

O alvo é o que decide DADO, não o que fala com banco:

1. O SELECT traz as 34 colunas da view, já em ``snake_case`` do Postgres — o
   espelho existe para os campos novos (CNAE, montagem, nota fiscal);
2. ``nf_quitada`` é booleano de TRÊS estados: ``NULL`` sem nota. O ``QuitacaoNF``
   cru diz "Não" para 4.310 das 5.643 linhas só porque nota nenhuma foi emitida
   — gravar isso como "em aberto" erraria por um fator de três;
3. Texto vazio vira ``NULL`` (a view devolve ``''`` em AcaoContato/Lead/
   SituacaoCliente, nunca nulo);
4. **View vazia não poda**: snapshot com zero linha apagaria a tabela inteira;
5. Insert que falha também não poda — a tabela fica com o snapshot anterior.
"""

from __future__ import annotations

import pytest

import extract_orcamentos_espelho as etl

COLUNAS = (
    "cotacao", "tipo_doc", "num_doc", "num_oport", "status_wbc", "n_wbc", "versao",
    "data_criacao_pn", "cod_pn", "nome_pn", "contato_cliente", "email_cliente",
    "representante", "valor", "data_oport", "data_cotacao", "uf", "municipio",
    "data_contato_cliente", "acao_contato", "lead", "situacao_cliente", "n_bitrix",
    "pct_comissao", "retorno", "indice", "cnae", "descricao_cnae", "tipo_montagem",
    "valor_montagem", "montador", "num_nf", "data_nf", "nf_quitada",
)


# ── O SELECT ─────────────────────────────────────────────────────────────────

def test_sql_traz_as_34_colunas_em_snake_case() -> None:
    sql = etl.sql_orcamentos("SBOALTAMIRAPROD")
    for coluna in COLUNAS:
        assert f'AS "{coluna}"' in sql, f"faltou {coluna}"
    assert 'FROM "SBOALTAMIRAPROD"."VW_EVOL_ORCAMENTO_ALT"' in sql


def test_sql_deriva_nf_quitada_com_nulo_quando_nao_ha_nota() -> None:
    sql = etl.sql_orcamentos("SBOALTAMIRAPROD")
    assert 'CASE WHEN "NumNF" IS NULL THEN NULL' in sql
    assert '"QuitacaoNF" = \'Sim\' THEN TRUE' in sql


def test_sql_converte_texto_vazio_em_nulo() -> None:
    sql = etl.sql_orcamentos("SBOALTAMIRAPROD")
    for coluna in ("AcaoContato", "Lead", "SituacaoCliente"):
        assert f'NULLIF(TRIM("{coluna}"), \'\')' in sql


def test_sql_recusa_schema_invalido() -> None:
    with pytest.raises(ValueError):
        etl.sql_orcamentos("SBO; drop table x")


# ── A carga ──────────────────────────────────────────────────────────────────

class _Loader:
    """Dublê do SupabaseLoader: registra o que foi chamado."""

    def __init__(self, insert_ok: bool = True, poda_ok: bool = True) -> None:
        self.insert_ok, self.poda_ok = insert_ok, poda_ok
        self.inseridas: list = []
        self.podas: list = []
        self.rotinas: list = []

    def insert_data(self, tabela, registros, *a, **kw):
        self.inseridas.append((tabela, len(registros)))
        return self.insert_ok

    def delete_other_executions(self, tabela, exec_id):
        self.podas.append((tabela, exec_id))
        return self.poda_ok

    def registrar_rotina(self, nome, rotulo, **kw):
        self.rotinas.append((nome, kw.get("sucesso"), kw.get("erro")))
        return True


class _Extractor:
    """Dublê do SAPExtractor com um DataFrame pronto (ou None/vazio)."""

    def __init__(self, df) -> None:
        self.df = df

    def connect(self):
        return True

    def execute_query(self, sql):
        return self.df

    def close(self):
        return None


def _settings():
    class S:
        sap_host, sap_port, sap_user = "h", 30015, "u"
        sap_password, sap_database, sap_schema = "p", "d", "SBOALTAMIRAPROD"
    return S()


def _plantar(monkeypatch, df) -> None:
    monkeypatch.setattr(etl, "SAPExtractor", lambda *a, **kw: _Extractor(df))


def test_carga_insere_e_so_depois_poda(monkeypatch) -> None:
    import pandas as pd

    _plantar(monkeypatch, pd.DataFrame([{"cotacao": 1}, {"cotacao": 2}]))
    loader = _Loader()
    ok, linhas = etl._carga(loader, _settings(), "exec-1", [])

    assert ok is True and linhas == 2
    assert loader.inseridas == [(etl.TABELA, 2)]
    assert loader.podas == [(etl.TABELA, "exec-1")], "a poda usa o id desta execução"


def test_view_vazia_nao_poda_a_tabela(monkeypatch) -> None:
    import pandas as pd

    _plantar(monkeypatch, pd.DataFrame([]))
    loader = _Loader()
    falhas: list = []
    ok, linhas = etl._carga(loader, _settings(), None, falhas)

    assert ok is False and linhas == 0
    assert loader.inseridas == [] and loader.podas == []
    assert "view vazia" in falhas


def test_consulta_que_falha_nao_poda(monkeypatch) -> None:
    _plantar(monkeypatch, None)
    loader = _Loader()
    falhas: list = []
    assert etl._carga(loader, _settings(), None, falhas) == (False, 0)
    assert loader.podas == [] and falhas


def test_insert_que_falha_nao_poda(monkeypatch) -> None:
    import pandas as pd

    _plantar(monkeypatch, pd.DataFrame([{"cotacao": 1}]))
    loader = _Loader(insert_ok=False)
    falhas: list = []
    ok, _ = etl._carga(loader, _settings(), None, falhas)

    assert ok is False and loader.podas == []
    assert any("insert falhou" in f for f in falhas)


def test_poda_que_falha_e_erro_e_nao_aviso(monkeypatch) -> None:
    """Duas execuções na tabela = todo leitor vê tudo em dobro."""
    import pandas as pd

    _plantar(monkeypatch, pd.DataFrame([{"cotacao": 1}]))
    loader = _Loader(poda_ok=False)
    falhas: list = []
    ok, _ = etl._carga(loader, _settings(), None, falhas)

    assert ok is False
    assert any("poda" in f for f in falhas)


def test_scheduler_tem_o_job_do_espelho() -> None:
    from scripts import scheduled_execution

    assert hasattr(scheduled_execution, "job_orcamentos_espelho")
    assert scheduled_execution.ORCAMENTOS_ESPELHO_INTERVALO_MIN == 60
