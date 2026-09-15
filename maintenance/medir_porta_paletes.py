"""F0 do plano de porta-paletes: mede, na base inteira do WBC, o alcance da regra
"quantidade = inteiro antes de Módulo(s), só em linha de porta-paletes".

Só leitura. Usa a **mesma função de produção** (`domain.linhas.quantidade_no_texto`),
para que o número medido seja o número que o worker vai aplicar — medir com uma
regex paralela e implementar outra foi justamente o que o plano quis evitar.

Como rodar (raiz do repositório, `.env` com `SQL_*` ou `WBC_SQL_*`):

    python maintenance/medir_porta_paletes.py            # resumo
    python maintenance/medir_porta_paletes.py --sem-numero  # lista as linhas sem número
    python maintenance/medir_porta_paletes.py --amostra 20  # 20 linhas lidas, ao acaso

Conecta pelo `pymssql` quando ele existe (a .11) e pelo `pyodbc` quando não
(estação de desenvolvimento). Os dois caminhos passam por `assert_read_only_sql`.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# O console do Windows abre em cp1252 e engasga no "→" e nos acentos dos textos.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import URL, create_engine, text  # noqa: E402

from wbcpython.config import WbcSqlSettings  # noqa: E402
from wbcpython.domain.linhas import eh_porta_paletes, quantidade_no_texto  # noqa: E402
from wbcpython.infrastructure.wbc_sql.repository import RepositorioOrcamentosWbcSql  # noqa: E402
from wbcpython.safety import assert_read_only_sql  # noqa: E402

CONSULTA = """
SELECT ORCNUM, ORCITM, GRPCOD, ORCPRDQTD, ORCVAL, COALESCE(ORCTXT, '') AS ORCTXT
FROM INTEGRACAO_ORCIMP
"""


def _url(settings: WbcSqlSettings) -> URL:
    try:
        import pymssql  # noqa: F401
    except ImportError:
        return URL.create(
            "mssql+pyodbc",
            username=settings.username,
            password=settings.password.get_secret_value(),
            host=settings.host,
            port=settings.port,
            database=settings.database,
            query={"driver": "ODBC Driver 18 for SQL Server", "TrustServerCertificate": "yes"},
        )
    return RepositorioOrcamentosWbcSql.montar_url(settings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sem-numero", action="store_true", help="lista as linhas de porta-paletes sem número")
    parser.add_argument("--amostra", type=int, default=0, help="mostra N linhas lidas, ao acaso")
    args = parser.parse_args()

    assert_read_only_sql(CONSULTA, fonte="medir_porta_paletes")
    engine = create_engine(_url(WbcSqlSettings()))
    with engine.connect() as conexao:
        linhas = conexao.execute(text(CONSULTA)).fetchall()

    total = len(linhas)
    com_qtd_no_wbc = sum(1 for l in linhas if l.ORCPRDQTD is not None and Decimal(str(l.ORCPRDQTD)) > 0)
    porta = [l for l in linhas if eh_porta_paletes(l.ORCTXT)]
    lidas = [(l, quantidade_no_texto(l.ORCTXT)) for l in porta]
    com_numero = [(l, q) for l, q in lidas if q]
    sem_numero = [l for l, q in lidas if not q]
    fora = [l for l in linhas if not eh_porta_paletes(l.ORCTXT) and quantidade_no_texto(l.ORCTXT) is None and "modulo" in _norm(l.ORCTXT)]
    grupos_porta = Counter(str(l.GRPCOD) for l in porta)
    dist = Counter(q for _, q in com_numero)

    print(f"Linhas em INTEGRACAO_ORCIMP ............ {total:>7}")
    print(f"  com ORCPRDQTD > 0 ..................... {com_qtd_no_wbc:>7}")
    print(f"Linhas de porta-paletes (pelo texto) .... {len(porta):>7}")
    print(f"  com quantidade lida do texto .......... {len(com_numero):>7}  ({len(com_numero) / max(len(porta), 1):.1%})")
    print(f"  sem número (ficam em 1, com aviso) .... {len(sem_numero):>7}")
    print(f"Linhas com 'Módulo' que NÃO são porta-paletes (ficam em 1, sem aviso): {len(fora)}")
    print(f"GRPCOD das linhas de porta-paletes: {dict(grupos_porta.most_common())}")
    print("Quantidades mais comuns: " + ", ".join(f"{q}×{n}" for q, n in dist.most_common(12)))
    maior = max((q for _, q in com_numero), default=0)
    print(f"Maior quantidade lida: {maior}")

    print("\nOs dois textos-armadilha, contra a função de produção:")
    for texto_ in ("PORTA-PALETES ÁREA 1 10 Módulos", "PORTA-PALETES - OPÇÃO 1 14 Módulos"):
        print(f"  {texto_!r} → {quantidade_no_texto(texto_)}")

    if args.sem_numero:
        print(f"\n{len(sem_numero)} linha(s) de porta-paletes sem número:")
        for l in sem_numero:
            print(f"  {l.ORCNUM} it{l.ORCITM} grp{l.GRPCOD} R$ {l.ORCVAL}: {l.ORCTXT.strip()[:110]}")

    if args.amostra:
        print(f"\nAmostra de {args.amostra} linha(s) lidas:")
        for l, q in random.sample(com_numero, min(args.amostra, len(com_numero))):
            unitario = (Decimal(str(l.ORCVAL)) / q).quantize(Decimal("0.0001"))
            print(f"  {l.ORCNUM} it{l.ORCITM}: {q:>3} × {unitario} = {l.ORCVAL}  ← {l.ORCTXT.strip()[:80]}")
    return 0


def _norm(texto: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)).lower()


if __name__ == "__main__":
    raise SystemExit(main())
