# `ai_spec/` — onde está o que a especificação original dizia

O WBCPython nasceu com uma especificação em cinco arquivos (`ai_spec/00_index.md` a
`04_environment_constraints.md`), que ficava **fora** do repositório dele (`../../ai_spec/`,
na pasta da solução C#). Ela **não veio** para o ServidorIntegracaoSAP: não existia em
`D:\ProjetoAltamira` em 08/09/2026, e o código e as decisões registradas já a superaram
em vários pontos (ver `../DECISOES.md`, seção "Divergências deliberadas em relação ao
legado").

As docstrings do pacote ainda citam `ai_spec/0X_...`. Este índice diz onde cada assunto
mora **hoje**, para que a referência não leve a lugar nenhum:

| Referência antiga | O que regia | Onde está agora |
|---|---|---|
| `01_business_rules.md` | Máquina de estados do `SitCode`; cotação × pedido; encerramento | `wbcpython/domain/sitcode.py` (docstrings são a regra) + `wbcpython/domain/{cotacao,pedido,revisao}.py` + `../DECISOES.md` ("De negócio", "Cotação e pedido são procedimentos separados", "Encerrar a oportunidade…") |
| `02_data_model.md` | Tabelas do WBC (`INTEGRACAO_ORC*`), UDO `OrcDetalhe`, views HANA, normalização de números | `wbcpython/infrastructure/wbc_sql/{models,queries}.py`, `wbcpython/infrastructure/service_layer/orcdetalhe.py`, `wbcpython/infrastructure/hana/models.py`, `sql/hana/VW_INO_OPORTUNIDADE_INTEGRACAO.sql` + `../APRENDIZADOS.md` |
| `03_architecture.md` | Camadas (domain / application / infrastructure / host / dashboard) e o mapeamento DI-API → Service Layer | `../README.md` (seção "Estrutura") + docstrings de `wbcpython/{application,host,dashboard}/__init__.py` |
| `04_environment_constraints.md` | Regra 1 (nunca escrever em produção) e Regra 5 (nunca escrever no SQL Server do WBC) | `wbcpython/safety.py` + `tests/wbc/test_safety*.py`. A Regra 1 foi **revogada** na virada para produção (02/09/2026) — `../DECISOES.md`, "Virada para produção"; a Regra 5 continua absoluta, sem chave de desligamento |

Se a especificação original aparecer em alguma máquina, o lugar dela é esta pasta
(`docs/wbc/ai_spec/`), ao lado deste índice.
