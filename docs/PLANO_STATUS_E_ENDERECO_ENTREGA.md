# Plano — Fechar o `/status`, abrir o endereço de entrega

> **Status (2026-09-10): as duas frentes FECHADAS e no ar.**
>
> - **Frente A:** o `STATUS_ID` está no `.env` da .11 e foi conferido ao vivo — com ele o
>   `/status` vem completo, sem ele vem a visão mínima (§1.7).
> - **A4 ✅ feita** (V118.58 no repo do OrçaView): o proxy do modal prefere o
>   `OPORTUNIDADE_WBC_STATUS_ID` e só cai na chave forte se ele faltar — com `WARNING`
>   no log, para o fallback não ser silencioso.
> - **Só falta, e é com ele:** (1) mandar o texto do README para a outra equipe;
>   (2) pôr `OPORTUNIDADE_WBC_STATUS_ID` no `.env` do `.90` + restart. Até lá o card
>   funciona igual, usando a chave.
> - **Frente B: FECHADA e no ar.** O `.11` foi atualizado e reiniciado em 10/09, e o
>   smoke passou nos quatro casos (§2.8) — inclusive o cancelado e o furo do panorama.
> - **As 9 decisões estão fechadas.** Nenhuma pergunta em aberto.
> - **Suíte: 1661 passando, 12 skipped, zero erro** (era 1165 passando com 42 erros no
>   começo do dia — `flask` e `apscheduler` entraram a pedido dele, e o guard dos testes
>   de MCP foi corrigido).

Duas frentes independentes na API 8077 da `192.168.7.11`, que podem subir no mesmo deploy
ou em deploys separados:

| Frente | O quê | Rotas |
| --- | --- | --- |
| **A** | Um **`STATUS_ID` próprio** para o diagnóstico: quem tem, vê tudo; quem não tem, vê só saúde | `GET /status` |
| **B** | A Situação dos Pedidos passa a dizer **para onde a mercadoria vai** — já resolvido, sem escolha para quem consome | `GET /pedidos/situacao` · `GET /pedidos/{n}/situacao` |

**Fora de escopo deste plano** (achados da análise de 2026-09-10, ficam para uma decisão
à parte): a chave única sem identidade nem escopo (`OS_API_KEY` lê colaboradores, muda
status de OP no SAP e abre o painel 8079) e a leitura de arquivo arbitrária do painel
(`GET /fragmentos/log?arquivo=`).

---

## 1. Frente A — o `/status` aberto

### 1.1 O que sai hoje, sem chave nenhuma

Medido em 2026-09-10 11:43, com um `curl` da máquina do Marcelo, **sem header nenhum**:

| Bloco | O que publica | Por que importa |
| --- | --- | --- |
| `system.hostname` / `ip` / `os` | `SAPBusinessOneI` · `192.168.7.11` · `Windows-2022Server-10.0.20348-SP0` | Identifica a máquina e a build do Windows |
| `system.python` | `3.12.10` | Versão do runtime |
| `checks.sap.detail` | `SAPBusinessOneHana-vm:30015` | **Host e porta do HANA de produção** |
| `checks.sql_server.detail` | `192.168.0.1:1433 / WBCCAD` | **Host, porta e nome do banco do WBC** |
| `checks.supabase.detail` | a URL do projeto Supabase | Aponta o projeto que os 3 apps usam |
| `wbc_worker.db` | `C:\Python\ServidorIntegracaoSAP\state\wbc_tracking.db` | Caminho de instalação em disco |
| `windows_update` | `pendentes: 3`, `dias_sem_patch: 5`, `ultimo_patch_kb` | Nível de patch da máquina |
| `system.disk_*` / `cpu` / `mem` | 36,6 GB livres de 126,4 GB, CPU 95,3% | Capacidade e carga |
| `api_auth.api_key_configurada` | `true` | Diz se a API está exigindo chave |

São **≥12 campos** que, juntos, desenham a topologia da integração para qualquer um na
LAN — sem autenticar, sem deixar rastro.

### 1.2 Quem chama o `/status` hoje (o que não pode quebrar)

