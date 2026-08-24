"""situacao_pedidos_hana -- leitura da view de Situacao dos Pedidos no HANA (F2).

A camada de I/O que falta ao :mod:`situacao_pedidos` (que e' puro de proposito e nao
pode ganhar rede sem quebrar o teste de diffabilidade). Aqui mora o SELECT, o cache e a
traducao de "HANA fora do ar" para uma mensagem que a F3 vira 503.

**Esta e' a primeira leitura HANA sincrona servida por request neste repo.** Todas as
rotas de leitura de hoje batem no Supabase; o ``sap_connection`` so era usado pelos
pipelines (que rodam sozinhos, sem ninguem esperando) e pelo *ping* do ``/status``. Tres
consequencias, todas deliberadas:

1. **Uma conexao por leitura, aberta e fechada na hora.** Conexao ``hdbcli`` nao e'
   thread-safe e o waitress atende em varias threads; compartilhar uma so pediria lock e
   reconexao. Com o cache de 120s isso da, no pior caso, **uma** conexao a cada 2 min --
   barato demais para justificar a complexidade.
2. **Guarda de volume antes do SELECT.** A view devolve o recorte inteiro, sem filtro de
   data (decisao do dono). Se um dia ela mudar de natureza e passar a devolver historico,
   e' melhor um erro explicito que 200 mil linhas na resposta.
3. **Erro legivel, nunca 500 cru.** :class:`SAPIndisponivel` carrega a mensagem que vai
   para a tela de quem perguntou.

O SQL e' o MESMO do ``sap_hana_client.fetch_status_pedidos`` do V117 -- e' isso que faz a
.11 e a tela responderem igual. Ao mexer nele, mexa nos dois.

Plano: ``docs/PLANO_SITUACAO_PEDIDOS_MCP.md``.
"""
from __future__ import annotations

import logging
import threading
import time
from decimal import Decimal
from typing import Any

import sap_montagem_labels
from config import get_settings
from sap_connection import connect_sap_hana
from situacao_pedidos import _MAX_LINHAS, ValidationError

logger = logging.getLogger(__name__)

__all__ = [
    "CACHE_TTL_SEGUNDOS",
    "SAPIndisponivel",
    "fetch_status_pedidos",
    "fetch_udf_valid_values",
    "idade_do_cache_s",
    "limpar_cache",
    "ligar_rotulos_do_sap",
]

#: 120s. O que garante que as 3 consultas do plano respondam sobre o MESMO retrato -- e
#: o que impede um modelo em laco de martelar o HANA. Curto o bastante para "situacao
#: agora" continuar significando agora.
CACHE_TTL_SEGUNDOS = 120

#: Nome da view. Fixo, e nao configuravel por ``.env``: e' contrato com o V117, nao
#: preferencia de ambiente. O schema, esse sim, vem do ambiente (``SAP_SCHEMA``).
VIEW_STATUS_PEDIDO = "VW_STATUS_PEDIDO_DDP"


class SAPIndisponivel(RuntimeError):
    """HANA fora do ar, credencial ausente ou consulta que estourou.

    A F3 traduz para **503** com ``str(e)`` no corpo. Existe separada da
    :class:`situacao_pedidos.ValidationError` porque as duas viram HTTP diferente:
    parametro errado e' culpa de quem chamou (422), HANA fora nao e' (503).
    """


# As 23 colunas da view + o que vem dos joins. Copia fiel do V117 -- ver o docstring do
# modulo. Prazo_Entrega e' TEXTO pronto ("21/09 A 25/09", sem ano) e vai como veio.
STATUS_PEDIDO_COLS = (
    'v."DocEntry", v."DocNum", v."Data_Pedido", v."CardCode", v."CardName", '
    'v."GroupNum", v."PymntGroup", v."Integrar", v."Financeiro", v."Sinal", '
    'v."Producao", v."Entrega", v."Data_Entrega", v."Prazo_Entrega", '
    'v."Atrasado", v."DDO", v."Peso", v."StatusPedido", '
    'v."Data_Lib_Fin", v."Data_Lib_Prod", v."Data_Pagto", '
    'v."Total_OS", v."Total_OS_Fechadas", '
    # Montagem: a view nao carrega as UDFs; elas vem da ORDR, a MESMA fonte que a tela
    # de Pedidos usa. O nome do montador sai da UDT @INO_MONTADOR (o pedido guarda so o
    # CNPJ). A cotacao WBC + revisao idem: a view nao tem, a ORDR tem.
    'o."U_INO_COTWBC" AS "CotacaoWbc", '
    'o."U_INO_VERSAOWBC" AS "VersaoWbc", '
    'o."U_INO_TPO_MONTAGEM" AS "MontagemCod", '
    'o."U_INO_TIPO_MT" AS "MontagemTexto", '
    'o."U_INO_VL_MT" AS "MontagemValor", '
    'o."U_INO_MONTADOR" AS "MontadorCnpj", '
    'm."Name" AS "MontadorNome", '
    # Valor e vendedor: a view nao tem nenhum dos dois. Campos PADRAO, nao UDF --
    # `U_INO_Vendedor` da OSLP NAO existe em producao e ja quebrou um sync.
    'o."DocTotal", o."DocCur", o."SlpCode", '
    's."SlpName" AS "Vendedor"'
)

