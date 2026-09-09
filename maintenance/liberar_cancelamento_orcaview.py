"""Grava `U_INO_CancelaPedido = 'S'` (e `U_INO_AlteraPeso = 'S'`) no usuário do Service Layer.

Por quê (09/09/2026): a "regra 1996" do `SBO_SP_TransactionNotification` só deixa cancelar
pedido de venda quem tem `OUSR.U_INO_CancelaPedido = 'S'` — quatro usuários em produção,
e o `orcaview` (usuário do worker WBC desde a virada de 08/09) não estava entre eles. Cada
troca de parceiro caía em `-1116 (1996) Cancelamento de Pedido de vendas não permitido`.
Autorização padrão do B1 não resolve; é um UDF do cadastro de usuário.

Roda na .11, na raiz do projeto, com o `.env` de lá (usa o mesmo login do worker — o
`orcaview` é superusuário e pode editar o próprio cadastro pelo objeto `Users`):

    python maintenance\\liberar_cancelamento_orcaview.py            # mostra e pergunta
    python maintenance\\liberar_cancelamento_orcaview.py --aplicar  # grava sem perguntar

Vale já no ciclo seguinte do worker (login novo a cada ciclo). Somente leitura sem `--aplicar`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wbcpython.config import get_settings  # noqa: E402
from wbcpython.infrastructure.service_layer.client import ServiceLayerClient  # noqa: E402

CAMPOS = {"U_INO_CancelaPedido": "S", "U_INO_AlteraPeso": "S"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--aplicar", action="store_true", help="grava sem perguntar")
    parser.add_argument("--usuario", default="", help="UserCode alvo (padrão: o do login)")
    args = parser.parse_args()

    settings = get_settings()
    alvo = args.usuario or settings.service_layer.username
    with ServiceLayerClient(
        settings.service_layer,
        production_company_db=settings.production_company_db,
        block_production_writes=settings.block_production_writes,
    ) as cliente:
        resposta = cliente.get_json(
            "Users",
            params={
                "$filter": f"UserCode eq '{alvo}'",
                "$select": "InternalKey,UserCode,Superuser," + ",".join(CAMPOS),
            },
        )
        usuarios = resposta.get("value") or []
        if not usuarios:
            print(f"Usuário {alvo!r} não encontrado em Users.")
            return 1
        u = usuarios[0]
        print(f"Users({u['InternalKey']}) {u['UserCode']} superusuário={u.get('Superuser')}")
        for campo, desejado in CAMPOS.items():
            print(f"  {campo}: {u.get(campo)!r}  ->  {desejado!r}")
        pendentes = {c: v for c, v in CAMPOS.items() if str(u.get(c) or "").upper() != v}
        if not pendentes:
            print("Nada a fazer: já está tudo em 'S'.")
            return 0
        if not args.aplicar:
            resposta_humana = input("Gravar? [s/N] ").strip().lower()
            if resposta_humana != "s":
                print("Nada gravado.")
                return 0
        cliente.patch(f"Users({u['InternalKey']})", json=pendentes)
        conferido = cliente.get_json(
            f"Users({u['InternalKey']})", params={"$select": ",".join(CAMPOS)}
        )
        print("Gravado. Conferência:", {c: conferido.get(c) for c in CAMPOS})
        faltou = {c for c, v in CAMPOS.items() if str(conferido.get(c) or "").upper() != v}
        return 1 if faltou else 0


if __name__ == "__main__":
    sys.exit(main())
