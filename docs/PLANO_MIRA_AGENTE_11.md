# Plano — a .11 pronta para a Mira agir como agente

> **Status (02/10/2026, 10:20): F1 (segurança-base) NO AR na .11 e conferida só leitura** — os 3
> serviços reiniciaram com o código novo; chave-mestra, token antigo do MCP e telas seguem valendo
> (33/33 da API do CP, 8077 e MCP com e sem credencial). **Pende (Marcelo): migrar as chaves e,
> depois de alguns dias de auditoria, o firewall** (`docs/SEGURANCA_11.md`). Antes: codada. Decisões 3–9 aceitas com as recomendações, retenção da
> auditoria em **30 dias** (Marcelo). Antes: análise feita, nada implementado. Inventário levantado no código (só
> leitura). Escopo: **só a .11** — os outros servidores ficam para projetos seguintes.
> **Conclusão direta:** a .11 já responde bem a perguntas (19 ferramentas no MCP), mas **não está
> pronta para um agente que grava**: hoje uma chave única vale tudo, o MCP não sabe quem chama nem
> registra o que foi feito, e a "confirmação humana" das escritas depende só de o modelo obedecer.
> As fases F1 (segurança-base) e F3 (aprovação fora do modelo) vêm **antes** de dar qualquer
> escrita nova ao agente.

## Objetivo

A Mira (no .90, Sonnet 5.5) vai virar um agente de operação: consultar, diagnosticar e — com uma
pessoa aprovando — agir nas integrações da .11. Este plano diz o que existe, o que falta para os
casos reais e, principalmente, **as regras de segurança** que valem para tudo o que o agente fizer.

Escrita permitida ao agente (decisão do Marcelo, 01/10/2026), sempre com aprovação humana:
**processar pedido, sincronizar OS, forçar carga de oportunidades, reiniciar serviço da .11**.
Leitura inclui **ping e teste de porta a partir da .11**.

## 1. O que existe hoje na .11

### Serviços

| Serviço | Porta | Entrada | Grava no SAP? |
|---|---|---|---|
| API (`OrcaView-OS-API`) | 8077 | `X-API-Key` = `OS_API_KEY` (ou cookie das telas) | sim: status de OP (liberar) |
| MCP (`OrcaView-MCP`) | 8078 | `Bearer SIS_MCP_TOKEN` — **um token só, sem identidade** | indireto: sincronizar OS, forçar carga |
| Painel WBC | 8079 | cookie HMAC da `OS_API_KEY` | sim: ciclo de um orçamento |
| Controle de Produção | 8080 | cookie ou `X-API-Key` em `/api/*` | sim: OPs (processar, reprocessar, liberar, replanejar, encerrar) |
| Worker WBC | — | nenhuma (roda sozinho) | sim: cotações e pedidos |
| Agendador | — | nenhuma | não (SAP → Supabase) |

### Credenciais

| Credencial | Abre | Onde está hoje |
|---|---|---|
| `OS_API_KEY` | **tudo**: API inteira (inclusive apagar histórico e liberar OP), as 3 telas, `/api` do Controle de Produção | `.env` da .11, `mcp/.env` (`SIS_API_KEY`), `.env` do .90 (`OPORTUNIDADE_WBC_API_KEY`), a outra equipe (API dos Pedidos WBC), cookie no navegador de cada operador |
| `SIS_MCP_TOKEN` | o MCP inteiro, que por dentro usa a `OS_API_KEY` | `mcp/.env` + cada cliente MCP |
| `STATUS_ID` | só o `/status` completo | quem monitora |

### MCP — 19 ferramentas + 3 recursos

- **Leitura (17):** saúde (`verificar_saude`, `estado_integracao_wbc`, `estado_tarefa_wbc`,
  `estado_windows_update`), OS (`listar_pedidos_com_os`, `detalhe_pedido_os`,
  `listar_sincronizacoes_os`, `ultimos_erros`), oportunidades (`info_oportunidades`,
  `listar_sincronizacoes_oportunidades`), pedidos (`situacao_pedido`, `pedidos_bloqueados`,
  `panorama_pedidos`), WBC/OP (`estado_orcamento_wbc`, `situacao_op`), RH (`listar_colaboradores`,
  `resumo_colaboradores`).
