"""Leitura das oportunidades direto no HANA, em **uma** consulta.

Por que este módulo existe: o Service Layer entrega 20 registros por resposta e
ignora `$top` acima disso. Varrer a janela custava ~90 requisições paginadas,
mais quatro por orçamento só para descobrir se já havia cotação e pedido — cerca
de 900 idas ao SAP num ciclo de 200. Aqui é **uma** consulta: 1.785
oportunidades, com cotação e pedido já resolvidos, em 0,04 s.

**O que esta consulta reproduz.** Exatamente o mesmo retrato que
`montar_estado` obtinha do Service Layer:

| Campo | Origem | O que o Service Layer fazia |
|---|---|---|
| `U_INO_StatusWBC`, `U_INO_Update`, `U_INO_PN_Correc`, `CardCode` | `OOPR` | listagem paginada |
| cotação: existe? qual revisão? | `OQUT` | `buscar(COTACAO)`, duas vezes |
| pedido: existe? qual revisão? | `ORDR` | `buscar(PEDIDO)`, duas vezes |

Conferido contra o Service Layer em 40 orçamentos de homologação — incluindo
casos sem documento, com cotação, com pedido e com revisão aplicada: **zero
divergências**.

**O schema é o da company que recebe a escrita, não o `HANA_SCHEMA`.** Esta é a
parte perigosa do módulo. `HANA_SCHEMA` aponta para as views de relatório
(`SBOALTAMIRAPROD`, mesmo em homologação); a company que a integração escreve é
`SL_COMPANY_DB`. Ler o estado de uma e escrever na outra criaria documentos em
homologação com base no que produção já tem — silenciosamente, e com aparência
de normalidade. Por isso o schema vem de `service_layer.company_db` e há um
teste que fixa essa escolha.

Só leitura: a escrita continua toda pelo Service Layer, que é quem aplica as
regras de negócio do SAP.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from wbcpython.config import HanaSettings
from wbcpython.infrastructure.hana.identificadores import citar_identificador
from wbcpython.infrastructure.hana.repository import _abrir_conexao_hdbcli

logger = logging.getLogger(__name__)

#: Campos devolvidos com os mesmos nomes do Service Layer. Manter a nomenclatura
#: significa que `montar_estado` e o processador não precisam saber de onde o
#: dado veio — e que trocar a fonte não pode mudar a decisão.
CAMPOS = (
    "SequentialNo",
    "U_ORCNUM_WBC",
    "CardCode",
    "SalesPerson",
    "ContactPerson",
    "U_INO_StatusWBC",
    "U_INO_Update",
    "U_INO_PN_Correc",
)


def _sql(schema_citado: str) -> str:
    """Monta a consulta. `ROW_NUMBER` escolhe o documento mais recente.

    O critério é `DocEntry desc` entre os não cancelados — exatamente o que o
    Service Layer fazia com `$orderby=DocEntry desc&$top=1` e
    `Cancelled eq 'tNO'`.
    """
    return f"""
SELECT O."OpprId", O."U_ORCNUM_WBC", O."CardCode", C."CardName", O."SlpCode", O."CprCode",
       O."OpenDate", O."Status", O."U_INO_StatusWBC", O."U_INO_Update", O."U_INO_PN_Correc",
       Q."DocEntry" AS "COT_DOCENTRY", Q."DocNum" AS "COT_DOCNUM",
       Q."U_INO_VERSAOWBC" AS "COT_REVISAO", Q."DocStatus" AS "COT_STATUS",
       R."DocEntry" AS "PED_DOCENTRY", R."DocNum" AS "PED_DOCNUM",
       R."U_INO_VERSAOWBC" AS "PED_REVISAO",
       R."CardCode" AS "PED_CARDCODE", R."DocStatus" AS "PED_STATUS",
       R."U_INO_Congelado" AS "PED_CONGELADO"