#: LEFT, nunca INNER: pedido sem montador -- ou que suma da ORDR -- nao pode desaparecer
#: da resposta. Os KPIs contam a VIEW, nao o join.
STATUS_PEDIDO_JOINS = (
    'LEFT JOIN "{schema}"."ORDR" o ON o."DocEntry" = v."DocEntry" '
    'LEFT JOIN "{schema}"."@INO_MONTADOR" m ON m."Code" = o."U_INO_MONTADOR" '
    'LEFT JOIN "{schema}"."OSLP" s ON s."SlpCode" = o."SlpCode"'
)

_cache_lock = threading.Lock()
#: ``(timestamp, linhas)``. Entrada unica: a consulta nao tem parametro nenhum (sem
#: filtro de data -- decisao do dono), entao a chave seria sempre a mesma.
_cache: tuple[float, list[dict[str, Any]]] | None = None


def limpar_cache() -> None:
    """Esvazia o cache de linhas cruas (testes, e o ``?recarregar=1`` da F3)."""
    global _cache
    with _cache_lock:
        _cache = None


def _schema() -> str:
    s = get_settings()
    if not s.sap_ready():
        raise SAPIndisponivel(
            "Consulta ao SAP não configurada (faltam SAP_HOST/SAP_USER/SAP_PASSWORD).")
    if not s.sap_schema:
        raise SAPIndisponivel("Consulta ao SAP não configurada (falta SAP_SCHEMA).")
    return s.sap_schema


def _conectar():
    s = get_settings()
    try:
        return connect_sap_hana(
            s.sap_host, s.sap_port, s.sap_user, s.sap_password, s.sap_database)
    except Exception as e:
        logger.warning("[SIT_PED] HANA inacessível: %s", e)
        raise SAPIndisponivel(
            "Situação dos pedidos indisponível no momento (SAP HANA fora do ar).") from e


def _num(v: Any) -> Any:
    """``Decimal`` do HANA → float (serializável em JSON); o resto passa intacto."""
    return float(v) if isinstance(v, Decimal) else v


def _data_iso(v: Any) -> str | None:
    """Data/timestamp do HANA → ``'YYYY-MM-DD'``, que e' o que o nucleo espera."""
    if v is None:
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    s = str(v)
    return s[:10] if len(s) >= 10 and s[4:5] == "-" else s


