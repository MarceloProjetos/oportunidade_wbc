"""Repositório do UDO `OrcDetalhe` via Service Layer.

`OrcDetalhe` (tabela de cabeçalho `INO_ORCAM`, tabela de linhas
`INO_ORC_LINHA`) espelha o orçamento do WBC dentro do SAP.

**Comportamento essencial: é um histórico, não um registro mutável.**
Confirmado em dados reais — o mesmo `U_INO_COD` aparece em vários `DocEntry`
(ex.: o orçamento `00123316` existe como 513925 *e* 513927). Bate com o sistema
legado, que sempre chama `.Add()` e nunca `.Update()`, e resolve "o atual" com
`SELECT max("DocEntry")`.

Por isso este repositório **não expõe atualização**: cada verificação grava um
novo registro, e a leitura do estado corrente é sempre "o de maior DocEntry
para aquele código". Um método `atualizar()` aqui seria um convite a corromper
o histórico.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from wbcpython.infrastructure.service_layer.client import ServiceLayerClient
from wbcpython.infrastructure.service_layer.errors import ServiceLayerError

logger = logging.getLogger(__name__)

ENTIDADE = "OrcDetalhe"
COLECAO_LINHAS = "INO_ORC_LINHACollection"


class RepositorioOrcDetalhe(Protocol):
    """Contrato do UDO OrcDetalhe. Sem atualização, por decisão de projeto."""

    def ultimo_snapshot(self, codigo: str) -> dict[str, Any] | None: ...

    def criar_snapshot(self, dados: dict[str, Any]) -> dict[str, Any]: ...

    def existe(self, codigo: str) -> bool: ...


def _escapar_literal_odata(valor: str) -> str:
    """Escapa uma aspas simples num literal OData (duplicando-a).

    Sem isso, um código com apóstrofo quebraria o `$filter` — e, no limite,
    permitiria alterar o sentido do filtro. Os códigos de orçamento são
    numéricos hoje, mas o repositório não deve depender disso.
    """
    return valor.replace("'", "''")


class RepositorioOrcDetalheServiceLayer:
    """Implementação sobre o Service Layer."""

    def __init__(self, cliente: ServiceLayerClient) -> None:
        self._cliente = cliente

    # ------------------------------------------------------------- leitura

    def ultimo_snapshot(self, codigo: str) -> dict[str, Any] | None:
        """Devolve o snapshot mais recente do orçamento, ou `None`.

        "Mais recente" é o maior `DocEntry` — a mesma definição usada pelo
        sistema legado. Nunca assuma que existe apenas um registro por código.
        """
        registros = self._cliente.listar(
            ENTIDADE,
            filtro=f"U_INO_COD eq '{_escapar_literal_odata(codigo)}'",
            ordenar_por="DocEntry desc",
            top=1,
        )
        if not registros:
            return None

        # A listagem não traz a coleção de linhas aninhada; buscar o documento
        # completo por chave é o que garante os itens.
        return self.por_docentry(int(registros[0]["DocEntry"]))

    def por_docentry(self, doc_entry: int) -> dict[str, Any]:
        return self._cliente.get_json(f"{ENTIDADE}({doc_entry})")

    def existe(self, codigo: str) -> bool:
        registros = self._cliente.listar(
            ENTIDADE,
            filtro=f"U_INO_COD eq '{_escapar_literal_odata(codigo)}'",
            top=1,
            selecionar="DocEntry",
        )
        return bool(registros)

    def historico(self, codigo: str, *, limite: int = 20) -> list[dict[str, Any]]:
        """Todos os snapshots do orçamento, do mais novo para o mais antigo.

        Útil para o dashboard: mostra a evolução do orçamento ao longo das
        verificações, que é justamente o que o histórico do UDO guarda.
        """
        return self._cliente.listar(
            ENTIDADE,
            filtro=f"U_INO_COD eq '{_escapar_literal_odata(codigo)}'",
            ordenar_por="DocEntry desc",
            top=limite,
        )

    # -------------------------------------------------------------- escrita

    def criar_snapshot(self, dados: dict[str, Any]) -> dict[str, Any]:
        """Grava um novo snapshot (`POST`), equivalente ao `.Add()` do DI-API.

        Não existe contrapartida de atualização: ver a nota no topo do módulo.
        A trava de ambiente é aplicada pelo cliente, em `request()`.
        """
        if not dados.get("U_INO_COD"):
            raise ValueError(
                "U_INO_COD é obrigatório: é a chave de negócio que liga o snapshot "
                "ao orçamento do WBC."
            )

        resposta = self._cliente.post(ENTIDADE, json=dados)
        try:
            criado = resposta.json()
        except ValueError as exc:
            raise ServiceLayerError(
                "O SAP aceitou a gravação mas devolveu um corpo que não é JSON.",
                status_code=resposta.status_code,
                metodo="POST",
                caminho=ENTIDADE,
            ) from exc

        logger.info(
            "Snapshot do OrcDetalhe criado: U_INO_COD=%s DocEntry=%s",
            dados.get("U_INO_COD"),
            criado.get("DocEntry"),
        )
        return criado
