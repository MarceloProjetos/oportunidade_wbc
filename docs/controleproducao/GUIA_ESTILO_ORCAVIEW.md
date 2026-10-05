> **Documento histórico do pacote ControleProducao (montado em 24/09/2026 na máquina do
> Anderson, dono da aplicação).** Desde 28/09/2026 o código mora em `controleproducao/`
> do ServidorIntegracaoSAP: onde se lê `python -m app.cli …`, hoje é
> `python -m controleproducao …` na raiz do repositório; `python_app/app/` é
> `controleproducao/`; o `.env` é o do SIS (mesmos nomes); `.venv`, `scripts/00-06`,
> `config/log_config.json` e as `wheels/` não existem mais (serviço `OrcaView-ControleProducao`,
> `install_wbc_services.bat`, `deploy_update.bat`). Plano: `docs/PLANO_CONTROLE_PRODUCAO_11.md`.

# Guia de Estilo OrçaView — `status.html`, `Usuarios.html`, `Pedidos.html`

**Para quem é:** equipe de fora que precisa criar telas novas com a mesma cara do OrçaView.

**O que tem aqui:**
1. O alicerce que as três páginas dividem (`shared.css` + Bootstrap + tema).
2. A ficha de estilo de cada uma das três, como está no código hoje.
3. A **ponderação**: o que é padrão da casa e o que é licença de cada página.
4. A **aproximação**: um kit de tokens + CSS pronto para colar, que produz uma tela
   irmã das três sem copiar nenhuma delas.

Fontes lidas: `backend/static/css/shared.css`, `status.css`, `usuarios.css`, `pedidos.css`
e os três HTML em `backend/static/pages/`.

---

## 1. O alicerce comum

### 1.1 Stack

| Item | Valor |
|---|---|
| CSS base | Bootstrap **5.3.8** (`/static/vendor/bootstrap-5.3.8-dist/`) |
| Ícones | Bootstrap Icons **1.13.1** (`<i class="bi bi-...">`) |
| Camada da casa | `/static/css/shared.css` — **carrega depois do Bootstrap, sempre** |
| CSS da página | `/static/css/<pagina>.css` — por último, com `?v=NNN` para cache-busting |
| Fontes | **auto-hospedadas** em `/static/fonts/` e `/static/vendor/`. O app roda em LAN: **nada de CDN de fonte** |

Ordem obrigatória no `<head>` (o `theme-init` vem **antes de qualquer `<link>`**):

```html
<script src="/static/js/theme-init.js"></script>
<link href="/static/vendor/bootstrap-5.3.8-dist/css/bootstrap.min.css" rel="stylesheet">
<link rel="stylesheet" href="/static/vendor/bootstrap-icons-1.13.1/bootstrap-icons.css">
<link rel="stylesheet" href="/static/css/shared.css">
<link rel="stylesheet" href="/static/css/minha-pagina.css?v=1">
```

### 1.2 Tema claro/escuro

- **O escuro é o padrão.** `:root` = escuro; `:root[data-theme="light"]` sobrescreve.
- `theme-init.js` lê `localStorage['orcaview-theme']` e escreve `data-theme` +
  `data-bs-theme` no `<html>` antes do primeiro paint (anti-FOUC). Se não houver nada
  salvo, cai em `dark`.
- Para alternar use a API pronta: `OrcaViewTheme.toggle()` (`/static/js/theme.js`).
  Ela persiste e dispara `CustomEvent('themechange')` no `document` — quem desenha canvas
  ou gráfico escuta esse evento e redesenha.
- **Não invente mecanismo de tema próprio.** Toda cor sai de `var(--token)`; o tema troca
  o valor do token, nunca a regra.

### 1.3 Escala tipográfica (`shared.css`)

Tamanho de fonte **nunca** é literal — é sempre `var(--fs-*)`:

| Token | rem | ≈px | Uso típico |
|---|---|---|---|
| `--fs-2xs` | 0.625 | 10 | badge minúsculo |
| `--fs-xs` | 0.75 | 12 | rótulo em caixa alta, legenda |
| `--fs-sm` | 0.8125 | 13 | cabeçalho de tabela, texto auxiliar |
| `--fs-base` | 0.9375 | 15 | **corpo / célula de tabela** |
| `--fs-md` | 1.0625 | 17 | título de item, valor destacado |
| `--fs-section` | 1.2 | 19 | ícone de botão |
| `--fs-lg` | 1.25 | 20 | ícone, título de modal |
| `--fs-h3` / `--fs-h2` / `--fs-h1` | 1.4 / 1.6 / 1.8 | 22 / 26 / 29 | hierarquia de cabeçalho |
| `--fs-xl` | 1.5 | 24 | número grande em faixa |
| `--fs-2xl` | 1.875 | 30 | **`<h1>` da página** |
| `--fs-3xl` | 2.25 | 36 | ícone do cabeçalho, métrica |
| `--fs-kpi` / `--fs-4xl` | 2.5 / 3 | 40 / 48 | número de KPI, ícone de estado vazio |
| `--fs-icon-lg` / `--fs-icon-xl` | 3.2 / 4 | 51 / 64 | ícone de tela vazia |

### 1.4 Espaço, raio, camada, movimento

