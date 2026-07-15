"""Testes do núcleo compartilhado (pipeline_core)."""

import logging
from types import SimpleNamespace

import pytest

import pipeline_core
from pipeline_core import coerce_positive_int


@pytest.mark.parametrize('value,expected', [
    (84080, 84080),
    ('84080', 84080),
    ('  84080  ', 84080),   # espaços nas pontas
    ('0084080', 84080),     # zeros à esquerda
    (1, 1),
])
def test_coerce_positive_int_aceita_validos(value, expected):
    assert coerce_positive_int(value) == expected


@pytest.mark.parametrize('value', [
    '-5', '0', '000', '+5', '84080.0', 'abc', '', '1 OR 1=1', '84080; DROP', None,
])
def test_coerce_positive_int_rejeita_invalidos(value):
    with pytest.raises(ValueError):
        coerce_positive_int(value, what='NPED')


# ===================== Guarda de schema (view × tabela) =====================
# Regressão do incidente de 2026-07-15: a view ganhou "U_INO_ORCITM" e TODA sync
# morreu com PGRST204 após 3 retries e um erro genérico. A guarda tem de pegar isso
# ANTES de inserir e dizer o que fazer.

class _FakeTable:
    """Tabela falsa do supabase-py: devolve `linha` no select e registra inserts."""

    def __init__(self, linha, inserts):
        self._linha, self._inserts = linha, inserts

    def select(self, *_a, **_k):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        return SimpleNamespace(data=([self._linha] if self._linha is not None else []))

    def insert(self, lote):
        self._inserts.append(lote)
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=lote))


def _loader_fake(linha_da_tabela):
    """SupabaseLoader com client falso; `linha_da_tabela=None` simula tabela vazia."""
    loader = pipeline_core.SupabaseLoader.__new__(pipeline_core.SupabaseLoader)
    inserts = []
    loader.client = SimpleNamespace(table=lambda _t: _FakeTable(linha_da_tabela, inserts))
    return loader, inserts


def test_guarda_pega_coluna_faltante_antes_de_inserir(caplog):
    """O caso real: origem tem U_INO_ORCITM, tabela não → falha SEM inserir nada."""
    loader, inserts = _loader_fake({'id': 1, 'N_PED': 1, 'Solda': 1})
    data = [{'N_PED': 84172, 'Solda': 1, 'U_INO_ORCITM': 'ABC', 'Pintura': 0}]

    with caplog.at_level(logging.ERROR):
        assert loader.insert_data('vw_os_integracao', data) is False

    assert inserts == []                      # não tentou inserir: falhou antes
    log = caplog.text
    assert 'U_INO_ORCITM' in log and 'Pintura' in log   # nomeia TODAS, não só a 1ª
    assert 'add column if not exists "U_INO_ORCITM" text' in log      # ALTER pronto
    assert 'add column if not exists "Pintura" integer' in log
    assert "notify pgrst, 'reload schema'" in log


def test_guarda_nao_atrapalha_o_caminho_feliz():
    """Schema alinhado → insere normalmente."""
    loader, inserts = _loader_fake({'id': 1, 'N_PED': 1, 'Solda': 1})
    assert loader.insert_data('vw_os_integracao', [{'N_PED': 84172, 'Solda': 1}]) is True
    assert len(inserts) == 1


def test_tabela_vazia_nao_bloqueia_a_carga():
    """Ponto cego assumido: sem linha não dá p/ saber as colunas → deixa inserir
    (o PGRST204 do insert volta a ser o diagnóstico). Nunca bloquear por dúvida."""
    loader, inserts = _loader_fake(None)
    assert loader.insert_data('vw_os_integracao', [{'N_PED': 84172, 'Qualquer': 1}]) is True
    assert len(inserts) == 1


def test_colunas_faltantes_ignora_extras_da_tabela():
    """Coluna que só a TABELA tem (id, inserted_at...) é inofensiva — tem default."""
    loader, _ = _loader_fake({'id': 1, 'N_PED': 1, 'inserted_at': 'x', 'origem_view': 'v'})
    assert loader.colunas_faltantes('t', [{'N_PED': 84172}]) == []


def test_erro_de_schema_nao_e_retentado():
    """PGRST204 é determinístico: retentar 3x só atrasa e esconde a mensagem."""
    assert pipeline_core.e_erro_de_schema(
        Exception("{'message': \"Could not find the 'X' column\", 'code': 'PGRST204'}")
    ) is True
    assert pipeline_core.e_erro_de_schema(Exception('connection reset by peer')) is False

    tentativas = []

    def _sempre_falha():
        tentativas.append(1)
        raise Exception("{'code': 'PGRST204'}")

    with pytest.raises(Exception):
        pipeline_core.with_retries(
            _sempre_falha, retry_on=pipeline_core._retry_se_transitorio, attempts=3,
        )
    assert len(tentativas) == 1          # falhou de primeira, sem backoff


@pytest.mark.parametrize('valor,esperado', [
    (1, 'integer'), (True, 'boolean'),          # bool antes de int (é subclasse)
    (1.5, 'numeric'), ('abc', 'text'),
    ('2026-07-15T00:00:00', 'timestamp'),
    (None, 'text'),                              # só nulos: text aceita tudo
])
def test_tipo_pg_sugerido(valor, esperado):
    assert pipeline_core._tipo_pg_sugerido([{'c': valor}], 'c') == esperado
