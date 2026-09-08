"""Tudo sobre o **pedido de venda**: criação e manutenção.

Módulo espelho de `domain.cotacao`. O que está aqui vale para o pedido e só para
o pedido — e a lista do que difere não é pequena.

**Cabeçalho** (`ServiceProcess.cs:294-344`):

| Campo | Origem no WBC |
|---|---|
| `DocDueDate` | hoje + `PRZENT` |
| `Comments` | `PGTCOD` cru, cortado em 150 |
| `U_INO_TIPO_MT` | `TIPMONCOD` |
| `U_INO_VL_MT` | **`ORCBAS3`** |
| `U_INO_COM` | `ORCPERCOM` |
| `U_INO_VL_COM` | `ORCVALCOM` |

**Linhas**: além de `U_INO_ORCITM` e `U_INO_D_Adicionais`, o pedido leva
`U_INO_Composicao` e `U_INO_Id_IntWBC` (`:378-395`, `:653-667`) — e **não** leva
`U_INO_ACAB`. Das 16.242 linhas de pedido do WBC em produção, apenas 6 têm
`U_INO_ACAB` e 16.088 têm `U_INO_Composicao`.

**O que o pedido não leva** e a cotação leva: `U_INO_PrazoEntrega`,
`U_INO_ValorTransp`, `U_INO_ValorEmbalagem`, `U_INO_PessoaContato`,
`U_INO_CondPag` e `U_INO_Montagem`. Conferido: em 4.431 dos 4.432 pedidos do
WBC em produção esses campos estão vazios.

**Duas armadilhas** que a separação torna visíveis:

1. `U_INO_VL_MT` sai de `ORCBAS3` aqui e de `ORCVALMON` na cotação
   (`:281` contra `:1180`). Colunas diferentes do mesmo registro. Não há como
   saber se é intenção; os dois comportamentos foram preservados e a pergunta
   está em `RETOMADA.md`.
2. A condição de pagamento vai **crua** para o `Comments` — sem a troca de `|`
   e `;` por quebra de linha que a cotação faz no `U_INO_CondPag`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from wbcpython.domain.linhas import (
    DEPOSITO_PADRAO,
    FATOR_DE_EMBARQUE,
    ResultadoLinhas,
    composicao,
    resolver_linhas,
)
from wbcpython.domain.venda_comum import (
    LIMITE_COM,
    LIMITE_COMENTARIO,
    campos_comuns,
    numero_curto,
)


def comentario(texto: str) -> str:
    """`Comments` do pedido: o `PGTCOD` cru, cortado em 150 caracteres.

    O código-fonte que temos faz mais do que isso — depois de cortar, ele quebra
    o texto em palavras e aplica `Distinct()` (`ServiceProcess.cs:332-334`), o
    que apagaria palavras repetidas no meio da frase. **Produção não faz isso.**

    O pedido 19500 (orçamento `00125401`) tem `Comments` idêntico, caractere por
    caractere, ao `PGTCOD` do WBC — inclusive com o "da" repetido em "da
    mercadoria" e "da emissão", que o `Distinct()` teria comido. O mesmo vale
    para os outros pedidos conferidos.

    Ou seja: o binário que roda em produção **não é** este código-fonte. Entre
    reproduzir o fonte e reproduzir o comportamento observado, vale o
    comportamento observado — é ele que o comercial lê hoje na impressão. A
    divergência está registrada em `RETOMADA.md`.
    """
    return texto[:LIMITE_COMENTARIO]


def cabecalho(impressao: Any, *, hoje: date | None = None) -> dict[str, Any]:
    """Cabeçalho do pedido — o conjunto de `ServiceProcess.cs:294-344`.

    Dois dos campos não são UDF, e é por isso que escaparam das varreduras que
    procuravam por `U_INO_`:

    * `DocDueDate` = data de hoje + `PRZENT` (`:294`). Conferido em produção:
      nos 4.432 pedidos do WBC, `DocDueDate - DocDate` é exatamente o prazo de
      entrega, sempre.
    * `Comments` = a condição de pagamento (`:327-331`), preenchida em 4.328
      deles.
    """
    vencimento = (hoje or date.today()) + timedelta(  # noqa: DTZ011
        days=impressao.prazo_entrega
    )
    return {
        "DocDueDate": vencimento.isoformat(),
        "Comments": comentario(impressao.pagamento_codigo),
        "U_INO_TIPO_MT": impressao.montagem_tipo,
        # `VL_MT` e `VL_COM` são `db_Float`; `COM` é texto de 10 caracteres.
        "U_INO_VL_MT": float(impressao.base3),
        "U_INO_COM": numero_curto(impressao.percentual_comissao)[:LIMITE_COM],
        "U_INO_VL_COM": float(impressao.valor_comissao),
    }


def _udfs_da_linha(item: Any, orcamento: Any) -> dict[str, Any]:
    """`U_INO_Composicao` e `U_INO_Id_IntWBC` — os dois UDFs de linha do pedido."""
    del orcamento  # tudo vem da própria linha
    return {
        "U_INO_Composicao": composicao(item.texto),
        "U_INO_Id_IntWBC": str(item.id_integracao),
    }


def linhas(
    orcamento: Any,
    de_para: dict[str, Any],
    *,
    deposito: str = DEPOSITO_PADRAO,
    pesos: Mapping[int, Decimal] | None = None,
    fator_de_embarque: Decimal = FATOR_DE_EMBARQUE,
) -> ResultadoLinhas:
    """`DocumentLines` do pedido.

    `pesos` (`ORCITM` → peso líquido do nível 1 da árvore) vira `Weight1` na
    linha, com a folga de embalagem aplicada. É o que distingue esta função da
    equivalente em `domain.cotacao`, que não tem o parâmetro: o legado grava o
    peso no pedido e deixa a linha da cotação comentada (`ServiceProcess.cs:640`
    contra `:377`).

    Sem `pesos`, o pedido sai como antes — com o peso do cadastro do item.
    """
    return resolver_linhas(
        orcamento,
        de_para,
        udfs_da_linha=_udfs_da_linha,
        deposito=deposito,
        pesos=pesos,
        fator_de_embarque=fator_de_embarque,
    )


def montar_payload(
    orcamento: Any,
    *,
    parceiro: str | None,
    filial: int,
    snapshot_id: int | None = None,
    oportunidade: dict[str, Any] | None = None,
    trocando_de_parceiro: bool = False,
    hoje: date | None = None,
) -> dict[str, Any]:
    """Corpo do pedido, sem as linhas — ver a nota em `domain.cotacao`."""
    payload = campos_comuns(
        orcamento,
        parceiro=parceiro,
        filial=filial,
        snapshot_id=snapshot_id,
        oportunidade=oportunidade,
        trocando_de_parceiro=trocando_de_parceiro,
    )
    payload.update(cabecalho(orcamento.impressao, hoje=hoje))
    return payload
