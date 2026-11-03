"""HTTP client for the SAP Service Layer.

Broadly replaces the role of `SAPbobsCOM.Company` (DI API) in the original addon:
login/session, CRUD of business documents and UDO calls via `GeneralService`.

Concept mapping (see section 6.3 of migration_guide.md):

| DI API (C#)                                            | Service Layer (Python)                          |
|---------------------------------------------------------|--------------------------------------------------|
| `Company.Connect()`                                      | `ServiceLayerClient.login()` (lazy, on first request) |
| `oComp.GetBusinessObject(oOrders).Update()`               | `PATCH /Orders({DocEntry})`                        |
| `oComp.GetBusinessObject(oSalesOpportunities).Update()`   | `PATCH /SalesOpportunities({DocEntry})`            |
| `oComp.GetBusinessObject(oItems).Add()`                   | `POST /Items`                                      |
| `oComp.GetBusinessObject(oProductTrees).Add()`            | `POST /ProductTrees`                               |
| `oComp.GetBusinessObject(oProductionOrders).Add()`        | `POST /ProductionOrders`                           |
| `sCmp.GetGeneralService("X").GetByParams(...)`            | `GET /X('{key}')`                                  |
| `sCmp.GetGeneralService("X").Update(...)`                 | `PATCH /X('{key}')`                                |
| `sCmp.GetGeneralService("X").Add(...)`                    | `POST /X`                                          |
| `Recordset.DoQuery(sql)`                                  | `POST /SQLQueries('name')/List` (HANA view) or     |
|                                                            | `GET /Entity?$filter=...` for simple cases         |

Deliberately thin: each business module (pedidos_wbc, manutencao_op, ...) decides which
entities/UDOs to call; this file only handles session, headers and error handling.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from controleproducao.config import Settings
from controleproducao.core.exceptions import ServiceLayerAuthError, ServiceLayerError
from controleproducao.core.perf import PERFIL
from wbcpython import safety

logger = logging.getLogger(__name__)

# `Orders(19466)` / `Items('ABC%23')` -> `Orders(?)` / `Items(?)`, para agrupar no perfil.
_CHAVE_NA_ROTA = re.compile(r"\([^)]*\)")


def _json_safe(value: Any) -> Any:
    """Converts types `httpx`/`json.dumps` cannot serialize, coming from raw HANA/SQL Server
    reads (`hdbcli`/`pymssql` return `Decimal` for numeric columns and `date`/`datetime` for
    date columns), before building a Service Layer request body.

    Added 2026-09-15 while testing `pedidos-wbc processar-novos` against real homologation:
    `cria_ordem_producao` builds `PlannedQuantity` from a value read straight from HANA
    (`Decimal`), which the default `json.dumps` of `httpx` rejects (`TypeError: Object of
    type Decimal is not JSON serializable`). The C# original had no equivalent (the DI API
    takes `decimal`/`DateTime` natively on the COM properties, with no JSON step)."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _verificacao_tls(settings: Settings) -> bool | str:
    """What to pass as httpx's `verify`.

    2026-09-24, preparing the Windows deployment: `SL_CA_BUNDLE` had been documented in
    `.env.example` since the start but the client ignored it (only `SL_VERIFY_SSL` counted).
    On Windows it matters more because Python does not use the system certificate store, only
    the `certifi` bundle: a Service Layer with an internal-CA certificate left
    `SL_VERIFY_SSL=false` (verification off) as the only way out. With `SL_CA_BUNDLE` pointing
    to the CA `.pem` the connection is verified against it. `SL_VERIFY_SSL=false` still turns
    everything off (and wins).
    """
    if not getattr(settings, "sl_verify_ssl", True):
        return False
    ca_bundle = getattr(settings, "sl_ca_bundle", None)
    if ca_bundle:
        return ca_bundle
    return True


