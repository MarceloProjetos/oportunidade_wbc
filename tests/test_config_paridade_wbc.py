"""Os defaults do worker WBC existem em DOIS configs e não podem divergir.

O ``config.py`` da raiz relê as variáveis do worker (``TRACKING_DB_URL``, ``WORKER_*``,
``PAINEL_PORTA``) para o check ``wbc_worker`` do ``/status``, com cópias próprias dos
defaults. Se uma cópia andar e a outra não, um ``.env`` sem a variável faz o monitor
avaliar outro banco, outro expediente ou outra porta que não os do worker — sem erro
nenhum. Os defaults são lidos da DECLARAÇÃO do campo, não de uma instância: o ``.env``
da máquina não entra na comparação.
"""

from datetime import time

import config
from wbcpython.config import Settings, TrackingSettings


def _default(classe, campo: str):
    return classe.model_fields[campo].default


def test_banco_de_acompanhamento_e_o_mesmo():
    wbc = _default(TrackingSettings, "db_url").get_secret_value()
    assert wbc == config.WBC_TRACKING_DB_URL_DEFAULT


def test_intervalo_do_worker_e_o_mesmo():
    assert _default(Settings, "worker_interval_seconds") == config.WBC_WORKER_INTERVAL_S_DEFAULT


def test_expediente_do_worker_e_o_mesmo():
    assert _default(Settings, "worker_horario_inicio") == time.fromisoformat(
        config.WBC_WORKER_HORARIO_INICIO_DEFAULT
    )
    assert _default(Settings, "worker_horario_fim") == time.fromisoformat(config.WBC_WORKER_HORARIO_FIM_DEFAULT)
    assert _default(Settings, "worker_dias_de_trabalho") == config.WBC_WORKER_DIAS_DEFAULT


def test_porta_do_painel_e_a_mesma():
    assert _default(Settings, "painel_porta") == config.WBC_PAINEL_PORTA_DEFAULT
