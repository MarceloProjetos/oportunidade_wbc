"""Mapeamento do orçamento do WBC para o payload do UDO `OrcDetalhe`.

Transformação pura: entra um `OrcamentoWbc` (lido do SQL Server), sai o
dicionário que o Service Layer espera num `POST /b1s/v1/OrcDetalhe`.

O `OrcDetalhe` é o **retrato histórico** do orçamento no momento em que a
integração agiu: é append-only, nunca atualizado, e é para ele que o UDF
`U_INO_ORCAMENTO` da cotação aponta. Um retrato com metade dos campos vazios
não serve ao propósito — daí o cuidado abaixo.

--------------------------------------------------------------------------
As linhas têm dois caminhos, e o legado escolhe entre eles
--------------------------------------------------------------------------
1. **Com árvore de produtos** (`INTEGRACAO_ORCPRDARV` tem linhas): cada linha
   do snapshot é um componente — código, cor, nível, descrição, quantidade,
   peso, preço unitário e total. É o detalhamento de engenharia, e pode ter
   dezenas de linhas para um orçamento de dois itens.
2. **Sem árvore**: sobram as linhas de `INTEGRACAO_ORCIMP`, e o legado grava
   apenas **sequência e texto** (`U_INO_ORCITM`, `U_INO_ORCTXT`).

**Quando cada caminho vale, e por quê.** A árvore só é preenchida no WBC quando
o orçamento vira **pedido**: medido sobre os orçamentos alterados em 2026,
os 600 com `SitCode 60` têm árvore — **100%** —, contra **1 em 1.255** dos que
estão em `SitCode 40` (cotação emitida). Enquanto é só cotação, não existe
detalhamento de engenharia para gravar.

Repare que a escolha aqui **não olha o SitCode**: olha se a árvore existe. É de
propósito. A regra de negócio ("árvore aparece no pedido") é do WBC, não nossa;
codificá-la aqui criaria uma segunda fonte de verdade que sairia do ar no dia
em que o WBC mudasse. O legado faz igual (`if ret.Count > 0`).

Reproduzir os dois caminhos importa: gravar preço e quantidade no caminho 2 —
como esta implementação chegou a fazer — inventa `Qtde = 1` e um "preço" que é
o valor total da linha, ficando *parecido* com o caminho 1 sem ser. Melhor
gravar menos e verdadeiro.

--------------------------------------------------------------------------
Campo que continua sem origem
--------------------------------------------------------------------------
`U_INO_LINHA` guarda o número da linha do **pedido** no SAP correspondente ao
item, e o legado o preenche consultando `RDR1` (`GetLinha`) — só faz sentido
quando já existe pedido, e vem nulo nos registros de referência. Fica de fora
até haver um caso real que o exija.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from wbcpython.domain.numeros import formatar_para_sap
from wbcpython.infrastructure.wbc_sql.models import (
    ItemArvoreWbc,
    ItemOrcamentoWbc,
    OrcamentoWbc,
)

COLECAO_LINHAS = "INO_ORC_LINHACollection"

#: Limite dos campos de texto do UDO. Textos maiores são cortados, e os que têm
#: campo de continuação (`_2`) transbordam para ele em vez de perder o resto.
LIMITE_TEXTO = 254

#: Campos de texto do UDO que guardam números (ver `domain/numeros`).
CAMPOS_NUMERICOS_TEXTUAIS = ("U_ORCVALEMB", "U_ORCIMP_RETORNO", "U_ORCIMP_INDICE_VENDAS")


def _data_iso(valor: date | None) -> str | None:
    return valor.isoformat() if valor else None


def _texto(valor: str) -> str:
    return (valor or "").strip()


def _com_continuacao(payload: dict[str, Any], campo: str, valor: str, *, continuacao: str) -> None:
    """Grava um texto longo, transbordando o excedente para o campo `_2`.

    O legado faz isso com `PGTCOD` e simplesmente **corta** `ORCPGT` e
    `ORCIMP_MONTAGEM`. Aqui todo campo que tem par de continuação no UDO usa o
    par: o campo existe justamente para isso, e um acabamento cortado no meio
    de uma frase é um retrato histórico errado.
    """
    valor = _texto(valor)
    if not valor:
        return
    payload[campo] = valor[:LIMITE_TEXTO]
    if len(valor) > LIMITE_TEXTO:
        payload[continuacao] = valor[LIMITE_TEXTO : LIMITE_TEXTO * 2]


def montar_payload_orcdetalhe(
    orcamento: OrcamentoWbc, *, capturado_em: date | None = None
) -> dict[str, Any]:
    """Monta o corpo do `POST /b1s/v1/OrcDetalhe` a partir do orçamento do WBC.

    Campos sem origem no WBC são **omitidos**, não zerados: omitir deixa o SAP
    aplicar o padrão dele, enquanto zerar afirmaria um valor que não temos.

    `capturado_em` existe para o teste fixar a data; em produção é hoje.
    """
    imp = orcamento.impressao

    payload: dict[str, Any] = {
        "U_INO_COD": orcamento.orcnum,
        # A revisão do OrcDetalhe é a **da impressão** (`ORCIMP_REVISAO`), não a
        # do cabeçalho que dirige a máquina de estados. São colunas distintas no
        # WBC e o legado usa cada uma no seu lugar:
        # `SetProperty("U_ORCIMP_REVISAO", item.ORCIMP_REVISAO)`, sem alternativa.
        #
        # Havia aqui um `or orcamento.revisao` que contradizia o próprio
        # comentário acima. O efeito é silencioso e real: no orçamento
        # `00125528`, a impressão não tem revisão e o cabeçalho tem `'A'` — a
        # produção grava vazio, e nós gravávamos `'A'`. O campo passava a
        # significar uma coisa ou outra conforme o dado, que é o pior dos casos
        # para quem lê o relatório.
        "U_ORCIMP_REVISAO": imp.revisao,
        "U_CLINOM": orcamento.cliente_nome,
        "U_REPCOD": orcamento.representante,
        # --- valores ---------------------------------------------------------
        "U_ORCVALVND": float(imp.valor_venda),
        "U_ORCVALLST": float(imp.valor_lista),
        "U_ORCVALINV": float(imp.valor_investimento),
        "U_ORCVALLUC": float(imp.valor_lucro),
        "U_ORCVALEXP": float(imp.valor_expedicao),
        "U_ORCVALCOM": float(imp.valor_comissao),
        "U_ORCPERCOM": float(imp.percentual_comissao),
        "U_ORCVALTRP": float(imp.valor_transporte),
        "U_ORCVALMON": float(imp.valor_montagem),
        "U_ORCBAS1": float(imp.base1),
        "U_ORCBAS2": float(imp.base2),
        "U_ORCBAS3": float(imp.base3),
        # --- cliente e contato ----------------------------------------------
        "U_CLICOD": imp.cliente_codigo,
        "U_CLICONCOD": imp.contato_codigo,
        "U_CLICON": imp.contato,
        # --- condições comerciais -------------------------------------------
        "U_TIPMONCOD": imp.montagem_tipo,
        "U_PRZENT": imp.prazo_entrega,
        "U_TABELA_PRECO": imp.tabela_preco,
        # --- dados de impressão ---------------------------------------------
        "U_ORCIMP_EMAIL": imp.email,
        "U_ORCIMP_FONE": imp.fone,
        "U_ORCIMP_CIDADE": imp.cidade or orcamento.municipio,
        "U_ORCIMP_UF": imp.uf or orcamento.uf,
        "U_ORCIMP_TIPO_VENDA": imp.tipo_venda,
        "U_ORCIMP_TRANSPORTE": imp.transporte,
        # Campos tipados como texto no SAP — gravados sempre no mesmo formato,
        # para não perpetuar a inconsistência de separador decimal existente.
        "U_ORCVALEMB": formatar_para_sap(imp.valor_embalagem),
        "U_ORCIMP_RETORNO": formatar_para_sap(orcamento.retorno),
        "U_ORCIMP_INDICE_VENDAS": formatar_para_sap(orcamento.indice_vendas),
        "U_ORCIMP_NEGOCIACAO": formatar_para_sap(orcamento.negociacao),
        COLECAO_LINHAS: montar_linhas_snapshot(orcamento),
    }

    _com_continuacao(payload, "U_PGTCOD", imp.pagamento_codigo, continuacao="U_PGTCOD2")
    _com_continuacao(
        payload, "U_ORCIMP_ACABAMENTO", imp.acabamento, continuacao="U_ORCIMP_ACABAMENTO2"
    )
    if imp.pagamento_texto:
        payload["U_ORCPGT"] = imp.pagamento_texto[:LIMITE_TEXTO]
    if imp.montagem:
        payload["U_ORCIMP_MONTAGEM"] = imp.montagem[:LIMITE_TEXTO]

    # `U_INO_DATA` é **quando o retrato foi tirado**, não a data do orçamento.
    # O legado grava `DateTime.Now` aqui, e faz sentido: o OrcDetalhe é
    # append-only, então o mesmo orçamento gera vários registros ao longo do
    # tempo e a data é o que os distingue. Gravar a data do orçamento — como
    # esta implementação fazia — deixava todos os retratos com a mesma data,
    # justamente perdendo a informação que o campo carrega.
    payload["U_INO_DATA"] = _data_iso(capturado_em or date.today())  # noqa: DTZ011

    return payload


def montar_linhas_snapshot(orcamento: OrcamentoWbc) -> list[dict[str, Any]]:
    """Escolhe entre os dois caminhos de linha e monta a coleção."""
    if orcamento.arvore:
        return [_linha_da_arvore(item) for item in orcamento.arvore]
    return [_linha_de_texto(item) for item in orcamento.itens]


def _linha_da_arvore(item: ItemArvoreWbc) -> dict[str, Any]:
    """Linha vinda da árvore de produtos — o detalhamento de engenharia.

    `U_INO_CODIGO` sai em maiúscula, como no legado: é código, e código com
    caixa variável cria duplicata onde não há.
    """
    return {
        "U_INO_ORCITM": str(item.orcitm),
        "U_INO_CODIGO": item.produto.upper() or None,
        "U_INO_PROD": item.descricao or None,
        "U_INO_COR": item.cor or None,
        "U_INO_NIVEL": str(item.nivel),
        "U_INO_Qtde": float(item.quantidade),
        "U_INO_PESO": float(item.peso),
        "U_INO_PRECO": float(item.preco_unitario),
        "U_INO_TOTAL": float(item.total),
    }


def _linha_de_texto(item: ItemOrcamentoWbc) -> dict[str, Any]:
    """Linha do caminho sem árvore: só sequência e texto, como no legado.

    Nada de quantidade ou preço aqui. `ORCPRDQTD` é nula em todas as linhas do
    WBC e `ORCVAL` é o total, não o unitário — gravá-los produziria um retrato
    que parece detalhado e não é.
    """
    return {
        "U_INO_ORCITM": str(item.orcitm),
        "U_INO_ORCTXT": item.texto,
    }