- **Escrita (2):** `sincronizar_pedido_os`, `forcar_carga_oportunidades` — com `confirmar=True`.
  ⚠️ **Quem decide `confirmar=True` é o próprio modelo.** Não há nada no servidor que exija uma
  pessoa.

### Rastros (o que fica registrado)

| Onde | O que guarda | Problema para um agente |
|---|---|---|
| `logs/mcp_service.log` | subida do serviço | **não registra nenhuma ferramenta chamada** (log de acesso desligado) |
| `logs/api.log` | erros; só as rotas `/ordens-producao/` registram IP e programa (desde 29/09) | 6 dias; a API **não tem registro de acesso**; chamadas do MCP chegam como `127.0.0.1`, sem dizer quem |

> **Por que isto importa (caso real):** em 29/09/2026 descobriu-se que **um chamador da rota de
> status de OP encerrou 552 OPs ou mais sem movimentar estoque — e nada identificava quem era**
> (`api.py`, `_registra_chamada_de_op`). Com um agente chamando a .11 sozinho, esse cenário fica
> mais provável, não menos. É por isso que a F1 vem antes de tudo.
| Execuções do Controle de Produção (Supabase) | as 30 últimas, com `solicitante` | `solicitante` é texto declarado por quem chama |
| Acompanhamento do worker (SQLite) | eventos por orçamento | bom — é o que `estado_orcamento_wbc` lê |
| Deploy | **nada em arquivo** — só a tela do `cmd` | impossível responder "o deploy falhou, o que aconteceu?" |

Rede: a 8078 respondeu do notebook (01/10), ou seja, **está aberta para a LAN**, não só para o .90.
Não há regra de firewall documentada para ela (a 8080 tem, restrita a `192.168.0.0/16`). Tráfego
em HTTP puro: o token viaja em texto.

## 2. Os casos reais × o que falta

| Pergunta / pedido ao agente | Hoje | Falta |
|---|---|---|
| "A máquina/porta X responde?" (ping e porta **a partir da .11**) | ❌ não existe | `testar_conexao(destino, porta)` com **lista fechada** de destinos |
| "Os serviços da .11 estão no ar?" | ⚠️ `/status` cobre worker, CP e conexões; **não** os 6 serviços do Windows | `estado_servicos` (os 6, com estado e desde quando) |
| "Reinicie o serviço Y" | ❌ não existe | `reiniciar_servico(nome)` — só os 6, com aprovação, nunca com o CP ocupado, worker pela parada por arquivo |
| "Por que a integração do orçamento N falhou?" | ✅ `estado_orcamento_wbc` (status, regra, último erro, eventos) | o trecho do log do worker sobre o orçamento (o painel tem essa busca; o MCP não) |
| "Processe o pedido N" | ⚠️ a API `/api/pedidos-wbc` existe (plano + token + trava); o MCP não a expõe | ferramentas MCP sobre ela: listar, conferir, executar (com aprovação), acompanhar |
| "Sincronize as OS do N" / "Force a carga" | ⚠️ existe, mas a confirmação é só do modelo e ninguém fica registrado | refazer no padrão de aprovação fora do modelo (F3) |
| "O que mudou no pedido N e quem mudou?" | ⚠️ só aparece no log do Processar (a "CAUSA" do peso) | `historico_pedido(n)` lendo o histórico do SAP (ADOC/ADO1) |
| "O deploy falhou, o que aconteceu?" | ❌ nada gravado | deploy gravando `logs/deploy_*.log` + `ultimo_deploy` + a versão (commit) no `/status` |

## 3. Regras de segurança (valem para tudo o que o agente fizer)

Estas regras são o centro do plano. Cada uma vira código **e** teste automatizado — regra sem teste
volta a quebrar calada.

0. **Segurança acrescenta, nunca tira função** (Marcelo, 02/10/2026 — regra acima de todas). Tudo
   o que existe hoje continua disponível para quem já usa, agente incluído (RH, sincronizar OS,
   forçar carga). A segurança põe **em volta**: identidade, escopo, registro, limites, aprovação.
   Uma proteção que exigiria tirar uma função existente vira **decisão dele**, nunca padrão.