FROM {schema_citado}."OOPR" O
LEFT JOIN {schema_citado}."OCRD" C ON C."CardCode" = O."CardCode"
LEFT JOIN (
    SELECT "U_INO_COTWBC", "DocEntry", "DocNum", "U_INO_VERSAOWBC", "DocStatus",
           ROW_NUMBER() OVER (
               PARTITION BY "U_INO_COTWBC" ORDER BY "DocEntry" DESC
           ) AS "RN"
    FROM {schema_citado}."OQUT"
    WHERE "CANCELED" = 'N' AND LENGTH("U_INO_COTWBC") > 0
) Q ON Q."U_INO_COTWBC" = O."U_ORCNUM_WBC" AND Q."RN" = 1
LEFT JOIN (
    SELECT "U_INO_COTWBC", "DocEntry", "DocNum", "U_INO_VERSAOWBC", "CardCode", "DocStatus",
           "U_INO_Congelado",
           ROW_NUMBER() OVER (
               PARTITION BY "U_INO_COTWBC" ORDER BY "DocEntry" DESC
           ) AS "RN"
    FROM {schema_citado}."ORDR"
    WHERE "CANCELED" = 'N' AND LENGTH("U_INO_COTWBC") > 0
) R ON R."U_INO_COTWBC" = O."U_ORCNUM_WBC" AND R."RN" = 1
WHERE O."U_INO_IntegrouWBC" = 'Y'
  AND LENGTH(O."U_ORCNUM_WBC") > 0
  AND O."OpenDate" >= ?
  AND (? IS NULL OR O."U_ORCNUM_WBC" = ?)