| Chamador | Como chama | Manda credencial? | O que precisa de volta |
| --- | --- | --- | --- |
| `mira-watchdog.js` (.90) | `/status?checks=worker&strict=1` | **não** | Só o **código HTTP** (200 vs 503) |
| Modal de status do OrçaView (.90) | `/api/oportunidade-wbc/status` → proxy | **sim** (`OPORTUNIDADE_WBC_API_KEY`) | O corpo inteiro, para desenhar o card |
| Tool MCP `verificar_saude` (8078) | via API 8077 | **sim** (injetada server-side) | O corpo inteiro |
| `host_breaker.py` (.90) | TCP na 8077 | — | Só o socket |
| Marcelo, no navegador | `/status` | não | Diagnóstico legível |

> **Ressalva honesta:** a API não escreve access log por requisição, então este mapa vem
> da leitura do código dos três repos, não de um log de quem realmente chamou. Se existir
> um monitor de terceiros apontado para o `/status`, ele não aparece aqui — e passaria a
> ver a visão mínima.

### 1.3 O desenho: um `STATUS_ID` de baixo privilégio

**Decisão do Marcelo (10/09):** em vez de "quem tem a chave forte vê tudo", nasce uma
credencial **só para o diagnóstico** — o `STATUS_ID` — compartilhada com o OrçaView e com
a outra equipe. Ninguém perde funcionalidade, e ler o `/status` deixa de exigir a chave
que também escreve no SAP.

| Quem | Credencial | O que recebe |
| --- | --- | --- |
| Watchdog do .90, monitor externo, curioso na LAN | nenhuma | **Visão mínima**: `ok`, `healthy`, `restrito: true`, `checks.<nome>.ok`, `alerts` como **contagem**. Os códigos do `?strict=1` são idênticos aos de hoje |
| Outra equipe · OrçaView · Marcelo no navegador | **`STATUS_ID`** | O payload **completo**, exatamente como sai hoje |
| Quem já tem a `OS_API_KEY` | `OS_API_KEY` | O payload completo — continua valendo, nada quebra |

**As três invariantes deste desenho** (cada uma com teste cravando):

1. **O `STATUS_ID` não abre mais nada.** `_autorizado()` — o guard das outras 18 rotas —
   **não** o aceita. Teste: `STATUS_ID` em `/rh/colaboradores` → **401**.
2. **O `?strict=1` é calculado antes da redução.** O watchdog do .90 decide pelo código
   HTTP e não muda uma linha.
3. **Fail-closed na credencial nova.** Sem `STATUS_ID` no `.env`, uma credencial
   qualquer não abre nada: só a `OS_API_KEY` abre o completo, e o resto fica na visão
   mínima. A única porta que continua aberta é a antiga: **sem `OS_API_KEY` configurada
   a API inteira é aberta** (fail-open documentado no topo do `api.py`) e o `/status`
   acompanha — uma regra de fail-open só para esta rota seria uma segunda regra para
   lembrar. Quem denuncia esse estado é o próprio `api_auth` do payload.

**Onde o ID é aceito:** nos mesmos três lugares da chave (`X-API-Key`,
`Authorization: Bearer`, `?key=`), com `compare_digest`. Assim o modal do .90 e a outra
equipe **só trocam o valor** — nenhum código de cliente muda.

**Como nasce:** `python -c "import secrets; print(secrets.token_urlsafe(32))"`, gravado no
`.env` da .11 pelo Marcelo. Nunca no repositório, nunca neste documento.

**Rotação:** trocar o `STATUS_ID` não derruba cookie do painel WBC nem quebra rota
nenhuma — é justamente o ponto de ele existir separado da `OS_API_KEY`.

### 1.4 O que cada um vê

| Campo | Anônimo | Com `STATUS_ID` ou `OS_API_KEY` |
| --- | --- | --- |
| `ok`, `healthy`, `service`, `timestamp`, `uptime_s` | ✅ | ✅ |
| `restrito: true` | ✅ (novo — diz que a visão é mínima) | ausente |
| `checks.<nome>.ok` | ✅ | ✅ |
| `checks.<nome>.ms` / `.detail` / `.error` | ❌ | ✅ |
| `alerts` | **contagem** (`alerts: 2`) | ✅ lista com os textos |
| `scheduler`, `scheduled_task`, `wbc_worker` | ❌ | ✅ |
| `system` (hostname, ip, os, python, disco, CPU) | ❌ | ✅ |
| `windows_update` | ❌ | ✅ |
| `api_auth` | ❌ | ✅ |

### 1.5 Onde mexe

- **`api.py`** — `_status_id_ok()` (novo, só usado na rota `/status`) e
  `_status_publico(data)`. **Não** é o decorator `@requer_chave`: a rota continua aberta,
  só muda o que ela conta.
