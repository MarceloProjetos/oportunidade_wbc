# Incidentes e medições — o "porquê" das regras do CLAUDE.md

O `CLAUDE.md` guarda a **regra**; aqui fica a **história** que a justificou (datas, números
medidos, o que quebrou). Movido do `CLAUDE.md` em 24/09/2026 (`docs/PLANO_DX_AGENTE.md`, F1),
sem perder fato. Cada seção tem o nome da regra correspondente.

---

## `/status` em dois níveis (10/09/2026)

Sem credencial o `/status` devolve a visão mínima (`_status_publico`); o completo pede
`OS_API_KEY` **ou** `STATUS_ID` — credencial de baixo privilégio, entregue à outra equipe.
`_autorizado()` **não** aceita o `STATUS_ID`, e há teste cravando 401 nas outras rotas.
O código HTTP não depende da credencial porque o watchdog do `.90` chama
`?checks=worker&strict=1` sem nada e decide pelo código. A redução mora em `api.py`;
`monitoring.py` ficou intacto porque `collect_status`/`SELECTABLE_CHECKS` são contrato entre
repos. Plano: `docs/PLANO_STATUS_E_ENDERECO_ENTREGA.md`.

## Vendas BI: `SUM("VlrPedido")` sem índice (medido em 11/08/2026)

O modelo do Power BI tem a coluna `valorXindice` (`VlrPedido * Indice_Pedido`), que **não** é
a que o dashboard mostra. Agosto/2026 fechou em **R$ 1.314.876,11** pelo bruto (igual ao PBI)
e **R$ 1.309.079,46** pelo índice. Trocar faz a tela do app divergir do Power BI sem erro
nenhum aparecer. Idem faturamento: `SUM("Valor")` de `VW_FATO_FATURAMENTO`, sem
`ValorAdiant`. O vendedor casa por **nome** (`OSLP.SlpName` = `app_profiles.slp_name`)
porque o `slp_code` dos dois lados diverge.

## Troca de parceiro: o SAP recusa o cancelamento ao `orcaview`

Na troca com pedido existente (cancelar e recriar), o SAP responde `-1116 (1996)` ao usuário
`orcaview` — regra do `SBO_SP_TransactionNotification`; o `financeiro04` pode. Não é defeito
da integração. Decisão: `docs/wbc/DECISOES.md`, "`U_INO_Update = 'Y'` antes de existir pedido".

## Windows Update: "0 pendentes" mente (a .12 ficou 610 dias sem patch)

A busca `IsInstalled=0` **responde** (3,1 s aqui, 22,5 s na .12), diz **0** e está errada,
porque o cache de varredura do agente está vazio. A .12 ficou **610 dias sem patch**
exatamente assim. Por isso `windows_update.py` só publica `pendentes` quando
`LastSearchSuccessDate` (COM `Microsoft.Update.AutoUpdate`, 7-17 ms) é recente.

Custos medidos: reboot pendente 0,2 ms (winreg, independe do agente) · varredura 7-17 ms ·
`Get-HotFix` ~1 s · busca 3,1 s aqui / 30 s a frio — daí a thread daemon no `api.main()`: a
busca estouraria o timeout de quem chama o `/status`. `LastInstallationSuccessDate` parece
um `Get-HotFix` barato mas inclui **ruído do Defender** (divergiu nas duas máquinas).

**Nunca alerta** — decisão do Marcelo em 16/07/2026, revisando a D1 do plano: *"se um dia o
servidor não reiniciar não importa"*. Alerta derruba `healthy` e faz o `?strict=1` responder
503: era o único ponto em que este módulo mexeria no comportamento de quem monitora a
integração. Esta máquina tem `AUOptions=4` (instala sozinha — é isso que a mantém em dia);
`2` foi o que matou a .12. Plano: `../SAP_RDP/docs/PLANO_WINDOWS_UPDATE.md`.

Os testes stubam `_windows_update_signal` (`_stub_all_ok`) porque **esta máquina TEM reboot
pendente**: sem o stub os testes dependeriam de um fato do ambiente.

## Tarefa legada "Integração WBC" desativada (08/09/2026)

A virada para o worker desativou a tarefa agendada. O check `scheduled_task` **ficou** — o
card do `.90` (`status.js`) e a tool MCP `estado_tarefa_wbc` leem o bloco — mas nasce
`retired=true`, `available=false`, sem alerta (`WBC_TASK_MONITOR_DEFAULT = False`).
`WBC_TASK_MONITOR=true` + `monitor_wbc_task.ps1` são o rollback. A tarefa
`OrcaView-Monitor-WBC-Task` do Task Scheduler pode ser removida (`Unregister-ScheduledTask`);
com o monitor desligado o JSON dela é ignorado.

## Import do WBCPython por cópia, sem histórico (08/09/2026)

O histórico do repo antigo `MCPs\WBCPython` versiona um `.env.bak` com senha, e este repo é
público. Por isso o import foi por cópia — e nunca um `git subtree add`.

## O worker ficou meio dia no Python 3.12 (14/09/2026)

O worker é o único dos 5 serviços que **não passa por um `.bat`**: o NSSM chama o
`python.exe` direto, com o caminho absoluto em `Application`, resolvido por `where python`
**no dia em que o `install_wbc_services.bat` rodou**. É de propósito: com o `.bat` no meio, o
Ctrl+C do NSSM morria no *"Terminate batch job (Y/N)?"* (08/09/2026).

Na migração para o 3.14, 4 serviços foram para o 3.14.7 no reboot e o worker ficou no 3.12
até as 13:12, **escrevendo em produção**, sem nenhum sinal: o `system.python` do `/status` é
o do processo da API e dizia 3.14. Descoberto na véspera de desinstalar o 3.12 — o que teria
derrubado justamente o serviço que cria cotação e pedido. Detalhe:
`docs/arquivo/PLANO_PYTHON_314_NA_11.md` §7.

## Worker morto aos 60 s no meio do ciclo #133 (09/09/2026)

O Ctrl+C do NSSM (`AppStopMethodConsole 60000`) chegava, mas o worker não reagia
(`Event.wait()` sem timeout no Windows) e era **morto aos 60 s** — no meio do ciclo #133, com
a trava presa por 30 min. Matar no meio de um POST no SAP deixa cotação criada sem vínculo
(`docs/wbc/RETOMADA.md`). Daí a parada por arquivo (`state\wbc_worker.stop`,
`wbcpython/host/parada.py`).

## `__init__.py` fora do git (08/09/2026)

O pacote `wbcpython/` chegou à `.11` sem nenhum `__init__.py` nem o `__main__.py`: a regra
`_*.py` do `.gitignore` (arquivos temporários) casa com os dois nomes, e localmente tudo
funcionava porque os arquivos existiam no disco. `tests/test_repo_layout.py` pergunta ao git,
não ao disco.

## A suíte abria conexão real com produção (24/09/2026)

A trava do `tests/conftest.py` (F0 do `PLANO_DX_AGENTE`) pegou 12 testes que abriam conexão
REAL — 11 com o HANA de produção e 1 relendo o Supabase de produção. E o `hdbcli` 2.29.25 no
Python 3.14 derruba o processo com access violation quando a conexão falha: o pytest morria
sem dizer qual teste vazou. Por isso a trava é nos **drivers**, não só no `.env`.
