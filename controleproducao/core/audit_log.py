"""Log de auditoria — equivalente ao UDO `INO_LOG` gravado por
`ProcessDefault.preencheLog(oComp, OrcNum, tipo, status, op, mensagem)` no addon original.

Chamado profusamente nos `catch` de todo o C# legado. Na fase 1 (portar 1:1), mantemos o
mesmo comportamento: nunca deixamos uma falha de log derrubar o fluxo principal.
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
    """Grava uma linha no UDO INO_LOG.

    Campos (mesmos nomes do addon C#):
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
