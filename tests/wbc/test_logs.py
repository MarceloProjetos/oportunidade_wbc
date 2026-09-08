"""Testes do log compartilhado entre a tela do comando e o painel.

O ponto que estes testes protegem é a promessa da estrutura: **uma** execução,
**duas** telas, o mesmo texto. Se o arquivo deixar de ser escrito, ou o leitor
deixar de reconhecer o formato que o escritor produz, o painel fica em branco
sem erro nenhum — e um monitor em branco parece "está tudo calmo".
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from wbcpython import logs


@pytest.fixture(autouse=True)
def _log_limpo():
    """Cada teste começa e termina sem handlers nossos pendurados na raiz."""
    yield
    logs.configurar(arquivo=None, tela=False)
    for handler in list(logging.getLogger().handlers):
        logging.getLogger().removeHandler(handler)


class TestEscrita:
    def test_a_mesma_linha_vai_para_a_tela_e_para_o_arquivo(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """A promessa inteira do módulo, num teste."""
        destino = tmp_path / "wbcpython.log"
        logs.configurar(arquivo=destino)

        logging.getLogger("wbcpython.teste").info("ciclo concluído")

        assert "ciclo concluído" in capsys.readouterr().out
        assert "ciclo concluído" in destino.read_text(encoding="utf-8")

    def test_a_tela_recebe_o_texto_puro(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Data e nível em cada linha tornariam o relatório da prévia ilegível."""
        logs.configurar(arquivo=tmp_path / "x.log")
        logging.getLogger("wbcpython.teste").info("  [ok] 00125536")

        assert capsys.readouterr().out == "  [ok] 00125536\n"

    def test_o_arquivo_recebe_momento_nivel_e_origem(self, tmp_path: Path) -> None:
        """O painel precisa dos campos; a tela, não."""
        destino = tmp_path / "x.log"
        logs.configurar(arquivo=destino)
        logging.getLogger("wbcpython.teste").warning("atenção")

        (linha,) = logs.ler(destino)
        assert linha.nivel == "WARNING"
        assert linha.origem == "wbcpython.teste"
        assert linha.mensagem == "atenção"
        assert linha.momento

    def test_configurar_duas_vezes_nao_duplica_a_saida(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """O Streamlit reexecuta o script a cada clique, e os testes chamam em
        sequência: sem a limpeza, cada linha sairia duas, três, dez vezes."""
        logs.configurar(arquivo=tmp_path / "x.log")
        logs.configurar(arquivo=tmp_path / "x.log")
        logging.getLogger("wbcpython.teste").info("uma vez")

        assert capsys.readouterr().out.count("uma vez") == 1

    def test_arquivo_desligado_mantem_a_tela(self, capsys: pytest.CaptureFixture) -> None:
        assert logs.configurar(arquivo=None) is None
        logging.getLogger("wbcpython.teste").info("só na tela")
        assert "só na tela" in capsys.readouterr().out

    def test_arquivo_impossivel_nao_derruba_o_comando(self, tmp_path: Path) -> None:
        """Um comando de leitura não pode falhar por causa do log."""
        atrapalho = tmp_path / "arquivo"
        atrapalho.write_text("não sou diretório", encoding="utf-8")

        assert logs.configurar(arquivo=atrapalho / "sub" / "x.log") is None


class TestRuido:
    """A assimetria deliberada: relatório na tela, rastro completo no arquivo."""

    def test_http_das_bibliotecas_fica_fora_da_tela(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Numa prévia de 700 orçamentos, uma linha de HTTP por requisição
        afogaria o relatório que a pessoa veio ler."""
        destino = tmp_path / "x.log"
        logs.configurar(arquivo=destino)

        logging.getLogger("httpx").info("HTTP Request: GET /Quotations")
        logging.getLogger("wbcpython.pendentes").info("  [ok] 00125536")

        assert capsys.readouterr().out == "  [ok] 00125536\n"

    def test_mas_continua_no_arquivo(self, tmp_path: Path) -> None:
        """É justamente a chamada que precedeu o erro."""
        destino = tmp_path / "x.log"
        logs.configurar(arquivo=destino)
        logging.getLogger("httpx").info("HTTP Request: GET /Quotations")

        assert "HTTP Request" in destino.read_text(encoding="utf-8")

    def test_erro_de_biblioteca_aparece_na_tela(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Filtrar ruído não pode virar esconder falha."""
        logs.configurar(arquivo=tmp_path / "x.log")
        logging.getLogger("httpx").error("conexão recusada")

        assert "conexão recusada" in capsys.readouterr().out


class TestLeitura:
    def _escrever(self, destino: Path, linhas: list[str]) -> None:
        destino.write_text("\n".join(linhas) + "\n", encoding="utf-8")

    def test_mais_recentes_primeiro(self, tmp_path: Path) -> None:
        """Quem abre um monitor quer o que acabou de acontecer."""
        destino = tmp_path / "x.log"
        self._escrever(
            destino,
            [
                "2026-09-01 10:00:00 | INFO     | wbcpython.a | primeira",
                "2026-09-01 10:00:01 | INFO     | wbcpython.a | segunda",
            ],
        )
        assert [linha.mensagem for linha in logs.ler(destino)] == ["segunda", "primeira"]

    def test_limite_corta_o_fim_e_nao_o_comeco(self, tmp_path: Path) -> None:
        destino = tmp_path / "x.log"
        self._escrever(
            destino,
            [f"2026-09-01 10:00:0{i} | INFO     | wbcpython.a | linha {i}" for i in range(5)],
        )
        assert [linha.mensagem for linha in logs.ler(destino, limite=2)] == ["linha 4", "linha 3"]

    def test_filtra_por_nivel_e_por_texto(self, tmp_path: Path) -> None:
        destino = tmp_path / "x.log"
        self._escrever(
            destino,
            [
                "2026-09-01 10:00:00 | INFO     | wbcpython.a | orçamento 00125536",
                "2026-09-01 10:00:01 | ERROR    | wbcpython.a | falhou 00125536",
                "2026-09-01 10:00:02 | ERROR    | wbcpython.a | falhou 00000001",
            ],
        )
        assert len(logs.ler(destino, nivel="ERROR")) == 2
        assert len(logs.ler(destino, busca="00125536")) == 2
        assert len(logs.ler(destino, nivel="ERROR", busca="00125536")) == 1

    def test_linha_fora_do_formato_sobrevive(self, tmp_path: Path) -> None:
        """Traceback ocupa várias linhas; descartar o que não casa esconderia
        justamente a explicação do erro."""
        destino = tmp_path / "x.log"
        self._escrever(
            destino,
            [
                "2026-09-01 10:00:00 | ERROR    | wbcpython.a | estourou",
                "Traceback (most recent call last):",
                '  File "x.py", line 1, in <module>',
            ],
        )
        lidas = logs.ler(destino)
        assert len(lidas) == 3
        assert lidas[0].mensagem.startswith("  File")
        assert lidas[0].nivel == ""

    def test_arquivo_inexistente_e_lista_vazia(self, tmp_path: Path) -> None:
        assert logs.ler(tmp_path / "nao_existe.log") == []

    def test_erro_e_marcado_como_grave(self, tmp_path: Path) -> None:
        destino = tmp_path / "x.log"
        self._escrever(destino, ["2026-09-01 10:00:00 | ERROR    | a | x"])
        assert logs.ler(destino)[0].grave
