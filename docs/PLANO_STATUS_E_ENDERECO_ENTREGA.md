# Plano — Fechar o `/status`, abrir o endereço de entrega

> **Status: nada implementado.** Plano escrito em 2026-09-10 a partir de duas medições
> reais feitas no mesmo dia (o payload do `/status` da .11 puxado sem chave nenhuma, e o
> pedido 84348 na tela de Pedidos). Revisado às 2026-09-10 com as decisões do Marcelo
> (D1, D3, D4, D6 fechadas). Nenhuma linha de código escrita ainda.

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
3. **Sem `STATUS_ID` no `.env`, só a `OS_API_KEY` abre o completo** — e o anônimo segue
   na visão mínima. Fail-closed na credencial nova, ao contrário da `OS_API_KEY`, que
   ainda cai aberta por desenho antigo.

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

### 1.6 Fases da frente A

| Fase | Entrega | Dono |
| --- | --- | --- |
| **A0** | Inventário dos chamadores — ✅ feito em 10/09 (§1.2) | eu |
| **A1** | `STATUS_ID` + `_status_publico()` + os testes das 3 invariantes | eu |
| **A2** | Docs: `API_OS_INTEGRACAO.md` §3.4, `API_ORDENS_PRODUCAO.md` §9, `README.md`, `CLAUDE.md`, `.env.example`, CHANGELOG — **e o texto de entrega do ID para a outra equipe** (o que ele abre, o que ele **não** abre) | eu |
| **A3** | Marcelo gera o ID e grava no `.env` da .11 · deploy · smoke: `curl` anônimo (mínimo), com o ID (completo), watchdog do .90 verde | ID e restart dele, pull e smoke meus |
| **A4** | OrçaView: o proxy do modal passa a mandar o `STATUS_ID` em vez da chave forte — 1 linha no `.env` do .90 + 1 em `admin_integrations_routes.py`. **Nota honesta:** o .90 continua precisando da `OS_API_KEY` para o sync da Mira (`assistente_sap_routes.py`); o ganho aqui é o modal de status deixar de carregar a chave que escreve no SAP | eu |

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

### 2.3 O que passa a sair

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

### 2.4 Fatos que travam o desenho

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
6. **Volume.** Recorte medido em 24/08: ~237 pedidos, ~74 KB no `resumo` e ~237 KB no
   `completo`. Estimativa desta mudança: **+25 KB** no `resumo` (a `linha` pronta pesa
   mais que um flag) e **+60 KB** no `completo`.

### 2.5 Fases da frente B

| Fase | Entrega | Dono |
| --- | --- | --- |
| **B0** | Medir no HANA, no recorte inteiro: (a) as 12 colunas `*DlvryP` e as 9 `*S` do **84348**; (b) **quantos dos ~237 pedidos têm Local de Entrega preenchido** — confere o "70% no padrão" que ele estimou; (c) **quantas linhas têm 1–2 caracteres** nesses campos, que é o que decide a D8 | eu |
| **B1** | `STATUS_PEDIDO_COLS` + `LEFT JOIN "RDR12" a ON a."DocEntry" = v."DocEntry"` (LEFT, nunca INNER — pedido sem RDR12 não pode sumir do recorte). Medir o custo da consulta antes e depois | eu |
| **B2** | Município: coleta dos `AbsId` do recorte + um `SELECT` na OCNT, cache no mesmo TTL de 120 s. Fora do JOIN (§2.4.2) | eu |
| **B3** | `endereco_entrega_efetivo()` em `situacao_pedidos_hana.py` — a regra da §2.2, com teste dos quatro casos: os dois preenchidos e diferentes · só ShipTo · nenhum dos dois · **Local de Entrega com 1–2 caracteres** (cai no padrão) | eu |
| **B4** | Publicar: objeto no `completo`, os 3 campos resolvidos no `resumo`, decoração em `api.py`. **Paridade de chaves no caminho do cancelado** | eu |
| **B5** | MCP: `situacao_pedido` já é `completo` por default; `panorama_pedidos` ganha `entrega_cidade_uf` + `entrega_difere` no resumo. Docstrings das tools dizendo que **o endereço da resposta já é o de despacho** — senão o modelo procura o ShipTo e responde a cidade errada | eu |
| **B6** | Docs da outra equipe: `API_SITUACAO_PEDIDOS.md` ganha a **7ª armadilha** ("o endereço da resposta já é o de despacho; `ponto_entrega` é cadastro, não destino") + §6.3 com o exemplo do 84348. CHANGELOG | eu |
| **B7** | Smoke real: 84348 (deve vir Juiz de Fora, `difere=true`) · um pedido sem Local de Entrega (vem o padrão, `difere=false`) · um cancelado (chave presente) · conferência contra a tela do .90 | pull meu, restart dele |

---

## 3. Decisões

| # | Assunto | Estado |
| --- | --- | --- |
| **D1** | Como o `/status` distingue quem vê o quê | ✅ **decidido 10/09** — um `STATUS_ID` próprio, de baixo privilégio, compartilhado com o OrçaView e a outra equipe. Não é flag de funcionalidade (essas seguem sem `*_ENABLED`): é credencial, da mesma família de `OS_API_KEY` e `SIS_MCP_TOKEN` |
| **D2** | A redução mora em `api.py`, não em `monitoring.py` | ✅ decidido — `collect_status()`/`SELECTABLE_CHECKS` são contrato entre repos |
| **D3** | O texto dos `alerts` na visão anônima | ✅ **resolvida pela D1** — quem precisa do texto agora tem o `STATUS_ID`. No anônimo, `alerts` vira **contagem** |
| **D4** | Endereço no perfil `resumo` | ✅ **decidido 10/09** — *"não posso deixar a outra equipe tomar a decisão"*. A API resolve e devolve o endereço de despacho pronto (`entrega_linha`, `entrega_cidade_uf`, `entrega_difere`); vazio ou com menos de 3 caracteres cai no padrão |
| **D5** | `entrega_endereco` no caminho do pedido cancelado | **Aberta.** Recomendado: ler a RDR12 desse DocEntry (uma linha, caminho raro) em vez de emitir a chave com nulos — cancelado também tem endereço, e a paridade de chaves é testada |
| **D6** | Incluir o Local de Saída (`*GIP`) e o endereço de cobrança | ✅ **decidido 10/09 — não agora.** Fica registrado para quando alguém pedir |
| **D7** | Restringir 8077 / 8078 / 8079 no firewall a IPs conhecidos | **Aberta, depende dele** — precisa da lista de máquinas da outra equipe |
| **D8** | A régua dos 3 caracteres diverge do V118 | **Aberta, decide com o número da B0.** O `_entrega_efetiva()` do OrçaView aceita qualquer texto não-branco; a régua dele é ≥3. Se a B0 achar **zero** linha com 1–2 caracteres, aplico só na .11 e documento a diferença. Se achar alguma, **porto a régua para o V118** — senão a tela, o PDF e a API discordam sobre o mesmo pedido |

---

## 4. O que eu preciso do Marcelo

1. **Gerar o `STATUS_ID`** e gravar no `.env` da .11 (`python -c "import secrets; print(secrets.token_urlsafe(32))"`) — fase A3.
2. **Restart dos serviços na .11** depois de cada pull (A3, A4 e B7) — o pull eu faço por WinRM.
3. **D5** — pedido cancelado lê a RDR12, ou vem com a chave nula?
4. **D7** — se quiser fechar o firewall, a lista de IPs da outra equipe.
5. **D8** — só depois da medição da B0; eu volto com o número.

---

*Fonte da verdade deste plano: este arquivo. A página publicada espelha o mesmo conteúdo.*
