"""The shared shell of the .11 screens — "Central Integração SAP" (PLANO_CASA_COMUM_11).

Three processes serve the operator's screens: the painel WBC (8079), the Controle de
Produção (8080) and the Painel de Sincronização (8077). They stay separate on purpose (an
``hdbcli`` crash or a heavy task must not take the others down), but the operator must not
feel the port change. This package is what they share, and the ONLY place it is defined:

- ``templates/casa/_casa.html`` — Jinja macros: ``cabeca()`` (stylesheet + theme before the
  first paint) and ``barra()`` (brand, the screens in order, environment pill, Sair, theme);
- ``static/casa.css`` — the house palette (dark default, ``data-theme="light"`` override),
  the bar, the page title and the off-production strip. No ``color-mix``: older Chrome/Edge
  on the shop floor PCs drop it silently (30/09/2026);
- ``static/casa.js`` — the theme button.

Theme: a cookie, not ``localStorage``. Storage is per origin, so per PORT — each screen kept
its own theme. Cookies ignore the port, exactly like the shared login cookie ``wbc_painel``.

An app plugs in with :func:`instalar` (Jinja globals + loader) and serves ``STATIC_DIR`` at
``/casa``. Each app supplies the hrefs of the screens; the order and labels live here.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jinja2 import ChoiceLoader, Environment, FileSystemLoader

PASTA = Path(__file__).resolve().parent
TEMPLATES_DIR = PASTA / "templates"
STATIC_DIR = PASTA / "static"

#: Bump when casa.css / casa.js change: the three apps link them with ``?v=``.
VERSAO = "3"

MARCA = "Central Integração SAP"

#: The screens, in menu order (owner's decision 2, 01/10/2026): the order follows the pedido
#: (WBC → SAP → OPs); Sincronização runs alongside and comes last but one.
#: (key, label, icon, tooltip)
TELAS: tuple[tuple[str, str, str, str], ...] = (
    ("integracao", "Integração WBC", "integracao",
     "Integração WBC × SAP Business One — cotações e pedidos criados pelo worker"),
    ("pedidos", "Pedidos WBC", "pedidos",
     "Pedidos de venda do WBC → itens, estruturas e Ordens de Produção"),
    ("ops", "Manutenção de OP", "ops",
     "Liberar, replanejar e encerrar Ordens de Produção"),
    ("sincronizacao", "Sincronização", "sincronizar",
     "Sincronização SAP → Supabase: Ordens de Serviço e Oportunidades"),
    ("tarefas", "Execuções", "execucoes",
     "Execuções do Controle de Produção, em andamento e as últimas terminadas"),
)

COOKIE_TEMA = "casa_tema"
TEMA_PADRAO = "escuro"          # owner's decision 5: dark by default, light on the button


def itens_da_barra(hrefs: Mapping[str, str], ativa: str | None) -> list[dict[str, Any]]:
    """The menu items for one app. Every screen needs an href: a missing one is a bug in
    the app wiring, so it raises instead of silently dropping a screen from the menu."""
    return [
        {"chave": chave, "rotulo": rotulo, "icone": icone, "dica": dica,
         "href": hrefs[chave], "ativa": chave == ativa}
        for chave, rotulo, icone, dica in TELAS
    ]


def tema(cookies: Mapping[str, str] | None) -> str:
    """``"claro"`` or ``"escuro"`` from the request cookies; anything else is the default."""
    valor = (cookies or {}).get(COOKIE_TEMA, "")
    return valor if valor in ("claro", "escuro") else TEMA_PADRAO


def instalar(env: Environment) -> None:
    """Make an app's Jinja environment able to render the shell: the casa templates become
    reachable as ``casa/...`` (after the app's own, which keep priority) and the globals the
    macros need are registered."""
    if env.loader is None:
        env.loader = FileSystemLoader(TEMPLATES_DIR)
    else:
        env.loader = ChoiceLoader([env.loader, FileSystemLoader(TEMPLATES_DIR)])
    env.globals["casa"] = {"marca": MARCA, "versao": VERSAO, "cookie_tema": COOKIE_TEMA}
    env.globals["casa_itens"] = itens_da_barra
    env.globals["casa_tema"] = tema