- **`config.py`** — `status_id` no `Settings` + `.env.example`. É **credencial**, não flag
  de funcionalidade: a regra de "função de produção não ganha `*_ENABLED`" continua
  valendo e não é contrariada aqui — `STATUS_ID` é da mesma família de `OS_API_KEY` e
  `SIS_MCP_TOKEN`.
- **`monitoring.py` NÃO muda.** `collect_status()` e `SELECTABLE_CHECKS` são contrato
  entre repos (o card do .90 e a tool MCP leem os blocos pelo nome). A redução é
  apresentação, não coleta.
- **`tests/test_api.py`** — anônimo vem mínimo; com `STATUS_ID` vem completo; com
  `OS_API_KEY` vem completo; `?strict=1` devolve o mesmo código nos três;
  `?checks=worker&strict=1` (o do watchdog) inalterado; e o teste da invariante 1.


### 1.7 A1 no ar — smoke em produção (10/09, 13:52)

O Marcelo reiniciou a .11 e o serviço subiu **já com o A1**. Conferido ao vivo:

| O quê | Resultado |
| --- | --- |
| `GET /status` sem credencial | `restrito: true`, sem `system`, sem `windows_update`, `alerts` como número |
| `X-API-Key` **errada** | Também a visão mínima — credencial inválida não vira 401 nesta rota, vira menos informação |
| `?checks=worker&strict=1` **sem credencial** (o do watchdog) | **200**, como antes |
| Tool MCP `verificar_saude` (manda a chave server-side) | Payload **completo**: `system.hostname`, `checks.sap.detail`, `api_auth` |
| Tool MCP `situacao_pedido(84348)` | Responde normal — `doc_entry 19763`, `valor_total 550.812,11`, igual à tela |

Ou seja: **a A3 está feita para o código**; falta só o `STATUS_ID` no `.env` para a
outra equipe deixar de depender da chave forte. Enquanto ele não existir, quem precisa
do payload completo usa a `OS_API_KEY` — que é exatamente como era antes.

### 1.6 Fases da frente A

| Fase | Entrega | Dono |
| --- | --- | --- |
| **A0** | Inventário dos chamadores — ✅ feito em 10/09 (§1.2) | eu |
| **A1** | ✅ **CONCLUÍDA 10/09** — `status_id` no `config.py` + `.env.example`; `_credencial_enviada()`/`_confere()` extraídos do `_autorizado()`; `_status_completo_autorizado()` e `_status_publico()` no `api.py`; a rota calcula o código HTTP antes de reduzir. **6 testes novos** em `tests/test_api.py` (um por invariante + o não-vazamento + as duas credenciais + o fail-open), **150 passando no módulo**. O refactor derrubou o `test_autorizado_usa_compare_digest`, que inspecionava o fonte do `_autorizado`: agora ele olha o `_confere` **e** exige que o `_autorizado` delegue — senão a garantia de tempo constante se perderia calada | eu |
| **A2** | ✅ **CONCLUÍDA 10/09** — sem doc novo: a explicação entra na seção de Monitoramento do `README.md` (que já documentava o `/status`) e os outros três apontam para lá em uma linha. Essa seção **é** o texto de entrega para a outra equipe — mas só mandar **depois da A3**: sem o ID no `.env`, quem recebê-lo bate no `/status` e leva a visão mínima sem entender por quê | eu |
| **A3** | ✅ **CONCLUÍDA 10/09** — código no ar desde as 13:52 e `STATUS_ID` no `.env` desde as ~14:20. Conferido ao vivo: **com** o ID vem `system.hostname` e `api_auth`; **sem** ele, `restrito: true`. Falta só **mandar o texto do README para a outra equipe** | ID e restart dele |
| **A4** | ✅ **CONCLUÍDA 10/09** — V118.58 no repo do OrçaView. O proxy prefere o `OPORTUNIDADE_WBC_STATUS_ID`; **o fallback é de propósito e barulhento** (a ordem dos dois deploys não pode quebrar o card, mas o log denuncia). 4 testes novos — a rota não tinha nenhum. **Nota honesta:** a chave forte fica no `.env` do .90, porque o branch SAP/OS da Mira precisa dela; o ganho é o card de status não portá-la mais. **Falta:** a variável no `.env` do .90 + restart | dele |

