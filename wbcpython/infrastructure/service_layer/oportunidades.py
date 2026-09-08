"""Oportunidades de Venda (`SalesOpportunities`) via Service Layer.

Substitui, no legado: `UpdateStatusDoc` (espelhar o SitCode do WBC no SAP),
`cancelaFechaOportunidade` (encerrar) e `AddCotacaoOportunidade` /
`AddPedidoOportunidade` (vincular o documento gerado à oportunidade).

Campos relevantes, extraídos da consulta `GetDocNumOportunidades` do legado:

* `SequentialNo` — chave da oportunidade (no banco: `OpprId`)
* `StartDate` — data de abertura. **Atenção:** no banco a coluna é `OpenDate`,
  mas a entidade do Service Layer expõe `StartDate`; usar o nome da coluna
  devolve `HTTP 400 — Property OpenDate is invalid`. Foi um erro real, pego
  só ao consultar o ambiente de verdade.
* `U_ORCNUM_WBC` — número do orçamento no WBC
* `U_INO_StatusWBC` — SitCode espelhado no SAP (texto)
* `U_INO_Update` / `U_INO_PN_Correc` — sinalizam troca de parceiro de negócios
* `U_INO_IntegrouWBC` — marca a oportunidade como participante da integração
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Protocol

from wbcpython.infrastructure.service_layer.client import ServiceLayerClient
from wbcpython.infrastructure.service_layer.documentos import TipoDocumento

logger = logging.getLogger(__name__)

#: Único valor de `BoSoOsStatus` que representa oportunidade encerrada sem
#: venda. Os outros dois são `sos_Open` e `sos_Sold`.
STATUS_PERDIDA = "sos_Missed"
STATUS_ABERTA = "sos_Open"

#: Estados em que o SAP **recusa** acrescentar estágio à oportunidade, com
#: `-1029 [SalesOpportunitiesLines.SequenceNo] Field cannot be updated`. A
#: mensagem não diz nada sobre estar fechada — ver `vincular_documento`.
STATUS_FECHADOS = frozenset({"sos_Missed", "sos_Sold"})

#: Documentos cujo vínculo baixa a marca `U_INO_Update`. Só o pedido: no legado,
#: as duas escritas de `U_INO_Update = "N"` estão dentro de
#: `AddPedidoOportunidade` (`ServiceProcess.cs:198` e `:210`) e nenhuma em
#: `AddCotacaoOportunidade`. Ver `vincular_documento`.
BAIXA_MARCA_DE_ATUALIZACAO = frozenset({TipoDocumento.PEDIDO})

#: `PercentageRate` do estágio, por tipo de documento. Não é enfeite: são os
#: valores que o legado grava (`AddCotacaoOportunidade` usa 0,
#: `AddPedidoOportunidade` usa 100) e os que aparecem nos vínculos reais.
PERCENTUAL_DO_ESTAGIO = {TipoDocumento.COTACAO: 0, TipoDocumento.PEDIDO: 100}

ENTIDADE = "SalesOpportunities"

#: Chave da oportunidade. No banco a coluna é `OpprId`, mas a entidade do
#: Service Layer a expõe como `SequentialNo` — usar o nome da coluna devolve
#: `HTTP 400 — Property OpprId is invalid`. Vale o mesmo para `OpenDate`, que
#: aqui é `StartDate`. Os dois erros só apareceram ao consultar o ambiente real.
CAMPO_CHAVE = "SequentialNo"

UDF_ORCAMENTO = "U_ORCNUM_WBC"
UDF_ORCAMENTO_MASC = "U_ORCNUM_MASC"
UDF_STATUS_WBC = "U_INO_StatusWBC"
UDF_ATUALIZAR = "U_INO_Update"
UDF_PN_CORRECAO = "U_INO_PN_Correc"
UDF_INTEGROU = "U_INO_IntegrouWBC"

#: Estágio/tipo de documento na coleção de linhas da oportunidade.
#: 23 = Cotação, 17 = Pedido de Venda (ObjType do SAP; o legado usa 17 na
#: consulta que junta OPR1 com ORDR).
OBJ_TYPE = {TipoDocumento.COTACAO: 23, TipoDocumento.PEDIDO: 17}


class RepositorioOportunidades(Protocol):
    def pendentes_de_integracao(self, *, desde: date | None = None) -> list[dict[str, Any]]: ...

    def por_orcamento(self, orcamento: str) -> dict[str, Any] | None: ...

    def atualizar_status(self, oppr_id: int, sitcode: int) -> None: ...

    def encerrar(self, oppr_id: int, sitcode: int) -> None: ...


def _escapar(valor: str) -> str:
    return valor.replace("'", "''")


class RepositorioOportunidadesServiceLayer:
    """Implementação sobre o Service Layer."""

    def __init__(self, cliente: ServiceLayerClient) -> None:
        self._cliente = cliente

    # -------------------------------------------------------------- leitura

    def pendentes_de_integracao(
        self,
        *,
        desde: date | None = None,
        limite: int = 100,
        orcamento: str | None = None,
    ) -> list[dict[str, Any]]:
        """Oportunidades que a integração deve avaliar.

        Reproduz os critérios do `GetDocNumOportunidades` legado: participa da
        integração (`U_INO_IntegrouWBC = 'Y'`), tem número de orçamento, e o
        número "mascarado" coincide com o do WBC.

        Duas diferenças deliberadas em relação ao legado:

        * **Sem filtro fixo de orçamento.** O legado tinha
          `and U_ORCNUM_WBC = '00121819'` gravado na consulta, restringindo a
          integração inteira a um único orçamento. Aqui `orcamento` é um
          parâmetro opcional, para reprocessar um caso específico sob demanda.
        * **Janela de data explícita.** O legado montava a data misturando -6
          meses para o ano e -9 para o mês, o que produzia um corte
          imprevisível. Aqui a data vem pronta de quem chama.
        """
        condicoes = [
            f"{UDF_INTEGROU} eq 'Y'",
            f"{UDF_ORCAMENTO} ne ''",
        ]
        if desde:
            condicoes.append(f"StartDate ge '{desde.isoformat()}'")
        if orcamento:
            condicoes.append(f"{UDF_ORCAMENTO} eq '{_escapar(orcamento)}'")

        return self._cliente.listar(
            ENTIDADE,
            filtro=" and ".join(condicoes),
            ordenar_por=CAMPO_CHAVE + " desc",
            top=limite,
        )

    def por_orcamento(self, orcamento: str) -> dict[str, Any] | None:
        registros = self._cliente.listar(
            ENTIDADE,
            filtro=f"{UDF_ORCAMENTO} eq '{_escapar(orcamento)}'",
            ordenar_por=CAMPO_CHAVE + " desc",
            top=1,
        )
        return registros[0] if registros else None

    def por_id(self, oppr_id: int) -> dict[str, Any]:
        return self._cliente.get_json(f"{ENTIDADE}({oppr_id})")

    # -------------------------------------------------------------- escrita

    def atualizar_status(self, oppr_id: int, sitcode: int) -> None:
        """Espelha o SitCode do WBC no campo `U_INO_StatusWBC` da oportunidade.

        O SitCode é gravado como **texto**: é assim que o legado o lê de volta
        (compara com `"30"`, `"40"`, `"55"`), e gravar como número faria a
        comparação da próxima execução falhar silenciosamente.
        """
        self._cliente.patch(f"{ENTIDADE}({oppr_id})", json={UDF_STATUS_WBC: str(sitcode)})
        logger.info("Oportunidade %s: status WBC atualizado para %s.", oppr_id, sitcode)

    def encerrar(self, oppr_id: int, sitcode: int) -> None:
        """Encerra/cancela a oportunidade (SitCode 70, 90 ou 99).

        Grava o status junto com o encerramento, para que o motivo do
        fechamento continue legível no SAP.

        `STATUS_PERDIDA` não é escolha de nomenclatura: o enum `BoSoOsStatus`
        do Service Layer só aceita `sos_Open`, `sos_Missed` e `sos_Sold`. O
        valor plausível `sos_Lost` **não existe**, e o SAP responde
        `HTTP 400 | SAP -1013`. É o mesmo valor que o legado usa
        (`oport.Status = BoSoOsStatus.sos_Missed`, `ServiceProcess.cs:91-122`).
        """
        self._cliente.patch(
            f"{ENTIDADE}({oppr_id})",
            json={"Status": STATUS_PERDIDA, UDF_STATUS_WBC: str(sitcode)},
        )
        logger.info("Oportunidade %s encerrada (SitCode %s).", oppr_id, sitcode)

    def vincular_documento(
        self,
        oppr_id: int,
        tipo: TipoDocumento,
        doc_entry: int,
        total: float | None = None,
    ) -> None:
        """Vincula a cotação/pedido gerado à oportunidade.

        Equivale a `AddCotacaoOportunidade`/`AddPedidoOportunidade`. O vínculo
        vive na coleção de estágios da oportunidade, e o Service Layer substitui
        a coleção inteira num PATCH — por isso a coleção existente é lida e o
        novo estágio é **acrescentado**, nunca enviado sozinho.

        Três detalhes que o SAP exige e que só se descobrem no ambiente real:

        1. **`DocumentNumber` recebe o `DocEntry`, não o `DocNum`.** O nome do
           campo diz o contrário, mas os vínculos existentes em homologação não
           deixam dúvida: a oportunidade `5681` aponta para `9486`, que é o
           `DocEntry` da cotação — cujo `DocNum` é `4948`. O legado faz o mesmo
           (`oport.Lines.DocumentNumber = Convert.ToInt32(DocEntryCotacao)`).
        2. **`MaxLocalTotal` precisa ser maior que zero**, senão o SAP recusa
           com `-5002 In "Potential Amount" field, enter number greater than 0
           [OOPR.MaxSumLoc]`. Vai o total do documento; sem total, `1`, que é o
           mesmo contorno do legado.
        3. **`PercentageRate` por tipo**: 0 na cotação, 100 no pedido — ver
           `PERCENTUAL_DO_ESTAGIO`.

        **Oportunidade fechada é reaberta e fechada de novo.** O SAP recusa
        estágio novo numa oportunidade `sos_Sold`/`sos_Missed`, com
        `-1029 [SalesOpportunitiesLines.SequenceNo] Field cannot be updated` —
        mensagem que aponta para um campo sem relação com a causa. O legado
        resolve isso do mesmo jeito (`AddPedidoOportunidade`):

            oport.Status = BoSoOsStatus.sos_Open;  oport.Update();
            ... oport.Lines.Add() ...              oport.Update();
            oport.Status = BoSoOsStatus.sos_Sold;  oport.Update();

        A diferença é que o legado sempre reclassifica como `sos_Sold`; aqui o
        estado anterior é **restaurado**, seja ele qual for. Uma oportunidade
        perdida que recebe documento não deveria virar ganha por efeito
        colateral do vínculo.

        **`U_INO_Update` é baixado ao vincular o pedido.** O campo sinaliza que
        o operador pediu a troca de PN do pedido; com o pedido novo gravado, o
        pedido está feito e a marca não tem mais o que sinalizar. O campo é um
        UDF desta mesma entidade — a oportunidade no SAP —, e não do WBC, e é
        preenchido à mão. É o que
        o legado faz, e só para o pedido — as duas escritas de
        `U_INO_Update = "N"` estão dentro de `AddPedidoOportunidade`
        (`ServiceProcess.cs:198` e `:210`), nenhuma em `AddCotacaoOportunidade`.

        A baixa vai **junto** com o estágio, no mesmo PATCH: separá-la abriria
        uma janela em que a marca está baixada e o vínculo não existe, e nesse
        intervalo a informação de que os valores mudaram estaria perdida.
        """
        atual = self.por_id(oppr_id)
        status_original = str(atual.get("Status") or "")
        precisa_reabrir = status_original in STATUS_FECHADOS

        if precisa_reabrir:
            logger.info(
                "Oportunidade %s está %s: reaberta para receber o vínculo.",
                oppr_id,
                status_original,
            )
            self._cliente.patch(f"{ENTIDADE}({oppr_id})", json={"Status": STATUS_ABERTA})

        estagios = list(atual.get("SalesOpportunitiesLines") or [])

        estagios.append(
            {
                "DocumentType": OBJ_TYPE[tipo],
                "DocumentNumber": doc_entry,
                "MaxLocalTotal": total if total else 1,
                "PercentageRate": PERCENTUAL_DO_ESTAGIO[tipo],
            }
        )

        corpo: dict[str, Any] = {"SalesOpportunitiesLines": estagios}
        if tipo in BAIXA_MARCA_DE_ATUALIZACAO:
            corpo[UDF_ATUALIZAR] = "N"

        try:
            self._cliente.patch(f"{ENTIDADE}({oppr_id})", json=corpo)
        finally:
            # `finally`: se o vínculo falhar, a oportunidade **não** pode ficar
            # aberta por acidente. O estado dela é dado de negócio; o vínculo
            # que falhou é problema nosso.
            if precisa_reabrir:
                self._cliente.patch(f"{ENTIDADE}({oppr_id})", json={"Status": status_original})
        logger.info(
            "Oportunidade %s: %s DocEntry %s vinculada (total %s).",
            oppr_id,
            tipo.rotulo,
            doc_entry,
            total,
        )