1. **O modelo nunca aprova uma escrita.** A ferramenta de escrita só **pede**: devolve o plano e
   um pedido de aprovação. Quem aprova é a **pessoa**, num botão da Mira, e esse clique — não o
   modelo — chama a .11. O modelo não tem ferramenta para aprovar. Assim, um texto malicioso que
   chegue ao modelo (nome de cliente, observação de pedido, conteúdo de página) não consegue
   gravar nada.
2. **Cada cliente com a sua credencial e o seu escopo.** Fim da chave única: uma para o agente da
   Mira, uma para a outra equipe (só `/api/pedidos-wbc`), uma para o web, uma para as telas. Cada
   chave abre só o que precisa; revogar uma não derruba as outras.
3. **Identidade real em toda chamada.** O agente informa **quem está conversando com a Mira**
   (usuário logado no OrçaView), e quem informa é o backend do .90 — não o modelo, que não pode
   inventar nem trocar esse nome. A .11 registra pessoa + canal ("mira-agente").
4. **Registro de tudo, que o agente não apaga.** Toda ferramenta chamada — leitura e escrita —
   gera uma linha: quando, quem, canal, ferramenta, parâmetros, resultado, duração. Guardado na
   .11 por **30 dias** (decisão de 02/10; antes: 6). Nenhuma credencial do agente apaga histórico: as rotas
   `DELETE /historico` ficam só para o administrador.
5. **Listas fechadas, nunca comando livre.** Ping e porta só para destinos cadastrados (sem
   varredura de rede); reiniciar só os 6 serviços da .11; **nenhuma** ferramenta que rode comando,
   PowerShell, SQL ou arquivo arbitrário.
6. **Limites de uso.** Teto por ferramenta (ex.: reiniciar 3 por hora, processar 10 pedidos por
   hora), lote máximo por pedido de aprovação, e respostas com tamanho limitado. Passou do limite,
   recusa com motivo claro.
7. **Escrita só no expediente.** Fora de seg–sex 7h–19h o agente só lê — ninguém para conferir
   uma OP criada às 2h da manhã.
8. **Botão de desligar.** Um interruptor na .11 corta o agente em um passo, sem reiniciar nada:
   tudo (leitura e escrita) ou só as escritas. Segue o padrão do arquivo de parada do worker.
9. **Rede fechada.** 8078 (MCP) e escrita da 8077 aceitam só o .90 e a máquina do administrador
   (firewall). Depois, TLS.
10. **RH com registro.** As ferramentas de RH (colaboradores) ficam com o agente, como hoje (decisão 7);
    cada consulta fica na auditoria, com quem pediu.
11. **O que já existe continua; o novo entra aos poucos.** As escritas que o MCP já tem (sincronizar
    OS, forçar carga) seguem com o agente. As **novas** (processar pedido, reiniciar serviço) entram uma
    por vez, depois da F3 e de observadas.
12. **O que nunca é do agente:** deploy, `.env`, reprocessar pedido, encerrar/cancelar OP, liberar
    ou replanejar OP, apagar dados, cadastro de chaves.

## 4. Fases (ordem real; cada uma depende da anterior)

### F1 — Segurança-base *(pré-requisito de tudo)* — no ar 02/10 ~10:19; pende a migração das chaves
*Quando fechar: dá para saber quem fez o quê pelo MCP, e uma credencial vazada não abre tudo.*
- Credenciais por cliente com escopo (regra 2) e identidade nas chamadas (regra 3).
- Registro de auditoria append-only com 30 dias (regra 4); `DELETE /historico` só com o escopo próprio (a chave-mestra e as telas continuam podendo).
- Interruptor do agente (regra 8). Firewall da 8078 (regra 9) — script `maintenance/firewall_mcp_8078.ps1`.
- **O que mudou no caminho:** a 8077 **não** vai para firewall (as pessoas abrem a Sincronização de
  qualquer PC); fica protegida pelas chaves com escopo. No Windows, uma regra de **bloqueio** vence a
  liberação por programa do `python.exe` — por isso o script bloqueia todos menos os permitidos.
