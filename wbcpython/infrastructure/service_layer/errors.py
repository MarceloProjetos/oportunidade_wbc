"""Service Layer errors.

The SL returns errors in a JSON envelope::

    {"error": {"code": -5002, "message": {"lang": "en-us", "value": "..."}}}

This module turns it into a readable exception that keeps SAP's numeric code, which is what
lets callers tell, e.g., "CNPJ already exists" from "session expired" programmatically.
"""

from __future__ import annotations

from typing import Any

import httpx


class ServiceLayerError(RuntimeError):
    """Error returned by the SAP Business One Service Layer."""

    def __init__(
        self,
        mensagem: str,
        *,
        status_code: int,
        sap_code: int | str | None = None,
        metodo: str | None = None,
        caminho: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.sap_code = sap_code
        self.metodo = metodo
        self.caminho = caminho
        self.mensagem = mensagem

        partes = [f"HTTP {status_code}"]
        if sap_code is not None:
            partes.append(f"SAP {sap_code}")
        if metodo and caminho:
            partes.append(f"{metodo} {caminho}")
        super().__init__(f"[{' | '.join(partes)}] {mensagem}")

    @property
    def sessao_expirada(self) -> bool:
        """True when the error means an invalid/expired session.

        The SL signals it with HTTP 401 and, in some versions, with SAP code -304 under another status.
        """
        return self.status_code == 401 or str(self.sap_code) == "-304"


class ServiceLayerLoginError(ServiceLayerError):
    """Authentication failure on the Service Layer (user, password or company DB)."""


def _extrai_valor_mensagem(bloco: Any) -> str | None:
    """Extracts the message text, which may come as a str or as `{"value": ...}`."""
    if isinstance(bloco, str):
        return bloco
    if isinstance(bloco, dict):
        valor = bloco.get("value")
        if isinstance(valor, str):
            return valor
    return None


def erro_de_resposta(
    resposta: httpx.Response,
    *,
    metodo: str,
    caminho: str,
    login: bool = False,
) -> ServiceLayerError:
    """Builds the right exception from an error response.

    Tolerates responses that are not the expected envelope (proxy HTML, empty body, malformed JSON):
    falls back to the raw text, truncated to 500 chars. `login=True` yields `ServiceLayerLoginError`.
    """
    sap_code: int | str | None = None
    mensagem: str | None = None

    try:
        corpo = resposta.json()
    except (ValueError, UnicodeDecodeError):
        corpo = None

    if isinstance(corpo, dict):
        erro = corpo.get("error")
        if isinstance(erro, dict):
            sap_code = erro.get("code")
            mensagem = _extrai_valor_mensagem(erro.get("message"))

    if not mensagem:
        bruto = (resposta.text or "").strip()
        mensagem = (bruto[:500] + "…") if len(bruto) > 500 else (bruto or "sem detalhes")

    classe = ServiceLayerLoginError if login else ServiceLayerError
    return classe(
        mensagem,
        status_code=resposta.status_code,
        sap_code=sap_code,
        metodo=metodo,
        caminho=caminho,
    )
