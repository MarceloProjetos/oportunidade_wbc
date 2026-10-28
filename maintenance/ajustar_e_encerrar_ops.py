"""Ajusta o cadastro e encerra (com estoque) as OPs de pedidos que falharam no lote.

Complemento de ``encerrar_ops_pedidos_fechados.py`` (28/10/2026). O lote falhou por tres
motivos, e cada um tem o seu ajuste aqui:

- ``10001069 Item ... is inactive`` -> ``--reativar ITEM``: o item e ativado so durante o
  encerramento e volta ao estado original no fim (mesmo se o encerramento falhar).
- ``10001287 Item cost not found`` -> ``--custo ITEM=VALOR``: reavaliacao de estoque
  (troca de preco, ``RevalType=P``) no deposito 01, gravando o custo medio do item. E um
  documento contabil: nao se desfaz sozinho — so com outra reavaliacao.
- ``Server disconnected`` (Service Layer caiu) -> nenhum ajuste, so rodar de novo.

The close itself is the existing ``manutencao-op encerrar --pedido N --sim`` command, run as
a subprocess, so its rules (IP gate, child-before-parent, dependents skipped) still apply.
Item and revaluation writes go through the CP ``ServiceLayerClient``, which carries the same
production IP gate.

Uso (na raiz do SIS, na .11):

    python maintenance/ajustar_e_encerrar_ops.py --pedido 83991                     # previa
    python maintenance/ajustar_e_encerrar_ops.py --pedido 84000 --reativar FEC00000000000000491 --executar
    python maintenance/ajustar_e_encerrar_ops.py --pedido 84004 --custo ALPRFQ30-KG20000F280=7.50 --executar
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from controleproducao.config import get_settings  # noqa: E402
from controleproducao.core.service_layer_client import ServiceLayerClient  # noqa: E402

DEPOSITO = "01"


def _custos(pares: list[str]) -> dict[str, float]:
    custos = {}
    for par in pares:
        item, _, valor = par.partition("=")
        if not item or not valor:
            raise SystemExit(f"--custo invalido: {par!r} (use ITEM=VALOR, ex.: ALPRFQ30-KG20000F280=7.50)")
        custos[item.strip()] = float(valor.replace(",", "."))
        if custos[item.strip()] <= 0:
            raise SystemExit(f"--custo {par!r}: o valor precisa ser maior que zero.")
    return custos


def encerra_pedido(pedido: int, log) -> int:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1", "COLUMNS": "160"}
    proc = subprocess.run(
        [sys.executable, "-m", "controleproducao", "manutencao-op", "encerrar", "--pedido", str(pedido), "--sim"],
        cwd=_RAIZ, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    log.write(f"\n===== pedido {pedido} — saida {proc.returncode} — {datetime.now():%H:%M:%S} =====\n")
    log.write(proc.stdout)
    log.write(proc.stderr)
    log.flush()
    # The command's own summary lines (closed / skipped / failed) are what the operator needs.
    for linha in proc.stdout.splitlines():
        if linha.startswith(("Encerradas", "Falharam", "  - OP")) or "pulada" in linha:
            print("   " + linha.strip())
    return proc.returncode


async def executar(pedidos: list[int], reativar: list[str], custos: dict[str, float]) -> int:
    originais: dict[str, dict] = {}
    caminho_log = _RAIZ / "logs" / f"ajustar_encerrar_ops_{datetime.now():%Y%m%d_%H%M%S}.log"
    (_RAIZ / "logs").mkdir(exist_ok=True)
    falhas = []
    async with ServiceLayerClient(get_settings()) as sl:
        try:
            for item in reativar:
                atual = await sl.get(f"Items('{item}')", {"$select": "ItemCode,Valid,Frozen"})
                originais[item] = {"Valid": atual["Valid"], "Frozen": atual["Frozen"]}
                await sl.patch(f"Items('{item}')", {"Valid": "tYES", "Frozen": "tNO"})
                print(f"Item {item} ativado (estava Valid={atual['Valid']}, Frozen={atual['Frozen']}).")

            if custos:
                reav = await sl.post("MaterialRevaluation", {
                    "DocDate": date.today().isoformat(),
                    "RevalType": "P",
                    "Comments": "Custo para encerrar OPs de pedidos fechados (maintenance/ajustar_e_encerrar_ops.py)",
                    "MaterialRevaluationLines": [
                        {"ItemCode": item, "WarehouseCode": DEPOSITO, "Price": valor}
                        for item, valor in custos.items()
                    ],
                })
                print(f"Reavaliacao de estoque gravada: DocNum {reav.get('DocNum')} "
                      f"({', '.join(f'{i}={v:g}' for i, v in custos.items())}).")

            with caminho_log.open("a", encoding="utf-8") as log:
                for pedido in pedidos:
                    codigo = encerra_pedido(pedido, log)
                    print(f"Pedido {pedido}: {'ok' if codigo == 0 else f'FALHA (saida {codigo})'}", flush=True)
                    if codigo == 2:
                        print("Escrita em producao bloqueada nesta maquina — rode na .11. Abortado.")
                        return 2
                    if codigo != 0:
                        falhas.append(pedido)
        finally:
            # Restore inactive items even when a close failed: an item left active by accident
            # is a master-data change nobody asked for.
            for item, estado in originais.items():
                try:
                    await sl.patch(f"Items('{item}')", estado)
                    print(f"Item {item} devolvido ao estado original ({estado}).")
                except Exception as exc:  # noqa: BLE001 — report and keep restoring the others
                    print(f"ATENCAO: item {item} NAO voltou ao estado original ({estado}): {exc}. "
                          "Inative-o a mao no SAP.")
    print(f"Log: {caminho_log}")
    return 1 if falhas else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pedido", type=int, action="append", required=True, help="Pedido (DocNum). Pode repetir.")
    ap.add_argument("--reativar", action="append", default=[], help="Item inativo a ativar so durante o encerramento.")
    ap.add_argument("--custo", action="append", default=[], help="ITEM=VALOR: custo medio a gravar no deposito 01.")
    ap.add_argument("--executar", action="store_true", help="Grava de verdade (sem isto, so mostra o plano).")
    args = ap.parse_args()
    custos = _custos(args.custo)

    print(f"Pedidos: {', '.join(map(str, args.pedido))}")
    for item in args.reativar:
        print(f"  ativar durante o encerramento: {item}")
    for item, valor in custos.items():
        print(f"  custo medio no deposito {DEPOSITO}: {item} = {valor:g} (reavaliacao de estoque, contabil)")
    if not args.executar:
        print("\nPrevia apenas. Para gravar: --executar")
        return 0
    return asyncio.run(executar(args.pedido, args.reativar, custos))


if __name__ == "__main__":
    sys.exit(main())