```
--space-1:4px  --space-2:8   --space-3:12  --space-4:16  --space-5:20
--space-6:24   --space-8:32  --space-10:40 --space-12:48   (não existem 7, 9 e 11)
--radius-sm/md/lg/pill : 8px · 10px · 12px · 999px
--z-base 1 · --z-raised 10 · --z-dropdown 1050 · --z-sticky 1055 · --z-fixed 1060
--z-overlay 1070 · --z-modal 1080 · --z-popover 1090 · --z-toast 1100
--duration-fast .15s · --duration-normal .25s · --duration-slow .35s
--easing-default ease · --easing-out cubic-bezier(.23,1,.32,1) · --easing-bounce cubic-bezier(.34,1.56,.64,1)
```

### 1.5 Paleta base

**Escuro (`:root`)**

| Token | Valor | Papel |
|---|---|---|
| `--bg-dark` | `#1a1a1a` | fundo da página |
| `--bg-card` | `#2d2d2d` | cartão / painel |
| `--bg-input` | `#3a3a3a` | campo, superfície afundada |
| `--border-subtle` | `#404040` | régua, borda de cartão |
| `--text-white` | `#ffffff` | texto principal |
| `--text-gray` | `#9ca3af` | texto secundário |
| `--text-claude` / `--border-claude` | `#da7756` | **acento da marca** (coral) |
| `--hover-claude` | `#e8956e` | acento clareado (hover / texto sobre escuro) |
| `--brand-strong` / `--brand-stronger` | `#c45a3a` / `#b04e30` | parada de gradiente / borda ativa |
| `--brand-rgb` | `218,119,86` | canais crus, para `rgba(var(--brand-rgb),A)` |

**Claro (`:root[data-theme="light"]`)**

| Token | Valor |
|---|---|
| `--bg-dark` | `#fbf7f3` |
| `--bg-card` | `#ffffff` |
| `--bg-page` | `#dde3ec` |
| `--bg-input` | `#ffffff` |
| `--border-subtle` | `#e2e8f0` |
| `--text-white` | `#1f2937` |
| `--text-gray` | `#6b7280` |
| `--text-claude` | `#c45a3a` |

**Semânticas (valem nos dois temas)**

`--color-success #28a745` · `--color-danger #dc3545` · `--color-warning #ffc107` ·
`--color-info #0dcaf0` · `--whatsapp-green #25D366`

> ⚠️ `--text-white` e `--text-gray` são nomeados pelo **papel**, não pelo tom: no tema
> claro `--text-white` é quase preto. Nunca assuma a cor pelo nome do token.

### 1.6 Componentes que já vêm prontos no `shared.css`

Reuse antes de escrever:

- **Menu lateral (drawer)**: `.menu-hamburger`, `.dropdown-menu-custom`, `.menu-backdrop` —
  injetados por `/static/js/menu.js` com `data-current-page="/minha-rota"`. Não recrie.
- **Botão de tema**: `.theme-toggle-btn` (redondo, `position:fixed`, canto superior direito).
- **Botões em relevo**: `.btn-relief`, e os overrides de `.btn-primary/success/danger/warning/info/outline-secondary`
  (gradiente vertical + sombra + `translateY(-1px)` no hover + afundar no `:active`).
- **Interruptor**: `.orca-toggle` (trilho + knob, acessível por teclado; variantes por variável
  `--ot-trilho-on` etc.).
- **Skeleton de carregamento**: `.skeleton-value`, `.skeleton-text` (shimmer; tire com `.loaded`).
- **Confirmação**: `window.confirmAction()` (`fetchUtils.js`) usa `.ov-confirm`.
- **Calendário**: `.flatpickr-calendar.orca-fp` — 392px, dia de 48px, já na paleta da página.
- **Utilitários**: `.ov-truncate`, `.ov-break-word`, `.ov-table-wrap`, `.ov-table-dark`,
  `.glass-card`, `.section-header-premium`, `.empty-state-centered`, `.alert-claude`.

### 1.7 Acessibilidade — não negociável

- Foco: `outline: 2px solid <acento>; outline-offset: 2px`. O `shared.css` já põe isso em
  `:focus-visible` global; se você zerar o `box-shadow` do Bootstrap num campo, **reponha o anel**.
- `prefers-reduced-motion` já é tratado globalmente: a regra restringe a **lista de
  propriedades** que podem transicionar (transform/width/height saem; cor, fundo, sombra e
  opacidade ficam). Não escreva um bloco próprio zerando tudo.
- Contorno de **controle** (checkbox, input, select) precisa de 3:1 contra o fundo —
  é mais forte que a borda decorativa de cartão.
- Ícone decorativo leva `aria-hidden="true"`; botão só de ícone leva `aria-label` e `title`.

---

## 2. As três páginas, lado a lado

