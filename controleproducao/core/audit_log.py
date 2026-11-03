"""Audit log — equivalent to the `INO_LOG` UDO written by
`ProcessDefault.preencheLog(oComp, OrcNum, tipo, status, op, mensagem)` in the original addon.

Called profusely in the `catch` blocks of all the legacy C#. In phase 1 (1:1 port) we keep the
same behavior: a log failure never brings down the main flow.
"""
from __future__ import annotations

import logging

from controleproducao.core.service_layer_client import ServiceLayerClient

logger = logging.getLogger(__name__)

_UDO_NAME = "INO_LOG"


async def preenche_log(
    sl: ServiceLayerClient,
    orc_num: str,
    tipo: str,
    status: str,
    op: str,
    mensagem: str,
) -> None:
    """Write one row to the INO_LOG UDO.

    Fields (same names as the C# addon):
        U_OrcNum, U_TipoDocumento, U_Status, U_Tipo, U_Mensagem
    """
    body = {
        "U_OrcNum": orc_num,
        "U_TipoDocumento": tipo,
        "U_Status": status,
        "U_Tipo": op,
        "U_Mensagem": mensagem,
    }
    try:
        await sl.create_entity(_UDO_NAME, body)
    except Exception:  # noqa: BLE001 - mesmo comportamento do catch vazio no C# original
        logger.exception(
            "Falha ao gravar log de auditoria (INO_LOG) — orc_num=%s tipo=%s status=%s", orc_num, tipo, status
        )