def _linhas(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """SELECT → lista de dicts, com as colunas vindas do ``cursor.description``.

    Direto no cursor, e nao pelo ``db_utils.read_dbapi_query`` (que e' o padrao deste
    repo): aquele devolve ``DataFrame``, e a passagem pelo pandas transformaria ``NULL``
    em ``NaN`` e coluna inteira com nulo em float -- o nucleo portado espera ``None`` e
    ``int``. Os pipelines querem DataFrame porque carregam em lote; aqui a saida e' JSON.

    Coluna ``CHAR`` do HANA vem preenchida com espaco a direita; o ``rstrip`` evita
    divergencia cosmetica com a tela (que le pelo Service Layer, sem o padding).
    """
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        return [
            {c: (v.rstrip() if isinstance(v, str) else v) for c, v in zip(cols, r)}
            for r in cur.fetchall()
        ]
    except Exception as e:
        logger.error("[SIT_PED] Falha na consulta HANA: %s", e)
        raise SAPIndisponivel(
            "Falha ao consultar a situação dos pedidos no SAP HANA.") from e
    finally:
        try:
            cur.close()
        except Exception as e:  # cursor ja morto junto com a conexao
            logger.debug("Falha ao fechar cursor HANA (ignorada): %s", e)


def _buscar_no_hana() -> list[dict[str, Any]]:
    """Uma ida ao HANA: conta, confere o volume, seleciona e converte os tipos."""
    schema = _schema()
    conn = _conectar()
    try:
        total = int(_linhas(
            conn, f'SELECT COUNT(*) AS "N" FROM "{schema}"."{VIEW_STATUS_PEDIDO}"'
        )[0].get("N") or 0)
        if total > _MAX_LINHAS:
            raise ValidationError(
                f"A view devolveu {total:,} pedidos (máximo {_MAX_LINHAS:,}) — ela "
                f"mudou de natureza; avise o suporte.".replace(",", "."))

        sql = (
            f'SELECT {STATUS_PEDIDO_COLS} '
            f'FROM "{schema}"."{VIEW_STATUS_PEDIDO}" v '
            f'{STATUS_PEDIDO_JOINS.format(schema=schema)} '
            f'ORDER BY v."Producao", v."Data_Pedido"'
        )
        linhas = _linhas(conn, sql)
    finally:
        try:
            conn.close()
        except Exception as e:
            logger.debug("Falha ao fechar conexão HANA (ignorada): %s", e)

    for r in linhas:
        r["Peso"] = _num(r.get("Peso"))
        r["MontagemValor"] = _num(r.get("MontagemValor"))
        r["DocTotal"] = _num(r.get("DocTotal"))
        for c in ("Data_Pedido", "Data_Entrega", "Data_Pagto",
                  "Data_Lib_Fin", "Data_Lib_Prod"):
            r[c] = _data_iso(r.get(c))
    logger.info("[SIT_PED] %d pedidos lidos da view.", len(linhas))
    return linhas


def fetch_status_pedidos(*, recarregar: bool = False) -> list[dict[str, Any]]:
    """Linhas cruas da view, com cache curto (:data:`CACHE_TTL_SEGUNDOS`).

    Args:
        recarregar: ignora o cache e vai ao HANA. **Nao** e' o default: as 3 consultas
            do plano tem de ver o mesmo retrato, e um cliente MCP em laco passaria a
            interrogar o SAP a cada frase.

    Returns:
        Copia rasa da lista cacheada -- isola o cache de um ``sort()`` acidental de quem
        chamou. Os dicts em si continuam compartilhados: **ninguem pode muta-los**.
        (:func:`situacao_pedidos.normalizar` nao muta; ele constroi dicts novos.)

    Raises:
        SAPIndisponivel: HANA fora, credencial ausente ou consulta que falhou.
        ValidationError: volume acima de ``_MAX_LINHAS``.
    """
    global _cache

    if not recarregar:
        agora = time.monotonic()
        with _cache_lock:
            if _cache and (agora - _cache[0]) < CACHE_TTL_SEGUNDOS:
                return list(_cache[1])

    linhas = _buscar_no_hana()
    with _cache_lock:
        _cache = (time.monotonic(), linhas)
    return list(linhas)


def idade_do_cache_s() -> float | None:
    """Ha quantos segundos o retrato foi tirado — ``None`` se nao ha cache.

    A F3 devolve isto no corpo: quem le "bloqueado" precisa saber se o dado e' de agora
    ou de 2 minutos atras.
    """
    with _cache_lock:
        if not _cache:
            return None
        return round(time.monotonic() - _cache[0], 1)


def fetch_udf_valid_values(table_id: str, alias_id: str) -> list[dict[str, str]]:
    """Valores validos de uma UDF, como o SAP os define -- a fonte dos rotulos.

    O B1 guarda a metadata da UDF em ``CUFD`` (``TableID`` + ``AliasID`` → ``FieldID``) e
    a lista do dropdown em ``UFD1`` (``FldValue`` + ``Descr``, na ordem do ``IndexID``).
    Ler daqui e' o que evita um mapa de rotulos chumbado no codigo.

    O ``AliasID`` e' o nome do campo **sem** o prefixo ``U_``: a coluna
    ``ORDR.U_INO_TPO_MONTAGEM`` tem ``AliasID = 'INO_TPO_MONTAGEM'``. O filtro por tabela
    e' obrigatorio -- o mesmo alias existe em ~30 delas e a lista de valores e' por tabela.

    Returns:
        ``[{"value": ..., "descr": ...}, ...]`` na ordem do SAP; ``[]`` quando o campo
        nao tem lista. Nunca levanta: quem chama (``sap_montagem_labels``) tem fallback,
        e ficar sem rotulo e' pior que o rotulo de ontem.
    """
    try:
        schema = _schema()
        conn = _conectar()
    except SAPIndisponivel as e:
        logger.warning("[UDF] %s.U_%s indisponível: %s", table_id, alias_id, e)
        return []

    try:
        linhas = _linhas(
            conn,
            f'SELECT v."FldValue", v."Descr" '
            f'FROM "{schema}"."CUFD" c '
            f'JOIN "{schema}"."UFD1" v '
            f'  ON v."TableID" = c."TableID" AND v."FieldID" = c."FieldID" '
            f'WHERE c."TableID" = ? AND c."AliasID" = ? '
            f'ORDER BY v."IndexID"',
            (table_id, alias_id),
        )
    except SAPIndisponivel as e:
        logger.warning("[UDF] %s.U_%s falhou: %s", table_id, alias_id, e)
        return []
    finally:
        try:
            conn.close()
        except Exception as e:
            logger.debug("Falha ao fechar conexão HANA (ignorada): %s", e)

    valores = [
        {"value": str(r.get("FldValue") or "").strip(),
         "descr": str(r.get("Descr") or "").strip()}
        for r in linhas
        if str(r.get("FldValue") or "").strip()
    ]
    logger.info("[UDF] %s.U_%s: %d valores válidos.", table_id, alias_id, len(valores))
    return valores


def ligar_rotulos_do_sap() -> None:
    """Liga o gancho que a F1 deixou: o rotulo de montagem passa a vir do SAP.

    Chamado uma vez na subida da API (F3). Sem isto, ``sap_montagem_labels`` responde
    pelo ``FALLBACK_LABELS`` -- que esta correto, mas congelado em 27/07/2026.
    """
    sap_montagem_labels.registrar_fonte(fetch_udf_valid_values)
    logger.info("[MONTAGEM] rótulos passam a vir do SAP (fallback continua de rede).")
