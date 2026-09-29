"""The CLI's write commands leave a trace on disk (30/09/2026).

Replanejar, Reprocessar and `--force` are CLI-only and write to production; their progress
used to exist only in the terminal that ran them. The conftest points `CP_CLI_LOG_FILE` at
this test's `tmp_path`, so nothing lands in the repo's logs/.
"""
import logging
import sys


def test_comando_de_escrita_deixa_rastro_em_arquivo(tmp_path, monkeypatch):
    from controleproducao import cli

    monkeypatch.setattr(sys, "argv", ["controleproducao", "manutencao-op", "replanejar", "156209"])
    cli._ativa_perfil_e_progresso_silencioso()
    logging.getLogger("controleproducao.modules.manutencao_op.service").info("OP 156209 replanejada")
    for handler in logging.getLogger().handlers:
        handler.flush()

    texto = (tmp_path / "controleproducao_cli.log").read_text(encoding="utf-8")
    assert "CLI: python -m controleproducao manutencao-op replanejar 156209" in texto
    assert "company DB" in texto                      # which SAP the command wrote to
    assert "OP 156209 replanejada" in texto


def test_arquivo_que_nao_abre_nao_derruba_o_comando(tmp_path, monkeypatch, caplog):
    from controleproducao import cli
    from controleproducao.config import get_settings

    bloqueio = tmp_path / "um_arquivo"
    bloqueio.write_text("x", encoding="utf-8")
    monkeypatch.setenv("CP_CLI_LOG_FILE", str(bloqueio / "cli.log"))   # parent is a file
    get_settings.cache_clear()

    with caplog.at_level(logging.WARNING):
        cli._grava_tambem_em_arquivo()                # must not raise
    assert "Sem log da CLI em arquivo" in caplog.text
