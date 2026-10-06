# Changelog

Mudanças notáveis deste projeto. Formato inspirado em
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

Meses anteriores em `docs/changelog/AAAA-MM.md` (a raiz guarda só o mês corrente; ao virar
o mês, mova as entradas do mês que fechou para lá).

## [2026-10-06] — "← OrçaView" vai para o https do .90

O OrçaView do .90 passou a atender também em `https://192.168.0.90` (porta 443, no mesmo processo da
8000; plano em `web_orcaview_V118/docs/PLANO_HTTPS_90.md`, F4).

- `wbcpython/padroes.py`: `ORCAVIEW_URL` = `https://192.168.0.90/` — o botão "← OrçaView" das três
  telas (API, painel WBC, Controle de Produção). `ORCAVIEW_URL` no `.env` continua ganhando.
- `operacao/conexoes.py`: a sonda `orcaview-90` olha a 443 e a 8000.
- Testes que fixavam o endereço antigo atualizados; a trava de literal no `config.py` cobre também o novo.

## [2026-10-05] — Controle de Produção: o pedido segue o Detalhe do Orçamento novo (bug)

Cada Processar/Reprocessar grava um `OrcDetalhe` novo, mas o `ORDR.U_INO_ORCAMENTO` ("Detalhe do
Orçamento") ficava no antigo: o número do registro criado vinha de um esqueleto que devolvia 0
(84454 ficou no 546783 depois de cinco execuções). Medição 05/10, só leitura: 23 de 68 pedidos
abertos desde 01/09 fora do último Detalhe; 5 apontando para o Detalhe de outro orçamento, obra
do `max("DocEntry")` do addon legado.

- `preenche_tabela` devolve o `DocEntry` da resposta do `POST` (`_doc_entry_do_detalhe`), nunca
  o `max()`; o campo vai no mesmo PATCH de sempre.
- Log da execução: "Detalhe do Orçamento NNN criado." e "Detalhe do Orçamento do pedido: antigo →
  novo (conferido no SAP)", relido no HANA; ERRO em vermelho se não bateu ou se não há Detalhe
  novo. A releitura depois do PATCH nunca interrompe o pedido.
- Pedidos já errados não foram mexidos: cada Processar/Reprocessar novo os acerta.
- Precisa de deploy na .11 (restart do Controle de Produção).

## [2026-10-05] — Peso reescalado pelo SAP: a correção automática liga em 06/10 00:00

- `application/pesos_reescalados.GRAVA_A_PARTIR_DE = datetime(2026, 10, 6)` (F4, sim do Marcelo
  em 05/10): o worker passa a gravar o peso de antes da troca nas trocas de quantidade feitas a
  partir de 06/10 00:00; as anteriores ficam como estão. Rollback: voltar a `None`.
- Log do Processar: troca anterior à data diz "corrija à mão" em vez de ficar calada.
- Precisa de deploy na .11.

## [2026-10-05] — Peso reescalado pelo SAP: o worker confere (em simulação) e o Processar diz o que acontece

Quando uma pessoa muda a quantidade de uma linha no SAP, o próprio SAP multiplica o `Weight1`
(84457: 1 → 30, 148,94 → 4.468,20 kg). O peso que a integração grava é o da linha inteira e não
muda com isso. Medição de 05/10, só leitura: 26 reescalas em 15 de 99 pedidos desde 01/09, quase
todas consertadas à mão. Plano: `docs/PLANO_PESO_REESCALADO.md`.

- `wbcpython/domain/peso_reescalado.py`: a regra pura. Desfaz só a reescala de uma troca de
  quantidade feita por pessoa, se ninguém digitou outro peso depois, se o peso de antes era da
  integração e se o total da linha mudou no máximo 5%; espera 3 min depois do último salvamento.
- Worker: passo depois de cada ciclo agendado (pedidos com alteração de pessoa nos últimos 3
  dias). **Em simulação** (`application/pesos_reescalados.GRAVA_A_PARTIR_DE = None`): só registra
  no log e no acompanhamento o peso que voltaria. Ligar é um commit com o momento de início, e
  trocas anteriores a ele nunca são desfeitas (o passado não se altera — decisão do Marcelo).
- `python -m wbcpython pesos-reescalados [--dias N] [--pedido N]`: só leitura.
- `atualizar_pesos` aceita `If-Match` (ETag); `estado_das_linhas()` lê a ETag e as linhas num GET
  só. Quando a escrita for ligada, o worker grava sob a trava de execução, só as linhas que ainda
  estão como a decisão viu, e nunca a mesma reescala duas vezes. `pesos-reescalados --pedido N`
  diz se o Service Layer manda ETag (conferir antes de ligar).
- Processar pedidos novos: depois da CAUSA, uma linha com o que a integração faz com o peso
  (a mesma regra); as consultas ganharam `LineTotal`. Contrato: `API_PEDIDOS_WBC.md` 6.4.1.
- Precisa de deploy na .11 e restart do worker e do Controle de Produção.

## [2026-10-05] — Wake-on-LAN do .90: gatilho diário seg–sex 06:15, além do boot

Segunda 05/10 o .90 não ligou. A .11 **não reinicia todo dia**, como o instalador dizia: ela desliga
às 21:02 de seg a sex e a BIOS a liga às ~06:12; no fim de semana nada acontece. Ligada à mão no
sábado 03/10 às 09:25, ficou no ar até segunda, sem boot na segunda — e a tarefa
`OrcaView-WOL-AltservidorIA` só disparava no boot. O .90 foi acordado à mão do notebook.

- `install_wol_task.ps1`: segundo gatilho **semanal seg–sex às 06:15** (`-DailyAt`, `-DailyDays`;
  `-DailyAt ''` volta a ser só boot), com a mesma segunda chance de 15 min. Em dia normal o disparo
  das 06:15 cai na instância do boot ainda em curso (`IgnoreNew`) ou só registra "JA responde ao ping".
- Precisa **reinstalar na .11** como Administrador (`deploy_update.bat` + o instalador).

## [2026-10-02] — Situação dos pedidos: `lib_fin_em` não inventa hora com o histórico cortado

O SAP guarda só as **99 últimas versões** de cada pedido (`ADOC`) e apaga as mais antigas. A
`ultima_liberacao` contava a versão mais antiga que sobrou, já liberada, como "liberado desde a
criação": 26 dos 295 pedidos saíam com `lib_fin_em` mais tarde que o real, e a hora andava para
frente a cada alteração (84348: 15:06 em 25/09, 16:30 em 02/10; 84080: 27/08 para uma liberação de
junho). Achado ao conferir os exemplos do guia da equipe consumidora.

- Histórico que não começa no `LogInstanc` 1 só aceita uma passagem N→S **visível**; sem ela,
  `lib_fin_em` = `null` (a regra 2 do contrato: "não dá para saber").
- `lib_producao_em` continua certo quando o sinal foi pago depois da versão mais antiga que sobrou
  (`liberado_ate`, chave interna `_LibFinAte`): 84348 segue 25/09 08:13.
- Medido com a regra nova (02/10, 295 pedidos): 18 de 284 com Produção liberada ficam sem hora;
  `data_lib_prod` erra de −34 a +15 dias (o "−118" de 25/09 era o histórico cortado).
- Contrato `API_SITUACAO_PEDIDOS.md` (2.8, 4, 6.2, 10, 13: o 403 `sem_permissao` de 02/10 faltava) e
  guia `docs/API_SITUACAO_PEDIDOS_NOVOS_CAMPOS.md` revisados — exemplos conferidos no SAP, armadilha
  do `new Date("AAAA-MM-DD")` no JS, regra do pedido cancelado.

## [2026-10-02] — F5 do plano da Mira: aprovar pelo grupo do WhatsApp

`docs/PLANO_MIRA_AGENTE_11.md`, F5. O código é do web (V118.439, `services/mira_aprovacoes.py`); aqui só
documentação: `docs/APROVACOES_11.md` (seção F5, as duas credenciais) e `docs/SEGURANCA_11.md` (os 2
comandos: `aprovar` para o `orcaview-90`, cliente novo `mira-agente` com `--agente`). Firewall do Windows
da .11 está desligado: regra da 8078 sem efeito, registrado em `SEGURANCA_11.md` e no `CLAUDE.md`.

## [2026-10-02] — F3/F4 do plano da Mira: o agente pede, uma pessoa aprova

`docs/PLANO_MIRA_AGENTE_11.md`, F3 e F4. Contrato: `docs/APROVACOES_11.md`.

- **Pedido de aprovação** (`seguranca/aprovacoes.py`, rotas `/aprovacoes`): o agente pede, nada
  executa; vale 30 min, código de 4 dígitos, decidido uma vez. Escopo novo `aprovar` (nunca de
  agente) e `servico:reiniciar`.
- **Central `/inicio`**: seção "Aprovações do agente" — quem decide escreve o nome, aprova ou recusa.
- **4 ações** (`operacao/acoes_agente.py`): sincronizar OS, forçar carga, **processar pedido** (o
  plano é conferido de novo na aprovação) e **reiniciar serviço** (`operacao/reinicio.py`, com as
  travas do deploy). Execução pelas rotas de sempre, em nome de quem aprovou.
- **MCP**: `pedir_sincronizar_os`, `pedir_forcar_carga`, `pedir_processar_pedido`,
  `pedir_reiniciar_servico`, `acompanhar_aprovacao` (29 ferramentas). A porta 8078 carimba quem
  pediu e recusa o `confirmar=True` de credencial de agente; pessoas continuam como antes.
- `python -m seguranca acrescentar ... --declara-usuario`; `estado_servicos` mostra a conta.

## [2026-10-02] — F2 do plano da Mira: a .11 se explica sozinha (só leitura)

`docs/PLANO_MIRA_AGENTE_11.md`, F2. Pacote novo `operacao/`, rotas com escopo `leitura` e 5
ferramentas no MCP (24 no total). Nada grava.

- **`estado_servicos`** (`GET /operacao/servicos`): os 6 serviços NSSM, estado e desde quando.
- **`testar_conexao`** (`GET /operacao/conexoes[/<destino>]`): DNS, ping e porta a partir da .11,
  só para 9 destinos de uma lista fechada (nunca host ou porta livre); 20 por minuto.
- **`historico_pedido`** (`GET /pedidos/<n>/historico`): o histórico de alterações do SAP
  (ADOC/ADO1), versão a versão, com quem salvou e se foi pessoa ou integração. Conferido no
  84453 real (12 versões, 0,34 s).
- **`log_orcamento_wbc`** (`GET /wbc/orcamentos/<n>/log`): o log do worker sobre um orçamento.
- **`ultimo_deploy`** (`GET /operacao/deploy`): commit no ar × no disco e os passos do último
  deploy. `deploy_update.bat` grava `logs/deploy.log` (só acrescenta linhas; nenhum passo mudou —
  vale a partir do 2º deploy, porque o 1º roda a cópia antiga). O `/status` completo traz `versao`.

## [2026-10-02] — `python -m seguranca acrescentar`: escopo a mais sem trocar a chave

O Altamira View migrou para a chave própria sem o escopo `rh` e levou 44 × 403 em
`/rh/colaboradores` (auditoria da .11). Trocar a chave para corrigir quebraria o cliente de novo;
`acrescentar NOME --escopos rh` mantém a chave e só soma escopos (nunca tira). Passo a passo de
"cliente migrado com 403" em `docs/SEGURANCA_11.md`.

## [2026-10-02] — Segurança-base: chave por cliente, auditoria de 30 dias, interruptor do agente

F1 de `docs/PLANO_MIRA_AGENTE_11.md`. Entra pelo `deploy_update.bat` (API, Controle de Produção e
MCP). **Nada quebra no deploy**: a `OS_API_KEY` e o `SIS_MCP_TOKEN` continuam valendo para tudo; a
migração dos clientes é um passo a passo em `docs/SEGURANCA_11.md`.

- **Credenciais por cliente com escopo** (`seguranca/`, `python -m seguranca`): cada cliente com
  a sua chave, guardada só como SHA-256 em `state/credenciais.json`. Escopos: leitura, rh,
  os:sincronizar, oportunidades:carga, vendas_bi:carga, op:status, historico:apagar,
  pedidos_wbc, manutencao_op, mcp, admin. A 8077 declara o escopo em cada rota
  (`@requer_chave('leitura')`); a `/api/*` da 8080 por prefixo; o MCP por ferramenta. Fora do
  escopo → **403 `sem_permissao`**. Apagar histórico passa a exigir `historico:apagar`.
- **Auditoria** em `logs/auditoria/{api,controleproducao,mcp}-AAAA-MM-DD.jsonl`: quem, quando,
  rota/ferramenta, argumentos (MCP), escopo, resultado, IP, duração — **30 dias** (decisão do
  Marcelo). Sem rota que apague. Até hoje o MCP não registrava nenhuma ferramenta chamada e a
  8077 só registrava as rotas de OP.
- **Identidade**: `X-SIS-Usuario` (quem pediu) é aceito só de clientes marcados para declarar
  (o .90, o MCP) e vai para a auditoria.
- **Regras do agente** (credencial `--agente`): interruptor `python -m seguranca
  desligar-agente [--so-escrita]` e escrita só em dia útil das 7h às 19h → **403
  `agente_bloqueado`**. Pessoas não são afetadas. Um agente nunca recebe `admin`.
- **MCP**: `mcp/acesso_mcp.py` substitui o token único — token por cliente (escopo `mcp`),
  ferramenta no seu escopo (ferramenta nova sem escopo exige `admin`).
- **Firewall da 8078**: `maintenance/firewall_mcp_8078.ps1` (bloqueio que vence a liberação do
  `python.exe`; mostra antes de aplicar, `-Remover` desfaz). Você roda na .11, **depois** de a
  auditoria mostrar quais IPs usam o MCP hoje (todos entram na lista).
- **Regra acima de todas: segurança acrescenta, nunca tira função** (Marcelo). Nenhum acesso de
  hoje some: chave-mestra, token antigo do MCP e login das telas continuam valendo; RH e as duas
  escritas do MCP continuam disponíveis ao agente.
- 49 testes novos (credenciais, auditoria, agente, escopo de cada rota, porta do MCP).

## [2026-10-01] — Deploy: sem GitHub, não para nada

- 01/10 ~15:53 a .11 não resolveu `github.com` ("Could not resolve host") e o `deploy_update.bat`,
  que **parava os 6 serviços antes** de buscar o código, derrubou tudo e precisou religar (voltou:
  API, painel, Controle de Produção, MCP e worker conferidos, ciclo 4436 às 15:56 sem erro). O
  comando que falhou estava igual desde 10/07 — a causa foi a rede/DNS da máquina.
- Agora o **`git fetch` roda antes de parar qualquer serviço**: sem GitHub o deploy para ali com
  "NADA foi parado nem alterado". Depois da parada, a atualização é só local (`git merge --ff-only
  origin/master`, do que já foi baixado). Testado no `cmd` com um remoto que não resolve.
- Vale a partir do deploy **seguinte** a um que dê certo (o `.bat` roda de uma cópia do antigo).

## [2026-10-01] — Guia `API_PEDIDOS_WBC.md` mais didático

Só documentação. O guia ganhou um começo para quem chega sem contexto: "Como ler este guia" (por
objetivo), **A ideia em um minuto** (o caminho de um pedido e a conversa da página com a API, em
diagramas), um **tutorial em 5 etapas** com código curto e um ponto de conferência em cada etapa
(as 4 primeiras não gravam nada), **glossário**, **a vida de uma execução** (diagrama de estados) e
uma **lista de verificação antes de pôr no ar**. A referência continua igual, renumerada (6 a 15).

## [2026-10-01] — A causa do peso diferente também na API (`resultado.pesos_diferentes`)

Entra pelo `deploy_update.bat` (Controle de Produção). Só leitura.

- O "Processar pedidos novos" devolve, no resultado da execução, **`pesos_diferentes`**: cada linha
  com o peso do SAP diferente da árvore do WBC + 10%, com a **causa** estruturada (tipo, usuário,
  momento, quantidade e peso antes/depois, se a integração gravou certo, e a frase da tela). A API
  `/api/pedidos-wbc/execucoes/{id}` a devolve como está; guia: `API_PEDIDOS_WBC.md` §6.4.1.
- O pedido com peso diferente fecha como **ATENÇÃO** ("1 linha(s) com peso diferente da árvore do
  WBC (veja a CAUSA acima)") em vez de "concluído" — o desfecho continua `ok`.
- Pedido 84453 corrigido pelo Marcelo com `wbcpython pesos` (15:22): 176,90 kg, conferido no SAP.

## [2026-10-01] — Peso diferente no Processar: o log diz quem mudou a linha no SAP

Entra pelo `deploy_update.bat` (Controle de Produção). Só leitura.

- Pedido 84453 (orçamento 00125348): a integração criou a linha com quantidade 2 e 176,90 kg (árvore
  do WBC 160,82 kg + 10%) às 11:51; às 14:00 um usuário mudou a quantidade para 1 no SAP e o **SAP
  reescalou o peso** para 88,45 kg. O mesmo aconteceu no 84444 em 29/09. O "Processar pedidos novos"
  dizia só "DIFERENTE", e parecia defeito da integração.
- Agora, quando o peso está diferente, a linha seguinte do acompanhamento diz a **causa**, lida do
  histórico de alterações do SAP (ADOC/ADO1): "Fulano mudou a quantidade da linha 0 de 2 para 1 no
  SAP em 01/10/2026 às 14:00, e o SAP refez o peso na mesma proporção (176,90 → 88,45 kg). A
  integração tinha gravado o peso certo (176,90 kg) ao criar o pedido." Conferido contra o SAP de
  produção (84453). Sem histórico legível, fica só o "DIFERENTE", como antes.
- O peso **não** é corrigido sozinho: para acertar um pedido, `python -m wbcpython pesos --pedido N`
  (com `--simular` antes), no terminal da .11.

## [2026-10-01] — API JSON dos Pedidos WBC, para outro grupo clonar a tela

Entra pelo `deploy_update.bat` (reinicia o Controle de Produção). Plano:
`docs/PLANO_API_PEDIDOS_WBC.md`. Guia para quem consome: **`API_PEDIDOS_WBC.md`**.

- **`/api/pedidos-wbc`** (porta 8080, `X-API-Key`): lista "Pedidos novos"/"integrados" (15 por
  página, como a tela), confere e executa **Processar** e **Reprocessar** (mesmo plano e token de
  10 minutos da tela), acompanha e interrompe. **Sem "forçar"** (decisão de 01/10): `force` no
  corpo é recusado com 400. `solicitante` obrigatório ao gravar.
- **Uma regra só:** a lógica da tela foi para `pedidos_wbc/acoes.py`, e a tela e a API chamam as
  mesmas funções — as respostas trazem os textos da tela (rótulos, avisos, recusas). Uma execução
  da API bloqueia a da tela e aparece em Execuções, com quem pediu.
- **Página pronta** `docs/exemplos/pedidos_wbc_clone.html`: o clone completo da tela (lista,
  conferência, execução, tema claro/escuro), num arquivo só, que usa só a API. Conferida contra a
  API com o SAP simulado.
- **Execução (tela e API):** a linha "ERRO" ou "ATENÇÃO" de um pedido agora sai marcada com ⚠ e
  em vermelho no acompanhamento — antes ia sem a marca.

## [2026-10-01] — Textos mais curtos no painel WBC, nos Pedidos WBC e na Sincronização

Só texto; entra pelo `deploy_update.bat` (painel WBC, Controle de Produção e API 8077).

- Sincronização (8077): o subtítulo virou "Ordens de Serviço sob demanda e Oportunidades no
  agendador" e saiu o rodapé "API: … · Sincronizar = … · Forçar = …" (com a linha do script que o
  preenchia; um teste confere que todo `$('id')` do script tem o seu elemento).

- Painel WBC, subtítulo: "cotações e pedidos no SAP a partir do WBC".
- Aba Executar, "Processar um orçamento": o aviso virou "Roda o ciclo só para o orçamento
  informado nos últimos 24 meses. Espera terminar (até 3 minutos) e conferir no SAP". A etiqueta
  "escreve no SAP" aparecia duas vezes (no título do bloco e no cartão); ficou só a do cartão.
- Pedidos WBC: saiu a nota "Nada é gravado neste passo…" abaixo do "Processar selecionados…".

## [2026-10-01] — Configuração: um lugar só para os padrões das três telas

Sem efeito em produção (os valores são os mesmos); entra no próximo `deploy_update.bat`.

- Banco de acompanhamento, expediente e intervalo do worker, portas da API (8077), do painel
  (8079) e do Controle de Produção (8080), o log do Controle de Produção e o endereço do OrçaView
  estavam copiados em três `config.py`, com um teste comparando cópia por cópia. Agora moram em
  **`wbcpython/padroes.py`** e os três leem dali. O teste de paridade virou um teste de que todos
  leem do mesmo lugar e de que ninguém volta a fixar um valor num `config.py`.

## [2026-10-01] — Simplificações da revisão: Detalhe, código morto e duas ferramentas no MCP

Entra pelo `deploy_update.bat` (painel WBC, API 8077, Controle de Produção e MCP).

- **Detalhe do orçamento: "Solicitar reprocessamento" virou "Processar este orçamento…"**. O botão
  antigo só gravava um evento que ninguém lia e prometia reavaliar "no próximo ciclo" — falso
  para orçamento fora da janela do worker. O novo abre a aba Executar com o número já preenchido
  em "Verificar pendentes" e em "Processar um orçamento", e rola até ele. Nada roda sem o clique
  lá. A rota `POST /fragmentos/reprocessar` saiu.
- **Código morto removido:** as leituras de oportunidade pelo Service Layer
  (`pendentes_de_integracao`/`por_orcamento`; o ciclo lê só pelo HANA), o campo `opcao` dos
  comandos do painel (nunca preenchido), e a CLI do Encerrar passou a usar
  `service.classifica_encerramento` em vez da cópia própria das regras.
- **MCP: duas ferramentas novas, só leitura.** `situacao_op` (status de uma OP e as transições
  permitidas, sobre a rota que já existia) e `estado_orcamento_wbc` — o que o worker sabe de um
  orçamento (status, regra, documentos, último erro, eventos), pela rota nova
  **`GET /wbc/orcamentos/<orcnum>`** da API (lê o acompanhamento em somente leitura; `404
  fora_do_acompanhamento` = o worker nunca o avaliou).

## [2026-10-01] — Worker: snapshot só antes de gravar documento; vínculo que falha não refaz a cotação

Entra pelo `deploy_update.bat` (worker e painel). Decisão em `docs/wbc/DECISOES.md`.

- **Snapshot `@INO_ORCAM` só quando um documento vai ser gravado**, uma vez por orçamento, logo antes
  do primeiro envio ao SAP. Antes saía em toda decisão com ação — um orçamento sem itens ou a preço
  zero, ou uma mudança só de status, gravava um registro novo a cada 3 minutos sem documento
  nenhum apontando para ele. O `U_INO_ORCAMENTO` do documento continua levando o `DocEntry` dele.
- **Vínculo com a oportunidade que falha vira aviso**, e o status da oportunidade é espelhado do
  mesmo jeito. Antes a falha interrompia o orçamento antes do espelhamento, e na regra
  `emitido_apos_revisao_no_sap` isso cancelaria e recriaria a cotação a cada ciclo. O documento
  continua achado pelo orçamento; a falha fica no log e no histórico do orçamento como erro.

## [2026-10-01] — Revisão geral, lote 4: robustez da API, do worker e do deploy

Entra pelo `deploy_update.bat` (todos os serviços). Achados da revisão geral de 01/10/2026.

- **HANA fora do ar não derruba mais a API inteira.** Cada conexão que falhava levava ~51 s (3
  tentativas de 15 s), presa numa das 4 threads do waitress: quatro leituras ao mesmo tempo (o
  .90, o MCP, um lote de sincronização) tomavam todas, e até o `/health` parava de responder —
  o vigia do .90 via a API toda fora. Agora há um **disjuntor** em `sap_connection`: depois de
  uma falha de conexão, as chamadas dos 30 s seguintes falham na hora com "HANA indisponível há
  instantes" (`HanaIndisponivel`, um `ConnectionError`). E o waitress sobe com **8** threads.
- **A trava do worker é renovada entre orçamentos** (no máximo uma vez por minuto). Ela valia
  30 min fixos: um ciclo longo (janela estendida) podia passar disso, e outro processo — agora
  também o ciclo de um orçamento do painel, que espera a trava — assumiria a trava "vencida" no
  meio e os dois rodariam juntos.
- **Acompanhamento em WAL** (SQLite): o registro dos 1.880 orçamentos de cada ciclo caiu de 8,4 s
  para 3,1 s (medido), e as leituras do painel e do `/status` não bloqueiam mais o worker.
  `synchronous` continua FULL (o NORMAL ganhava só 0,1 s e podia perder o último registro numa
  queda de energia). A leitura somente-leitura do `/status` foi testada em WAL.
- **Log do WBC com vários processos:** só o worker contínuo rotaciona o `logs\wbcpython.log`; o
  painel, os comandos que ele dispara e a CLI acrescentam sem segurar o arquivo aberto. No
  Windows a rotação falha com o arquivo aberto em outro processo, e o handler padrão perdia
  **toda** linha enquanto isso durasse. Se a rotação falhar mesmo assim, o worker segue
  escrevendo no arquivo atual e tenta de novo em 1 min.
- **`ROTINAS_ESTADO_SUPABASE` saiu do `.env`:** o registro das rotinas em `rotinas_execucao`
  liga pelo IP da máquina (só na .11), como as outras funções de produção. A linha no `.env`, se
  existir, passa a ser ignorada.
- **`deploy_update.bat`:** se o `pip` falhar, o código volta ao commit anterior antes de religar
  (antes subia código novo com dependências velhas); "DEPLOY OK" só sai se a API, o painel e o
  Controle de Produção responderem 200 (senão, "DEPLOY TERMINOU COM AVISOS" e saída 2); a porta
  da API vem do `.env`; e avisa quando o Python do worker (NSSM) não é o mesmo do `pip`.



Entra pelo `deploy_update.bat` (API 8077, agendador e `OrcaView-MCP`). Achados da revisão geral
de 01/10/2026, conferidos no código antes de corrigir.

- **Vendas BI apagava o ranking e os KPIs por vendedor quando a consulta de detalhe falhava**, e
  registrava "sucesso": a falha virava lista vazia, a carga gravava os quatro cartões zerados e a
  poda por carimbo apagava o resto (o app mostrava R$ 0,00 em "Hoje" até a carga seguinte).
  Agora consulta que falha é diferente de consulta vazia: sem o detalhe, cartões e ranking ficam
  com a carga anterior, a poda não roda e o desfecho em `rotinas_execucao` é **falha**, dizendo
  qual consulta caiu.
- **Sincronização de OS com o pedido ocupado por outro processo** respondia 502 "erro" e gravava
  "falha" no histórico — o 409 "ocupado" da API nunca acontecia (a exceção era engolida antes).
  Agora chega como 409 "ocupado", sem linha de falha.
- **`/status`: cargas de oportunidades que falham viram alerta.** Só a idade da última carga
  contava; um dia inteiro de cargas no horário terminando em `falha` aparecia "saudável". Duas
  falhas seguidas dentro da janela = alerta (uma isolada não).
- **MCP:** cada rota tem o seu tempo de espera (`/status` 60 s, HANA 45 s, sync de OS 120 s,
  carga de oportunidades 180 s; antes 12 s para tudo), e estourar o tempo diz "a API demorou" —
  numa escrita, manda conferir o histórico antes de repetir — em vez de "inacessível". As tools
  de bloco do `/status` avisam "diagnóstico reduzido por falta de credencial" quando a chave não
  chega (a descrição dizia "aberto, não exige chave", falso desde 10/09). `pedidos_bloqueados`
  ganhou o mesmo teto de 40 do `panorama_pedidos` (podia devolver ~74 KB).
- **Os testes do MCP voltaram a rodar no notebook** (eram pulados calados com o `mcp` 2.x):
  `tests/mcp_fastmcp_stub.py` faz o papel do FastMCP 1.x só quando o real não importa — na .11
  continua o real. README do MCP atualizado (chave, tools que faltavam, caminhos).

## [2026-10-01] — Revisão geral, lote 2: Controle de Produção (Pedidos WBC)

Entra pelo `deploy_update.bat` (`OrcaView-ControleProducao`). Achados da revisão geral de
01/10/2026, conferidos no código antes de corrigir.

- **"Interromper" cortava a gravação no meio** em Processar, Reprocessar e Cancelar OPs (só o
  Encerrar parava entre etapas): podia sobrar OP criada sem `U_INO_OP`, sem os semiacabados e
  com o pedido já marcado como processado. Agora o módulo 2 para **entre pedidos** (no
  cancelamento, entre OPs); os que não começaram aparecem no log como "NÃO foram iniciados".
- **"Cancelar OPs" pela tela não devolvia o pedido a "não processado"** (a CLI devolvia): o pedido
  ficava `U_INO_ProcessWBC='Y'` com linhas apontando para OPs canceladas, e a retomada
  documentada não funcionava pela tela. Agora faz o mesmo que a CLI — limpa os vínculos quando
  todas as OPs ficaram canceladas (`service.cancela_ops_conferidas`).
- **O cancelamento confiava na conferência de até 10 min atrás:** uma OP liberada nesse meio
  tempo (PCP, Liberar, API, um Encerrar do outro módulo) era cancelada mesmo assim. Agora as OPs
  são relidas na execução: qualquer bloqueante recusa tudo, e só as conferidas que continuam
  planejadas são canceladas.
- **O token de confirmação não era preso à operação:** um token conferido no Processar era
  aceito no Reprocessar (que cancela todas as OPs planejadas do pedido). Agora cada token só vale
  na rota da sua operação (`Plano.tipo`), nos dois módulos.
- No módulo 2 a trava de escrita e a checagem de "já existe execução" vêm **antes** de gastar o
  token, como no módulo 3: uma recusa não obriga mais a conferir de novo.

## [2026-10-01] — Revisão geral, lote 1: segurança do painel e "Interromper"

Entra pelo `deploy_update.bat` (painel WBC e API 8077). Achados da revisão geral de 01/10/2026,
conferidos no código antes de corrigir.

- **A aba Log do painel lia qualquer arquivo do servidor:** `/fragmentos/log?arquivo=.env`
  mostrava o `.env` com todas as senhas a quem tivesse a chave. O painel agora lê só o `LOG_FILE`
  (o campo "Arquivo" virou só leitura), e o "Próximo ciclo" só o retrato padrão
  (`state/wbc_previsao.json`). O parâmetro `?arquivo=` é ignorado.
- **Os POST do painel não conferiam a origem** (a 8077 e o Controle de Produção conferiam). Desde
  hoje um deles é o ciclo que escreve no SAP sem senha, e o `SameSite` não separa portas: uma
  página em outra porta da .11 podia dispará-lo com o cookie de quem estivesse logado. Agora
  escrita só com `Origin`/`Referer` do próprio painel, ou com a chave (`X-API-Key`), mesmo sem
  chave configurada.
- **"Interromper" do ciclo não mata mais o processo.** O `terminate` era `TerminateProcess` no
  Windows: nenhum tratador rodava, a trava ficava presa 30 min, a execução ficava "em andamento"
  e, se caísse entre a gravação e o vínculo, sobrava cotação sem vínculo. Agora o painel grava o
  arquivo de parada **do próprio comando** (`WORKER_ARQUIVO_DE_PARADA` no ambiente dele): o ciclo
  termina o orçamento em curso, libera a trava e sai. Se ainda estiver esperando a trava do
  worker, desiste na hora. A tela mostra "interrupção pedida". Comandos só de leitura continuam
  encerrados na hora.
- **Redirecionamento aberto no login da 8077:** `/entrar?proximo=/%09/site` levava para fora do
  servidor depois da chave (o navegador descarta tab/quebra de linha da URL). `destino_local`
  (`casa/acesso.py`, das três telas) recusa caractere de controle e qualquer coisa com host.
- **A chave ia para o `api.log`** quando passada em `?key=` nas rotas de OP: o registro de
  chamadas agora mascara `key`/`api_key`.

## [2026-10-01] — Ciclo de um orçamento: 24 meses e espera pelo worker

Entra pelo `deploy_update.bat` (worker e painel). Decisão em `docs/wbc/DECISOES.md` ("Ciclo do
painel…", último bloco).

- **O 00123304 não rodou** às 10:07:33: o ciclo agendado do worker tinha tomado a trava 1 s antes,
  e a tela disse "[ok] 0 orçamento(s) avaliado(s)… nenhum erro". Agora o ciclo de um orçamento
  **espera** o do worker terminar (até 3 min) e, se ainda assim não rodar, a tela mostra falha:
  "Ciclo NÃO rodou: …".
- **Janela do orçamento avulso: 24 meses** (era 12), pedido do Marcelo. Vale para "Processar este
  orçamento" e para "Verificar pendentes" com orçamento. Se o `.env` da .11 tiver
  `MESES_DE_JANELA_DIRIGIDA`, apague a linha.
- O cartão "Processar um orçamento" diz a janela e a espera.

## [2026-10-01] — Texto da janela no ciclo com orçamento e na "Verificar pendentes"

Só mensagem de log/saída; nada muda no que é lido ou gravado. Entra pelo `deploy_update.bat`
(worker e painel).

- **Ciclo com `--orcamento`:** dizia "janela: OpenDate >= 2026-03-01, 7 meses (padrão)", mas a
  busca usa a janela dirigida (12 meses). Agora diz "orçamento 00123300; janela dirigida:
  OpenDate >= 2025-10-01, 12 meses; teto de … escrita(s)".
- **"Verificar pendentes":** imprimia sempre os meses da janela padrão (7), mesmo no ensaio de 12
  ou com orçamento. Agora diz os meses lidos e de onde vieram — "padrão", "ensaio" ou "dirigida,
  só o orçamento …".

## [2026-10-01] — Painel WBC: ciclo de um orçamento, sem senha; "Simular" sai da tela

Entra pelo `deploy_update.bat` (`OrcaView-WBC-Painel`). Decisão em `docs/wbc/DECISOES.md`
("Ciclo do painel: um orçamento por vez…").

- **"Ciclo de integração" processa só o orçamento informado** (pedido do Marcelo): o campo
  Orçamento é obrigatório — conferido na tela, na rota (só números; `123566` vira `00123566`) e
  no executor. Sem ele nada roda: a janela inteira continua sendo trabalho do worker.
- **Sem senha e liberado em produção**, numa seção própria, "Processar um orçamento", com o botão
  "Processar este orçamento". O **nome** de quem executa continua obrigatório e vai para o
  histórico do orçamento. A confirmação e o aviso de irreversibilidade ficam.
- **"Simular um ciclo" saiu do painel** (a CLI mantém `ciclo --simular`).
- "Recalcular pesos" e "Preencher datas de abertura" seguem como estavam: senha, e indisponíveis
  em produção.

## [2026-10-01] — Central Integração SAP, F5: a página inicial com o estado

Entra pelo `deploy_update.bat` (os três serviços de tela). Só leitura. `casa.css?v=4`.

- **`/inicio` na 8077:** um cartão por tela (Integração WBC, Controle de Produção, Sincronização)
  com o estado do serviço por trás — faixa colorida e pílula ("Em dia", "Executando", "Parado",
  "Sem resposta"…) e os fatos que importam (último ciclo e ciclos de hoje do worker; execuções em
  andamento do Controle de Produção; última carga de oportunidades e última OS sincronizada) — e,
  embaixo, as conexões (SAP HANA, SQL Server do WBC, Supabase, disco). Tudo vem do `/status` e dos
  logs de sincronização, os mesmos que o monitor do .90 lê: nenhum check novo. Atualiza a cada
  minuto (só com a aba visível) e mostra os avisos do monitor quando houver.
- **A marca "Central Integração SAP" da barra virou o link para o início**, nas três telas (no
  painel e no Controle de Produção, `/inicio` redireciona para a 8077 — `casa/destinos.inicio`).
  A ordem do menu não mudou, e `/` continua levando ao painel WBC (decisão de 08/09).
- Mesmo login das outras telas; a entrada da 8077 ganhou o atalho "Ver o estado da Central" quando
  o painel não responde.

## [2026-10-01] — Central Integração SAP, F4: acabamento

Entra pelo `deploy_update.bat` (os três serviços de tela). Nenhum comportamento muda.

- **Uma regra só para "onde mora a outra tela"**: `casa/destinos.py` (o `.env` se configurado,
  senão o mesmo host na porta da tela). Eram seis cópias em `api.py`, `wbcpython/dashboard/web.py`
  e `controleproducao/core/acesso.py`.
- **A entrada da 8077 (`GET /`) também na casca:** enquanto passa para o painel, já mostra a barra
  que o painel vai mostrar; se o painel não responde, o aviso e os caminhos para as outras telas
  saem no visual da casa (era azul e branco). O comportamento (sonda de 3 s, `location.replace`)
  é o mesmo.
- Docs: `README` (entrada e links), `docs/wbc/README.md` (visual, barra, aba Ciclos),
  `docs/controleproducao/GUIA_OPERADOR.md` (como navegar; "Cancelar as OPs" na Manutenção de OP)
  e `docs/controleproducao/README.md`.

## [2026-10-01] — Central Integração SAP, F3: a Sincronização entra na casa

Entra pelo `deploy_update.bat` (`OrcaView-OS-API`). As cargas, as rotas JSON e o `X-API-Key`
dos scripts, do MCP e do .90 não mudaram; o `/status` e o `/health` também não. `casa.css?v=3`.

- **A Sincronização ganhou a cara das outras duas telas:** a mesma barra (com "Sincronização"
  destacada), o título de página da casa, cartões e pílulas iguais aos do painel, tema escuro por
  padrão e o botão para claro. O azul próprio, o logo "OS" e os emojis saíram; os números de
  Oportunidades viraram dois indicadores (linhas na tabela · cadência).
- **Mesmo login das outras telas (decisão 6):** sem o cookie, `/sincronizar` leva à tela da chave
  (`/entrar`, nova na 8077); quem já entrou no painel ou no Controle de Produção abre direto. O
  campo de colar a chave, o cadeado e o `localStorage` com a chave **saíram** da página — as
  chamadas dela vão pelo cookie. Escrita feita pelo cookie só vale vinda da própria página
  (`Origin`/`Referer` do mesmo host:porta). `X-API-Key` continua igual para quem chama por script.
- O cookie (nome, HMAC, comparação) mudou de casa: `casa/acesso.py`. O
  `wbcpython/dashboard/acesso.py` só o reexporta — nada muda para o painel e o Controle de Produção.
- Rotas novas na 8077, todas abertas porque só redirecionam ou servem a casca: `/entrar`,
  `/sair`, `/orcaview` (`ORCAVIEW_URL`, padrão o .90 — paridade com os outros dois),
  `/controle-producao/<tela>` e `/casa/<arquivo>`.
- Achado na prévia: a janela "Buscar na lista" abria sozinha ao carregar, porque um `display: grid`
  vencia o atributo `hidden`. A regra `[hidden] { display: none !important }` agora fica no
  `casa.css` e vale nas três telas.
- Resultado "ocupado" de um pedido aparece como "Em andamento" (antes, "FALHA"), e o 429 da carga de
  oportunidades ganhou mensagem própria.

## [2026-10-01] — Central Integração SAP, F2: o painel WBC entra na casa

Entra pelo `deploy_update.bat` (`OrcaView-WBC-Painel`); só aparência — rotas, fragmentos e
HTMX iguais. `painel.css?v=20261001`, `casa.css?v=2`.

- **A mesma barra do Controle de Produção** no topo do painel, com "Integração WBC" destacada.
  Os três botões contornados (Sincronização, Pedidos WBC → OPs, Manutenção de OP) saíram — estão
  na barra, com "Execuções" do Controle de Produção junto (`/controle-producao/tarefas`).
- **Tarja vermelha de produção saiu** (decisão 3): a pílula `SBOALTAMIRAPROD` vermelha na barra
  é a mesma das outras telas.
- **Título de página** no bloco da casa (ladrilho coral + título + subtítulo), na mesma posição e
  tamanho do Controle de Produção. As abas ficam numa linha logo abaixo, presa sob a barra ao
  rolar a lista, com "Incluir fora da janela" à direita.
- **Aba "Execuções" agora se chama "Ciclos"** (decisão 4): "Execuções" na barra é a do Controle de
  Produção. O endereço `/?aba=execucoes` continua valendo.
- **Tema:** o mesmo cookie das outras telas; quem tinha escolhido claro no painel é migrado na 1ª
  visita. A paleta base vem do `casa.css` (uma definição só).
- **"← OrçaView"** também no painel (`/orcaview`, `ORCAVIEW_URL` — padrão o .90, igual ao do
  Controle de Produção; teste de paridade).
- Conteúdo um pouco mais largo (1500 → 1800 px) e margem de 20 px, alinhado com a barra.
- No tema claro, o coral como texto escureceu para `#a94a2e` (5,7:1 sobre branco) nas duas telas
  — era o ajuste que o painel já tinha.

## [2026-10-01] — Central Integração SAP, F1: a casca comum estreia no Controle de Produção

Entra pelo `deploy_update.bat` (`OrcaView-ControleProducao`); só aparência — nenhuma rota,
regra ou gravação mudou. Plano: `docs/PLANO_CASA_COMUM_11.md`.

- **Pasta nova `casa/`**, a casca que as três telas da .11 vão dividir: barra do topo, paleta,
  título de página e botão de tema. O Controle de Produção é o primeiro a usar; o painel WBC
  (F2) e a Sincronização (F3) vêm depois.
- **Barra:** marca "Central Integração SAP" e as telas na ordem do pedido — ← OrçaView ·
  Integração WBC · Pedidos WBC · Manutenção de OP · Sincronização · Execuções. A tela aberta
  fica destacada. Linha coral fina no topo, igual em toda tela.
- **Ambiente:** pílula com a company DB na barra, vermelha em produção. Fora de produção a faixa
  cinza continua.
- **Tema:** escuro por padrão, o botão troca para claro. A escolha agora fica num cookie, que vale
  nas três portas; quem tinha escolhido claro antes é migrado na primeira visita.
- **Título de página:** o ícone ganhou um ladrilho coral (vermelho na confirmação e no erro), o
  mesmo desenho da marca.
- Notebook de 1280 px: o menu esconde os ícones e mantém os nomes. Celular: as telas vão para uma
  segunda linha que rola de lado. `style.css?v=12`.

## [2026-10-01] — Controle de Produção: "Cancelar as OPs" na Manutenção de OP; menu junto

Entra pelo `deploy_update.bat` (`OrcaView-ControleProducao`); só a tela.

- **"Cancelar as OPs de um pedido" mudou de página** (pedido do Marcelo): saiu de Pedidos WBC e
  fica na Manutenção de OP, abaixo de "Encerrar todas as OPs de um pedido". A rota continua
  `/pedidos-wbc/cancelar-ops/*` (a operação, a trava de execução e o comando da CLI são do módulo
  Pedidos WBC); só o "Voltar" da conferência e dos erros agora leva à Manutenção de OP.
- **Menu do topo**: "Painel WBC" e "Execuções" vieram para junto de "Manutenção de OP"; só "Sair"
  e o botão de tema ficam à direita.
- **"Sincronização SAP → Supabase" no menu**, entre "Manutenção de OP" e "Painel WBC" — o mesmo
  botão do painel WBC. Passa por `/sincronizacao` (aberta, só redireciona): `SIS_PAINEL_URL` se
  houver, senão o mesmo host na `OS_API_PORT` (8077), em `/sincronizar`. Em tela de até ~1300 px
  o "Sair" e o tema descem para uma segunda linha.

