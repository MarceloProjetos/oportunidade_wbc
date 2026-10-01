# Changelog

Mudanças notáveis deste projeto. Formato inspirado em
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

Meses anteriores em `docs/changelog/AAAA-MM.md` (a raiz guarda só o mês corrente; ao virar
o mês, mova as entradas do mês que fechou para lá).

## [2026-10-01] — Textos mais curtos no painel WBC e nos Pedidos WBC

Só texto; entra pelo `deploy_update.bat` (painel WBC e Controle de Produção).

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

