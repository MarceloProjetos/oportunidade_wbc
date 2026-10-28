"""Encerra (com estoque) as OPs abertas dos pedidos FECHADOS — um pedido por vez.

Pedido de 28/10/2026 (Marcelo): pedidos com ``StatusPedido = 'Fechado'`` na
``VW_STATUS_PEDIDO`` ainda tinham 7.187 OPs Planejadas/Liberadas (235 pedidos), nenhuma com
insumo baixado ou produto apontado. Decisao: encerrar COM movimentacao de estoque.

This is a thin loop over the existing command ``manutencao-op encerrar --pedido N --sim``,
run as a subprocess per order, so every rule of that command still applies (IP gate of the
.11, child-before-parent ordering, dependents skipped when a child fails). Nothing here
talks to the Service Layer directly.

Uso (na raiz do SIS, na .11):

    python maintenance/encerrar_ops_pedidos_fechados.py               # previa, so leitura
    python maintenance/encerrar_ops_pedidos_fechados.py --executar --limite 5
    python maintenance/encerrar_ops_pedidos_fechados.py --executar    # todos
    python maintenance/encerrar_ops_pedidos_fechados.py --lista state/pedidos.json --custo-zero 0.01 --executar

``--lista``: so os pedidos do JSON (o export do DBeaver da VW_STATUS_PEDIDO, ou uma lista de
numeros); pedido sem OP aberta e pulado sozinho. ``--custo-zero``: quando o SAP recusa a
saida por item sem custo (10001287), grava esse custo medio nos insumos com custo zero do
pedido (reavaliacao de estoque, documento contabil) e tenta o pedido mais uma vez.

Parar no meio: crie o arquivo ``state\\encerrar_ops.stop`` — o laco termina o pedido em
andamento e para antes do proximo. NAO use Ctrl+C: pode cortar uma OP entre a saida de
insumo e a entrada do produto.

Log completo (saida de cada comando) em ``logs\\encerrar_ops_<data_hora>.log``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from controleproducao.config import get_settings  # noqa: E402
from controleproducao.core.hana_reader import HanaDirectReader  # noqa: E402
from controleproducao.core.service_layer_client import ServiceLayerClient  # noqa: E402

PARADA = _RAIZ / "state" / "encerrar_ops.stop"

PEDIDOS_COM_OP_ABERTA = """
SELECT T0."OriginNum" "Pedido", COUNT(*) "OPs",
       SUM(CASE WHEN T0."Status" = 'P' THEN 1 ELSE 0 END) "Planejadas"
FROM OWOR T0
WHERE T0."Status" IN ('P', 'R')
  AND T0."OriginNum" IN (SELECT V."DocNum" FROM "VW_STATUS_PEDIDO" V WHERE V."StatusPedido" = 'Fechado')
GROUP BY T0."OriginNum"
ORDER BY T0."OriginNum"
""".strip()


# Components still to issue whose average cost is zero in the line's warehouse: the ones the
# SAP refuses with 10001287 "Item cost not found".
INSUMOS_SEM_CUSTO = """
SELECT DISTINCT W."ItemCode", W."wareHouse"
FROM OWOR T
  INNER JOIN WOR1 W ON W."DocEntry" = T."DocEntry"
  INNER JOIN OITM I ON I."ItemCode" = W."ItemCode"
  LEFT JOIN OITW S ON S."ItemCode" = W."ItemCode" AND S."WhsCode" = W."wareHouse"
WHERE T."OriginNum" = ? AND T."Status" IN ('P', 'R') AND I."InvntItem" = 'Y'
  AND W."PlannedQty" > W."IssuedQty" AND COALESCE(S."AvgPrice", 0) = 0
  AND W."ItemCode" NOT IN (
    SELECT O."ItemCode" FROM OWOR O WHERE O."OriginNum" = T."OriginNum" AND O."Status" IN ('P', 'R'))
