"""situacao_pedidos_hana -- reads the order-status view from HANA (F2).

The I/O layer that :mod:`situacao_pedidos` lacks (that one is pure on purpose and cannot gain
network access without breaking the diffability test). The SELECT, the cache and the translation
of "HANA is down" into a message that F3 turns into a 503 live here.

**This was the first synchronous HANA read served per request in this repo** (the later
``pedidos_compra_hana`` copies its shape). Until then every read route hit Supabase;
``sap_connection`` was used only by the pipelines (which run on their own, with nobody waiting)
and by the ``/status`` *ping*. Three consequences, all deliberate:

1. **One connection per read, opened and closed on the spot.** An ``hdbcli`` connection is not
   thread-safe and waitress serves in several threads; sharing one would need a lock and
   reconnection logic. With the 120 s cache this means, in the worst case, **one** connection
   every 2 min -- too cheap to justify the complexity.
2. **Volume guard before the SELECT.** The view returns the whole slice, with no date filter
   (owner's decision). If it ever changes nature and starts returning history, an explicit
   error beats 200 thousand rows in the response.
3. **Readable error, never a raw 500.** :class:`SAPIndisponivel` carries the message that goes
   to the screen of whoever asked.

The SQL is the SAME as the web's ``sap_hana_client.fetch_status_pedidos`` (OrcaView V118, earlier
V117) -- that is what makes the .11 and the screen answer the same. When changing one, change both.

Plan: ``PLANO_SITUACAO_PEDIDOS_MCP.md (removido em 2026-09-29)``.

"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sap_montagem_labels
from config import get_settings
from sap_connection import connect_sap_hana
from situacao_pedidos import _MAX_LINHAS, ValidationError, now_br
from sql_seguro import nome_simples, sql

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

#: TEMPORARY (Marcelo, 2026-10-09): orders this API and the MCP report as released in all
#: three stages while SAP still blocks them, so another team can test romaneio/OS on a real
#: order. Only the raw rows read here change: SAP, the web (.90) and the app read their own
#: sources and keep seeing the real status. Roll back = empty the set.
LIBERACAO_FORCADA_DOCNUM: frozenset[int] = frozenset({84326})


class SAPIndisponivel(RuntimeError):
    """HANA down, missing credential, or a query that blew up.

    F3 translates it to **503** with ``str(e)`` in the body. It is separate from
    :class:`situacao_pedidos.ValidationError` because the two become different HTTP codes: a wrong
    parameter is the caller's fault (422), HANA being down is not (503).

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
    's."SlpName" AS "Vendedor", '
    # Endereco de entrega (RDR12). Ver ENDERECO_COLS.
    + ", ".join(f'a."{c}"' for c in
                ("StrtDlvryP", "StrNoDlvrP", "BldDlvryP", "BlckDlvryP", "CityDlvryP",
                 "StatDlvryP", "ZipDlvryP", "CntyDlvryP", "CtryDlvryP",
                 "StreetS", "StreetNoS", "BuildingS", "BlockS", "CityS",
                 "StateS", "ZipCodeS", "CountyS", "CountryS"))
)

#: As colunas de endereco da RDR12, por sufixo. **A grafia e' irregular e o mapa e'
#: explicito de proposito**: ``StrNoDlvrP`` nao tem o "y", e ha ``BldDlvryP`` ao lado de
#: ``BuildingS``. Um loop de sufixo produziria nome errado -- foi a armadilha da
#: implementacao de 13/08 no OrcaView (``sap_hana_client._DELIVERY_PLACE_COLS``, de onde
#: isto e' copia fiel).
#:
#: ``*DlvryP`` = "Local de entrega" (o campo BR que a tela marca com o selo "difere do
#: ponto de entrega"); ``*S`` = ShipTo, o "Ponto de Entrega". Medido em 10/09 no recorte
#: de 266 pedidos: 38 tem o Local preenchido, e em 24 deles a CIDADE difere do ShipTo.
ENDERECO_COLS = {
    "local": {"logradouro": "StrtDlvryP", "numero": "StrNoDlvrP",
              "complemento": "BldDlvryP", "bairro": "BlckDlvryP",
              "cidade": "CityDlvryP", "uf": "StatDlvryP", "cep": "ZipDlvryP",
              "municipio_cod": "CntyDlvryP", "pais": "CtryDlvryP"},
    "ponto": {"logradouro": "StreetS", "numero": "StreetNoS",
              "complemento": "BuildingS", "bairro": "BlockS",
              "cidade": "CityS", "uf": "StateS", "cep": "ZipCodeS",
              "municipio_cod": "CountyS", "pais": "CountryS"},
}