**Rollback:** `git revert` + restart. Não haverá flag `STATUS_*_ENABLED` no `.env` — o
que existe é a credencial.

---

## 2. Frente B — o endereço de entrega na Situação dos Pedidos

### 2.1 O problema, no pedido 84348

O SAP guarda **dois** endereços de entrega no mesmo pedido, e eles podem apontar para
cidades diferentes:

| Campo da tela | Origem (RDR12) | No 84348 |
| --- | --- | --- |
| **Ponto de Entrega — ENTREGA** (ShipTo) | colunas `*S` | AV NOSSA SENHORA DO CARMO, 279 — **BELO HORIZONTE-MG** |
| **Local de Entrega** | colunas `*DlvryP` | AVENIDA DEUSDEDITH SALGADO, 4010 — **JUIZ DE FORA-MG** |

A tela de Pedidos marca o segundo card com o selo **"difere do ponto de entrega"**. Quem
despachar pelo ShipTo manda a carga para a cidade errada — 250 km errada, neste pedido.

### 2.2 A regra: **a API resolve, quem consome não escolhe**

**Decisão do Marcelo (10/09):** *"não posso deixar a outra equipe tomar a decisão"*. Então
a resposta não oferece duas opções — ela devolve **o endereço de despacho, já resolvido**:

> **Se houver Local de Entrega preenchido, ele é o endereço da resposta.**
> **Vazio, ou com menos de 3 caracteres, conta como ausente** → responde o padrão (o Ponto
> de Entrega), que é a maioria dos casos.

Preenchido = **rua, cidade ou CEP** com **≥ 3 caracteres** depois do `strip()`. É a mesma
regra do `_entrega_efetiva()` que já roda em produção no OrçaView
(`web_orcaview_V118/backend/services/pedido_report.py`), **com a régua dos 3 caracteres
somada por decisão dele** — ver D8.

### 2.3 O que a B0 mediu no HANA de produção (10/09)

Duas passadas de leitura no `SBOALTAMIRAPROD`, sobre o recorte inteiro da view:

| Medida | Número | O que muda |
| --- | --- | --- |
| Colunas `*DlvryP` e `*S` na RDR12 | **21 de 21 conferem** | A grafia irregular do V118 está certa; nenhum mapa a corrigir |
| Pedido **84348** | DocEntry **19763** · Local = `AVENIDA DEUSDEDITH SALGADO, 4010`, **JUIZ DE FORA** · ShipTo = `AV NOSSA SENHORA DO CARMO, 279`, **BELO HORIZONTE** | O caso de ouro do smoke está confirmado no banco |
| Pedidos no recorte | **266** (não 237 — aquele número era de 24/08) | Volume 12% maior que o estimado |
| Com Local de Entrega | **38 (14,3%)** | — |
| Caem no padrão (ShipTo) | **228 (85,7%)** | O "70%" dele era conservador: são **86%** |
| **Entregam em cidade diferente do ShipTo** | **24 (9,0%)** | **São 24 pedidos hoje em que despachar pelo ShipTo erra a cidade** |
| Linhas com 1–2 caracteres | **0** | ✅ **Fecha a D8** — a régua ≥3 fica só na .11, documentada |
| Pedidos sem Local **e** sem ShipTo | **0 de 266** | `entrega_endereco` nunca vem vazio — dá para prometer isso no contrato |
| `CountyS` preenchido | **266 de 266** (e `CntyDlvryP` em 38 de 38) | O lookup na OCNT sempre resolve; `municipio` praticamente nunca é `null` |

**Dois achados que mudam o código:**

1. **O CEP vem em dois formatos, nas duas colunas.** `ZipDlvryP`: 4 com hífen, 33 sem
   (`'36033000'`). `ZipCodeS`: 147 com hífen, 117 sem. Se a API repassar como veio, quem
   consome recebe `36033000` num pedido e `30330-000` no outro, **no mesmo campo**. →
   **A API normaliza para `NNNNN-NNN`** quando há 8 dígitos; qualquer outra coisa passa
   como veio (não inventar CEP).
2. **A caixa da cidade é inconsistente entre as duas colunas** — `CityDlvryP` =
   `'BELO HORIZONTE'` contra `CityS` = `'Belo Horizonte'` no 84317, `'SANTOS'` contra
   `'Santos'` no 84284. → O flag `difere_do_ponto_de_entrega` **não** pode ser comparação
   de strings de cidade: ele é *"existe Local de Entrega preenchido"*, que é exatamente a
   regra do selo da tela. Dos 38 com Local, **24 mudam de cidade e 14 são outro endereço
   na mesma cidade** — o selo aparece nos 38, e está certo assim.