""".strip()
# The NOT IN keeps out semi-finished items an open child OP of the same order produces: they
# get their real cost when that child closes, and a 0.01 revaluation would skew it.


def le_lista(caminho: str) -> set[int]:
    """Order numbers from the DBeaver export (``{"<sql>": [{"DocNum": ...}]}``) or a plain list."""
    dados = json.loads(Path(caminho).read_text(encoding="utf-8-sig"))
    if isinstance(dados, dict):
        dados = next(iter(dados.values()))
    return {int(x["DocNum"] if isinstance(x, dict) else x) for x in dados}


def insumos_sem_custo(pedido: int) -> list[tuple[str, str]]:
    leitor = HanaDirectReader(get_settings())
    try:
        return [(r["ItemCode"], r["wareHouse"]) for r in leitor.fetch_all(INSUMOS_SEM_CUSTO, (pedido,))]
    finally:
        leitor.close()


async def _reavalia(insumos: list[tuple[str, str]], custo: float, pedido: int) -> object:
    async with ServiceLayerClient(get_settings()) as sl:
        reav = await sl.post("MaterialRevaluation", {
            "DocDate": date.today().isoformat(),
            "RevalType": "P",
            "Comments": f"Custo para encerrar OPs do pedido fechado {pedido} (encerrar_ops_pedidos_fechados.py)",
            "MaterialRevaluationLines": [
                {"ItemCode": item, "WarehouseCode": deposito, "Price": custo} for item, deposito in insumos
            ],
        })
    return reav.get("DocNum")


def levanta_pedidos() -> list[dict]:
    leitor = HanaDirectReader(get_settings())
    try:
        return leitor.fetch_all(PEDIDOS_COM_OP_ABERTA)
    finally:
        leitor.close()


def encerra_pedido(pedido: int, log) -> tuple[int, str]:
    """Runs the existing close command for one order; returns its exit code and output."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1", "COLUMNS": "160"}
    proc = subprocess.run(
        [sys.executable, "-m", "controleproducao", "manutencao-op", "encerrar", "--pedido", str(pedido), "--sim"],
        cwd=_RAIZ, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    log.write(f"\n===== pedido {pedido} — saida {proc.returncode} — {datetime.now():%H:%M:%S} =====\n")
    log.write(proc.stdout)
    log.write(proc.stderr)
    log.flush()
    return proc.returncode, proc.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--executar", action="store_true", help="Encerra de verdade (sem isto, so a previa).")
    ap.add_argument("--limite", type=int, default=None, help="So os N primeiros pedidos da lista.")
    ap.add_argument("--lista", help="JSON com os pedidos a encerrar; os demais pedidos fechados ficam de fora.")
    ap.add_argument("--custo-zero", help="Custo medio (com ponto, ex.: 0.01) para insumo sem custo; tenta de novo.")
    args = ap.parse_args()
    custo_zero = None
    if args.custo_zero is not None:
        if "," in args.custo_zero:
            # PowerShell reads an unquoted comma as an array: 0,01 arrives as 0,1.
            raise SystemExit("--custo-zero: use ponto (ex.: 0.01). No PowerShell, 0,01 chega aqui como 0,1.")
        custo_zero = float(args.custo_zero)
        if custo_zero <= 0:
            raise SystemExit("--custo-zero precisa ser maior que zero.")

    pedidos = levanta_pedidos()
    if args.lista:
        da_lista = le_lista(args.lista)
        pedidos = [p for p in pedidos if int(p["Pedido"]) in da_lista]
        print(f"Lista {args.lista}: {len(da_lista)} pedido(s); {len(pedidos)} ainda com OP aberta.")
    if args.limite:
        pedidos = pedidos[: args.limite]
    total_ops = sum(int(p["OPs"]) for p in pedidos)
    print(f"{len(pedidos)} pedido(s) fechado(s) com {total_ops} OP(s) aberta(s) "
          f"({sum(int(p['Planejadas']) for p in pedidos)} planejada(s)).")
    if not args.executar:
        for p in pedidos:
            print(f"  pedido {p['Pedido']}: {p['OPs']} OP(s)")
        print("\nPrevia apenas. Para encerrar: --executar")
        return 0
    if PARADA.exists():
        print(f"Arquivo de parada presente ({PARADA}). Apague-o para rodar.")
        return 1

    (_RAIZ / "logs").mkdir(exist_ok=True)
    caminho_log = _RAIZ / "logs" / f"encerrar_ops_{datetime.now():%Y%m%d_%H%M%S}.log"
    ok, falhas = [], []
    with caminho_log.open("a", encoding="utf-8") as log:
        for i, p in enumerate(pedidos, start=1):
            if PARADA.exists():
                print(f"\nParado pelo arquivo {PARADA} antes do pedido {p['Pedido']}.")
                break
            pedido = int(p["Pedido"])
            codigo, saida = encerra_pedido(pedido, log)
            if codigo not in (0, 2) and custo_zero and "10001287" in saida:
                insumos = insumos_sem_custo(pedido)
                if insumos:
                    doc = asyncio.run(_reavalia(insumos, custo_zero, pedido))
                    print(f"   pedido {pedido}: custo {custo_zero:g} gravado em {len(insumos)} insumo(s) sem custo "
                          f"(reavaliacao {doc}: {', '.join(i for i, _ in insumos)}); tentando de novo.")
                    log.write(f"\n# reavaliacao {doc}: {insumos} = {custo_zero}\n")
                    codigo, saida = encerra_pedido(pedido, log)
            situacao = "ok" if codigo == 0 else f"FALHA (saida {codigo})"
            print(f"[{i}/{len(pedidos)}] pedido {pedido} ({p['OPs']} OPs): {situacao}", flush=True)
            if codigo == 2:
                # Exit 2 is the production-write block (not the .11): every order would hit it.
                print("Escrita em producao bloqueada nesta maquina — rode na .11. Abortado.")
                return 2
            (ok if codigo == 0 else falhas).append(pedido)

    print(f"\nPedidos sem erro: {len(ok)} | com falha: {len(falhas)}")
    if falhas:
        print("Com falha (detalhe no log): " + ", ".join(map(str, falhas)))
    print(f"Log: {caminho_log}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