| | **status.html** | **Usuarios.html** | **Pedidos.html** |
|---|---|---|---|
| Papel | painel de saúde / telemetria | CRUD administrativo | documento operacional (SAP) |
| Prefixo de classe | `.sh-*` | `.usuarios-*`, `.rep-*` | `.pedidos-*`, `.detalhe-*` |
| Tokens próprios | `--sh-*` (paleta "Grafite") | `--role-*`, `--linha-tabela` | `--p-*` (paleta da página) |
| Fonte de interface | **Onest** | Inter (herda `shared`) | **IBM Plex Sans** |
| Fonte de número | Space Grotesk + IBM Plex Mono | — (tabular do Inter) | tabular do Plex Sans |
| Acento escuro | `#e0654a` (coral próprio) | `#da7756` (shared) | `#da7756` (aponta p/ shared) |
| Acento claro | `#e0654a` | `#c45a3a` (shared) | **`#0854A0` (azul SAP)** |
| Fundo escuro | `#0b0b0e` (quase preto) | `#1a1a1a` (shared) | `#1a1a1a` (shared) |
| Fundo claro | `#eef1f5` + degradê | `#eef0f3` | `#F1F1F1` |
| Raio de cartão | **16px** | **12px** (resumo 10px) | **14px** |
| Borda de cartão | 1px `rgba(255,255,255,.07)` | **2px** `rgba(255,255,255,.22)` | 1px `#404040` / `#CBD5E1` |
| Escala do conteúdo | `zoom: .9` em `.sh-main` | `zoom: .9` em `.usuarios-shell` | 100% |
| Largura | `container-fluid` (cheia) | `max-width: 1389px`, centrada | cheia, `flex-column`, 100vh |
| Grid | 12 colunas, `gap:16px` | `auto-fit`/`auto-flow column` | `repeat(auto-fit, minmax(380px,1fr))` |
| Abas | não tem | `.nav-tabs` do Bootstrap | abas "pasta" próprias |
| Tamanho de fonte | `var(--fs-*)` | `var(--fs-*)` | `var(--p-fs-*)` **em px** (12.5, 14…) |

**A leitura disso:** as três dividem 100% do alicerce (tema, escala, espaço, menu, botões,
foco). O que cada uma fez de próprio foi **uma paleta de superfície e uma fonte** — nunca
uma estrutura diferente.

---

## 3. Ficha de cada página

### 3.1 `status.html` — painel "Grafite"

**Intenção:** densidade de telemetria. Superfícies chapadas (sem vidro), quase-preto,
acento coral só onde precisa chamar. Três fontes: interface em Onest, números em
Space Grotesk, números dos medidores em IBM Plex Mono.

**Paleta `--sh-*` (escuro → claro)**

```
--sh-page      #0b0b0e → #eef1f5   (claro tem degradê 180deg até #e6eaef, fixed)
--sh-panel     #101014 → #ffffff   painéis (um degrau mais fundo que o card)
--sh-surface   #141419 → #ffffff   cartão
--sh-surface-2 #191a20 → #f6f7f9   item dentro do cartão
--sh-border    rgba(255,255,255,.07) → #e6e9ed
--sh-text      #f3f3f6 → #14161c
--sh-text-2    #a2a2ad → #5a616e
--sh-text-3    #82828f → #5f6875   (rótulo em caixa alta)
--sh-accent    #e0654a nos dois temas  (+ -08 / -14 / -30 de opacidade)
--sh-ok        #6ee79f → #157a41   --sh-warn #d97706 → #b45309
--sh-crit      #dc2626 → #b91c1c   --sh-idle #64748b → #475569
--sh-card-radius 16px · --sh-item-radius 12px
arcos: cpu #a855f7 · ram #4f7cf7 · gpu #e0a422 · disk #2fb6a8
```

**Estrutura**

- `.sh-main { zoom: .9; }` — **o zoom vai no conteúdo, nunca no `body`.** No `body` ele
  encolheria também os `position:fixed` (backdrop do menu, overlay de loading, modais) e
  sobraria faixa sem cobrir a tela. Esse foi um bug real.
- `.sh-topbar`: faixa única — hambúrguer + título + pulso + estado + frase + uptime + ações.
  A frase (`.sh-topbar__sub`) é a única parte elástica: `flex:1`, `nowrap`, `ellipsis`.
- `.sh-grid`: `grid-template-columns: repeat(12,1fr); gap:16px`. Os blocos pedem faixas
  (`span 7` / `span 5` / `span 6`) e caem para `span 12` abaixo de 1100px.
- `.sh-card`: `--sh-surface` + 1px `--sh-border` + raio 16 + `padding:20px 22px` +
  hover `border-color: --sh-accent-30` e `translateY(-2px)`.
- `.sh-card__title`: `--fs-sm`, peso 700, **caixa alta**, `letter-spacing:.08em`, cor `--sh-text-3`,
  ícone em `--sh-accent`.
- `.sh-table`: `th` em caixa alta/`.08em`/`--sh-text-3`; `td` com `padding:16px 14px`;
  `tr:hover` recebe `--sh-accent-08`.
- Estado vira pílula: `padding:5px 14px; border-radius:999px`, fundo e texto do mesmo par
  (`--sh-ok-bg`/`--sh-ok`, etc.), **sem borda**.
- Número sempre `font-variant-numeric: tabular-nums` — senão a coluna dança a cada poll.

**Detalhes que valem copiar:** o pulso animado (`.sh-hero__pulse`, `::before` que expande e
some), o chip "ao vivo/pausado" que é espelho honesto do poller, e o medidor circular
(SVG `viewBox 0 0 180 180` em contêiner de 136px, r=67 → C=421; o JS escreve
`stroke-dasharray: "<v/100*421> 421"`. **Mudar o `r` no CSS obriga a mudar o C no JS.**)

### 3.2 `Usuarios.html` — a régua do CRUD

**Intenção:** é a página que o dono do produto usa como referência de "aparência boa":
fonte maior, mais respiro, bordas nítidas. Não tem paleta própria — consome o `shared.css`
e acrescenta **cor por papel** (grupo de acesso).