### 2.4 O que passa a sair

**Perfil `completo`** (hoje 35 campos → 36):

```jsonc
"entrega_endereco": {
  "fonte": "local_entrega",              // ou "ponto_entrega" — informativo
  "difere_do_ponto_de_entrega": true,    // o selo da tela, para desenhar; NÃO para decidir
  "logradouro": "AVENIDA DEUSDEDITH SALGADO",
  "numero": "4010",
  "complemento": null,
  "bairro": "SALVATERRA",
  "cidade": "JUIZ DE FORA",
  "uf": "MG",
  "cep": "36033-000",
  "pais": "BR",
  "municipio": "JUIZ DE FORA",           // nome resolvido na OCNT; null quando não resolve
  "linha": "AVENIDA DEUSDEDITH SALGADO, 4010 - SALVATERRA, 36033-000 JUIZ DE FORA-MG",
  "ponto_entrega": { /* o ShipTo — REFERÊNCIA CADASTRAL, não destino */ }
}
```

**Perfil `resumo`** (o default da lista, 11 campos → 14) — também já resolvido, sem
escolha:

```jsonc
"entrega_linha":     "AVENIDA DEUSDEDITH SALGADO, 4010 - SALVATERRA, 36033-000 JUIZ DE FORA-MG",
"entrega_cidade_uf": "JUIZ DE FORA-MG",
"entrega_difere":    true   // informativo: dá para desenhar o selo. O endereço acima JÁ é o certo
```

O `ponto_entrega` **só existe no `completo`**, e a documentação vai dizer com todas as
letras: *é referência cadastral, não destino de despacho*. Quem estiver no `resumo` não
tem sequer como escolher errado.


### 2.7 O que isto custou no payload (medido em 10/09, 268 pedidos)

| Rota / perfil | Antes | Depois |
| --- | --- | --- |
| `GET /pedidos/situacao` (`resumo`, o default) | ~74 KB | **120 KB** |
| `GET /pedidos/situacao?campos=completo` | ~237 KB | **435 KB** |

**Maior que a estimativa do plano** (+28 KB e +67 KB). São ~0,7 KB por pedido, e quase
tudo é o `ponto_entrega` aninhado — que existe justamente para ninguém perder o dado
cadastral. O `resumo`, que é o default e o caminho recomendado, continua a metade do
`completo`.

Duas consequências registradas: a **B5** precisa decidir o que o `panorama_pedidos` leva
no `campos=completo` (é o que mais sente, porque vira token), e a **B6** tem de dizer
isto na cara de quem consome — o doc já tem a linha *"a resposta está enorme → use
`resumo`"*, e agora ela vale mais.


### 2.8 B7 — smoke em produção (10/09, depois do deploy)

Quatro casos, pelas tools MCP (que injetam a chave), contra a .11 já atualizada:

| Caso | Pedido | Resultado |
| --- | --- | --- |
| Local de Entrega vence | **84348** | `fonte: local_entrega` · `AVENIDA DEUSDEDITH SALGADO, 4010 - SALVATERRA, 36033-000 JUIZ DE FORA-MG` · `ponto_entrega` aninhado em Belo Horizonte |
| Cai no padrão | **84199** | `difere: false` · `AUTAZ MIRIM, 2531 - COROADO, 69082-265 MANAUS-AM` |
| **Cancelado** (D5) | **84282** | Lê a RDR12: `DR RICARDO JAFET, 2419 - IPIRANGA, 04123-030 SAO PAULO-SP`, com `municipio` resolvido na OCNT |
| **Panorama com filtro** (o furo da B5) | `montador=fabiano` | Os 3 campos vieram, e o ShipTo **não** vazou |

A `linha` do 84348 bate **caractere a caractere** com a aba Logística da tela de Pedidos
— que foi de onde este plano saiu.

### 2.5 Fatos que travam o desenho

1. **`situacao_pedidos.py` é PORTE do `situacao_pedidos_service.py` do OrçaView**, e
   `tests/test_situacao_pedidos_diffavel.py` compara os dois função por função. → O
   endereço **não entra nesse arquivo**, nem em `CAMPOS_RESUMO`. Entra na camada de I/O
   (`situacao_pedidos_hana.py`) e na decoração em `api.py` — os dois só existem na .11.
