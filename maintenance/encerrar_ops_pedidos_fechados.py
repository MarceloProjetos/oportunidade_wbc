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

Parar no meio: crie o arquivo ``state\\encerrar_ops.stop`` — o laco termina o pedido em
andamento e para antes do proximo. NAO use Ctrl+C: pode cortar uma OP entre a saida de
insumo e a entrada do produto.

Log completo (saida de cada comando) em ``logs\\encerrar_ops_<data_hora>.log``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from controleproducao.config import get_settings  # noqa: E402
from controleproducao.core.hana_reader import HanaDirectReader  # noqa: E402

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


def levanta_pedidos() -> list[dict]:
    leitor = HanaDirectReader(get_settings())
    try:
        return leitor.fetch_all(PEDIDOS_COM_OP_ABERTA)
    finally:
        leitor.close()


def encerra_pedido(pedido: int, log) -> int:
    """Runs the existing close command for one order; returns its exit code."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1", "COLUMNS": "160"}
    proc = subprocess.run(
        [sys.executable, "-m", "controleproducao", "manutencao-op", "encerrar", "--pedido", str(pedido), "--sim"],
        cwd=_RAIZ, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    log.write(f"\n===== pedido {pedido} — saida {proc.returncode} — {datetime.now():%H:%M:%S} =====\n")
    log.write(proc.stdout)
    log.write(proc.stderr)
    log.flush()
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--executar", action="store_true", help="Encerra de verdade (sem isto, so a previa).")
    ap.add_argument("--limite", type=int, default=None, help="So os N primeiros pedidos da lista.")
    args = ap.parse_args()

    pedidos = levanta_pedidos()
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
            codigo = encerra_pedido(pedido, log)
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