**Tokens próprios**

```
--role-admin        #e35d6a      --role-vendas   #4caf7d
--role-fabrica      #c86fb0      --role-rh       #5b9bd5
--role-financeiro   #3bbfad      --role-compras  var(--color-warning)
--role-diretoria    #a78bfa      --role-representante var(--text-claude)
--linha-tabela      rgba(255,255,255,.22) → rgba(0,0,0,.24)
--linha-tabela-w    2px
--bg-input          #333 → #f4f1ed
body (claro)        #eef0f3   ← cinza claro para o cartão branco destacar
```

**Estrutura**

- `.usuarios-shell { max-width:1389px; margin:0 auto; padding: 24px 16px 48px; zoom:.9 }`.
  De novo: **zoom na casca, não no `body`**.
- `.usuarios-topbar`: `padding-left:72px; padding-right:72px` — folga para o hambúrguer
  fixo (esquerda) e o botão de tema (direita), que ficam a 100% enquanto a casca está a 90%.
- Ícone do título em `--fs-3xl` coral; `h1` em `--fs-2xl`; subtítulo em `--fs-sm` cinza.
- **Cards de resumo** (`.usuarios-resumo`): `grid` com `grid-auto-flow: column` e
  `minmax(0,1fr)` — abaixo de 900px vira `repeat(auto-fit, minmax(120px,1fr))`.
  Cada card tem `border-left: 4px solid var(--role-cor)`, número em `--fs-2xl`/700 e
  rótulo em `--fs-xs` caixa alta `.06em`.
  > Regra da casa: faixa de KPI é **`grid` com `auto-fit`**, nunca `flex` + `min-width` —
  > o flex deixa buraco à direita.
- **Badge de papel** (`.role-badge`): pílula 999px, `--fs-xs`/600, cor = `--role-cor`,
  fundo `color-mix(in srgb, var(--role-cor) 16%, transparent)`, borda a 45%.
- **Tabela** (`.usuarios-tabela`): casca com raio 12 e borda de 2px; `thead th` em
  `--fs-xs` caixa alta `.06em` sobre `rgba(0,0,0,.18)`; `td` `padding:11px 16px` em
  `--fs-sm`; linha inteira clicável com `:hover` em `rgba(var(--brand-rgb),.06)` e foco por
  `box-shadow: inset 0 0 0 2px` (`outline` em `<tr>` com `border-collapse` sai torto).
- Avatar circular de 38px com inicial, fundo `color-mix(--role-cor 30%, --bg-input)`.
- **Movimento**: sombra no hover vale em qualquer ponteiro; o `translateY(-2px)` fica dentro
  de `@media (hover:hover) and (pointer:fine)` — no toque o `:hover` gruda depois do tap e o
  card ficaria levantado.

### 3.3 `Pedidos.html` — cromo de documento

**Intenção:** parecer um documento fiscal ao lado do SAP Business One. Redesign completo
em V117.645. É a página mais "sistema" das três: rodapé fixo com totais, abas em forma de
pasta, tabela de itens larga com rolagem horizontal.

**Tokens `--p-*` — definidos no `body`, não no `:root`**

No **escuro** eles *apontam* para o `shared.css` (nenhum hex próprio):

```
--p-bg → --bg-dark · --p-card → --bg-card · --p-surface → --bg-input
--p-border → --border-subtle · --p-text → --text-white · --p-text-2 → --text-gray
--p-accent → --text-claude · --p-accent-text → --hover-claude
```

No **claro** é o cromo frio do SAP (a única linha com hex da página):

```
--p-bg #F1F1F1 · --p-card #FFFFFF · --p-surface #EBEFF5 · --p-border #CBD5E1
--p-text #1F2733 · --p-text-2 #5A6675 · --p-accent #0854A0 · --p-on-accent #FFFFFF
--p-shadow 0 1px 2px rgba(0,0,0,.05), 0 6px 20px rgba(0,0,0,.05)
```

Translúcidos **sem literal de cor**, via `color-mix` sobre o acento:
`--p-a08 / --p-a13 / --p-a20 / --p-a40` = 8% / 13% / 20% / 40%.

> Por que dois tokens de acento: `--p-accent` é **preenchimento e borda**;
> `--p-accent-text` é **glifo** (texto e ícone). O terracota sobre a superfície dá 3,7:1 —
> reprova em texto pequeno; o clareado dá 4,9:1.

**Escala própria, em px** (a página é densa e precisou de meio pixel):

```
--p-fs-flabel 10 · --p-fs-th 10.5 · --p-fs-title 11 · --p-fs-xs 11
--p-fs-label 12.5 · --p-fs-sm 12.5 · --p-fs-base 14 · --p-fs-value 14
--p-fs-md 15 · --p-fs-h1 18 · --p-fs-total 21
```

**Estrutura**

- `body` em **IBM Plex Sans** (woff2 vendorizada em `/static/vendor/ibm-plex-sans/`),
  `accent-color: var(--p-accent)`, `:root{color-scheme:dark}` /
  `:root[data-theme=light]{color-scheme:light}` para a barra de rolagem e o popup de
  `<select>` acompanharem o tema.