2. **`OCNT` não pode entrar no JOIN.** Existe linha histórica com `County = ''` e o HANA
   avalia a conversão em linhas que o filtro descartaria: a consulta morre com
   *"invalid number"*. O padrão que funciona é o do `fetch_municipios` do V118 — um
   `SELECT ... WHERE AbsId IN (...)` à parte.
3. **A grafia das colunas da RDR12 é irregular:** `StrNoDlvrP` (sem "y"), `BPDelivryP`,
   `BlckDlvryP`. Mapa explícito, **nunca** loop de sufixo — foi a armadilha da
   implementação de 13/08 no OrçaView.
4. **O pedido cancelado responde 200 pelo caminho da ORDR** (`fonte: "ordr"`), e há teste
   comparando as chaves dos dois caminhos. → `entrega_endereco` tem de existir também lá.
5. **O combinado de compatibilidade** (`API_SITUACAO_PEDIDOS.md` §12) permite campo novo
   sem aviso, mas proíbe mudar nome ou tipo de campo existente. → Tudo aqui é aditivo.
7. **A lista de colunas do SELECT também é contrato entre os repos** — e o teste que
   cobra isso (`test_situacao_pedidos_diffavel.py`) estava **pulando calado desde
   08/09**: ele procurava a pasta `web_orcaview_V117`, renomeada para V118 na migração.
   Religado na B1 (a suíte foi de 1616 para 1639 passando, com os *skips* caindo de 29
   para 9) — e, boa notícia, **o núcleo portado não tinha divergido** nesses dois dias.
   As 18 colunas de endereço ficam declaradas como **extensão legítima da .11**
   (`COLUNAS_SO_DA_11`), com um teste contrário para a lista de exceções não virar um
   saco que abafa divergência de verdade.
6. **Volume.** A B0 mediu **266 pedidos** no recorte (o ~237 era de 24/08). Hoje são
   ~74 KB no `resumo` e ~237 KB no `completo`; esta mudança soma ~**+28 KB** e
   ~**+67 KB** — a `linha` pronta pesa mais que um flag, e é o preço de não deixar
   ninguém escolher.

### 2.6 Fases da frente B

| Fase | Entrega | Dono |
| --- | --- | --- |
| **B0** | ✅ **CONCLUÍDA 10/09** — resultados em §2.3. 21/21 colunas conferem · 84348 = DocEntry 19763 (JF contra BH) · 266 pedidos, 86% caem no padrão · **zero** linha com 1–2 caracteres (fecha a D8) · achados novos: CEP em 2 formatos e caixa de cidade inconsistente | eu |
| **B1** | ✅ **CONCLUÍDA 10/09** — 18 colunas de endereço + `LEFT JOIN "RDR12"` por `DocEntry`, com o mapa `ENDERECO_COLS` explícito. **Custo medido no HANA de produção** (3 rodadas alternadas): mediana 142 ms antes, 107 ms depois — **o delta está dentro do ruído** (a 1ª execução paga o plano: 486 ms); com o cache de 120 s, pago no máximo 1×/2 min. Ninguém consome ainda: a regra é a B3 | eu |
| **B2** | ✅ **CONCLUÍDA 10/09** — `_injetar_municipios()`: **uma** consulta `WHERE AbsId IN (...)` para o recorte inteiro, na **mesma conexão** (antes do `finally` que a fecha) e nas **mesmas linhas**, que já têm o cache de 120 s. Sem tabela, sem cache próprio, sem job. Best-effort: OCNT fora não derruba a Situação — as chaves ficam `null`. 7 testes | eu |
| **B3** | ✅ **CONCLUÍDA 10/09** — `endereco_entrega_efetivo()`, com CEP normalizado para `NNNNN-NNN` (D9) e `linha` pronta. 9 testes + **contraprova sobre o recorte real** (268 pedidos): 38 pelo Local, 230 pelo padrão, 24 mudando de cidade — batendo com a B0 —, zero sem endereço, zero sem município e **zero CEP fora do padrão** | eu |
| **B4** | ✅ **CONCLUÍDA 10/09** — objeto no `completo`, 3 campos no `resumo`, decoração em `api.py`, cancelado lendo a RDR12 (D5). 6 testes novos + **ponta a ponta contra o HANA de produção**: 268 pedidos, 268 com endereço, 38 com o selo, zero sem cidade/uf, e o ShipTo **não** vaza para o `resumo` | eu |
| **B5** | ✅ **CONCLUÍDA 10/09** — docstrings das 3 tools + do `verificar_saude` (o `restrito` é falta de credencial, não servidor mudo). **Furo achado e fechado:** o `panorama_pedidos` com filtro busca o `completo` e projetava por nome, perdendo o endereço — a MESMA tool respondia com endereço sem filtro e sem endereço com filtro. `_endereco_resumido()` normaliza as duas formas. 6 testes (só rodam onde há mcp 1.x; a lógica pura foi verificada contra o fonte) | eu |
| **B6** | ✅ **CONCLUÍDA 10/09** — a **7ª armadilha** (2.7), o §6.3 com o dicionário do bloco, o §6.1 de 11→14 campos, o §6.2 de 35→36, o cuidado com IA no §8.4, o contrato no §12 e o sintoma novo no §13. Mais a entrada de CHANGELOG da frente inteira | eu |
| **B7** | ✅ **CONCLUÍDA 10/09** — os quatro casos passaram em produção (§2.8), e a `linha` do 84348 bate caractere a caractere com a tela | deploy dele |

