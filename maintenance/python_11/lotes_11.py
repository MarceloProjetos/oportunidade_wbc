"""The update lots of the .11, and the checks around them. Standard library plus pip's own vendored packaging.

Only packages already installed are upgraded: installing a candidate the machine never had
would add code to production for nothing. ``hdbcli`` stays out on purpose: 2.29.25 killed the
process on Python 3.14 when the connection failed.

Usage: ``python lotes_11.py alvos <lote>`` | ``conferir <pip report.json>`` | ``importar``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.utils import canonicalize_name
from pip._vendor.packaging.version import Version

RAIZ = Path(__file__).resolve().parents[2]

LOTES: dict[str, list[str]] = {
    "1": [
        "certifi==2026.10.23",
        "brotli==1.2.1",
        "urllib3==2.8.0",
        "aiohttp==3.14.5",
        "pyasn1==0.6.5",
        "PyJWT==2.15.1",
        "python-multipart==0.0.33",
        "multidict==7.2.1",
        "werkzeug==3.1.9",
        "mcp>=1.30,<2",
    ],
    "2": [
        "pillow==13.0.0",
        "filelock==4.2.9",
        "oauthlib==4.0.1",
    ],
    "3": [
        "fastapi==0.162.0",
        "starlette==1.10.0",
        "uvicorn==0.54.0",
    ],
}

# Each one runs in its own process with the service's working directory: a failure here means
# the service would not start. The mcp folder shadows the mcp library when run from the root.
IMPORTACOES: list[tuple[str, str]] = [
    (".", "import api"),
    (".", "import scripts.scheduled_execution"),
    (".", "import wbcpython.dashboard.web"),
    (".", "import wbcpython.host.worker"),
    (".", "import controleproducao.main"),
    ("mcp", "import mcp_server"),
]


def instalados() -> dict[str, str]:
    return {canonicalize_name(d.metadata["Name"]): d.version for d in metadata.distributions()}


def alvos(lote: str) -> list[str]:
    presentes = instalados()
    return [p for p in LOTES[lote] if canonicalize_name(Requirement(p).name) in presentes]


def conferir(relatorio: Path) -> int:
    """Overlay a ``pip --dry-run --report`` on the installed set and list what would break."""
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    estado = instalados()
    requisitos: dict[str, list[str]] = {}
    for item in dados["install"]:
        nome = canonicalize_name(item["metadata"]["name"])
        print(f"muda {nome}: {estado.get(nome)} -> {item['metadata']['version']}")
        estado[nome] = item["metadata"]["version"]
        requisitos[nome] = item["metadata"].get("requires_dist") or []
    for dist in metadata.distributions():
        requisitos.setdefault(canonicalize_name(dist.metadata["Name"]), dist.requires or [])
    conflitos = 0
    for nome, lista in sorted(requisitos.items()):
        for texto in lista:
            req = Requirement(texto)
            if req.marker and not req.marker.evaluate({"extra": ""}):
                continue
            alvo = canonicalize_name(req.name)
            if alvo in estado and not req.specifier.contains(Version(estado[alvo]), prereleases=True):
                print(f"CONFLITO {nome} {estado[nome]} exige {texto}, ficaria {alvo} {estado[alvo]}")
                conflitos += 1
    print(f"conflitos: {conflitos}")
    return 1 if conflitos else 0


def importar() -> int:
    falhas = 0
    for pasta, codigo in IMPORTACOES:
        r = subprocess.run(
            [sys.executable, "-c", codigo],
            cwd=RAIZ / pasta,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        if r.returncode:
            falhas += 1
            print(f"FALHOU {codigo}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else r.returncode}")
        else:
            print(f"ok {codigo}")
    return 1 if falhas else 0


def main() -> int:
    acao = sys.argv[1] if len(sys.argv) > 1 else ""
    if acao == "alvos":
        print("\n".join(alvos(sys.argv[2])))
        return 0
    if acao == "conferir":
        return conferir(Path(sys.argv[2]))
    if acao == "importar":
        return importar()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
