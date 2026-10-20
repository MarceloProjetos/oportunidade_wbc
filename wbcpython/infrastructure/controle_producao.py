"""Is the Controle de Produção (8080, same machine) in the middle of a run?

The partner swap cancels the planned OPs of an order before cancelling the order
(`docs/PLANO_TROCA_PN_COM_OP.md`). The Controle de Produção marks
`U_INO_ProcessWBC = 'Y'` before its first OP and a run takes seconds to minutes (84506:
25 s), so the worker must not cancel while it runs: OPs created after the read would end
up on a cancelled order. `GET /health/ocupado` is the open route `deploy_update.bat`
already uses for the same question.
"""

from __future__ import annotations

import http.client
import urllib.error
import urllib.request

_SEM_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def controle_producao_ocupado(porta: int, *, timeout_s: float = 3.0) -> bool | None:
    """``True`` busy, ``False`` idle, ``None`` unknown (the caller must wait).

    Connection refused means the service is down, so nothing is being processed: idle.
    A timeout or an odd answer is unknown, never idle.
    """
    try:
        with _SEM_PROXY.open(f"http://127.0.0.1:{int(porta)}/health/ocupado", timeout=timeout_s) as r:
            corpo = r.read(16).decode("ascii", "replace").strip()
    except urllib.error.URLError as exc:
        return False if isinstance(exc.reason, ConnectionRefusedError) else None
    except ConnectionRefusedError:
        return False
    except (OSError, http.client.HTTPException):
        # `HTTPException` (bad status line, truncated body) is not an OSError.
        return None
    return {"1": True, "0": False}.get(corpo)