#: LEFT, nunca INNER: pedido sem montador -- ou que suma da ORDR -- nao pode desaparecer
#: da resposta. Os KPIs contam a VIEW, nao o join.
#:
#: A ``RDR12`` (endereco do documento) e' 1:1 por ``DocEntry`` e entra pelo mesmo motivo:
#: LEFT. Medido em 10/09 nos 266 pedidos do recorte, nenhum ficou sem linha na RDR12 --
#: mas basta um para o INNER apagar um pedido da lista sem erro nenhum.
#:
#: **A OCNT NAO entra aqui.** O municipio e' um codigo (``CntyDlvryP``/``CountyS`` =
#: ``OCNT.AbsId``) e ha linha historica com o campo vazio; no plano de execucao do join o
#: HANA avalia a conversao em linhas que o filtro descartaria e a consulta MORRE com
#: "invalid number". Quem resolve o nome e' um SELECT a parte (F2), como o
#: ``fetch_municipios`` do OrcaView.
STATUS_PEDIDO_JOINS = (
    'LEFT JOIN "{schema}"."ORDR" o ON o."DocEntry" = v."DocEntry" '
    'LEFT JOIN "{schema}"."@INO_MONTADOR" m ON m."Code" = o."U_INO_MONTADOR" '
    'LEFT JOIN "{schema}"."OSLP" s ON s."SlpCode" = o."SlpCode" '
    'LEFT JOIN "{schema}"."RDR12" a ON a."DocEntry" = v."DocEntry"'
)

_cache_lock = threading.Lock()
#: ``(timestamp, linhas)``. Entrada unica: a consulta nao tem parametro nenhum (sem
#: filtro de data -- decisao do dono), entao a chave seria sempre a mesma.
_cache: tuple[float, list[dict[str, Any]]] | None = None


def limpar_cache() -> None:
    """Empty the raw-rows cache (used by the tests).

    The routes' ``?recarregar=1`` does not call this: it passes ``recarregar=True`` to
    :func:`fetch_status_pedidos`, which reads HANA and refreshes the cache.

    """
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
    # O schema vem do .env e entra no SQL como "{schema}" (nome de objeto não é parâmetro):
    # conferido pela mesma regra do resto do repo antes de virar texto de consulta.
    try:
        return nome_simples(s.sap_schema, what="SAP_SCHEMA")
    except ValueError as e:
        raise SAPIndisponivel(f"SAP_SCHEMA inválido no .env: {e}") from e


def _conectar() -> Any:
    s = get_settings()
    try:
        return connect_sap_hana(
            s.sap_host, s.sap_port, s.sap_user, s.sap_password, s.sap_database)
    except Exception as e:
        logger.warning("[SIT_PED] HANA inacessível: %s", e)
        raise SAPIndisponivel(
            "Situação dos pedidos indisponível no momento (SAP HANA fora do ar).") from e


def _num(v: Any) -> Any:
    """HANA ``Decimal`` → float (JSON-serializable); anything else passes through untouched."""
    return float(v) if isinstance(v, Decimal) else v


def _data_iso(v: Any) -> str | None:
    """HANA date/timestamp → ``'YYYY-MM-DD'``, which is what the core expects."""
    if v is None:
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    s = str(v)
    return s[:10] if len(s) >= 10 and s[4:5] == "-" else s


