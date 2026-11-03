"""HTTP client for the SAP Business One Service Layer.

Replaces the legacy system's DI-API connection (`Company.Connect()`) with session-authenticated
REST calls.

Design points:

* **A single exit door for data.** Every data request goes through `request()`, and the
  safety lock (`assert_http_call_allowed`: production writes only from the production machine
  IP, see `wbcpython/safety.py`) is applied there and only there. No other module needs to
  remember to check the environment before writing. The two exceptions are `login()` and
  `logout()`, which talk to the transport directly out of necessity (going through
  `request()` would be recursive, since it is what triggers the login). They write no
  business data; any new method must still go through `request()`.
* **Session renewed automatically.** The Service Layer expires the session on inactivity;
  on a session-expired error the client logs in once more and repeats the request,
  transparently.
* **Login only when there is something to ask.** Entering the `with` does not authenticate:
  login happens on the first request (`request()`), and `logout` on exit only if a login
  happened. A worker cycle with no write (most of them) never touches the Service Layer
  (Marcelo's decision, 2026-09-08: ~260 logins/day just to find there was nothing to do).
  The opportunities read comes from HANA.
* **Credentials never appear in logs or error messages.**
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import Any, Self

import httpx

from wbcpython.config import ServiceLayerSettings
from wbcpython.infrastructure.service_layer.errors import (
    ServiceLayerError,
    ServiceLayerLoginError,
    erro_de_resposta,
)
from wbcpython.safety import assert_http_call_allowed

logger = logging.getLogger(__name__)

#: Registros por requisição ao paginar. O padrão do Service Layer é 20; este
#: valor vai no cabeçalho `Prefer: odata.maxpagesize`, que ele respeita.
#: Cem é o meio-termo entre número de idas ao servidor e tamanho de resposta.
TAMANHO_DE_PAGINA = 100


class ServiceLayerClient:
    """Service Layer session client.

    Recommended use, as a context manager (guarantees the logout):

        with ServiceLayerClient(settings.service_layer,
                                production_company_db=settings.production_company_db) as sl:
            dados = sl.get_json("OrcDetalhe", params={"$top": 1})

    `block_production_writes` is `settings.block_production_writes` in real callers (True
    unless running on the production machine); the default is the safe one.
    """

    def __init__(
        self,
        settings: ServiceLayerSettings,
        *,
        production_company_db: str,
        block_production_writes: bool = True,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._production_company_db = production_company_db
        self._block_production_writes = block_production_writes
        self._autenticado = False

        self._http = httpx.Client(
            base_url=settings.base_url.rstrip("/"),
            verify=settings.verify,
            timeout=settings.timeout_seconds,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    # ------------------------------------------------------------------ sessão

    @property
    def autenticado(self) -> bool:
        return self._autenticado

    @property
    def company_db(self) -> str:
        return self._settings.company_db

    def login(self) -> None:
        """Authenticates and keeps the session cookie (B1SESSION/ROUTEID)."""
        if not self._settings.username or not self._settings.password.get_secret_value():
            raise ServiceLayerLoginError(
                "Usuário e/ou senha do Service Layer não configurados. "
                "Preencha SL_USERNAME e SL_PASSWORD no .env "
                "(veja COMO_INICIAR.md).",
                status_code=0,
            )

        # O corpo do login carrega a senha: nunca deve ser logado.
        resposta = self._http.post(
            "/Login",
            json={
                "CompanyDB": self._settings.company_db,
                "UserName": self._settings.username,
                "Password": self._settings.password.get_secret_value(),
            },
        )

        if resposta.status_code >= 400:
            self._autenticado = False
            raise erro_de_resposta(resposta, metodo="POST", caminho="/Login", login=True)

        self._autenticado = True
        logger.info(
            "Sessão do Service Layer estabelecida (company_db=%s).",
            self._settings.company_db,
        )

    def logout(self) -> None:
        """Ends the session. Never raises: it is called on shutdown."""
        if not self._autenticado:
            return
        try:
            self._http.post("/Logout")
        except httpx.HTTPError as exc:
            logger.warning("Falha ao encerrar a sessão do Service Layer: %s", exc)
        finally:
            self._autenticado = False

    def close(self) -> None:
        self.logout()
        self._http.close()

    def __enter__(self) -> Self:
        # Sem login aqui: `request()` autentica na primeira chamada de verdade.
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ----------------------------------------------------------- requisições

    def request(
        self,
        metodo: str,
        caminho: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Runs a request. **The client's only HTTP exit door.**

        Applies the safety lock, ensures an active session and renews it automatically when
        it expires.
        """
        metodo = metodo.upper()

        # Trava de segurança — antes de qualquer coisa sair da máquina.
        assert_http_call_allowed(
            metodo,
            self._settings.company_db,
            production_company_db=self._production_company_db,
            block_production_writes=self._block_production_writes,
        )

        if not self._autenticado:
            self.login()

        resposta = self._enviar(metodo, caminho, params=params, json=json, headers=headers)

        if resposta.status_code >= 400:
            erro = erro_de_resposta(resposta, metodo=metodo, caminho=caminho)

            # A renovação é decidida pelo erro, não só pelo status: o Service
            # Layer sinaliza sessão inválida com 401, mas em algumas versões
            # devolve o código -304 sob outro status. Testar apenas 401 deixava
            # justamente esse caso sem retry.
            if erro.sessao_expirada:
                logger.info("Sessão do Service Layer expirada; refazendo login.")
                self._autenticado = False
                self.login()
                resposta = self._enviar(metodo, caminho, params=params, json=json, headers=headers)
                if resposta.status_code >= 400:
                    raise erro_de_resposta(resposta, metodo=metodo, caminho=caminho)
                return resposta

            raise erro

        return resposta

    def _enviar(
        self,
        metodo: str,
        caminho: str,
        *,
        params: dict[str, Any] | None,
        json: Any | None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return self._http.request(
            metodo,
            "/" + caminho.lstrip("/"),
            params=params,
            json=json,
            headers=headers,
        )

    # ------------------------------------------------------------ conveniência

    def get(
        self,
        caminho: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return self.request("GET", caminho, params=params, headers=headers)

    def get_json(
        self,
        caminho: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Reads the response as JSON, turning an invalid body into a readable error.

        A `200` that is not JSON is the classic symptom of a wrong `SL_BASE_URL`, a proxy or
        a captive portal in the path. Without this handling `.json()` blew up with a raw
        `JSONDecodeError`, exactly when the person most needs a hint about what is wrong.
        """
        resposta = self.get(caminho, params=params, headers=headers)
        try:
            return resposta.json()
        except ValueError as exc:
            amostra = (resposta.text or "")[:200].strip()
            raise ServiceLayerError(
                f"Resposta não é JSON (content-type: "
                f"{resposta.headers.get('content-type', 'desconhecido')}). "
                f"Verifique se SL_BASE_URL aponta mesmo para o Service Layer e se não há "
                f"proxy no caminho. Início da resposta: {amostra!r}",
                status_code=resposta.status_code,
                metodo="GET",
                caminho=caminho,
            ) from exc

    def post(self, caminho: str, *, json: Any) -> httpx.Response:
        return self.request("POST", caminho, json=json)

    def patch(
        self, caminho: str, *, json: Any, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        return self.request("PATCH", caminho, json=json, headers=headers)

    def delete(self, caminho: str) -> httpx.Response:
        return self.request("DELETE", caminho)

    # -------------------------------------------------------------- utilidades

    def listar(
        self,
        entidade: str,
        *,
        filtro: str | None = None,
        ordenar_por: str | None = None,
        top: int | None = None,
        selecionar: str | None = None,
        tamanho_de_pagina: int = TAMANHO_DE_PAGINA,
    ) -> list[dict[str, Any]]:
        """OData query that **walks through the Service Layer pagination**.

        The SL returns at most 20 records per response and `$top` does not break that limit:
        asking `$top=100` returns 20. Worse, when `$top` is in the query the SL **omits**
        `@odata.nextLink`, so the response looks complete. That is how the cycle came to scan
        only the 20 most recent opportunities of a window with 1,785, with nothing in the log
        showing the cut.

        The way out is the `Prefer: odata.maxpagesize` header, which the SL honors, and a
        `$skip` loop until a page comes back short. `$skip` over a key `$orderby` is stable:
        the ordering is by identifier, not by date.

        `top` stays the **ceiling** of returned records, now a real one instead of a number
        the server silently ignores.
        """
        coletados: list[dict[str, Any]] = []

        while True:
            if top is not None and len(coletados) >= top:
                break

            pagina = tamanho_de_pagina
            if top is not None:
                pagina = min(pagina, top - len(coletados))

            params: dict[str, Any] = {"$top": pagina}
            if coletados:
                params["$skip"] = len(coletados)
            if filtro:
                params["$filter"] = filtro
            if ordenar_por:
                params["$orderby"] = ordenar_por
            if selecionar:
                params["$select"] = selecionar

            valores = self._pagina(entidade, params, pagina)
            coletados.extend(valores)

            # Página curta significa fim: não há o que buscar adiante.
            if len(valores) < pagina:
                break

        return coletados

    def _pagina(self, entidade: str, params: dict[str, Any], tamanho: int) -> list[dict[str, Any]]:
        corpo = self.get_json(
            entidade,
            params=params,
            headers={"Prefer": f"odata.maxpagesize={tamanho}"},
        )
        if isinstance(corpo, dict):
            valores = corpo.get("value")
            if isinstance(valores, list):
                return valores
        raise ServiceLayerError(
            f"Resposta inesperada ao listar '{entidade}': esperava um objeto com 'value'.",
            status_code=200,
            metodo="GET",
            caminho=entidade,
        )

    def testar_conexao(self) -> str:
        """Connectivity smoke test: logs in and does a minimal read.

        Returns a short description of the result, suited to display in the CLI.
        """
        self.login()
        self.listar("OrcDetalhe", top=1, selecionar="DocEntry")
        return f"Conexão OK com {self._settings.base_url} (company_db={self._settings.company_db})."
