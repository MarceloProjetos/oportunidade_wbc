"""Exporta a tabela ``ordens_servico_engenharia`` (Supabase) para JSON.

Lê os dados **já sincronizados** no Supabase (não vai ao SAP) usando a chave
``service_role`` — coerente com a decisão de segurança "leitura só backend".
Suporta exportar **um/vários NPED** ou a **tabela inteira**, com opções para
JSON enxuto (sem os textos NCLOB gigantes), formato compacto e descrição do
Status.

Exemplos (CLI)::

    # Um pedido → arquivo em exports/ (nome gerado automaticamente)
    python export_os_json.py 84080

    # Vários pedidos no mesmo arquivo
    python export_os_json.py 84080 84095 84100

    # Tabela inteira, enxuta (sem colunas NCLOB), num caminho específico
    python export_os_json.py --all --slim -o exports/todas_os.json

    # Imprimir no stdout (p/ pipe), compacto, só o array de linhas
    python export_os_json.py 84080 --stdout --compact --array

Programático::

    from export_os_json import export_os
    payload = export_os(npeds=[84080])          # dict com metadados + rows
    rows = export_os(npeds=[84080], as_array=True)   # só a lista de linhas
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from supabase import create_client, Client
from supabase.client import ClientOptions

from config import get_settings
from pipeline_core import coerce_positive_int

# UTF-8 console on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

# httpx loga cada requisição em INFO — ruidoso ao paginar/exportar.
logging.getLogger('httpx').setLevel(logging.WARNING)

# Colunas de texto grande (NCLOB/NVARCHAR(5000)) descartadas no modo --slim:
# são repetidas em cada linha do pedido e podem inflar bastante o JSON.
NCLOB_COLS = [
    'InfoAdicPED', 'InfoAdicPED2', 'ComposicaoPED', 'MATExistPED',
    'AcabamentoPED', 'CapacidadePED', 'ObsImpostOrcamento',
]

# PostgREST/Supabase devolve no máx. 1000 linhas por requisição → paginamos.
PAGE_SIZE = 1000


def _client() -> Client:
    """Cria o cliente Supabase com a chave service_role (escrita/leitura total)."""
    s = get_settings()
    if not (s.supabase_url and s.supabase_service_role_key):
        raise SystemExit(
            "Faltam SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY no .env "
            "(a leitura desta tabela exige service_role)."
        )
    options = ClientOptions(postgrest_client_timeout=s.supabase_timeout_s)
    return create_client(s.supabase_url, s.supabase_service_role_key, options)


def fetch_rows(
    client: Client,
    table: str,
    npeds: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """Busca todas as linhas (paginando) — opcionalmente filtrando por NPED(s).

    Ordena por ``NPED`` e ``id`` para uma saída estável (``id`` preserva a ordem
    em que as linhas vieram da view na sincronização).
    """
    rows: List[Dict[str, Any]] = []
    start = 0
    while True:
        query = (
            client.table(table)
            .select('*')
            .order('NPED')
            .order('id')
            .range(start, start + PAGE_SIZE - 1)
        )
        if npeds:
            query = query.in_('NPED', npeds)
        batch = query.execute().data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows


def fetch_status_map(client: Client, status_table: str) -> Dict[str, str]:
    """Lê o lookup de Status (código → descrição). Vazio se falhar (não bloqueia)."""
    try:
        data = client.table(status_table).select('codigo,descricao').execute().data or []
        return {r['codigo']: r['descricao'] for r in data}
    except Exception:
        return {}


def transform_rows(
    rows: List[Dict[str, Any]],
    *,
    slim: bool = False,
    with_status: bool = True,
    status_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Aplica --slim (remove NCLOB) e adiciona ``status_desc`` (descrição do Status)."""
    status_map = status_map or {}
    out: List[Dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        if slim:
            for col in NCLOB_COLS:
                row.pop(col, None)
        if with_status:
            row['status_desc'] = status_map.get(row.get('Status'))
        out.append(row)
    return out


def build_payload(
    rows: List[Dict[str, Any]],
    *,
    table: str,
    filter_desc: Dict[str, Any],
    as_array: bool = False,
) -> Any:
    """Monta o resultado final: array puro (``as_array``) ou envelope com metadados."""
    if as_array:
        return rows
    return {
        'exported_at': datetime.now().isoformat(),
        'source_table': table,
        'filter': filter_desc,
        'count': len(rows),
        'rows': rows,
    }


def export_os(
    npeds: Optional[List[int]] = None,
    *,
    all_rows: bool = False,
    slim: bool = False,
    with_status: bool = True,
    as_array: bool = False,
    table: Optional[str] = None,
    status_table: Optional[str] = None,
) -> Any:
    """Exporta as OS para uma estrutura Python (dict envelope ou lista de linhas).

    Args:
        npeds: Lista de NPEDs a exportar. Ignorado se ``all_rows=True``.
        all_rows: Se ``True``, exporta a tabela inteira.
        slim: Remove as colunas de texto grande (NCLOB).
        with_status: Adiciona ``status_desc`` (descrição do Status).
        as_array: Retorna só a lista de linhas (sem o envelope de metadados).
        table / status_table: Sobrescrevem os defaults do ``.env``/config.

    Returns:
        ``dict`` (envelope) ou ``list`` (se ``as_array``).
    """
    s = get_settings()
    table = table or s.os_table_name
    status_table = status_table or s.os_status_table

    if not all_rows and not npeds:
        raise ValueError("Informe npeds=[...] ou all_rows=True")

    # Valida/normaliza os NPEDs (inteiros positivos) antes de filtrar no PostgREST.
    npeds_validos = None if all_rows else [coerce_positive_int(n, what='NPED') for n in npeds]

    client = _client()
    rows = fetch_rows(client, table, npeds=npeds_validos)
    status_map = fetch_status_map(client, status_table) if with_status else {}
    rows = transform_rows(rows, slim=slim, with_status=with_status, status_map=status_map)

    filter_desc: Dict[str, Any] = {'all': True} if all_rows else {'nped': npeds_validos}
    return build_payload(rows, table=table, filter_desc=filter_desc, as_array=as_array)


def _default_output_path(npeds: Optional[List[int]], all_rows: bool) -> str:
    """Gera um caminho em exports/ com timestamp (e os NPEDs, se poucos)."""
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    if all_rows:
        alvo = 'ALL'
    elif npeds and len(npeds) <= 3:
        alvo = '_'.join(str(n) for n in npeds)
    else:
        alvo = f'{len(npeds or [])}npeds'
    return os.path.join('exports', f'ordens_servico_{alvo}_{stamp}.json')


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Exporta ordens_servico_engenharia (Supabase) para JSON.",
    )
    p.add_argument('npeds', nargs='*', type=int, help='Um ou mais NPED a exportar.')
    p.add_argument('--all', dest='all_rows', action='store_true',
                   help='Exporta a tabela inteira (ignora npeds).')
    p.add_argument('-o', '--output', help='Arquivo de saída (.json). Default: exports/<gerado>.')
    p.add_argument('--stdout', action='store_true', help='Escreve no stdout em vez de arquivo.')
    p.add_argument('--compact', action='store_true', help='JSON sem indentação (uma linha).')
    p.add_argument('--array', action='store_true', help='Só o array de linhas (sem metadados).')
    p.add_argument('--slim', action='store_true',
                   help='Remove as colunas de texto grande (NCLOB) para reduzir o tamanho.')
    p.add_argument('--no-status', dest='with_status', action='store_false',
                   help='Não adiciona status_desc (descrição do Status).')
    p.add_argument('--table', help='Sobrescreve a tabela (default: OS_TABLE_NAME).')
    p.add_argument('--status-table', help='Sobrescreve o lookup de Status.')
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if not args.all_rows and not args.npeds:
        print("Uso: python export_os_json.py <NPED> [<NPED> ...]  |  --all  "
              "[--slim --compact --array --no-status -o arquivo.json --stdout]",
              file=sys.stderr)
        return 2

    payload = export_os(
        npeds=args.npeds or None,
        all_rows=args.all_rows,
        slim=args.slim,
        with_status=args.with_status,
        as_array=args.array,
        table=args.table,
        status_table=args.status_table,
    )

    n = len(payload) if isinstance(payload, list) else payload['count']
    text = json.dumps(payload, ensure_ascii=False, indent=None if args.compact else 2, default=str)

    if args.stdout:
        sys.stdout.write(text)
        sys.stdout.write('\n')
        print(f"[export] {n} linha(s) escritas no stdout.", file=sys.stderr)
        return 0

    out_path = args.output or _default_output_path(args.npeds or None, args.all_rows)
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(text)
    size_kb = os.path.getsize(out_path) / 1024
    print(f"[export] {n} linha(s) → {out_path} ({size_kb:.1f} KB)", file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
