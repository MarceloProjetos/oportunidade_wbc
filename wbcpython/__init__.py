"""WBCPython — integração WBC x SAP Business One via Service Layer.

Reescrita em Python do WBCServConsole (legado C#/.NET com DI-API). Desde
2026-09-08 é um pacote do ServidorIntegracaoSAP: roda com `python -m wbcpython`
na raiz do repositório, lê o mesmo `.env` e é instalado pelo `requirements.txt`.

Guia: `docs/wbc/README.md`. Decisões: `docs/wbc/DECISOES.md`. As docstrings que
citam `ai_spec/` apontam para a especificação original, que não está no
repositório — `docs/wbc/ai_spec/00_index.md` diz onde cada assunto mora hoje.

Regras de ambiente: a trava de escrita em produção (`WBC_BLOCK_PRODUCTION_WRITES`)
existe e falha fechada, mas está DESLIGADA por decisão na virada para produção
(02/09/2026); a de somente-leitura do SQL Server do WBC não tem chave. Ver
`wbcpython.safety`.
"""

__version__ = "0.1.0"
