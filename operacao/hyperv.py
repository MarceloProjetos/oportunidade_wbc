"""The Hyper-V hosts' state, as each host reports it (F5c3 of the web repo's docs/PLANO_TEO_REDE_E_ROTINAS.md).

On 09/10 the .12 hung for the second time in two days and everything that explained it was on the host
ALTSAP (192.168.7.253): the VMs' Heartbeat, Veeam recovery checkpoints left behind since 02/2025 (all three
SAP VMs run on ``.avhdx`` differencing disks), the virtual switch and VMQ, the host's own errors, a device
announcing IPv6 on the LAN. No agent holds a password on a host, so the host PUSHES, as the ALTHOST does
for the backups (``operacao/backup.py``): a scheduled task runs ``maintenance/estado_hyperv.ps1`` every hour
as SYSTEM and POSTs to ``/operacao/hyperv/estado`` with a key of scope ``hyperv:relatar``.

The same script runs on either host: the ORIGIN decides which host the report is (never the body's own
host name, which is untrusted text), one file per host. Validation is a closed shape, as in ``backup``.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from operacao import backup
from operacao.backup import MENSAGEM_MAX, RelatoInvalido, _data, _lista, _numero, _texto

#: Origin address -> host id (and file). The ALTSAP first; the ALTHOST may run the same script later.
ORIGENS: dict[str, str] = {"192.168.7.253": "altsap", "192.168.7.250": "althost"}
NOMES = {"altsap": "ALTSAP", "althost": "ALTHOST"}
PASTA = backup.RAIZ / "state"
CORPO_MAX = backup.CORPO_MAX

# Field kinds: "t" text (120), "c" short text (40), "d" date, "n" number >= 0, "b" bool.
_FORMAS: dict[str, dict[str, str]] = {
    "vms": {"nome": "t", "estado": "c", "status": "t", "heartbeat": "c", "uptime_h": "n", "cpu_pct": "n",
            "memoria_gb": "n"},
    "checkpoints": {"vm": "t", "nome": "t", "tipo": "c", "criado_em": "d"},
    "discos": {"vm": "t", "arquivo": "t", "diferencial": "b", "cadeia": "n", "tamanho_gb": "n"},
    "volumes": {"letra": "c", "total_gb": "n", "livre_gb": "n"},
    "switches": {"nome": "t", "tipo": "c", "placa": "t"},
    "vmq": {"placa": "t", "ligado": "b"},
    "eventos": {"log": "t", "fonte": "t", "id": "n", "nivel": "c", "n": "n", "ultimo": "d"},
}
_IPV6 = {"desligado": "b", "descoberta_roteador": "b"}
_IPV6_LISTAS = {"rotas": {"proximo": "c", "placa": "t"}, "vizinhos": {"ip": "c", "mac": "c"},
                "enderecos": {"ip": "c", "origem": "c"}}
_IPV6_MAX = 10


def _valor(tipo: str, v: Any) -> Any:
    if tipo == "t":
        return _texto(v)
    if tipo == "c":
        return _texto(v, 40)
    if tipo == "d":
        return _data(v)
    if tipo == "n":
        return _numero(v)
    if v is None:
        return None
    if not isinstance(v, bool):
        raise RelatoInvalido("campo booleano com tipo errado")
    return v


def _itens(itens: list[dict[str, Any]], forma: dict[str, str]) -> list[dict[str, Any]]:
    return [{campo: _valor(tipo, i.get(campo)) for campo, tipo in forma.items()} for i in itens]


def validar(corpo: Any) -> dict[str, Any]:
    """The report reduced to the known shape. Raises :class:`RelatoInvalido`."""
    if not isinstance(corpo, dict):
        raise RelatoInvalido("o corpo tem de ser um objeto JSON")
    versao = corpo.get("versao_do_script")
    if not isinstance(versao, int) or isinstance(versao, bool) or not 0 < versao < 1000:
        raise RelatoInvalido("'versao_do_script' ausente ou inválida")
    erros = corpo.get("erros")
    if erros is not None and not isinstance(erros, list):
        raise RelatoInvalido("'erros' tem de ser uma lista")
    ipv6 = corpo.get("ipv6")
    if ipv6 is not None and not isinstance(ipv6, dict):
        raise RelatoInvalido("'ipv6' tem de ser um objeto")
    cortes: list[str] = []
    relato: dict[str, Any] = {
        "versao_do_script": versao,
        "gerado_em": _data(corpo.get("gerado_em")),
        "maquina": _texto(corpo.get("host")),
    }
    for nome, forma in _FORMAS.items():
        relato[nome] = _itens(_lista(corpo.get(nome), nome, cortes), forma)
    ipv6 = ipv6 or {}
    relato["ipv6"] = {campo: _valor(tipo, ipv6.get(campo)) for campo, tipo in _IPV6.items()}
    for nome, forma in _IPV6_LISTAS.items():
        itens = _lista(ipv6.get(nome), f"ipv6.{nome}", cortes)
        if len(itens) > _IPV6_MAX:
            cortes.append(f"ipv6.{nome}: {len(itens)} itens, só os primeiros {_IPV6_MAX} foram guardados")
        relato["ipv6"][nome] = _itens(itens[:_IPV6_MAX], forma)
    relato["erros"] = [_texto(e, MENSAGEM_MAX) for e in (erros or [])[:10] if isinstance(e, (str, int, float))]
    relato["erros"] += cortes
    return relato


def arquivo_de(host_id: str, pasta: Path | None = None) -> Path:
    return (pasta or PASTA) / f"hyperv_{host_id}.json"


def gravar(relato: dict[str, Any], origem: str, agora: datetime | None = None, pasta: Path | None = None) -> str:
    """Keep the last report of the host behind ``origem``; returns its id. ``KeyError`` if unknown."""
    host_id = ORIGENS[origem]
    backup.gravar(relato, origem, agora, arquivo=arquivo_de(host_id, pasta))
    return host_id


def ler(agora: datetime | None = None, pasta: Path | None = None) -> dict[str, Any]:
    """Every known host's last report and age; ``relato: None`` for a host that never sent one."""
    hosts = {}
    for host_id in dict.fromkeys(ORIGENS.values()):
        nome = NOMES.get(host_id, host_id)
        hosts[host_id] = {"nome": nome, **backup.ler(agora, arquivo_de(host_id, pasta), sem_relato=(
            f"o {nome} ainda não mandou nenhum relato (a tarefa de hora em hora não está instalada nele?)"))}
    return {"hosts": hosts}