- **Migração (02/10, manhã):** `orcaview-90` (OrçaView do .90; leitura, rh, os:sincronizar), `altamira-view`
  (app da equipe GLMiranda no mesmo .90; leitura, os:sincronizar, pedidos_wbc — era quem ainda usava a
  chave-mestra pelo 192.168.0.148), `mcp-servico` (a chave do MCP para a API) e `mcp-marcelo` (o
  notebook) criadas e trocadas; conferidas na auditoria e pelas ferramentas. O .90 sai para a .11 pelo
  IP **192.168.0.148** (não há NAT: o notebook aparece como .229). A tarefa `AltamiraView-Boot` estava
  **desabilitada** — reabilitada.
- **Migração concluída 02/10 10:53:** auditoria mostra os 4 clientes com a própria chave (`orcaview-90`,
  `altamira-view`, `mcp-servico`, e `mcp-marcelo` na próxima sessão do Claude); nada do .148 com `chave-mestra`
  depois das 10:42. **O que mordeu:** a primeira troca do `mcp\.env` foi feita no arquivo do **notebook**, não no da
  .11 — o MCP seguiu com a chave-mestra até a troca certa. E o `/status` com a chave-mestra não entrava na
  auditoria (corrigido em `60e0d0b`, pende deploy, junto com `7aef94a`).
- **Pende:** em 1–2 dias, sem `mcp-legado` na auditoria → apagar `SIS_MCP_TOKEN` do `mcp\.env`; depois,
  firewall da 8078 com .148, .90 e .229 (reservar o .229 no DHCP).

### F2 — A leitura que falta
*Quando fechar: o agente responde aos 8 casos sem ninguém abrir a .11.*
- `estado_servicos`, `testar_conexao` (lista fechada), `historico_pedido` (ADOC/ADO1), trecho do
  log do worker por orçamento, deploy gravando log + `ultimo_deploy` + versão no `/status`.

### F3 — Aprovação fora do modelo
*Quando fechar: nenhuma escrita acontece sem o clique de uma pessoa identificada.*
- Protocolo pedir → aprovar (pessoa, na Mira) → executar, com o token preso à pessoa e à operação.
- `sincronizar_pedido_os` e `forcar_carga_oportunidades` passam para esse protocolo (o
  `confirmar=True` do modelo deixa de valer).

### F4 — As escritas novas
*Quando fechar: o agente processa pedido e reinicia serviço, sempre com aprovação.*
- Processar pedido via MCP sobre `/api/pedidos-wbc` (listar, conferir, pedir aprovação,
  acompanhar). Reiniciar serviço (só os 6; recusa com o Controle de Produção ocupado; worker pela
  parada por arquivo).

### F5 — O lado da Mira (.90) *(próximo projeto)*
- Cartão de aprovação na conversa, identidade do usuário, ligação ao MCP da .11. Este plano deixa
  o contrato pronto; a implementação é do projeto do .90.

## 5. Decisões

1. **Escopo só a .11** — ✅ decidido (Marcelo, 01/10).
2. **Escritas do agente: processar pedido, sincronizar OS, forçar carga, reiniciar serviço; ping
   a partir da .11** — ✅ decidido (Marcelo, 01/10).
3. **Reprocessar pedido e encerrar/cancelar OP ficam fora do agente** — *assumido; confirme.*
   Recomendado: fora — são as mais difíceis de desfazer.
4. **Quem pode aprovar cada escrita.** Recomendado: processar pedido = PCP e admin; sincronizar
   OS e forçar carga = qualquer usuário logado; reiniciar serviço = só admin.
5. **Escrita só no expediente (seg–sex 7h–19h).** Recomendado: sim.
6. **Retenção da auditoria — ✅ 30 dias** (Marcelo, 02/10).
7. **RH no agente — ✅ dentro** (Marcelo, 02/10: "mantenha as funções que existem hoje").
8. **TLS no MCP.** Recomendado: firewall já (F1); TLS numa fase seguinte.
9. **A outra equipe deixa a chave mestra.** Recomendado: sim, na F1 ela recebe uma chave só da
   `/api/pedidos-wbc`.
