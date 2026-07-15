"""Testes do núcleo compartilhado (pipeline_core)."""

import inspect
import logging
from types import SimpleNamespace

import pandas as pd
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


# ===================== Timezone (regressão 2026-07-15) =====================
# Medido em produção: o log gravava `16:43:30+00` para um evento das 16:43 BRT — 3h no
# passado. `datetime.now().isoformat()` é NAIVE, e o Postgres assume UTC ao gravar em
# timestamptz. Confirmado no banco:
#   '16:43:30'::timestamptz       -> 16:43:30+00   (errado, o bug)
#   '16:43:30-03:00'::timestamptz -> 19:43:30+00   (certo)
#   '16:43:30-03:00'::timestamp   -> 16:43:30      (offset ignorado: coluna sem tz não muda)

def test_agora_iso_tem_offset():
    """O ponto todo: sem offset, o Postgres assume UTC e a hora vai 3h p/ trás."""
    from datetime import datetime as _dt
    valor = pipeline_core.agora_iso()
    d = _dt.fromisoformat(valor)          # não levanta = ISO válido
    assert d.tzinfo is not None, f'sem offset: {valor!r} — o bug de 3h volta'
    assert d.utcoffset() is not None


def test_prepare_data_grava_extracao_com_offset():
    """data_hora_extracao passa a sair com offset (a coluna é `timestamp` e ignora o
    offset, então nada muda lá — mas a função é a mesma do log, que é timestamptz)."""
    from datetime import datetime as _dt
    df = pd.DataFrame({'N_PED': [84080]})
    registros, _ = pipeline_core.prepare_data(df)
    d = _dt.fromisoformat(registros[0]['data_hora_extracao'])
    assert d.tzinfo is not None


def test_registrar_sincronizacao_recebe_hora_com_offset():
    """Guarda o call-site real: o valor que chega ao log tem de ser aware."""
    from datetime import datetime as _dt

    import extract_ordens_servico_engenharia as os_mod
    fonte = inspect.getsource(os_mod.main)
    assert 'agora_iso()' in fonte, 'o log de sync voltou a usar datetime.now() naive?'
    assert 'datetime.now().isoformat()' not in fonte
    # e a função de fato entrega offset
    assert _dt.fromisoformat(pipeline_core.agora_iso()).tzinfo is not None


# ============ Integridade: insert parcial, poda e NULL (regressão 2026-07-15) ============
# O único grupo de bugs que PERDIA ou CORROMPIA dado. Cada teste abaixo guarda um
# invariante que não existia.

class _TabelaFalha:
    """Insere o 1º lote e estoura no 2º — simula o lote N de M falhando."""

    def __init__(self, estado):
        self.e = estado

    def select(self, *_a, **_k):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        return SimpleNamespace(data=[self.e['linha_tabela']])

    def insert(self, lote):
        self.e['lotes'].append(lote)

        def _go():
            if len(self.e['lotes']) > 1:
                raise Exception('timeout no lote 2')
            return SimpleNamespace(data=lote)
        return SimpleNamespace(execute=_go)

    def delete(self):
        self.e['delete_chamado'] = True
        return self

    def eq(self, col, val):
        self.e['delete_filtro'] = (col, val)
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[{'id': 1}]))


def test_insert_parcial_reverte_o_que_ja_entrou(caplog):
    """Lote 2 falha → os registros do lote 1 (já gravados) são REMOVIDOS.

    Sem isto, o pedaço novo convivia com a execução anterior (a poda só roda no
    sucesso) e a leitura somava as duas: total_orcamento inflado.
    """
    estado = {'linha_tabela': {'id': 1, 'N_PED': 1, 'id_execucao': 'x'}, 'lotes': []}
    loader = pipeline_core.SupabaseLoader.__new__(pipeline_core.SupabaseLoader)
    loader.client = SimpleNamespace(table=lambda _t: _TabelaFalha(estado))
    data = [{'N_PED': 84080, 'id_execucao': 'exec-NOVA'} for _ in range(4)]

    with caplog.at_level(logging.WARNING):
        assert loader.insert_data('vw_os_integracao', data, batch_size=2) is False

    assert estado.get('delete_chamado') is True
    # apaga EXATAMENTE a execução desta carga — nunca dado bom de outra
    assert estado['delete_filtro'] == ('id_execucao', 'exec-NOVA')
    assert 'parciais desta execução removidas' in caplog.text


def test_poda_com_id_execucao_null_usa_or(monkeypatch):
    """`neq` sozinho NÃO apaga linha com id_execucao NULL (NULL <> 'x' é NULL em SQL,
    não TRUE): a órfã sobreviveria a TODA poda, para sempre. Tem de usar `or_`."""
    capturado = {}

    class _T:
        def delete(self):
            return self

        def or_(self, expr):
            capturado['or'] = expr
            return self

        def neq(self, *_a):
            capturado['neq_sozinho'] = True     # se cair aqui, o bug voltou
            return self

        def eq(self, col, val):
            capturado.setdefault('eq', []).append((col, val))
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    loader = pipeline_core.SupabaseLoader.__new__(pipeline_core.SupabaseLoader)
    loader.client = SimpleNamespace(table=lambda _t: _T())
    assert loader.delete_other_executions('t', 'exec-A', where_eq={'N_PED': 84080}) is True

    assert 'neq_sozinho' not in capturado, 'voltou ao neq puro: linha NULL nunca seria podada'
    assert capturado['or'] == 'id_execucao.is.null,id_execucao.neq.exec-A'
    assert capturado['eq'] == [('N_PED', 84080)]   # o where_eq continua em AND


def test_os_sync_lock_e_por_pedido_e_exclusivo(tmp_path, monkeypatch):
    """Dois "processos" no MESMO pedido: o 2º não entra (senão cada um podava o outro
    e o pedido sumia). Pedidos DIFERENTES não se bloqueiam."""
    monkeypatch.setattr(pipeline_core, '_LOCK_DIR', str(tmp_path))

    with pipeline_core.os_sync_lock(84080):
        # mesmo pedido, outro "processo" → recusa na hora (timeout=0)
        with pytest.raises(pipeline_core.FileLockTimeout):
            with pipeline_core.os_sync_lock(84080):
                pass
        # pedido diferente → passa (o invariante é 1 escritor por N_PED, não global)
        with pipeline_core.os_sync_lock(84095):
            pass


def test_os_sync_lock_valida_nped():
    """O nped compõe o nome do arquivo — lixo não vira caminho."""
    with pytest.raises(ValueError):
        with pipeline_core.os_sync_lock('../../etc/passwd'):
            pass
