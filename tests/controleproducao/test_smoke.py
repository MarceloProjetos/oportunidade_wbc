"""Smoke test: garante que a aplicação (web e CLI) sobe corretamente.

Não testa lógica de negócio (isso depende de um ambiente SAP/WBC real — ver seção 10
do migration_guide.md), só a estrutura básica do app.
"""
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from controleproducao.cli import app as cli_app
from controleproducao.main import app

client = TestClient(app)
cli_runner = CliRunner()


def test_home_page():
    resp = client.get("/")
    assert resp.status_code == 200


def test_modulo_removido_nao_responde_mais():
    """O módulo 1 (Oportunidades) foi removido do porte em 22/09/2026, por decisão do
    Anderson: a tela existia no addon original mas não faz parte do fluxo que a aplicação
    nova precisa cobrir. Removido do código, da CLI, da navegação e da documentação."""
    assert client.get("/oportunidades").status_code == 404


def test_modulos_respondem():
    for path in ["/pedidos-wbc", "/manutencao-op", "/romaneio"]:
        resp = client.get(path)
        assert resp.status_code == 200


def test_cli_help():
    result = cli_runner.invoke(cli_app, ["--help"])
    assert result.exit_code == 0
    assert "pedidos-wbc" in result.output


def test_cli_modulo_nao_implementado_avisa_e_sai_com_erro():
    # pedidos-wbc (15/09) e manutencao-op (16-17/09) já foram implementados; romaneio é o
    # último esqueleto restante, então é ele quem exercita esse caminho agora.
    result = cli_runner.invoke(cli_app, ["romaneio", "buscar", "123"])
    assert result.exit_code == 2
    assert "não foi implementado" in result.output


# ---------------------------------------------------------------------------
# Portabilidade (23/09/2026)
# ---------------------------------------------------------------------------
def test_app_sobe_de_qualquer_diretorio_de_trabalho():
    """Os caminhos de `static/` e `templates/` precisam ser absolutos.

    Com `directory="controleproducao/static"` a aplicação só subia quando o `uvicorn` era chamado de
    dentro de `python_app/`: de outro diretório — ou de um serviço systemd sem
    `WorkingDirectory` — morria com `Directory 'controleproducao/static' does not exist` antes de
    servir a primeira página. `config.py` e `listas_fixas.py` já derivavam o caminho de
    `__file__`; estes dois pontos é que ficaram para trás.
    """
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    from controleproducao.core.templates import templates
    from controleproducao.main import app as aplicacao

    # O teste roda com o cwd da suíte; afirmar sobre o caminho é mais honesto do que
    # mudar de diretório no meio do processo (que afetaria os outros testes).
    diretorio_templates = Path(templates.env.loader.searchpath[0])
    assert diretorio_templates.is_absolute(), diretorio_templates
    assert diretorio_templates.is_dir()

    montagens = [r for r in aplicacao.routes if isinstance(getattr(r, "app", None), StaticFiles)]
    assert montagens, "a montagem de /static sumiu"
    for montagem in montagens:
        caminho = Path(montagem.app.directory)
        assert caminho.is_absolute(), caminho
        assert caminho.is_dir()


def test_romaneio_fora_do_menu():
    """24/09/2026 — a pedido do Anderson, o Romaneio saiu do menu e da página inicial.
    O módulo (esqueleto) e a rota continuam no código; só não são mais oferecidos."""
    for pagina in ["/", "/pedidos-wbc", "/manutencao-op"]:
        html = client.get(pagina).text
        assert 'href="/romaneio"' not in html, pagina


def test_env_com_bom_do_bloco_de_notas(tmp_path):
    """24/09/2026: o Bloco de Notas do Windows salva UTF-8 com BOM. Com encoding "utf-8"
    puro, o BOM grudava no nome da primeira variável e ela era ignorada em silêncio."""
    from controleproducao.config import Settings

    arquivo = tmp_path / ".env"
    arquivo.write_bytes("SL_COMPANY_DB=SBOTESTE\n# comentário com acento\nHANA_PORT=30016\n".encode("utf-8-sig"))
    s = Settings(_env_file=arquivo)
    assert s.sl_company_db == "SBOTESTE"
    assert s.hana_port == 30016