- `.pedidos-shell`: `flex-column`, `min-height:100vh`, sem `padding` — tela cheia.
- **Botões** (`.btn-pedidos-*`) — a receita de relevo da casa:
  1. gradiente **vertical de três paradas**: claro em 0%, a cor em 40%, escuro em 100%
     (a parada de 40% é o que dá a "barriga de vidro"; com duas paradas fica chapado);
  2. sólidos **sem borda** — o contorno é a própria sombra colorida `0 3px 8px <cor a 35%>`;
  3. brilho `inset 0 1px 0 rgba(255,255,255,.25)` no topo;
  4. `:hover` sobe 2px, clareia um degrau, sombra abre para `0 5px 15px`;
  5. `:active` desce 1px e afunda (sombra interna, sem glow).
  Os degraus saem de `color-mix` sobre os tokens — o relevo acompanha o tema sozinho.
  Fantasma (`secondary`, `icon`) tem borda visível e sombra neutra. Raio 10px,
  `padding: 9px 18px`; botão de ícone é 42×42.
- **Abas "pasta"** (`.pedidos-tabs .nav-link`): raio `10px 10px 0 0`, `margin-bottom:-1px`,
  ativa com `border-top: 2px solid var(--p-accent)` e `border-bottom-color` igual ao fundo —
  é o que costura a aba na área de conteúdo. Contador em pílula `--p-a20`.
- **Título de seção**: 11px, caixa alta, `.08em`, cor `--p-accent-text`, com um
  **quadradinho 6×6 do acento** via `::before` (os ícones dos `h4` são escondidos).
- **Campo rótulo/valor** (`.detalhe-field`): quando tem `.value`, vira `grid` de
  `var(--field-label-w) minmax(0,1fr)` alinhado pela **baseline**. Cada bloco redefine
  `--field-label-w` (160 no cabeçalho, 150 na grade de info, 130 nos extras).
- **Tabela de itens**: raio 14 na casca, `thead` em `--p-surface` com `th` 10.5px caixa alta;
  zebra por `color-mix(--p-surface 45%, transparent)`; hover em `--p-a08`; célula editável
  ganha lápis `\270E` no hover e `box-shadow: inset 3px 0 0 var(--p-accent)` quando editada.
- **Soma dos itens** é uma **barra no rodapé do card** (`.detalhe-soma-bar`), fora do scroll
  horizontal — a tabela vive num wrapper `.detalhe-items-scroll`. (`tfoot` foi rejeitado.)
- **Rodapé sticky** (`.pedidos-detalhe-footer`): `position:sticky; bottom:0`, fundo `--p-card`,
  sombra para cima `0 -6px 20px`. O total principal em `--p-fs-total` (21px) no acento.
- **Pílulas de status** (`.pedidos-status-badge`): 999px, 11px caixa alta `.05em`, borda de
  1px, com `::before` de 6×6 em `currentColor`. Tokens semânticos:
  `--status-aberto` (acento) · `--status-liberado` (verde) · `--status-fechado` (cinza) ·
  `--status-cancelado` (vermelho) · `--status-os-warn` (âmbar).

---

## 4. Ponderação — o que é padrão e o que é licença

**É padrão da casa (copie sempre):**

1. Escuro é o default; claro é override por `data-theme` no `<html>`.
2. Toda cor e todo tamanho de fonte saem de `var(--token)`.
3. Um **acento por página**, quente/coral, usado com parcimônia: título, ícone, foco,
   borda ativa, pílula. Nunca como fundo de área grande.
4. Escada de superfície: **página < painel < cartão < superfície interna**. Quatro degraus,
   não mais.
5. Cartão = fundo próprio + **borda de 1–2px** + raio 12–16 + sombra suave. Sem vidro.
6. Título de seção = **texto pequeno, caixa alta, `letter-spacing` 0.06–0.08em, cor apagada**.
   O acento entra no ícone ou num quadradinho, não no texto inteiro.
7. Tabela = cabeçalho em caixa alta apagado, régua de 1px, linha com hover translúcido do
   acento, números com `tabular-nums`.
8. Estado = **pílula 999px** com fundo translúcido + texto na mesma matiz.
9. Foco = `outline: 2px solid <acento>; outline-offset: 2px`. Sempre.
10. Translucidez por `color-mix(in srgb, var(--acento) N%, transparent)` — nunca `rgba()`
    com os canais escritos à mão.
11. Escalar a tela com `zoom` no **wrapper de conteúdo**; nunca no `body` (quebra os
    `position:fixed`).
12. `translateY` de hover só em `@media (hover:hover) and (pointer:fine)`.

**É licença da página (escolha uma, não misture):**

| Licença | Quem usa | Quando faz sentido |
|---|---|---|
| Paleta de superfície própria | status (`--sh-*`) | painel de telemetria que precisa de fundo quase preto |
| Fonte de interface própria | status (Onest), Pedidos (Plex Sans) | quando a tela imita outro sistema ou é 100% número |
| Acento diferente no tema claro | Pedidos (azul SAP) | a tela convive lado a lado com o sistema de origem |
| Escala de fonte em px | Pedidos (`--p-fs-*`) | densidade extrema; **caso excepcional** |
| Borda de 2px | Usuários (`--linha-tabela-w`) | tabela longa onde a régua fina some |
| Grid de 12 colunas | status | painel com blocos de larguras diferentes |

**O que ninguém faz** (e você também não deve): vidro/`backdrop-filter` em tela de dados,
sombra colorida em cartão, mais de um acento por tela, animação em carregamento de lista,
fundo de área grande no acento.

---

## 5. A aproximação — kit para a equipe de fora

