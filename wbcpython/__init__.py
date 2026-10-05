"""WBCPython — integração WBC x SAP Business One via Service Layer.

Reescrita em Python do WBCServConsole (legado C#/.NET com DI-API). Desde
2026-09-08 é um pacote do ServidorIntegracaoSAP: roda com `python -m wbcpython`
na raiz do repositório, lê o mesmo `.env` e é instalado pelo `requirements.txt`.

Guia: `docs/wbc/README.md`. Decisões: `docs/wbc/DECISOES.md`. As docstrings que
citam `ai_spec/` apontam para a especificação original, que não está no
repositório — `docs/wbc/ai_spec/00_index.md` diz onde cada assunto mora hoje.

Environment rules: the production write block fails closed and is lifted only on the
machine that owns `safety.PRODUCTION_MACHINE_IP` (the .11) — no `.env` switch since
2026-09-28. The WBC SQL Server read-only block has no switch at all. See
`wbcpython.safety`.
"""

__version__ = "0.1.0"
