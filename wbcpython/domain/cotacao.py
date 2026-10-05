"""Tudo sobre a **cotação**: criação e manutenção.

Cabeçalho, linhas e payload completo ficam aqui, e só aqui. O pedido tem o seu
módulo espelho (`domain.pedido`). Os dois não compartilham campo nenhum além do
que está em `domain.venda_comum` — porque no legado também não compartilham: são
dois blocos de código distintos, escritos em épocas diferentes, e as diferenças
entre eles são reais e verificáveis em produção.

**Cabeçalho** (`ServiceProcess.cs:1173-1189` ao criar, `:1374-1388` ao atualizar):

| UDF | Coluna do WBC |
|---|---|
| `U_INO_PessoaContato` | `CLICON` |
| `U_INO_CondPag` | `PGTCOD`, com `\\|` e `;` virando quebra de linha, cortado em 254 |
| `U_INO_PrazoEntrega` | `PRZENT` |
| `U_INO_ValorTransp` | `ORCVALTRP` |
| `U_INO_ValorEmbalagem` | `ORCVALEMB` |
| `U_INO_VL_MT` | `ORCVALMON` |
| `U_INO_TIPO_MT` | `TIPMONCOD` |
| `U_INO_Montagem` / `U_INO_Montagem2` | `ORCIMP_MONTAGEM`, partido em 200 |

**Linhas**: além de `U_INO_ORCITM` e `U_INO_D_Adicionais`, a cotação leva
`U_INO_ACAB` (`:1217`, `:1231`, `:1435`, `:1449`) — e **não** leva
`U_INO_Composicao` nem `U_INO_Id_IntWBC`. Das 579.088 linhas de cotação do WBC
em produção, nenhuma tem `U_INO_Composicao`. Isso importa: esse campo aparece na
view de impressão `VW_ORCAMENTO_IMPRESSAO`, ou seja, preenchê-lo mudaria o que o
cliente recebe.

**O que a cotação não leva** e o pedido leva: `DocDueDate`, `Comments`,
`U_INO_COM` e `U_INO_VL_COM`.
"""

from __future__ import annotations

from typing import Any

from wbcpython.domain.linhas import DEPOSITO_PADRAO, ResultadoLinhas, resolver_linhas
from wbcpython.domain.venda_comum import (
    LIMITE_CONDPAG,
    LIMITE_CONTATO,
    LIMITE_MONTAGEM,
    LIMITE_VALOR_EMBALAGEM,
    LIMITE_VALOR_TRANSP,
    campos_comuns,
    valor_texto,
)


def condicao_de_pagamento(texto: str) -> str:
    """`PGTCOD` no formato que o legado grava em `U_INO_CondPag`.

    O WBC junta as parcelas com `|` ou `;`; o legado troca as duas por quebra de
    linha e corta em 254 caracteres.

    Repare que o **pedido** faz diferente: lá o `PGTCOD` vai cru para o
    `Comments`, sem troca de separador. Não é descuido nosso — é o que produção
    mostra nos dois documentos.
    """
    return texto.replace("|", "\n").replace(";", "\n")[:LIMITE_CONDPAG]


def partir_montagem(texto: str) -> dict[str, str]:
    """Divide o texto da montagem entre `U_INO_Montagem` e `U_INO_Montagem2`.

    O segundo campo só entra quando o texto passa de 200 caracteres — se entrasse
    sempre, uma montagem curta gravaria vazio por cima do que já estivesse lá na
    atualização de uma cotação existente.
    """
    if len(texto) <= LIMITE_MONTAGEM:
        return {"U_INO_Montagem": texto}
    return {
        "U_INO_Montagem": texto[:LIMITE_MONTAGEM],
        "U_INO_Montagem2": texto[LIMITE_MONTAGEM:],
    }


def cabecalho(impressao: Any) -> dict[str, Any]:
    """UDFs de cabeçalho da cotação — o conjunto de `ServiceProcess.cs:1173-1189`."""
    udfs: dict[str, Any] = {
        "U_INO_PessoaContato": impressao.contato[:LIMITE_CONTATO],
        "U_INO_CondPag": condicao_de_pagamento(impressao.pagamento_codigo),
        "U_INO_PrazoEntrega": str(impressao.prazo_entrega),
        "U_INO_ValorTransp": valor_texto(impressao.valor_transporte, LIMITE_VALOR_TRANSP),
        "U_INO_ValorEmbalagem": valor_texto(impressao.valor_embalagem, LIMITE_VALOR_EMBALAGEM),
        # `U_INO_VL_MT` é `db_Float` no SAP, não texto — daí o número puro.
        # Na cotação ele vem de `ORCVALMON`; no pedido, de `ORCBAS3`.
        "U_INO_VL_MT": float(impressao.valor_montagem),
        "U_INO_TIPO_MT": impressao.montagem_tipo,
    }
    udfs.update(partir_montagem(impressao.montagem))
    return udfs


def _udfs_da_linha(item: Any, orcamento: Any) -> dict[str, Any]:
    """`U_INO_ACAB` é de cabeçalho na origem e de linha no destino.

    O legado repete o mesmo `ORCIMP_ACABAMENTO` em todas as linhas da cotação
    (`ServiceProcess.cs:1217`, `:1231`, `:1435`, `:1449`), e as cotações reais
    confirmam. Reproduzido tal e qual.
    """
    del item  # o acabamento é do orçamento, não da linha
    impressao = getattr(orcamento, "impressao", None)
    return {"U_INO_ACAB": getattr(impressao, "acabamento", "") if impressao else ""}


def linhas(
    orcamento: Any, de_para: dict[str, Any], *, deposito: str = DEPOSITO_PADRAO
) -> ResultadoLinhas:
    """`DocumentLines` da cotação."""
    return resolver_linhas(orcamento, de_para, udfs_da_linha=_udfs_da_linha, deposito=deposito)


def montar_payload(
    orcamento: Any,
    *,
    parceiro: str | None,
    filial: int,
    snapshot_id: int | None = None,
    oportunidade: dict[str, Any] | None = None,
    trocando_de_parceiro: bool = False,
) -> dict[str, Any]:
    """Corpo da cotação, sem as linhas.

    As linhas ficam de fora de propósito: resolvê-las depende do de-para de
    grupos, que é infraestrutura (vai ao SAP) e produz avisos que precisam ser
    registrados no acompanhamento. Quem monta o payload completo é a aplicação,
    que tem as duas coisas em mãos.
    """
    payload = campos_comuns(
        orcamento,
        parceiro=parceiro,
        filial=filial,
        snapshot_id=snapshot_id,
        oportunidade=oportunidade,
        trocando_de_parceiro=trocando_de_parceiro,
    )
    payload.update(cabecalho(orcamento.impressao))
    return payload