ORDER BY O."OpprId" DESC
"""


class RepositorioOportunidadesHana:
    """Oportunidades da janela, com os documentos já resolvidos."""

    def __init__(
        self,
        settings: HanaSettings,
        *,
        company_db: str,
        fabrica_de_conexao: Any = None,
    ) -> None:
        self._settings = settings
        # Ver a nota do módulo: o schema é o da company que recebe a escrita.
        self._schema = citar_identificador(company_db, descricao="SL_COMPANY_DB")
        self._fabrica = fabrica_de_conexao or (lambda: _abrir_conexao_hdbcli(settings))
        self._conexao: Any = None

    def _obter_conexao(self) -> Any:
        if self._conexao is None:
            self._conexao = self._fabrica()
        return self._conexao

    def close(self) -> None:
        if self._conexao is not None:
            try:
                self._conexao.close()
            except Exception as exc:  # noqa: BLE001 - encerramento não pode explodir
                logger.warning("Falha ao encerrar a conexão com o HANA: %s", exc)
            finally:
                self._conexao = None

    def pendentes_de_integracao(
        self, *, desde: date, orcamento: str | None = None
    ) -> list[dict[str, Any]]:
        """A janela inteira, numa consulta só.

        Não há teto de leitura: ler tudo aqui custa centésimos de segundo. O teto
        que importa é o de **escrita**, e ele vive no worker.
        """
        cursor = self._obter_conexao().cursor()
        try:
            cursor.execute(_sql(self._schema), (desde, orcamento, orcamento))
            colunas = [descricao[0] for descricao in cursor.description]
            linhas = cursor.fetchall()
        finally:
            cursor.close()

        return [self._para_dicionario(dict(zip(colunas, linha, strict=False))) for linha in linhas]

    def datas_de_abertura(self, orcnums: list[str]) -> dict[str, Any]:
        """`OpenDate` por orçamento, **sem** filtro de janela.

        Existe para preencher o histórico do acompanhamento: as linhas gravadas
        antes da coluna existir não têm data, e as que já saíram da janela nunca
        mais serão verificadas — ficariam nulas para sempre, e uma data nula
        passa por qualquer filtro de janela. Sem este preenchimento, o corte no
        painel não teria efeito nenhum sobre elas.
        """
        if not orcnums:
            return {}
        marcadores = ", ".join("?" for _ in orcnums)
        sql = (
            f'SELECT "U_ORCNUM_WBC", "OpenDate" FROM {self._schema}."OOPR" '
            f'WHERE "U_ORCNUM_WBC" IN ({marcadores})'
        )
        cursor = self._obter_conexao().cursor()
        try:
            cursor.execute(sql, tuple(orcnums))
            linhas = cursor.fetchall()
        finally:
            cursor.close()
        return {str(linha[0]).strip(): linha[1] for linha in linhas if linha[0]}

    @staticmethod
    def _para_dicionario(campos: dict[str, Any]) -> dict[str, Any]:
        """Traduz a linha para o formato do Service Layer.

        `documentos` viaja junto, pré-resolvido: é o que evita as quatro idas ao
        SAP por orçamento que existiam só para responder "já tem cotação?".

        A leitura é **por nome de coluna**, nunca por posição. A versão anterior
        usava `linha[0]`, `linha[1]`... e o custo apareceu na primeira vez que
        uma coluna entrou no meio do `SELECT`: todos os campos seguintes
        deslocaram uma casa, em silêncio. O `U_INO_StatusWBC` passou a receber o
        `Status`, o `Update` passou a receber o SitCode, e o ciclo seguiu
        rodando sem erro nenhum — decidindo sobre um retrato trocado.
        """
        return {
            "SequentialNo": campos["OpprId"],
            "U_ORCNUM_WBC": str(campos["U_ORCNUM_WBC"] or "").strip(),
            "CardCode": str(campos["CardCode"] or ""),
            # Apresentação apenas: nenhuma decisão olha o nome do parceiro. Ele
            # viaja junto porque o painel e a prévia precisam identificar o
            # cliente, e buscá-lo depois custaria uma ida por orçamento.
            "CardName": str(campos["CardName"] or ""),
            "SalesPerson": campos["SlpCode"],
            # A data que define a janela. Viaja junto para que o acompanhamento
            # possa guardá-la: sem ela, o painel não tem como aplicar o mesmo
            # corte que o worker aplica, e passa a mostrar oportunidades que o
            # ciclo já não olha mais — foi exatamente o que aconteceu quando a
            # janela encolheu de 6 para 3 meses.
            "OpenDate": campos["OpenDate"],
            "ContactPerson": campos["CprCode"],
            "Status": str(campos["Status"] or ""),
            "U_INO_StatusWBC": str(campos["U_INO_StatusWBC"] or ""),
            "U_INO_Update": str(campos["U_INO_Update"] or ""),
            "U_INO_PN_Correc": str(campos["U_INO_PN_Correc"] or ""),
            "documentos": {
                "cotacao": _documento(
                    campos["COT_DOCENTRY"],
                    campos["COT_REVISAO"],
                    docnum=campos["COT_DOCNUM"],
                    status=campos["COT_STATUS"],
                ),
                "pedido": _documento(
                    campos["PED_DOCENTRY"],
                    campos["PED_REVISAO"],
                    campos["PED_CARDCODE"],
                    docnum=campos["PED_DOCNUM"],
                    status=campos["PED_STATUS"],
                    # Campo só do pedido: `U_INO_Congelado` é UDF do `ORDR`, e
                    # a cotação não o tem. Fica no chamador, e não em
                    # `_documento`, para a cotação não ganhar uma chave vazia
                    # que sugere um campo que não existe nela.
                    extras={"U_INO_Congelado": str(campos["PED_CONGELADO"] or "")},
                ),
            },
        }


def _documento(
    doc_entry: Any,
    revisao: Any,
    parceiro: Any = None,
    *,
    docnum: Any = None,
    status: Any = None,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """`None` quando não há documento — o mesmo que o Service Layer devolvia.

    `CardCode` é o equivalente do `ChecaPNPedido` legado: é comparando o
    parceiro que o pedido **já tem** com o `U_INO_PN_Correc` que se sabe se a
    troca ainda está pendente. Ver `EstadoIntegracao.troca_de_parceiro_pendente`.

    `DocStatus` é `'O'` (aberto) ou `'C'` (fechado). Documento fechado não
    aceita alteração nem cancelamento no SAP, e a integração precisa saber disso
    **antes** de tentar — ver `DocumentosJaLidos.esta_fechado`.

    A coluna vem para os dois tipos porque `esta_fechado(tipo, ...)` responde
    sobre os dois: não é dado morto, é dado que a regra atual ainda não pergunta.
    As regras de hoje só consultam o pedido; existem 3 cotações fechadas em cada
    ambiente, e o dia em que elas forem tratadas não precisa mexer na consulta —
    ver `DECISOES.md`.

    `extras` carrega o que é de um tipo só. Hoje é o `U_INO_Congelado`, UDF do
    `ORDR` que a cotação não tem: `'Y'` protege as linhas do pedido de serem
    refeitas — ver `DocumentosJaLidos.esta_congelado`.
    """
    if doc_entry is None:
        return None
    return {
        "DocEntry": int(doc_entry),
        # `DocNum` é o número que as pessoas citam; `DocEntry` é a chave que o
        # Service Layer usa. Os dois viajam: um para a tela, outro para a API.
        "DocNum": None if docnum is None else int(docnum),
        "U_INO_VERSAOWBC": str(revisao or ""),
        "CardCode": str(parceiro or ""),
        "DocStatus": str(status or ""),
        **(extras or {}),
    }