O que segue é a **média das três**, resolvida: paleta do `shared.css` no escuro, cromo
cinza-azulado no claro (é o que status e Pedidos convergiram), raio e relevo de Pedidos
(é o redesign mais recente), respiro e densidade de Usuários.

Cole em `minha-pagina.css`, depois do `shared.css`.

### 5.1 Tokens

```css
/* ── Paleta da página (escuro = default) ─────────────────────────── */
body{
  --ov-page:#1a1a1a;          /* fundo atrás dos cartões          */
  --ov-panel:#242424;         /* painel (um degrau acima)         */
  --ov-card:#2d2d2d;          /* cartão                           */
  --ov-surface:#3a3a3a;       /* campo / item dentro do cartão    */
  --ov-border:#404040;        /* régua e borda decorativa         */
  --ov-border-strong:#9ca3af; /* contorno de CONTROLE (3:1)       */
  --ov-text:#ffffff;          /* texto principal                  */
  --ov-text-2:#9ca3af;        /* texto secundário                 */
  --ov-accent:#da7756;        /* preenchimento e borda            */
  --ov-accent-text:#e8956e;   /* glifo: texto e ícone             */
  --ov-on-solid:#ffffff;      /* texto sobre preenchimento sólido */
  --ov-shadow:0 4px 10px rgba(0,0,0,.30),0 1px 3px rgba(0,0,0,.22);

  /* translúcidos — SEM literal de cor */
  --ov-a08:color-mix(in srgb,var(--ov-accent) 8%,transparent);
  --ov-a13:color-mix(in srgb,var(--ov-accent) 13%,transparent);
  --ov-a20:color-mix(in srgb,var(--ov-accent) 20%,transparent);
  --ov-a40:color-mix(in srgb,var(--ov-accent) 40%,transparent);

  /* raios */
  --ov-r-card:14px; --ov-r-item:10px; --ov-r-input:9px; --ov-r-pill:999px;
}

/* ── Tema claro — só esta regra muda; o resto do arquivo lê os tokens ── */
:root[data-theme="light"] body{
  --ov-page:#EEF1F5; --ov-panel:#FFFFFF; --ov-card:#FFFFFF; --ov-surface:#EBEFF5;
  --ov-border:#CBD5E1; --ov-border-strong:#64748B;
  --ov-text:#1F2733; --ov-text-2:#5A6675;
  --ov-accent:#c45a3a; --ov-accent-text:var(--ov-accent);
  --ov-shadow:0 1px 2px rgba(0,0,0,.05),0 6px 20px rgba(0,0,0,.05);
}

/* Nativos (rolagem, popup de <select>, calendário do SO) seguem o tema */
:root{color-scheme:dark}
:root[data-theme="light"]{color-scheme:light}

body{background:var(--ov-page);color:var(--ov-text);
     font-family:'Inter','Segoe UI',system-ui,-apple-system,sans-serif}
```

### 5.2 Casca e cabeçalho

```css
/* zoom NA CASCA, jamais no body: no body ele encolhe os position:fixed */
.ov-shell{max-width:1400px;margin:0 auto;
          padding:var(--space-6) var(--space-4) var(--space-12);zoom:.9}

/* 72px de folga: hambúrguer (esq.) e botão de tema (dir.) ficam a 100% */
.ov-topbar{display:flex;align-items:center;gap:var(--space-4);
           margin-bottom:var(--space-6);padding-left:72px;padding-right:72px}
.ov-topbar > i{font-size:var(--fs-3xl);color:var(--ov-accent-text)}
.ov-topbar h1{font-size:var(--fs-2xl);margin:0;color:var(--ov-text)}
.ov-topbar p{font-size:var(--fs-sm);color:var(--ov-text-2);margin:0}
.ov-topbar-actions{margin-left:auto;display:flex;gap:var(--space-2)}
```

### 5.3 Faixa de KPI e cartão

```css
/* grid + auto-fit — NUNCA flex+min-width (deixa buraco à direita) */
.ov-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
         gap:var(--space-3);margin-bottom:var(--space-6)}
.ov-kpi{background:var(--ov-card);border:1px solid var(--ov-border);
        border-left:4px solid var(--ov-cor,var(--ov-accent));
        border-radius:var(--ov-r-item);padding:var(--space-3) var(--space-4)}
.ov-kpi b{display:block;font-size:var(--fs-2xl);font-weight:700;
          color:var(--ov-text);line-height:1.1;font-variant-numeric:tabular-nums}
.ov-kpi span{font-size:var(--fs-xs);text-transform:uppercase;
             letter-spacing:.06em;color:var(--ov-text-2)}

.ov-card{background:var(--ov-card);border:1px solid var(--ov-border);
         border-radius:var(--ov-r-card);padding:20px 22px;box-shadow:var(--ov-shadow)}
.ov-card--painel{background:var(--ov-panel)}

/* Título de seção: caps pequeno + quadradinho do acento */
.ov-card__title{display:flex;align-items:center;gap:8px;margin:0 0 12px;
  font-size:var(--fs-sm);font-weight:700;text-transform:uppercase;
  letter-spacing:.08em;color:var(--ov-text-2)}
.ov-card__title::before{content:'';width:6px;height:6px;
  background:var(--ov-accent);flex-shrink:0}
```

### 5.4 Tabela

