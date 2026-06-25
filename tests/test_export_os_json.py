"""Testes do exportador JSON de Ordens de Serviço (funções puras, sem rede)."""

import pytest

from export_os_json import build_payload, transform_rows, export_os


def _rows():
    return [
        {'NPED': 84080, 'Status': 'R', 'InfoAdicPED': 'x' * 1000,
         'ComposicaoPED': 'y', 'TotalOrcam': 10.5},
        {'NPED': 84080, 'Status': 'Z', 'InfoAdicPED': None,
         'ComposicaoPED': None, 'TotalOrcam': 2.0},
    ]


def test_transform_slim_removes_nclob_keeps_rest():
    out = transform_rows(_rows(), slim=True, with_status=False)
    assert 'InfoAdicPED' not in out[0]
    assert 'ComposicaoPED' not in out[0]
    assert out[0]['NPED'] == 84080
    assert out[0]['TotalOrcam'] == 10.5


def test_transform_adds_status_desc():
    out = transform_rows(_rows(), with_status=True, status_map={'R': 'Liberado'})
    assert out[0]['status_desc'] == 'Liberado'
    assert out[1]['status_desc'] is None  # 'Z' não está no lookup


def test_transform_without_status_does_not_add_field():
    out = transform_rows(_rows(), with_status=False)
    assert 'status_desc' not in out[0]


def test_transform_does_not_mutate_input():
    rows = _rows()
    transform_rows(rows, slim=True)
    assert 'InfoAdicPED' in rows[0]  # original intacto


def test_build_payload_array_returns_list():
    out = build_payload(_rows(), table='t', filter_desc={'nped': [84080]}, as_array=True)
    assert isinstance(out, list) and len(out) == 2


def test_build_payload_envelope_has_metadata():
    out = build_payload(_rows(), table='ordens_servico_engenharia',
                        filter_desc={'nped': [84080]})
    assert out['source_table'] == 'ordens_servico_engenharia'
    assert out['count'] == 2
    assert out['filter'] == {'nped': [84080]}
    assert 'exported_at' in out and isinstance(out['rows'], list)


def test_export_os_requires_npeds_or_all():
    with pytest.raises(ValueError):
        export_os()  # nem npeds nem all_rows
