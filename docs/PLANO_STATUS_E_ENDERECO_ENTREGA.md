# Plano — Fechar o `/status`, abrir o endereço de entrega

> **Status: nada implementado.** Plano escrito em 2026-09-10 a partir de duas medições
> reais feitas hoje (o payload do `/status` da .11 puxado sem chave nenhuma, e o pedido
> 84348 na tela de Pedidos). Nenhuma linha de código escrita ainda.

Duas frentes independentes na API 8077 da `192.168.7.11`, que podem subir no mesmo deploy
ou em deploys separados:

| Frente | O quê | Rotas |
| --- | --- | --- |
| **A** | O `/status` deixa de publicar o mapa da infraestrutura para quem não tem chave | `GET /status` |
| **B** | A Situação dos Pedidos passa a dizer **para onde a mercadoria vai** | `GET /pedidos/situacao` · `GET /pedidos/{n}/situacao` |

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

| Chamador | Como chama | Manda chave? | O que precisa de volta |
| --- | --- | --- | --- |
| `mira-watchdog.js` (.90) | `/status?checks=worker&strict=1` | **não** | Só o **código HTTP** (200 vs 503) |
| Modal de status do OrçaView (.90) | `/api/oportunidade-wbc/status` → proxy | **sim** (`OPORTUNIDADE_WBC_API_KEY`) | O corpo inteiro, para desenhar o card |
| Tool MCP `verificar_saude` (8078) | via API 8077 | **sim** (injetada server-side) | O corpo inteiro |
| `host_breaker.py` (.90) | TCP na 8077 | — | Só o socket |
| Marcelo, no navegador | `/status` | não | Diagnóstico legível |

> **Ressalva honesta:** a API não escreve access log por requisição, então este mapa vem
> da leitura do código dos três repos, não de um log de quem realmente chamou. Se existir
> um monitor de terceiros apontado para o `/status`, ele não aparece aqui — e passaria a
> ver a visão reduzida.

### 1.3 O desenho

**Sem chave → visão reduzida. Com chave → exatamente o que sai hoje.**

O `?strict=1` e os códigos HTTP são calculados **antes** da redução, então continuam
idênticos: o watchdog do .90 não muda uma linha.

| Campo | Sem chave | Com chave |
| --- | --- | --- |
| `ok`, `healthy`, `service`, `timestamp`, `uptime_s` | ✅ | ✅ |
| `alerts[]` | ✅ (texto do disco sem os GB — ver D3) | ✅ |
| `checks.<nome>.ok` e `.ms` | ✅ | ✅ |
| `checks.<nome>.detail` | ❌ removido | ✅ |
| `checks.<nome>.error` | vira `erro: true` | ✅ texto |
| `scheduler`, `scheduled_task` | ✅ | ✅ |
| `wbc_worker` | ✅ **menos** `db` (caminho) | ✅ |
| `system` | só `disk_low`, `disk_percent`, `cpu_percent`, `mem_percent` | ✅ |
| `system.hostname`/`ip`/`os`/`python`/`*_gb`/`mem_total_mb` | ❌ removidos | ✅ |
| `windows_update` | ❌ bloco inteiro fora | ✅ |
| `api_auth` | ❌ fora | ✅ |
| `restrito: true` | ✅ (novo — diz que a visão é reduzida) | ausente |

### 1.4 Onde mexe

- **`api.py`** — uma função `_status_publico(data)` aplicada na rota quando
  `_autorizado()` é falso. Não é decorator `@requer_chave`: a rota continua aberta.
- **`monitoring.py` NÃO muda.** `collect_status()` e `SELECTABLE_CHECKS` são contrato
  entre repos (o card do .90 e a tool MCP leem os blocos por nome) — a redução é uma
  camada de apresentação, não de coleta.
- **`tests/test_api.py`** — sem chave vem reduzido; com chave vem completo; `?strict=1`
  devolve o mesmo código nos dois; `?checks=worker&strict=1` (o do watchdog) inalterado.

### 1.5 Fases da frente A

| Fase | Entrega | Dono |
| --- | --- | --- |
| **A0** | Inventário dos chamadores — ✅ feito nesta sessão (§1.2) | eu |
| **A1** | `_status_publico()` + testes | eu |
| **A2** | Docs: `API_OS_INTEGRACAO.md` §3.4, `API_ORDENS_PRODUCAO.md` §9, `README.md`, `CLAUDE.md` (a linha que hoje diz "`/status` = diagnóstico profundo aberto") e CHANGELOG | eu |
| **A3** | Deploy + smoke: `curl` sem chave (reduzido), com chave (completo), watchdog do .90 verde | pull meu, restart dele |

**Rollback:** `git revert` + restart. **Não** haverá flag `STATUS_*` no `.env` — função de
produção aqui liga por constante, não por chave de ambiente.

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

**A regra já existe e já está em produção no OrçaView**, em
`web_orcaview_V118/backend/services/pedido_report.py`, função `_entrega_efetiva()`:

> Preenchido = tem **rua, cidade ou CEP**. Quando o Local de Entrega está preenchido, ele
> **vence** o ShipTo. Quando os dois concordam, tanto faz — a saída é a mesma.

Este plano **porta essa regra**, não inventa outra. É o mesmo teste que o PDF do pedido e
a tela já usam.

### 2.2 O que passa a sair

**Perfil `completo`** (hoje 35 campos → 36) ganha um objeto:

```jsonc
"entrega_endereco": {
  "fonte": "local_entrega",              // ou "ponto_entrega"
  "difere_do_ponto_de_entrega": true,    // o selo da tela, pronto
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
  "ponto_entrega": {                     // o ShipTo, SEMPRE, mesmo quando perdeu
    "logradouro": "AV NOSSA SENHORA DO CARMO",
    "numero": "279",
    "bairro": "CARMO",
    "cidade": "BELO HORIZONTE",
    "uf": "MG",
    "cep": "30330-000",
    "pais": "BR",
    "linha": "AV NOSSA SENHORA DO CARMO, 279 - CARMO, 30330-000 BELO HORIZONTE-MG"
  }
}
```

**A propriedade de segurança:** os campos do topo são **sempre o endereço efetivo**. Um
consumidor que ignore `fonte`, ignore o selo e leia só `cidade`/`uf`/`linha` **ainda
despacha certo**. Quem quiser mostrar os dois tem `ponto_entrega` aninhado.

**Perfil `resumo`** (o default da lista, 11 campos → 13) ganha só dois escalares baratos:

- `entrega_cidade_uf` — `"JUIZ DE FORA-MG"`
- `entrega_difere` — `true`

Assim quem usa a lista no default **vê que existe divergência** sem carregar o objeto
inteiro em 237 pedidos.

### 2.3 Fatos que travam o desenho

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
   `completo`. Estimativa desta mudança: **+9 KB** no `resumo` e **+60 KB** no `completo`.

### 2.4 Fases da frente B

| Fase | Entrega | Dono |
| --- | --- | --- |
| **B0** | Conferir a RDR12 do 84348 no HANA: as 12 colunas `*DlvryP` e as 9 `*S`; ver se `CntyDlvryP` vem preenchido e se é código. **É o caso de ouro do smoke** — um pedido real com o selo | eu |
| **B1** | `STATUS_PEDIDO_COLS` + `LEFT JOIN "RDR12" a ON a."DocEntry" = v."DocEntry"` (LEFT, nunca INNER — pedido sem RDR12 não pode sumir do recorte). Medir o custo da consulta antes e depois | eu |
| **B2** | Município: coleta dos `AbsId` do recorte + um `SELECT` na OCNT, cache no mesmo TTL de 120 s. Fora do JOIN (§2.3.2) | eu |
| **B3** | `endereco_entrega_efetivo()` em `situacao_pedidos_hana.py` — porte literal do `_entrega_efetiva()` do V118, com teste do caso "os dois preenchidos e diferentes", "só ShipTo", "nenhum dos dois" | eu |
| **B4** | Publicar: objeto no `completo`, os 2 escalares no `resumo`, decoração em `api.py`. **Paridade de chaves no caminho do cancelado** | eu |
| **B5** | MCP: `situacao_pedido` já vem completo por default; `panorama_pedidos` ganha os 2 escalares no resumo. Docstrings das tools avisando a regra do "difere" — senão o modelo lê o ShipTo e responde a cidade errada | eu |
| **B6** | Docs da outra equipe: `API_SITUACAO_PEDIDOS.md` ganha a **7ª armadilha** ("o endereço do topo é o efetivo; o ShipTo pode não ser para onde vai") + §6.3 com o exemplo do 84348. CHANGELOG | eu |
| **B7** | Smoke real: 84348 (deve vir Juiz de Fora, `difere=true`), um pedido sem Local de Entrega (ShipTo, `difere=false`), um cancelado (chave presente), e conferência contra a tela do .90 | pull meu, restart dele |

---

## 3. Decisões

| # | Assunto | Estado |
| --- | --- | --- |
| **D1** | A visão reduzida do `/status` é **constante**, sem flag no `.env` | ✅ decidido — regra do Marcelo: função de produção não ganha `*_ENABLED`. Rollback é `git revert` + restart |
| **D2** | A redução mora em `api.py`, não em `monitoring.py` | ✅ decidido — `collect_status()`/`SELECTABLE_CHECKS` são contrato entre repos |
| **D3** | O texto dos `alerts` na visão anônima | **Recomendado:** manter os textos, tirando só os GB do alerta de disco ("disco baixo" sem "36,6 GB livres de 126,4"). Alerta é operação, não topologia |
| **D4** | Endereço no perfil `resumo` da lista | **Recomendado:** os 2 escalares (`entrega_cidade_uf`, `entrega_difere`). Objeto inteiro no default custaria ~4× o payload da lista; não colocar nada esconderia a divergência de quem usa o default |
| **D5** | `entrega_endereco` no caminho do pedido cancelado | **Recomendado:** ler a RDR12 desse DocEntry (uma linha, caminho raro) em vez de emitir a chave com nulos — cancelado também tem endereço, e a paridade de chaves é testada |
| **D6** | Incluir também o **Local de Saída de Mercadoria** (`*GIP`) e o endereço de cobrança | **Recomendado: não agora.** Não foi pedido e quem consome esta API despacha para o destino. Fica registrado para quando alguém pedir |
| **D7** | Restringir 8077/8078/8079 no firewall a IPs conhecidos | **Aberta, depende dele** — precisa da lista de máquinas da outra equipe |

---

## 4. O que eu preciso do Marcelo

1. **Restart dos serviços na .11** depois de cada pull (A3 e B7) — o pull eu faço por WinRM.
2. **D3, D4, D5, D6** — um "ok" ou uma correção nas recomendações acima.
3. **D7** — se quiser fechar o firewall, a lista de IPs da outra equipe.

---

*Fonte da verdade deste plano: este arquivo. A página publicada espelha o mesmo conteúdo.*