```css
.ov-table-card{background:var(--ov-card);border:1px solid var(--ov-border);
               border-radius:var(--ov-r-card);overflow:hidden}
.ov-table-card > .ov-scroll{overflow:auto}     /* a rolagem é INTERNA ao card */
.ov-table{width:100%;border-collapse:collapse;font-size:var(--fs-base)}
.ov-table thead th{background:var(--ov-surface);padding:10px 14px;text-align:left;
  font-size:var(--fs-xs);font-weight:700;text-transform:uppercase;letter-spacing:.07em;
  color:var(--ov-text-2);border-bottom:1px solid var(--ov-border);white-space:nowrap}
.ov-table tbody td{padding:11px 14px;color:var(--ov-text);
                   border-bottom:1px solid var(--ov-border)}
.ov-table tbody tr:last-child td{border-bottom:none}
.ov-table tbody tr:nth-child(even){background:color-mix(in srgb,var(--ov-surface) 45%,transparent)}
.ov-table tbody tr:hover{background:var(--ov-a08)}
.ov-table .num{text-align:right;font-variant-numeric:tabular-nums}
/* linha focável: outline em <tr> com border-collapse sai torto */
.ov-table tbody tr:focus-visible{outline:none;box-shadow:inset 0 0 0 2px var(--ov-accent)}
```

### 5.5 Pílula de estado

```css
.ov-pill{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;
  border-radius:var(--ov-r-pill);font-size:var(--fs-xs);font-weight:600;
  text-transform:uppercase;letter-spacing:.05em;white-space:nowrap;
  border:1px solid;color:var(--ov-text-2);
  background:color-mix(in srgb,currentColor 14%,transparent);
  border-color:color-mix(in srgb,currentColor 40%,transparent)}
.ov-pill::before{content:'';width:6px;height:6px;border-radius:50%;background:currentColor}
.ov-pill.is-ok{color:var(--color-success)}
.ov-pill.is-warn{color:var(--color-warning)}
.ov-pill.is-erro{color:var(--color-danger)}
.ov-pill.is-aberto{color:var(--ov-accent-text)}
```

### 5.6 Botões (relevo da casa)

```css
.ov-btn,.ov-btn-ghost{display:inline-flex;align-items:center;justify-content:center;gap:8px;
  padding:9px 18px;border-radius:var(--ov-r-item);font-family:inherit;
  font-size:var(--fs-base);font-weight:600;letter-spacing:.3px;cursor:pointer;
  border:1px solid transparent;transition:var(--relief-transition);white-space:nowrap}

/* sólido: sem borda, gradiente de TRÊS paradas (0 / 40% / 100%), glow da própria cor */
.ov-btn{border:none;color:var(--ov-on-solid);text-shadow:0 1px 2px rgba(0,0,0,.30);
  background:linear-gradient(180deg,
    color-mix(in srgb,var(--ov-accent) 76%,white) 0%,
    var(--ov-accent) 40%,
    color-mix(in srgb,var(--ov-accent) 80%,black) 100%);
  box-shadow:0 3px 8px color-mix(in srgb,var(--ov-accent) 35%,transparent),
             inset 0 1px 0 rgba(255,255,255,.25)}
.ov-btn:hover{transform:translateY(-2px);
  box-shadow:0 5px 15px color-mix(in srgb,var(--ov-accent) 45%,transparent),
             inset 0 1px 0 rgba(255,255,255,.30)}

/* fantasma: borda visível, sombra neutra, sem glow */
.ov-btn-ghost{color:var(--ov-text);border-color:var(--ov-border);
  background:linear-gradient(180deg,
    color-mix(in srgb,var(--ov-card) 92%,white) 0%,var(--ov-card) 40%,var(--ov-surface) 100%);
  box-shadow:0 2px 5px rgba(0,0,0,.20),inset 0 1px 0 rgba(255,255,255,.08)}
.ov-btn-ghost:hover{transform:translateY(-2px);color:var(--ov-accent-text);
  border-color:var(--ov-accent)}

.ov-btn:active,.ov-btn-ghost:active{transform:translateY(1px);
  box-shadow:0 1px 2px rgba(0,0,0,.30),inset 0 1px 3px rgba(0,0,0,.20)}
.ov-btn:focus-visible,.ov-btn-ghost:focus-visible{
  outline:2px solid var(--ov-accent-text);outline-offset:2px}
.ov-btn:disabled,.ov-btn-ghost:disabled{opacity:.55;cursor:not-allowed;
  transform:none;box-shadow:none;text-shadow:none}
```

### 5.7 Campos

```css
.ov-field{display:flex;flex-direction:column;gap:4px;min-width:0}
.ov-field label{font-size:var(--fs-xs);text-transform:uppercase;letter-spacing:.05em;
                color:var(--ov-text-2)}
.ov-input,.ov-select,.ov-textarea{width:100%;padding:9px 12px;font-family:inherit;
  font-size:var(--fs-base);background:var(--ov-surface);color:var(--ov-text);
  border:1px solid var(--ov-border-strong);border-radius:var(--ov-r-input);
  transition:border-color .15s ease,box-shadow .15s ease}
.ov-input::placeholder,.ov-textarea::placeholder{color:var(--ov-text-2)}
.ov-input:focus,.ov-select:focus,.ov-textarea:focus{outline:none;
  border-color:var(--ov-accent);box-shadow:0 0 0 3px var(--ov-a20)}
.ov-textarea{min-height:84px;resize:vertical;line-height:1.5}
```

