"""The shared shell of the .11 screens (casa/, PLANO_CASA_COMUM_11).

What must not drift: the menu order the owner chose, the theme cookie that follows the
operator across ports, and a stylesheet that still paints on the shop floor's old browsers.
"""
from __future__ import annotations

import re

import pytest
from jinja2 import DictLoader, Environment

import casa


def test_ordem_do_menu_e_a_do_pedido():
    # Decision 2 (01/10/2026): WBC → SAP → OPs, Sincronização alongside, Execuções last.
    assert [rotulo for _, rotulo, _, _ in casa.TELAS] == [
        "Integração WBC", "Pedidos WBC", "Manutenção de OP", "Sincronização", "Execuções",
    ]
    assert casa.MARCA == "Central Integração SAP"


def test_itens_marcam_so_a_tela_ativa():
    hrefs = {chave: f"/{chave}" for chave, *_ in casa.TELAS}
    itens = casa.itens_da_barra(hrefs, "ops")
    assert [i["href"] for i in itens] == [f"/{c}" for c, *_ in casa.TELAS]
    assert [i["chave"] for i in itens if i["ativa"]] == ["ops"]


def test_href_faltando_e_erro_e_nao_tela_sumida():
    with pytest.raises(KeyError):
        casa.itens_da_barra({"pedidos": "/pedidos-wbc"}, None)


@pytest.mark.parametrize(("cookies", "esperado"), [
    (None, "escuro"), ({}, "escuro"), ({"casa_tema": "claro"}, "claro"),
    ({"casa_tema": "escuro"}, "escuro"), ({"casa_tema": "light"}, "escuro"),
])
def test_tema_do_cookie(cookies, esperado):
    assert casa.tema(cookies) == esperado


def test_barra_renderiza_num_ambiente_qualquer():
    """The macro needs nothing but `casa.instalar` — the painel WBC (F2) and the 8077 (F3)
    plug in the same way the Controle de Produção did."""
    env = Environment(loader=DictLoader({"p.html": (
        '{% import "casa/_casa.html" as c %}'
        '{{ c.barra(casa_itens(h, "pedidos"), "/orcaview", amb, mostrar_sair=true) }}'
    )}), autoescape=True)
    casa.instalar(env)
    hrefs = {chave: f"/{chave}" for chave, *_ in casa.TELAS}
    producao = env.get_template("p.html").render(
        h=hrefs, amb={"em_producao": True, "company_db": "SBOALTAMIRAPROD", "ambiente": "produção"})
    assert "casa-ambiente--producao" in producao and "SBOALTAMIRAPROD" in producao
    assert "casa-faixa" not in producao             # decision 3: no strip in production
    homolog = env.get_template("p.html").render(
        h=hrefs, amb={"em_producao": False, "company_db": "SBOALTAMIRAHOMOLOG", "ambiente": "homologação"})
    assert "casa-ambiente--producao" not in homolog and "casa-faixa" in homolog


def test_css_da_casa_sem_color_mix():
    """Chrome/Edge < 111 paint NOTHING for a color-mix() inside a var() declaration
    (30/09/2026, the buttons with no fill). The shell must render everywhere."""
    css = (casa.STATIC_DIR / "casa.css").read_text(encoding="utf-8")
    assert "color-mix(" not in css


def test_css_da_casa_tem_os_dois_temas_com_os_mesmos_tokens():
    css = (casa.STATIC_DIR / "casa.css").read_text(encoding="utf-8")
    escuro = css[css.index(":root {"):css.index(":root[data-theme=\"light\"] {")]
    claro = css[css.index(":root[data-theme=\"light\"] {"):]
    claro = claro[:claro.index("}")]
    tokens_claro = set(re.findall(r"(--casa-[\w-]+):", claro))
    tokens_escuro = set(re.findall(r"(--casa-[\w-]+):", escuro))
    # Every light override exists in dark (no light-only color), and every color in dark
    # is redefined in light — except the ones that read on both grounds.
    assert tokens_claro <= tokens_escuro
    assert tokens_escuro - tokens_claro == {"--casa-sobre-solido", "--casa-fonte", "--casa-barra-altura"}


def test_hidden_vence_qualquer_display():
    """A dialog styled `display: grid` and marked `hidden` opened on load (Sincronização,
    01/10/2026). The house rule makes the attribute win on the three screens."""
    css = (casa.STATIC_DIR / "casa.css").read_text(encoding="utf-8")
    assert "[hidden] { display: none !important; }" in css


def test_js_do_tema_grava_o_mesmo_cookie():
    js = (casa.STATIC_DIR / "casa.js").read_text(encoding="utf-8")
    assert f'"{casa.COOKIE_TEMA}=" +' in js
    assert "Path=/" in js