def _linhas(conn: Any, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """SELECT → list of dicts, with the columns taken from ``cursor.description``.

    Straight on the cursor rather than through ``db_utils.read_dbapi_query`` (the repo's usual way):
    that returns a ``DataFrame``, and going through pandas would turn ``NULL`` into ``NaN`` and any
    integer column holding a null into float -- the ported core expects ``None`` and ``int``. The
    pipelines want a DataFrame because they load in bulk; here the output is JSON. (Only
    ``_ExecutorDaConexao``, for ``faturamento_pedido``, goes through ``read_dbapi_query``.)

    HANA ``CHAR`` columns come right-padded with spaces; the ``rstrip`` avoids a cosmetic mismatch
    with the screen (which reads through the Service Layer, without the padding).

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


#: Chave injetada com o NOME do municipio, por lado do endereco. Comeca com ``_`` porque
#: nao e' coluna do SAP: e' derivada aqui, como o ``_DeliveryPlaceCountyName`` do V118.
MUNICIPIO_CHAVES = {"local": "_MunicipioLocal", "ponto": "_MunicipioPonto"}


def _injetar_municipios(conn: Any, schema: str, linhas: list[dict[str, Any]]) -> None:
    """Resolve ``CntyDlvryP``/``CountyS`` (codes = ``OCNT.AbsId``) to the city NAME, **in a single
    query**, and store it under :data:`MUNICIPIO_CHAVES`.

    Three decisions worth reading:

    1. **Outside the JOIN.** There are historical rows with the field empty; in the join's execution
       plan HANA evaluates the conversion on rows the filter would discard and the query DIES with
       "invalid number". A separate ``SELECT ... WHERE AbsId IN (...)`` works -- it is the pattern
       of OrcaView's ``fetch_municipios``.
    2. **Same connection, same cycle, same cache.** It opens no new connection (one per read is this
       module's rule) and keeps no cache of its own: the name travels inside the rows themselves,
       which already have the slice's 120 s cache. No table, no mirror, no job.
    3. **Best-effort.** If the OCNT query fails, the whole slice does NOT go down over a city name:
       the keys stay ``None`` (which already means "could not be determined") and it is logged.
       Measured 10/09: ``CountyS`` is filled in 266 of 266 orders, so in practice this is almost
       never empty.

    The ``IN`` list is built with ``int()`` on each element -- the codes enter as numbers, never as
    text coming from outside.

    """
    codigos: set[int] = set()
    for r in linhas:
        for lado, cols in ENDERECO_COLS.items():
            r[MUNICIPIO_CHAVES[lado]] = None          # a chave existe sempre
            bruto = str(r.get(cols["municipio_cod"]) or "").strip()
            if bruto.isdigit():
                codigos.add(int(bruto))
    if not codigos:
        return

    lista = ",".join(str(c) for c in sorted(codigos))
    try:
        nomes = {
            int(m["AbsId"]): (m.get("Name") or "").strip()
            for m in _linhas(conn, f'SELECT "AbsId", "Name" FROM "{schema}"."OCNT" '
                                   f'WHERE "AbsId" IN ({lista})')
            if m.get("AbsId") is not None
        }
    except Exception as e:
        # SAPIndisponivel inclusive: o nome da cidade nao vale derrubar a Situacao.
        logger.warning("[SIT_PED] municipios da OCNT indisponiveis (%s) — seguindo sem.", e)
        return

    for r in linhas:
        for lado, cols in ENDERECO_COLS.items():
            bruto = str(r.get(cols["municipio_cod"]) or "").strip()
            if bruto.isdigit():
                r[MUNICIPIO_CHAVES[lado]] = nomes.get(int(bruto))
    logger.info("[SIT_PED] %d municipios resolvidos na OCNT.", len(nomes))


# ─────────────── Liberacao real + primeira NF (PLANO_DATAS_LIBERACAO_NF) ───────────────
# As datas da view NAO sao liberacao: `Data_Lib_Prod` e' MAX(Data_Lib_Fin, Data_Pagto) + 3
# dias corridos, `Data_Lib_Fin` e' digitada (+1 dia em 182 de 252) e `Data_Pagto` e' a
# EMISSAO do sinal, nao o pagamento. Medido em 25/09/2026 nos 281 pedidos do recorte.
#
# A regra que o SAP aplica (281 de 281, zero excecao): Producao (= Entrega, sempre iguais)
# liberada <=> Financeiro liberado E (sem sinal OU a ULTIMA Solicitacao de Adiantamento
# nao cancelada esta fechada). Sinal reemitido re-bloqueia pedido que ja tinha pago.
# Entao o momento real e' o mais tardio entre:
#   - a ultima passagem de `U_INO_PedLib` de 'N' para 'S' no historico (ADOC);
#   - o REGISTRO do recebimento que fechou a ultima ODPI (ORCT.CreateDate + CreateTS). A
#     `DocDate` do recebimento e' contabil e vem retroativa (44 de 46 diferem).

#: A view de orcamentos, a mesma do ``extract_orcamentos_espelho``. A linha do pedido e'
#: ``TipoDoc = '17'``, com ``NumDoc`` = DocNum do pedido.
VIEW_ORCAMENTOS = "VW_EVOL_ORCAMENTO_ALT"

#: Chaves injetadas nas linhas cruas. Comecam com ``_`` porque nao sao colunas da view,
#: como :data:`MUNICIPIO_CHAVES`. Existem sempre; ``None`` = "nao foi possivel saber".
LIBERACAO_CHAVES = ("_LibFinEm", "_LibFinAte", "_SinalPagoEm", "_DataCriacaoPN",
                    "_Representante", "_NfDocNum", "_NfNumeroFiscal", "_NfData",
                    "_Incoterms", "_ObsMontagem")


def texto_livre(valor: Any) -> str | None:
    """Free text typed in SAP (``ObsMontagem``) → API text, or ``None`` when empty.

    SAP B1 stores line breaks as a bare CR (measured 13/10/2026: 419 of 1228 orders),
    which most JSON consumers do not render; they become LF and stay. ``"-"`` is what
    SAP keeps in place of an empty field elsewhere, so it also means "not filled in".
    """
    texto = str(valor or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return None if texto in ("", "-") else texto


def momento(data: Any, hhmmss: Any) -> datetime | None:
    """Date + integer ``HHMMSS`` time from B1 (``UpdateTS``, ``CreateTS``) → timezone-aware datetime.

    B1 stores the time apart, as an integer (``165116`` = 16:51:16), in the server's timezone, which
    is São Paulo's. Without a date or without a time it returns ``None``: an invented midnight would
    look like data.

    """
    if data is None or hhmmss is None:
        return None
    try:
        ts = int(hhmmss)
        h, m, s = ts // 10000, (ts // 100) % 100, ts % 100
        base = data if isinstance(data, date) else date.fromisoformat(str(data)[:10])
        return datetime(base.year, base.month, base.day, h, m, s, tzinfo=now_br().tzinfo)
    except (TypeError, ValueError):
        return None


def ultima_liberacao(versoes: list[tuple[Any, datetime | None]],
                     historico_completo: bool = True) -> datetime | None:
    """Moment of the LAST Financeiro passage to released, from the change log.

    ``versoes`` = ``[(U_INO_PedLib, momento), ...]`` in ``LogInstanc`` order. An order born
    ``'S'`` counts its first version (released since creation). Re-blocked and released
    again (11 in the 25/09 cut) → the most recent release. Last version not released → ``None``.

    ``historico_completo=False``: the SAP keeps only the last 99 versions of a document and
    drops the oldest (30 of 295 orders on 02/10/2026). The oldest RETAINED version being
    ``'S'`` then says nothing about when the release happened — counting it reported 84348
    as released at 16:30 when it was 15:06, and the time drifted later with every new save.
    Only a passage N→S visible inside the window counts; otherwise ``None``.
    """
    liberado_em, anterior = None, None
    for pl, quando in versoes:
        pl = str(pl or "").strip().upper()
        if pl == "S" and anterior != "S" and (anterior is not None or historico_completo):
            liberado_em = quando
        anterior = pl
    return liberado_em if anterior == "S" else None


def liberado_ate(versoes: list[tuple[Any, datetime | None]]) -> datetime | None:
    """Truncated log already released in every retained version → the release happened at
    or before the oldest one. An upper bound for :func:`liberacao_e_nf`, never a release time.
    """
    if versoes and all(str(pl or "").strip().upper() == "S" for pl, _ in versoes):
        return versoes[0][1]
    return None


def sinal_pago_em(odpis: list[dict[str, Any]],
                  recebimentos: dict[int, list[datetime | None]]) -> datetime | None:
    """When the order's down payment was paid, or ``None``.

    Only the LAST non-cancelled Advance Request counts (highest ``DocEntry``): the earlier ones are
    reissues of the same down payment, and a new open one blocks the order again. Open → ``None``.
    Closed → the latest registration among its receipts.

    """
    vivas = [o for o in odpis if str(o.get("CANCELED") or "N").strip().upper() == "N"]
    if not vivas:
        return None
    ultima = max(vivas, key=lambda o: int(o["DocEntry"]))
    if str(ultima.get("DocStatus") or "").strip().upper() != "C":
        return None
    momentos = [m for m in recebimentos.get(int(ultima["DocEntry"]), []) if m is not None]
    return max(momentos) if momentos else None


def _iso(v: datetime | None) -> str | None:
    return v.isoformat(timespec="seconds") if v is not None else None


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _injetar_liberacao_e_nf(conn: Any, schema: str, linhas: list[dict[str, Any]]) -> None:
    """Write :data:`LIBERACAO_CHAVES` onto the raw rows: 3 blocks, up to 4 queries for the whole slice.

    The blocks are the ADOC history; the down-payment documents (DPI1/ODPI) with their receipts
    (RCT2/ORCT, a second query); and the orcamentos view with the DANFE number.

    Same design as :func:`_injetar_municipios`: same connection, no cache of its own (it travels in
    the rows, which already have the 120 s one) and **best-effort per block** -- the history being
    down does not take the Situacao down nor erase the NF. Cost measured on 25/09: ~1.0 s on the
    281-order slice.

    The ``IN`` lists are built with ``int()`` on each element, as for the municipality.

    """
    for r in linhas:
        for k in LIBERACAO_CHAVES:
            r[k] = None
    entries = sorted({e for e in (_int(r.get("DocEntry")) for r in linhas) if e is not None})
    docnums = sorted({n for n in (_int(r.get("DocNum")) for r in linhas) if n is not None})
    por_entry = {_int(r.get("DocEntry")): r for r in linhas}
    if not entries:
        return
    in_entries = ",".join(str(e) for e in entries)

    # 1) Financeiro: historico do pedido (ADOC, ObjType 17).
    try:
        versoes: dict[int, list[tuple[Any, datetime | None]]] = {}
        primeira: dict[int, int | None] = {}
        for h in _linhas(conn,
                         f'SELECT "DocEntry", "UpdateDate", "UpdateTS", "U_INO_PedLib", '
                         f'"LogInstanc" '
                         f'FROM "{schema}"."ADOC" WHERE "ObjType" = \'17\' '
                         f'AND "DocEntry" IN ({in_entries}) '
                         f'ORDER BY "DocEntry", "LogInstanc"'):
            e = int(h["DocEntry"])
            primeira.setdefault(e, _int(h.get("LogInstanc")))
            versoes.setdefault(e, []).append(
                (h.get("U_INO_PedLib"), momento(h.get("UpdateDate"), h.get("UpdateTS"))))
        for e, vs in versoes.items():
            if e in por_entry:
                # LogInstanc starts at 1; a higher first one means the SAP dropped the oldest.
                completo = primeira.get(e) == 1
                por_entry[e]["_LibFinEm"] = _iso(ultima_liberacao(vs, completo))
                if not completo:
                    por_entry[e]["_LibFinAte"] = _iso(liberado_ate(vs))
    except Exception as e:
        logger.warning("[SIT_PED] historico (ADOC) indisponivel (%s) — seguindo sem.", e)

    # 2) Sinal: ultima ODPI do pedido e os recebimentos dela (RCT2 InvType 203 → ORCT).
    try:
        odpis: dict[int, list[dict[str, Any]]] = {}
        for o in _linhas(conn,
                         f'SELECT d."BaseEntry", p."DocEntry", p."DocStatus", p."CANCELED" '
                         f'FROM "{schema}"."DPI1" d '
                         f'JOIN "{schema}"."ODPI" p ON p."DocEntry" = d."DocEntry" '
                         f'WHERE d."BaseType" = 17 AND d."LineNum" = 0 '
                         f'AND d."BaseEntry" IN ({in_entries})'):
            odpis.setdefault(int(o["BaseEntry"]), []).append(o)
        todas = sorted({int(o["DocEntry"]) for os_ in odpis.values() for o in os_})
        recebimentos: dict[int, list[datetime | None]] = {}
        if todas:
            for r in _linhas(conn,
                             f'SELECT x."DocEntry", r."CreateDate", r."CreateTS", r."Canceled" '
                             f'FROM "{schema}"."RCT2" x '
                             f'JOIN "{schema}"."ORCT" r ON r."DocEntry" = x."DocNum" '
                             f'WHERE x."InvType" = \'203\' '
                             f'AND x."DocEntry" IN ({",".join(str(d) for d in todas)})'):
                if str(r.get("Canceled") or "N").strip().upper() != "Y":
                    recebimentos.setdefault(int(r["DocEntry"]), []).append(
                        momento(r.get("CreateDate"), r.get("CreateTS")))
        for e, os_ in odpis.items():
            if e in por_entry:
                por_entry[e]["_SinalPagoEm"] = _iso(sinal_pago_em(os_, recebimentos))
    except Exception as e:
        logger.warning("[SIT_PED] sinal (ODPI/ORCT) indisponivel (%s) — seguindo sem.", e)

    # 3) View de orcamentos (linha do pedido) + numero da DANFE da primeira nota.
    if not docnums:
        return
    try:
        por_docnum: dict[int, dict[str, Any]] = {}
        for v in _linhas(conn,
                         f'SELECT e."NumDoc", e."DataCriacaoPN", e."Representante", '
                         f'e."NumNF", e."DataNF", i."Serial", e."Incoterms", e."ObsMontagem" '
                         f'FROM "{schema}"."{VIEW_ORCAMENTOS}" e '
                         f'LEFT JOIN "{schema}"."OINV" i ON i."DocNum" = e."NumNF" '
                         f'WHERE e."TipoDoc" = \'17\' '
                         f'AND e."NumDoc" IN ({",".join(str(n) for n in docnums)})'):
            n = _int(v.get("NumDoc"))
            # Linha com NF ganha de linha sem NF se o pedido aparecer duas vezes.
            if n is not None and (n not in por_docnum or _int(v.get("NumNF")) is not None):
                por_docnum[n] = v
        for r in linhas:
            v = por_docnum.get(_int(r.get("DocNum")))
            if not v:
                continue
            r["_DataCriacaoPN"] = _data_iso(v.get("DataCriacaoPN"))
            r["_Representante"] = (str(v.get("Representante") or "").strip() or None)
            r["_NfDocNum"] = _int(v.get("NumNF"))
            r["_NfNumeroFiscal"] = _int(v.get("Serial")) if r["_NfDocNum"] else None
            r["_NfData"] = _data_iso(v.get("DataNF")) if r["_NfDocNum"] else None
            # Freight mode as the view labels it (its CASE over RDR12.Incoterms); blank → None.
            r["_Incoterms"] = (str(v.get("Incoterms") or "").strip() or None)
            r["_ObsMontagem"] = texto_livre(v.get("ObsMontagem"))
    except Exception as e:
        logger.warning("[SIT_PED] %s indisponivel (%s) — seguindo sem.", VIEW_ORCAMENTOS, e)


def liberacao_e_nf(r: dict[str, Any]) -> dict[str, Any]:
    """Raw row (with :data:`LIBERACAO_CHAVES`) → the 12 fields of the contract.

    Pure. ``lib_producao_em`` only exists when Producao is released NOW and every dated condition
    holds: without Financeiro's time, or with a down payment required and no payment time, it is
    ``None`` -- an "almost" here would be an invented date. The one exception: when Financeiro's
    time was lost with the old versions (only the upper bound ``_LibFinAte`` is known), a payment
    dated at or after that bound still dates the release. ``lib_entrega_em`` is the same: SAP does
    not separate Producao from Entrega (281 of 281).

    """
    fin = r.get("_LibFinEm")
    ate = r.get("_LibFinAte")
    sinal = r.get("_SinalPagoEm")
    lib_prod = None
    if str(r.get("Producao") or "").strip().lower().startswith("liberad"):
        if str(r.get("Sinal") or "").strip().lower().startswith("s"):
            if sinal and fin:
                lib_prod = max(fin, sinal, key=datetime.fromisoformat)
            elif sinal and ate and (datetime.fromisoformat(sinal)
                                    >= datetime.fromisoformat(ate)):
                # Financeiro's time was lost with the old versions, but it came no later
                # than ``ate``; the payment came after it, so the payment is the moment.
                lib_prod = sinal
        elif fin:
            lib_prod = fin
    nf = r.get("_NfDocNum")
    return {
        "lib_fin_em": fin,
        "sinal_pago_em": sinal,
        "lib_producao_em": lib_prod,
        "lib_entrega_em": lib_prod,
        "data_criacao_pn": r.get("_DataCriacaoPN"),
        "representante": r.get("_Representante"),
        "nf_doc_num": nf,
        "nf_numero_fiscal": r.get("_NfNumeroFiscal"),
        "nf_data": r.get("_NfData"),
        "primeira_nf_emitida": nf is not None,
        "incoterms": r.get("_Incoterms"),
        "obs_montagem": r.get("_ObsMontagem"),
    }


# ─────────────────── B3: qual dos dois enderecos vale ───────────────────
# O SAP guarda DOIS enderecos de entrega no mesmo pedido, e eles podem apontar para
# cidades diferentes. Medido em 10/09 no recorte: 38 pedidos de 266 tem "Local de
# Entrega" preenchido, e em 24 deles a CIDADE difere do ShipTo -- 24 pedidos em que
# despachar pelo Ponto de Entrega manda a carga para a cidade errada.
#
# A API RESOLVE, quem consome nao escolhe (decisao do Marcelo, 10/09: "nao posso deixar
# a outra equipe tomar a decisao"). O `ponto_entrega` viaja aninhado como REFERENCIA
# CADASTRAL, nunca como alternativa de despacho.

#: Minimo de caracteres para um campo contar como preenchido. Regra do Marcelo (10/09),
#: mais estrita que a do OrcaView (`pedido_report._entrega_efetiva`, que aceita qualquer
#: texto nao-branco). Medido antes de aplicar: das 266 linhas do recorte, ZERO tinha 1-2
#: caracteres nesses campos -- a diferenca entre as duas reguas e' teorica hoje, e por
#: isso ela fica so aqui, sem mexer na tela nem no PDF do V118.
MIN_CARACTERES = 3


def _texto(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def _cep(valor: Any) -> str | None:
    """CEP normalized to ``NNNNN-NNN``; anything that is not 8 digits passes through as it came.

    **Measured 10/09: SAP stores both formats, in both columns** -- ``ZipDlvryP`` had 4 with a
    hyphen and 33 without (``'36033000'``); ``ZipCodeS``, 147 and 117. Passing it through as it came
    would give ``36033000`` on one order and ``30330-000`` on another, in the SAME field, and
    consumers would have to normalize. We do not invent digits: we only format what already has 8.

    """
    bruto = _texto(valor)
    if not bruto:
        return None
    so_digitos = "".join(c for c in bruto if c.isdigit())
    if len(so_digitos) == 8:
        return f"{so_digitos[:5]}-{so_digitos[5:]}"
    return bruto


def _linha_do_endereco(e: dict[str, Any]) -> str | None:
    """``'AVENIDA DEUSDEDITH SALGADO, 4010 - SALVATERRA, 36033-000 JUIZ DE FORA-MG'``.

    A ready-made string for a label/screen, built part by part: an empty field leaves no dangling
    comma or orphan hyphen.

    """
    rua = ", ".join(p for p in (e.get("logradouro"), e.get("numero")) if p)
    if e.get("bairro"):
        rua = f"{rua} - {e['bairro']}" if rua else e["bairro"]
    cidade_uf = "-".join(p for p in (e.get("cidade"), e.get("uf")) if p)
    local = " ".join(p for p in (e.get("cep"), cidade_uf) if p)
    return ", ".join(p for p in (rua, local) if p) or None


def _endereco(r: dict[str, Any], lado: str) -> dict[str, Any]:
    """One side of the address (``local`` or ``ponto``) as cleaned fields."""
    cols = ENDERECO_COLS[lado]
    e: dict[str, Any] = {
        campo: (_texto(r.get(coluna)) or None)
        for campo, coluna in cols.items()
        if campo not in ("cep", "municipio_cod")
    }
    e["cep"] = _cep(r.get(cols["cep"]))
    e["municipio"] = r.get(MUNICIPIO_CHAVES[lado]) or None
    e["linha"] = _linha_do_endereco(e)
    return e


def _preenchido(e: dict[str, Any]) -> bool:
    """Has street, city OR CEP with at least :data:`MIN_CARACTERES` characters.

    The same three fields OrcaView's ``_entrega_efetiva`` tests -- it is what the screen uses to
    decide whether to draw the Local de Entrega card with the "differs from the delivery point" badge.

    """
    return any(len(_texto(e.get(c))) >= MIN_CARACTERES
               for c in ("logradouro", "cidade", "cep"))


def endereco_entrega_efetivo(r: dict[str, Any]) -> dict[str, Any]:
    """The order's DISPATCH address, already resolved -- plus the ShipTo as a reference.

    **If there is a filled-in Local de Entrega, it is that one.** Empty, or with fewer than
    :data:`MIN_CARACTERES`, it counts as absent and the default answers (the Ponto de Entrega),
    which is 86% of the cases (measured 10/09).

    The property holding this up: the TOP-level fields are always the effective address. Whoever
    ignores ``fonte``, ignores the badge and reads only ``cidade``/``uf``/``linha`` **still
    dispatches correctly** -- the lazy path is the correct path.

    ``difere_do_ponto_de_entrega`` means "a filled-in Local de Entrega exists", **not** a city
    comparison: the letter case of the two columns diverges in SAP (``'BELO HORIZONTE'`` against
    ``'Belo Horizonte'`` on 84317). It is the same rule as the screen's badge, which shows on all
    38 -- including the 14 that are another address in the SAME city.

    """
    local = _endereco(r, "local")
    ponto = _endereco(r, "ponto")
    usa_local = _preenchido(local)
    efetivo = local if usa_local else ponto
    return {
        "fonte": "local_entrega" if usa_local else "ponto_entrega",
        "difere_do_ponto_de_entrega": usa_local,
        **efetivo,
        "ponto_entrega": ponto,
    }


def cidade_uf(e: dict[str, Any]) -> str | None:
    """``'JUIZ DE FORA-MG'`` -- the ready pair, so the ``resumo`` profile does not have to join two
    fields (and the repo does not hold two assemblies of the same text).
    """
    return "-".join(p for p in (e.get("cidade"), e.get("uf")) if p) or None


def fetch_endereco_do_pedido(doc_num: int) -> dict[str, Any]:
    """The RDR12 of ONE order, by DocNum -- the path for the **CANCELLED** order.

    The view excludes cancelled orders, so such an order does not go through the slice and has
    nowhere to get its address. A cancelled order has an address too, and the ``entrega_endereco``
    key cannot be missing only on it (a test compares the keys of both paths) -- Marcelo's decision
    on 10/09 (D5): read that DocEntry's RDR12 instead of emitting nulls.

    A rare path (the route only gets here when the order is outside the view), one row, a connection
    opened and closed on the spot -- the same design as the slice. Returns ``{}`` when the DocNum
    does not exist; **the caller handles SAPIndisponivel**: the address is not worth taking down the
    answer "this order is cancelled".

    """
    schema = _schema()
    conn = _conectar()
    try:
        sel = ", ".join(f'a."{c}"' for grupo in ENDERECO_COLS.values()
                        for c in grupo.values())
        linhas = _linhas(
            conn,
            f'SELECT {sel} FROM "{schema}"."ORDR" o '
            f'LEFT JOIN "{schema}"."RDR12" a ON a."DocEntry" = o."DocEntry" '
            f'WHERE o."DocNum" = ?', (int(doc_num),))
        if not linhas:
            return {}
        _injetar_municipios(conn, schema, linhas)
        return linhas[0]
    finally:
        try:
            conn.close()
        except Exception as e:
            logger.debug("Falha ao fechar conexão HANA (ignorada): %s", e)


#: Raw keys injected by :func:`_injetar_faturamento` (``_`` = derived here, not a SAP column).
FATURAMENTO_CHAVES = {"faturamento": "_Faturamento", "remessa": "_Remessa",
                      "faturado_em": "_FaturadoEm"}


class _ExecutorDaConexao:
    """``execute_query`` (DataFrame) over this module's open connection, for ``faturamento_pedido``."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute_query(self, query: str, params: Any = None) -> Any:
        from db_utils import read_dbapi_query
        return read_dbapi_query(query, self._conn, params)


def _injetar_faturamento(conn: Any, schema: str, linhas: list[dict[str, Any]]) -> None:
    """Invoicing of every CLOSED row, in place; a pending future-delivery sale becomes ``Aberto``.

    SAP closes the pedido on the future-delivery NF (CFOP 5922/6922) before the goods leave, and
    the view keeps it with ``StatusPedido = 'Fechado'`` (84260, 84367, 84407, 84423, 84430, 84464
    and 84471 on 16/10/2026). Rewriting ``StatusPedido`` on the RAW row, before the ported core
    normalizes it, makes every consumer agree — status filter, KPIs, late flag, finance alert —
    without touching the ported file. The SAP value stays in ``_StatusPedidoSap``.

    Best-effort, like the other injections: a failed read keeps the view's answer.
    """
    from faturamento_pedido import ler_faturamento

    for r in linhas:
        for k in FATURAMENTO_CHAVES.values():
            r[k] = None
        r["_StatusPedidoSap"] = r.get("StatusPedido")
    fechadas = {e: r for r in linhas
                if str(r.get("StatusPedido") or "").strip().casefold() == "fechado"
                and (e := _int(r.get("DocEntry"))) is not None}
    if not fechadas:
        return
    resultado = ler_faturamento(_ExecutorDaConexao(conn), schema, list(fechadas))
    if resultado is None:
        logger.error("[SIT_PED] faturamento dos pedidos fechados indisponivel - "
                     "entrega futura segue como Fechado neste recorte.")
        return
    for e, f in resultado.items():
        r = fechadas.get(e)
        if r is None:
            continue
        r["_Faturamento"], r["_Remessa"], r["_FaturadoEm"] = f.tipo, f.remessa, f.faturado_em
        if f.pendente:
            r["StatusPedido"] = "Aberto"


def faturamento(r: dict[str, Any]) -> dict[str, Any]:
    """``faturamento``/``remessa``/``faturado_em`` of one raw row (``None`` when not read)."""
    return {campo: r.get(chave) for campo, chave in FATURAMENTO_CHAVES.items()}


def _buscar_no_hana() -> list[dict[str, Any]]:
    """One round trip to HANA: count, check the volume, select, inject the derived keys, convert types.

    The injections (municipality names, release/NF dates, invoicing) run on the same connection, so
    what reaches the cache is already complete.

    """
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
        # Ainda com a conexao aberta: o nome do municipio entra nas MESMAS linhas, que
        # ja vao para o cache de 120 s do recorte.
        _injetar_municipios(conn, schema, linhas)
        # Idem para a liberacao real e a primeira NF (PLANO_DATAS_LIBERACAO_NF).
        _injetar_liberacao_e_nf(conn, schema, linhas)
        # And how each closed pedido was invoiced (PLANO_ENTREGA_FUTURA).
        _injetar_faturamento(conn, schema, linhas)
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
    _forcar_liberacao(linhas)
    logger.info("[SIT_PED] %d pedidos lidos da view.", len(linhas))
    return linhas


def _forcar_liberacao(linhas: list[dict[str, Any]]) -> None:
    """Mark the three stages of :data:`LIBERACAO_FORCADA_DOCNUM` as released, in place.

    Runs before the rows enter the cache, so the list, the single-order route and the
    dashboard KPIs all see the same picture. The ``*_em`` timestamps are left to
    :func:`liberacao_e_nf`, which still refuses to invent a time it cannot date.
    """
    for r in linhas:
        if _int(r.get("DocNum")) in LIBERACAO_FORCADA_DOCNUM:
            r["Financeiro"] = r["Producao"] = r["Entrega"] = "Liberado"
            logger.warning("[SIT_PED] pedido %s: liberacao FORCADA (temporaria, "
                           "LIBERACAO_FORCADA_DOCNUM).", r.get("DocNum"))


def fetch_status_pedidos(*, recarregar: bool = False) -> list[dict[str, Any]]:
    """Raw rows of the view, with a short cache (:data:`CACHE_TTL_SEGUNDOS`).

    Args:
        recarregar: ignore the cache and go to HANA. It is **not** the default: the plan's 3 queries
            must see the same snapshot, and an MCP client in a loop would start interrogating SAP on
            every sentence.

    Returns:
        A shallow copy of the cached list -- it isolates the cache from an accidental ``sort()`` by
        the caller. The dicts themselves remain shared: **nobody may mutate them**.
        (:func:`situacao_pedidos.normalizar` does not mutate; it builds new dicts.)

    Raises:
        SAPIndisponivel: HANA down, missing credential, or a query that failed.
        ValidationError: volume above ``_MAX_LINHAS``.

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
    """How many seconds ago the snapshot was taken -- ``None`` if there is no cache.

    F3 returns this in the body: whoever reads "blocked" needs to know whether the data is from now
    or from 2 minutes ago.

    """
    with _cache_lock:
        if not _cache:
            return None
        return round(time.monotonic() - _cache[0], 1)


def fetch_udf_valid_values(table_id: str, alias_id: str) -> list[dict[str, str]]:
    """Valid values of a UDF, as SAP defines them -- the source of the labels.

    B1 keeps the UDF metadata in ``CUFD`` (``TableID`` + ``AliasID`` → ``FieldID``) and the dropdown
    list in ``UFD1`` (``FldValue`` + ``Descr``, in ``IndexID`` order). Reading from here is what
    avoids a label map hard-coded in the code.

    ``AliasID`` is the field name **without** the ``U_`` prefix: column ``ORDR.U_INO_TPO_MONTAGEM``
    has ``AliasID = 'INO_TPO_MONTAGEM'``. The filter by table is mandatory -- the same alias exists
    in ~30 of them and the value list is per table.

    Returns:
        ``[{"value": ..., "descr": ...}, ...]`` in SAP's order; ``[]`` when the field has no list.
        Never raises: the caller (``sap_montagem_labels``) has a fallback, and having no label is
        worse than yesterday's label.

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
    """Plug the hook F1 left in place: the assembly label now comes from SAP.

    Called once at API startup (F3). Without it, ``sap_montagem_labels`` answers from
    ``FALLBACK_LABELS`` -- which is correct but frozen at 27/07/2026.

    """
    sap_montagem_labels.registrar_fonte(fetch_udf_valid_values)
    logger.info("[MONTAGEM] rótulos passam a vir do SAP (fallback continua de rede).")


def fetch_previsao_montagem(doc_entry: int) -> dict[str, Any]:
    """Assembly forecast of one order in Previsão Financeira (``@ALTA_PREV``), for the
    Controle de montagem's D3 rule. Own connection; raises ``SAPIndisponivel`` when HANA is down.
    """
    import controle_montagem

    schema = _schema()
    padrao = f"CTR:%:OV:{int(doc_entry)}"
    consulta, params = sql(
        t"""SELECT "U_OrigRef", SUBSTRING("Remark", 1, 254) AS "Remark" FROM "{schema:ident}"."@ALTA_PREV" """
        t"""WHERE "U_OrigRef" LIKE {padrao} AND "Canceled" = 'N' ORDER BY "DocEntry" DESC""")
    conn = _conectar()
    try:
        return controle_montagem.previsao_das_linhas(_linhas(conn, consulta, tuple(params)), doc_entry)
    finally:
        try:
            conn.close()
        except Exception as e:  # noqa: BLE001 -- closing a dead connection must not hide the answer
            logger.debug("[SIT_PED] fechar conexao (previsao): %s", e)
