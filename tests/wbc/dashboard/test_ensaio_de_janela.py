"""O ensaio de janela: ver o que N meses fariam **sem armar nada**.

Por que este comando existe, e por que ele não podia faltar: a única forma de
descobrir o efeito de uma janela maior era armá-la — e o worker roda a cada
`WORKER_INTERVAL_SECONDS` (180 s na .11). Entre armar e ler a resposta havia uma
corrida que ninguém pode vencer de forma confiável, e o lado perdedor escreve
documentos irreversíveis em produção.
"""

from __future__ import annotations

import pytest

from wbcpython.dashboard import comandos as cmd


def _pendentes() -> cmd.Comando:
    return cmd.POR_ID["pendentes"]


class TestOArgumentoChegaNaCli:
    def test_o_campo_vira_a_opcao_meses(self) -> None:
        argv = cmd.montar_argv(_pendentes(), {"meses": "13"})

        assert "--meses" in argv
        assert argv[argv.index("--meses") + 1] == "13"

    def test_em_branco_nao_manda_nada(self) -> None:
        """Sem ensaio, a prévia segue a janela em vigor — que é o padrão."""
        assert "--meses" not in cmd.montar_argv(_pendentes(), {"meses": ""})

    def test_o_valor_nao_passa_por_shell(self) -> None:
        """`montar_argv` entrega lista ao subprocesso, nunca uma linha de comando.

        O campo é texto livre numa tela sem autenticação forte; se virasse
        string de shell, seria execução arbitrária na máquina do worker.
        """
        argv = cmd.montar_argv(_pendentes(), {"meses": "13 && del /q *"})

        assert argv[argv.index("--meses") + 1] == "13 && del /q *"


class TestOEnsaioNaoMenteNaAbaProximoCiclo:
    def test_ensaio_nao_exporta_o_retrato(self) -> None:
        """A aba "Próximo ciclo" mostra o que a PRÓXIMA passada vai fazer.

        Gravar ali o retrato de uma janela que ninguém armou faria a tela
        prometer um ciclo que não vai acontecer — e é essa aba que alguém lê
        para decidir se deixa o worker rodar.
        """
        argv = cmd.montar_argv(_pendentes(), {"meses": "13", "exportar": "1"})

        ajustado = cmd._ajustar_exportar(_pendentes(), argv, "state/wbc_previsao.json")

        assert "--exportar" not in ajustado
        assert "state/wbc_previsao.json" not in ajustado
        assert "--meses" in ajustado

    def test_sem_ensaio_o_retrato_continua_saindo(self) -> None:
        argv = cmd.montar_argv(_pendentes(), {"exportar": "1"})

        ajustado = cmd._ajustar_exportar(_pendentes(), argv, "state/wbc_previsao.json")

        assert ajustado[ajustado.index("--exportar") + 1] == "state/wbc_previsao.json"


class TestOComandoSegueSendoDeLeitura:
    def test_verificar_pendentes_nao_pede_senha(self) -> None:
        """O ensaio não pode herdar a senha por tabela.

        Ele lê e não grava nada — nem no SAP, nem no acompanhamento, nem no
        pedido de janela. Exigir senha aqui empurraria quem é de vendas a armar
        direto, sem conferir, que é o oposto do que este campo existe para
        provocar.
        """
        assert not _pendentes().protegido
        assert not _pendentes().escreve


@pytest.mark.parametrize("valor", ["13", "24"])
def test_o_ensaio_aceita_qualquer_janela_valida(valor: str) -> None:
    argv = cmd.montar_argv(_pendentes(), {"meses": valor})

    assert argv[argv.index("--meses") + 1] == valor