---

## 3. Decisões

| # | Assunto | Estado |
| --- | --- | --- |
| **D1** | Como o `/status` distingue quem vê o quê | ✅ **decidido 10/09** — um `STATUS_ID` próprio, de baixo privilégio, compartilhado com o OrçaView e a outra equipe. Não é flag de funcionalidade (essas seguem sem `*_ENABLED`): é credencial, da mesma família de `OS_API_KEY` e `SIS_MCP_TOKEN` |
| **D2** | A redução mora em `api.py`, não em `monitoring.py` | ✅ decidido — `collect_status()`/`SELECTABLE_CHECKS` são contrato entre repos |
| **D3** | O texto dos `alerts` na visão anônima | ✅ **resolvida pela D1** — quem precisa do texto agora tem o `STATUS_ID`. No anônimo, `alerts` vira **contagem** |
| **D4** | Endereço no perfil `resumo` | ✅ **decidido 10/09** — *"não posso deixar a outra equipe tomar a decisão"*. A API resolve e devolve o endereço de despacho pronto (`entrega_linha`, `entrega_cidade_uf`, `entrega_difere`); vazio ou com menos de 3 caracteres cai no padrão |
| **D5** | `entrega_endereco` no caminho do pedido cancelado | ✅ **decidido 10/09 — lê a RDR12** desse DocEntry (uma linha, caminho raro). A chave nunca vem nula por preguiça |
| **D6** | Incluir o Local de Saída (`*GIP`) e o endereço de cobrança | ✅ **decidido 10/09 — não agora.** Fica registrado para quando alguém pedir |
| **D7** | Restringir 8077 / 8078 / 8079 no firewall a IPs conhecidos | ✅ **decidido 10/09 — agora não.** Fica registrado; o `STATUS_ID` já tira a razão mais urgente de fechar a porta |
| **D8** | A régua dos 3 caracteres diverge do V118 | ✅ **fechada pela B0 (10/09): zero divergência.** Nenhuma das 266 linhas tem 1–2 caracteres nos campos do Local de Entrega, então a régua ≥3 **fica só na .11**, documentada, e o V118 não é tocado. A diferença é teórica hoje — se um dia aparecer, o `_entrega_efetiva()` de lá é que precisa ser revisto |
| **D9** | Formato do CEP na resposta | ✅ **decidido pela medição:** as duas colunas trazem os dois formatos (`ZipDlvryP` 4 com hífen / 33 sem; `ZipCodeS` 147 / 117). A API **normaliza para `NNNNN-NNN`** quando há 8 dígitos e passa o resto como veio |

---

## 4. O que eu preciso do Marcelo

Nada que bloqueie o começo. O que fica com ele:

1. **Gerar o `STATUS_ID`** e gravar no `.env` da .11 (`python -c "import secrets; print(secrets.token_urlsafe(32))"`) — só na fase **A3**, depois que o código estiver pronto.
2. **Restart dos serviços na .11** depois de cada pull (A3, A4 e B7) — o pull eu faço por WinRM.
3. ~~Um retorno na D8~~ — **não precisa mais**: a B0 deu zero, a régua fica só na .11.

---

*Fonte da verdade deste plano: este arquivo. A página publicada espelha o mesmo conteúdo.*