class ServiceLayerClient:
    """Async client: lazy login on the first request, a single re-login on 401 (expired
    session) and `/Logout` on `aclose()`.

    Typical use:
        async with ServiceLayerClient(settings) as sl:
            data = await sl.get("SalesOpportunities", params={"$filter": "..."})
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.sl_base_url,
            verify=_verificacao_tls(settings),
            timeout=float(getattr(settings, "sl_timeout_seconds", 60)),
        )
        self._logged_in = False

    async def __aenter__(self) -> ServiceLayerClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Logs out before closing the connection.

        The Service Layer caps sessions per user and the WBC worker on the .11 shares this
        user: one leaked session per task/command would eat the cap for everybody (SIS
        CLAUDE.md, "Sessão do Service Layer"). Straight ``post``, not ``_request`` — that
        would log in again. A failed logout is a warning, never an exception: ``__aexit__``
        must not mask the error of the block it closes, and the session expires on the SAP
        side anyway (~30 min of inactivity).
        """
        try:
            if self._logged_in:
                try:
                    await self._client.post("/Logout")
                except httpx.HTTPError as exc:
                    logger.warning("Logout da Service Layer falhou (a sessão expira sozinha): %s", exc)
                finally:
                    self._logged_in = False
        finally:
            await self._client.aclose()

    def _assegura_escrita(self, metodo: str) -> None:
        """The single production gate for CLI and web.

        Every write leaves through ``_request`` or ``_via_batch``, so this is the one place
        that can refuse it. Same rule as the WBC worker (``wbcpython.safety``): production
        writes only from the machine that owns ``PRODUCTION_MACHINE_IP``; no ``.env`` switch.
        GET stays free (``buscar``, ``comparar-ops``, ``diag`` read production from anywhere).
        Checked before ``login``: a refused write must not even open a session.
        """
        # `getattr` with the real production name as default: test stand-ins are bare
        # namespaces, and a stand-in must never make the gate more permissive.
        safety.assert_http_call_allowed(
            metodo,
            getattr(self._settings, "sl_company_db", None),
            production_company_db=getattr(self._settings, "wbc_production_company_db", "SBOALTAMIRAPROD"),
            block_production_writes=not safety.is_production_machine(),
        )

    # ------------------------------------------------------------------
    # Sessão
    # ------------------------------------------------------------------
    async def login(self) -> None:
        """Equivalent to `SAPbobsCOM.Company.Connect()`; normally implicit via `_ensure_login`.

        The Service Layer returns the `B1SESSION`/`ROUTEID` cookies, which httpx keeps in the
        `AsyncClient` cookie jar; nothing to handle manually.
        """
        payload = {
            "CompanyDB": self._settings.sl_company_db,
            "UserName": self._settings.sl_username,
            "Password": self._settings.sl_password,
        }
        resp = await self._client.post("/Login", json=payload)
        if resp.status_code != 200:
            raise ServiceLayerAuthError(
                f"Falha no login da Service Layer: {resp.status_code} {resp.text}",
                status_code=resp.status_code,
            )
        self._logged_in = True
        logger.info("Login na Service Layer efetuado (CompanyDB=%s)", self._settings.sl_company_db)

    async def _ensure_login(self) -> None:
        if not self._logged_in:
            await self.login()

    async def _request(self, method: str, path: str, *, retry_on_auth: bool = True, **kwargs: Any) -> httpx.Response:
        self._assegura_escrita(method)
        await self._ensure_login()
        # Rótulo sem a chave da entidade (`Orders(19466)` -> `Orders(?)`), para que todas as
        # chamadas à mesma rota caiam na mesma linha do relatório de perfil.
        rota = _CHAVE_NA_ROTA.sub("(?)", path)
        with PERFIL.medir("Service Layer", f"{method} {rota}"):
            resp = await self._client.request(method, path, **kwargs)

        if resp.status_code == 401 and retry_on_auth:
            # Sessão expirada (padrão SAP: ~30 min de inatividade) — refaz login uma vez.
            logger.warning("Sessão da Service Layer expirada, refazendo login...")
            self._logged_in = False
            await self._ensure_login()
            return await self._request(method, path, retry_on_auth=False, **kwargs)

        if resp.status_code >= 400:
            try:
                error_payload = resp.json()
            except ValueError:
                error_payload = {"raw": resp.text}
            message = error_payload.get("error", {}).get("message", {}).get("value", resp.text)
            raise ServiceLayerError(
                f"Service Layer retornou erro em {method} {path}: {message}",
                status_code=resp.status_code,
                payload=error_payload,
            )

        return resp

    # ------------------------------------------------------------------
    # Operações genéricas OData
    # ------------------------------------------------------------------
    async def get(self, entity_path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if _precisa_de_batch(entity_path):
            # No `$batch` não há objeto de request para carregar os parâmetros: a query
            # vai na própria URI da operação.
            caminho = entity_path
            if params:
                caminho += ("&" if "?" in caminho else "?") + urlencode(params)
            return await self._via_batch("GET", caminho) or {}
        resp = await self._request("GET", f"/{entity_path}", params=params)
        return resp.json()

    async def post(self, entity_path: str, body: dict[str, Any]) -> dict[str, Any]:
        if _precisa_de_batch(entity_path):
            return await self._via_batch("POST", entity_path, _json_safe(body)) or {}
        resp = await self._request("POST", f"/{entity_path}", json=_json_safe(body))
        return resp.json() if resp.content else {}

    async def patch(self, entity_path: str, body: dict[str, Any]) -> None:
        if _precisa_de_batch(entity_path):
            await self._via_batch("PATCH", entity_path, _json_safe(body))
            return
        await self._request("PATCH", f"/{entity_path}", json=_json_safe(body))

    # ------------------------------------------------------------------
    # Desvio pelo $batch — chaves que o servidor web recusa na URL
    # ------------------------------------------------------------------
    async def _via_batch(
        self, metodo: str, entity_path: str, body: dict[str, Any] | None = None, *, _relogin: bool = True
    ) -> dict[str, Any] | None:
        """Sends the request inside a `$batch`, where the URL travels in the BODY.

        **Why this exists.** Customer item codes contain a slash (`PPA3CPAB-1FF/D100220`).
        Percent-encoding is mandatory and correct (`Items('PPA3CPAB-1FF%2FD100220')`), but what
        rejects it is not the Service Layer: it is the web server in front of it, which by
        default blocks an encoded slash in the path and answers `404 Not Found` **in HTML**;
        the HTML error page is what gives the culprit away, since the Service Layer errors in
        JSON. Found 2026-09-22 on order 84391.

        In a `$batch` the operation URL is a line of the multipart body. The web server sees
        only `POST /$batch`, with nothing to inspect in the path, and the Service Layer gets
        the whole URI and decodes it itself. No server configuration changes.

        Chosen over `AllowEncodedSlashes` on the server because it does not depend on altering
        the Service Layer installation; both work, and if that setting is applied one day this
        detour simply stops being triggered.

        Not the `$batch` that groups several calls to save time (postponed by decision of
        16/09): here it is **one** operation per batch, for addressing, not performance.
        """
        self._assegura_escrita(metodo)
        await self._ensure_login()
        limite = f"batch_{uuid.uuid4().hex}"
        # Escrita vai em changeset (é o que a Service Layer documenta para operações de
        # modificação); leitura vai como parte solta, que é o formato para consulta.
        escrita = metodo in {"POST", "PATCH", "PUT", "DELETE"}
        corpo = _monta_corpo_batch(limite, metodo, f"{self._prefixo}/{entity_path}", body, escrita)

        with PERFIL.medir("Service Layer", f"$batch {metodo} {_CHAVE_NA_ROTA.sub('(?)', entity_path)}"):
            resp = await self._client.post(
                "/$batch",
                content=corpo.encode("utf-8"),
                headers={"Content-Type": f"multipart/mixed;boundary={limite}"},
            )

        status, payload, bruto = (
            (resp.status_code, None, resp.text) if resp.status_code >= 400 else _le_resposta_batch(resp.text)
        )

        if status == 401 and _relogin:
            # Sessão expirada: o 401 vem DENTRO do lote (ou no próprio POST /$batch), e o
            # tratamento do `_request` não o enxerga. Refaz o login e tenta UMA vez, como lá —
            # the replay carries `_relogin=False`: an SL that keeps answering 401 used to
            # recurse, and every round was a new /Login never logged out (the session cap).
            logger.warning("Sessão da Service Layer expirada (no $batch), refazendo login...")
            self._logged_in = False
            await self._ensure_login()
            return await self._via_batch(metodo, entity_path, body, _relogin=False)

        if resp.status_code >= 400:
            raise ServiceLayerError(
                f"Service Layer retornou erro no $batch de {metodo} {entity_path}: {resp.text}",
                status_code=resp.status_code,
            )

        if status is None or status >= 400:
            mensagem = ""
            if isinstance(payload, dict):
                mensagem = payload.get("error", {}).get("message", {}).get("value", "")
            raise ServiceLayerError(
                f"Service Layer retornou erro em {metodo} {entity_path} (via $batch): "
                f"{mensagem or bruto}",
                status_code=status,
                payload=payload if isinstance(payload, dict) else {"raw": bruto},
            )

        return payload if isinstance(payload, dict) else None

    @property
    def _prefixo(self) -> str:
        """Service Layer base path (`/b1s/v1`), required in the URIs inside the `$batch`."""
        return httpx.URL(self._settings.sl_base_url).path.rstrip("/")

    # ------------------------------------------------------------------
    # Helpers de alto nível usados pelos módulos de negócio
    # ------------------------------------------------------------------
    async def get_by_key(self, entity: str, key: str | int, chave_texto: bool = False) -> dict[str, Any]:
        """Equivalent to `oObj.GetByKey(key)` followed by reading the fields.

        `chave_texto=True` for string-key entities (`Items`, UDOs with `Code`) — see
        `_formata_chave`."""
        return await self.get(f"{entity}({_formata_chave(key, chave_texto)})")

    async def update_entity(
        self, entity: str, key: str | int, fields: dict[str, Any], chave_texto: bool = False
    ) -> None:
        """Equivalent to `oObj.GetByKey(key); oObj.UserFields...Value = ...; oObj.Update();`.

        `chave_texto=True` for string-key entities (`Items`, UDOs with `Code`)."""
        await self.patch(f"{entity}({_formata_chave(key, chave_texto)})", fields)

    async def create_entity(self, entity: str, body: dict[str, Any]) -> dict[str, Any]:
        """Equivalent to `oObj.Add()` after filling in the fields."""
        return await self.post(entity, body)


# Sequências que servidores web recusam no CAMINHO da URL, mesmo corretamente codificadas.
# `%2F` (barra) é o caso real: por padrão o Apache responde 404 antes de repassar à
# Service Layer. `%5C` (contrabarra) entra pelo mesmo motivo — não apareceu ainda, mas
# cai na mesma regra do servidor, e descobrir isso em produção custa uma investigação.
# O `%23` (`#`) NÃO está aqui de propósito: aquele passa e já é resolvido pelo encoding.
_SEQUENCIAS_RECUSADAS_NO_CAMINHO = ("%2F", "%2f", "%5C", "%5c")


def _precisa_de_batch(caminho: str) -> bool:
    return any(seq in caminho for seq in _SEQUENCIAS_RECUSADAS_NO_CAMINHO)


def _monta_corpo_batch(
    limite: str, metodo: str, uri: str, body: dict[str, Any] | None, escrita: bool
) -> str:
    """A batch with a single operation, in the Service Layer's multipart format.

    CRLF line breaks: required by MIME, and the Service Layer is strict about it — with bare
    LF it returns a parsing error without saying where.
    """
    corpo_json = json.dumps(body, ensure_ascii=False) if body is not None else None
    pedido = [f"{metodo} {uri} HTTP/1.1"]
    if corpo_json is not None:
        pedido.append("Content-Type: application/json")
    pedido.append("")
    if corpo_json is not None:
        # Linha em branco depois do corpo: pelo MIME o CRLF anterior à fronteira já
        # pertence a ela, mas os exemplos da própria Service Layer trazem a linha extra e
        # um `\r\n` sobrando não atrapalha a leitura do JSON.
        pedido.extend([corpo_json, ""])

    if not escrita:
        partes = [
            f"--{limite}",
            "Content-Type: application/http",
            "Content-Transfer-Encoding: binary",
            "",
            *pedido,
            f"--{limite}--",
            "",
        ]
        return "\r\n".join(partes)

    conjunto = f"changeset_{uuid.uuid4().hex}"
    partes = [
        f"--{limite}",
        f"Content-Type: multipart/mixed;boundary={conjunto}",
        "",
        f"--{conjunto}",
        "Content-Type: application/http",
        "Content-Transfer-Encoding: binary",
        "Content-ID: 1",
        "",
        *pedido,
        f"--{conjunto}--",
        "",
        f"--{limite}--",
        "",
    ]
    return "\r\n".join(partes)


def _le_resposta_batch(texto: str) -> tuple[int | None, Any, str]:
    """Extracts `(status, payload, raw)` from the first operation of the batch.

    The response is multipart and may nest a changeset, so instead of parsing the boundaries
    we look for the first HTTP status line — there is a single operation per batch (see
    `_via_batch`), and that line is what matters.
    """
    achado = re.search(r"^HTTP/1\.[01] (\d{3})", texto, flags=re.MULTILINE)
    status = int(achado.group(1)) if achado else None

    payload: Any = None
    inicio = texto.find("{", achado.end() if achado else 0)
    if inicio != -1:
        # O JSON vai até a próxima fronteira (`--batch...`); recorta e tenta ler.
        fim = texto.find("\r\n--", inicio)
        bruto_json = texto[inicio:fim if fim != -1 else None].strip()
        try:
            payload = json.loads(bruto_json)
        except ValueError:
            payload = None

    return status, payload, texto.strip()


def _formata_chave(chave: str | int, texto: bool = False) -> str:
    """Formats a key value for the OData URL (`Entity(key)`).

    - `texto=True` -> always a quoted literal, even if the value looks numeric. Use for
      entities whose key is a string: `Items` (ItemCode) and UDOs with `Code` (e.g.
      `INO_SETUPOBJ('1')` — the code is the text `"1"`, and `INO_SETUPOBJ(1)` would be refused).
    - `texto=False` (default) -> `int` or an all-digits string becomes an unquoted numeric
      key (`Orders(19466)`); anything else becomes a quoted literal.
    - In both cases strings get percent-encoding:
      `Items('ESCESP00000SUP000000%231205%23210%230')`

    The percent-encoding is **not optional** in this project: customer item codes contain `#`
    (e.g. `ESCESP00000SUP000000#1205#210#0`), and in a URL `#` starts the fragment — unencoded,
    the server receives only `PATCH /Items('ESCESP00000SUP000000` and answers `Unrecognized
    resource path`. Found 2026-09-15 testing the quote 00120634 order; see section 7.13 of
    migration_guide.md. The C# addon never had this: it addressed the object through the DI
    API (`oItems.GetByKey(code)`), building no URL.
    """
    if isinstance(chave, int) and not texto:
        return str(chave)

    valor = str(chave)

    # Tolera chamadores que já passam a chave entre aspas (`f"'{codigo}'"`): desembrulha
    # para reencodar o conteúdo corretamente, em vez de encodar as aspas junto.
    if len(valor) >= 2 and valor.startswith("'") and valor.endswith("'"):
        valor = valor[1:-1]
    elif not texto and valor.lstrip("-").isdigit():
        return valor

    # OData escapa aspa simples dentro do literal duplicando-a.
    escapado = valor.replace("'", "''")
    # `safe="'"`: tudo que não for alfanumérico/`_.-~` vira `%XX` — inclusive `#`, `/`,
    # `?`, `%`, `&` e espaço. As aspas ficam legíveis. O httpx não re-encoda o `%`.
    return "'" + quote(escapado, safe="'") + "'"