### 5.8 Movimento

```css
/* sombra vale em qualquer ponteiro; o deslocamento, só com mouse */
.ov-card--click{cursor:pointer;
  transition:transform var(--duration-fast) var(--easing-out),
             border-color var(--duration-fast) var(--easing-out),
             box-shadow var(--duration-fast) var(--easing-out)}
.ov-card--click:hover{box-shadow:0 4px 14px rgba(0,0,0,.35);border-color:var(--ov-accent)}
.ov-card--click:active{transform:scale(.99);transition-duration:.1s}
@media (hover:hover) and (pointer:fine){
  .ov-card--click:hover{transform:translateY(-2px)}
  .ov-card--click:active{transform:translateY(-1px) scale(.99);transition-duration:.1s}
}
@media (prefers-reduced-motion:reduce){
  .ov-card--click:hover,.ov-card--click:active{transform:none}
}
```

### 5.9 Esqueleto de página

```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Minha Página — OrçaView</title>
  <script src="/static/js/theme-init.js"></script>
  <link href="/static/vendor/bootstrap-5.3.8-dist/css/bootstrap.min.css" rel="stylesheet">
  <link rel="stylesheet" href="/static/vendor/bootstrap-icons-1.13.1/bootstrap-icons.css">
  <link rel="stylesheet" href="/static/css/shared.css">
  <link rel="stylesheet" href="/static/css/minha-pagina.css?v=1">
  <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
</head>
<body>
  <button id="btnToggleTheme" class="theme-toggle-btn" type="button"
          data-ia-click="OrcaViewTheme.toggle"
          aria-label="Alternar tema claro/escuro" title="Alternar tema (claro/escuro)">
    <i class="bi bi-sun-fill icon-go-light" aria-hidden="true"></i>
    <i class="bi bi-moon-stars-fill icon-go-dark" aria-hidden="true"></i>
  </button>

  <script src="/static/js/fetchUtils.js"></script>
  <script src="/static/js/menu.js?v=116" data-current-page="/minha-rota"></script>

  <main id="mainContent" class="ov-shell">
    <header class="ov-topbar">
      <i class="bi bi-box-seam" aria-hidden="true"></i>
      <div>
        <h1>Minha Página</h1>
        <p>Uma linha dizendo para que serve</p>
      </div>
      <div class="ov-topbar-actions">
        <button type="button" class="ov-btn-ghost"><i class="bi bi-download"></i> Exportar</button>
        <button type="button" class="ov-btn"><i class="bi bi-plus-lg"></i> Novo</button>
      </div>
    </header>

    <section class="ov-kpis"><!-- .ov-kpi --></section>

    <section class="ov-table-card">
      <div class="ov-scroll">
        <table class="ov-table"><!-- thead / tbody --></table>
      </div>
    </section>
  </main>

  <script src="/static/vendor/bootstrap-5.3.8-dist/js/bootstrap.bundle.min.js"></script>
  <script src="/static/js/theme.js"></script>
  <script src="/static/js/minha-pagina.js?v=1"></script>
</body>
</html>
```

---

## 6. Regras que quebram o build (se o CSS entrar neste repositório)

`tools/dev/check_css_tokens.py` é uma **catraca**: a contagem de cada arquivo só pode cair.
Três regras, aplicadas linha a linha em `backend/static/css/*.css`:

| Regra | O que pega | Saída |
|---|---|---|
| `font-size-literal` | `font-size:` com px/rem literal | use `var(--fs-*)` (a regra aceita **qualquer** `var(...)`) |
| `brand-color-literal` | `#da7756`, `#c45a3a`, `#e8956e` escritos à mão | `var(--border-claude)` / `var(--text-claude)` / `var(--hover-claude)` |
| `hex-color-literal` | qualquer hex | crie um token semântico |

Exceção sancionada: comentário `css-token-ok: <regra>` na linha ou até **2 linhas acima**,
com a justificativa. É o que permite as 1–2 linhas de **definição** da paleta da página —
e só elas.

Outras convenções do repositório:

- CSS **minificado à mão**, uma regra por linha; comentário em bloco explicando *por quê*,
  não *o quê*.
- HTML e JS levam `?v=NNN` quando mudam (o `.90` não manda `Cache-Control` em HTML e o
  Chrome serve página velha).
- `data-testid` nos elementos que os testes e os agentes precisam achar
  (ver `docs/AGENT_SELECTORS.md`).

---

## 7. Checklist de revisão

- [ ] Funciona nos **dois temas** — trocou com `OrcaViewTheme.toggle()` e olhou?
- [ ] Nenhum hex e nenhum `font-size` literal fora do bloco de paleta.
- [ ] Um único acento na tela.
- [ ] Todo número com `font-variant-numeric: tabular-nums`.
- [ ] Todo elemento interativo tem `:focus-visible` com anel de 2px.
- [ ] Faixa de KPI é `grid` + `auto-fit` (sem buraco à direita em tela larga).
- [ ] `zoom` (se houver) está no wrapper de conteúdo, não no `body`.
- [ ] `translateY` de hover dentro de `@media (hover:hover) and (pointer:fine)`.
- [ ] Rolagem horizontal de tabela é **interna ao cartão**; o rodapé de total fica fora dela.
- [ ] Testado a 1366px e a 1920px, e abaixo de 900px sem rolagem horizontal da página.
