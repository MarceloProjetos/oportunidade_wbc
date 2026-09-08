"""Erros do Service Layer.

O Service Layer devolve erros num envelope JSON no formato::

    {"error": {"code": -5002, "message": {"lang": "en-us", "value": "..."}}}

Este módulo traduz esse envelope numa exceção Python legível, preservando o
código numérico do SAP — que é o que permite distinguir programaticamente, por
exemplo, "CNPJ já existe" de "sessão expirada".
"""

from __future__ import annotations

from typing import Any

import httpx


class ServiceLayerError(RuntimeError):
    """Erro devolvido pelo Service Layer do SAP Business One."""

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
        """True quando o erro indica sessão inválida/expirada.

        O Service Layer sinaliza isso com HTTP 401 — e, em algumas versões, com
        o código -304 mesmo sob outro status.
        """
        return self.status_code == 401 or str(self.sap_code) == "-304"


class ServiceLayerLoginError(ServiceLayerError):
    """Falha de autenticação no Service Layer (usuário, senha ou company DB)."""


def _extrai_valor_mensagem(bloco: Any) -> str | None:
    """Extrai o texto da mensagem, que pode vir como str ou como {"value": ...}."""
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
    """Constrói a exceção adequada a partir de uma resposta de erro.

    É tolerante a respostas que não sejam o envelope esperado (HTML de proxy,
    corpo vazio, JSON malformado) — nesses casos usa o texto bruto, truncado.
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
