"""``python -m seguranca`` — managing credentials and the agent switch on the .11.

    python -m seguranca escopos
    python -m seguranca criar NOME --escopos leitura,os:sincronizar [--agente] [--declara-usuario]
    python -m seguranca acrescentar NOME --escopos rh,op:status [--declara-usuario]  (same key, only grows)
    python -m seguranca listar
    python -m seguranca revogar NOME
    python -m seguranca desligar-agente [--so-escrita] [--motivo TEXTO]
    python -m seguranca religar-agente
    python -m seguranca auditoria [SERVICO] [--dia AAAA-MM-DD] [--ultimas N]

The key printed by ``criar`` is shown ONCE and never stored in clear: copy it to the client.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from seguranca import agente, auditoria, credenciais


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m seguranca", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("escopos", help="lista os escopos possíveis")
    c = sub.add_parser("criar", help="cria um cliente e mostra a chave UMA vez")
    c.add_argument("nome")
    c.add_argument("--escopos", required=True, help="separados por vírgula")
    c.add_argument("--agente", action="store_true", help="agente de IA: interruptor e expediente valem")
    c.add_argument("--declara-usuario", action="store_true", help="pode informar X-SIS-Usuario")
    ac = sub.add_parser("acrescentar", help="acrescenta escopos a um cliente, mantendo a chave")
    ac.add_argument("nome")
    ac.add_argument("--escopos", default="", help="separados por vírgula")
    ac.add_argument("--declara-usuario", action="store_true", help="passa a poder informar X-SIS-Usuario")
    sub.add_parser("listar", help="lista os clientes (sem as chaves)")
    r = sub.add_parser("revogar", help="desativa um cliente")
    r.add_argument("nome")
    d = sub.add_parser("desligar-agente", help="corta o agente em um passo")
    d.add_argument("--so-escrita", action="store_true")
    d.add_argument("--motivo", default="")
    sub.add_parser("religar-agente")
    a = sub.add_parser("auditoria", help="mostra as últimas linhas da auditoria")
    a.add_argument("servico", nargs="?", default="api", choices=["api", "controleproducao", "mcp"])
    a.add_argument("--dia", type=date.fromisoformat, default=None)
    a.add_argument("--ultimas", type=int, default=20)
    args = p.parse_args(argv)

    if args.cmd == "escopos":
        for nome, texto in credenciais.ESCOPOS.items():
            print(f"  {nome:22} {texto}")
        return 0
    if args.cmd == "criar":
        escopos = [e.strip() for e in args.escopos.split(",") if e.strip()]
        try:
            chave = credenciais.criar(args.nome, escopos, agente=args.agente, declara_usuario=args.declara_usuario)
        except credenciais.CredencialInvalida as exc:
            print(f"ERRO: {exc}", file=sys.stderr)
            return 2
        print(f"Cliente '{args.nome}' criado com {', '.join(sorted(set(escopos)))}.")
        print("Chave (aparece só AGORA — copie para o cliente; não fica guardada em texto):")
        print(f"\n    {chave}\n")
        return 0
    if args.cmd == "acrescentar":
        escopos = [e.strip() for e in args.escopos.split(",") if e.strip()]
        try:
            todos = credenciais.acrescentar_escopos(args.nome, escopos, declara_usuario=args.declara_usuario)
        except credenciais.CredencialInvalida as exc:
            print(f"ERRO: {exc}", file=sys.stderr)
            return 2
        extra = " (declara usuário)" if args.declara_usuario else ""
        print(f"Cliente '{args.nome}' agora com: {', '.join(todos)}{extra}. "
              "A chave é a mesma; vale na próxima chamada.")
        return 0
    if args.cmd == "listar":
        clientes = credenciais.carregar()
        if not clientes:
            print("Nenhum cliente cadastrado (só a chave-mestra, OS_API_KEY).")
        for cl in clientes:
            marca = "ativo" if cl.get("ativo", True) else f"REVOGADO {cl.get('revogado_em', '')}"
            extras = " ".join(x for x, ligado in (("agente", cl.get("agente")),
                                                   ("declara-usuario", cl.get("declara_usuario"))) if ligado)
            print(f"  {cl.get('nome', '?'):24} {marca:28} {','.join(cl.get('escopos', []))} {extras}")
        print(f"\nAgente: {agente.estado()}")
        return 0
    if args.cmd == "revogar":
        if credenciais.revogar(args.nome):
            print(f"Cliente '{args.nome}' revogado. A chave dele deixa de valer na próxima chamada.")
            return 0
        print(f"Nenhum cliente ativo chamado '{args.nome}'.", file=sys.stderr)
        return 1
    if args.cmd == "desligar-agente":
        agente.desligar(so_escrita=args.so_escrita, motivo=args.motivo)
        print("Agente " + ("só lendo (escritas cortadas)." if args.so_escrita else "DESLIGADO (nada passa)."))
        return 0
    if args.cmd == "religar-agente":
        print("Agente religado." if agente.religar() else "O agente já estava ligado.")
        return 0
    if args.cmd == "auditoria":
        for linha in auditoria.ler(args.servico, args.dia)[-args.ultimas:]:
            print(linha)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
