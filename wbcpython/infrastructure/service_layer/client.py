"""Cliente HTTP do SAP Business One Service Layer.

Substitui a conexão DI-API (`Company.Connect()`) do sistema legado por chamadas
REST autenticadas por sessão.

Pontos de projeto:

* **Uma única porta de saída para dados.** Toda requisição a dados passa por
  `request()`, e é lá — e somente lá — que a trava de segurança é aplicada.
  Nenhum outro módulo precisa lembrar de verificar o ambiente antes de gravar.
  As duas exceções são `login()` e `logout()`, que falam direto com o transporte
  por necessidade (passar por `request()` seria recursivo, já que é ele quem
  dispara o login). Não são escrita de dados de negócio; qualquer método novo,
  porém, deve passar por `request()`.
* **Sessão renovada automaticamente.** O Service Layer expira a sessão por
  inatividade; ao receber 401 o cliente refaz o login uma vez e repete a
  requisição, de forma transparente.
* **Credenciais nunca aparecem em log ou em mensagem de erro.**
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
    """Cliente de sessão do Service Layer.

    Uso recomendado, como gerenciador de contexto (garante o logout):

        with ServiceLayerClient(settings.service_layer,
                                production_company_db=settings.production_company_db) as sl:
            dados = sl.get_json("OrcDetalhe", params={"$top": 1})
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
        """Autentica e guarda o cookie de sessão (B1SESSION/ROUTEID)."""
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
        """Encerra a sessão. Nunca levanta exceção — é chamado no encerramento."""
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
        self.login()
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
        """Executa uma requisição. **Única porta de saída HTTP do cliente.**

        Aplica a trava de segurança, garante sessão ativa e renova a sessão
        automaticamente em caso de expiração.
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
        """Lê a resposta como JSON, traduzindo corpo inválido em erro legível.

        Um `200` que não seja JSON é sintoma clássico de `SL_BASE_URL` errado,
        proxy ou portal cativo no caminho. Sem este tratamento, o `.json()`
        estourava com `JSONDecodeError` cru — justamente no cenário em que a
        pessoa mais precisa de uma pista do que está errado.
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
        """Consulta OData que **atravessa a paginação** do Service Layer.

        O SL entrega no máximo 20 registros por resposta, e `$top` não fura esse
        limite: pedir `$top=100` devolve 20. Pior — quando o `$top` está na
        consulta, o SL **omite** o `@odata.nextLink`, então a resposta parece
        completa. Foi assim que o ciclo passou a varrer só as 20 oportunidades
        mais recentes de uma janela com 1.785, sem nada no log indicando corte.

        A saída é o cabeçalho `Prefer: odata.maxpagesize`, que o SL respeita, e
        um laço de `$skip` até a página vir curta. `$skip` sobre `$orderby` de
        chave é estável: a ordenação é por identificador, não por data.

        `top` continua sendo o **teto** de registros devolvidos — agora um teto
        de verdade, e não um número que o servidor ignora em silêncio.
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
        """Smoke test de conectividade: faz login e uma leitura mínima.

        Devolve uma descrição curta do resultado, própria para exibir na CLI.
        """
        self.login()
        self.listar("OrcDetalhe", top=1, selecionar="DocEntry")
        return f"Conexão OK com {self._settings.base_url} (company_db={self._settings.company_db})."
