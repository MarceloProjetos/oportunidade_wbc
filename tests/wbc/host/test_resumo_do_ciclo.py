"""O resumo do ciclo — a linha que alguém lê para decidir se precisa agir.

O worker avalia a janela inteira de propósito (ler é barato; escrever não).
Chamar tudo isso de "processado com sucesso" produzia um resumo que anunciava
centenas de orçamentos tratados quando o ciclo não havia tocado em nenhum.
"""

from __future__ import annotations

from wbcpython.host.worker import ResultadoExecucao


class TestResumo:
    def test_separa_avaliado_de_com_acao(self) -> None:
        resultado = ResultadoExecucao(
            processados=719, sucessos=719, erros=0, com_acao=14, escritas=14
        )
        assert "719 orçamento(s) avaliado(s)" in resultado.resumo
        assert "14 com ação" in resultado.resumo
        assert "14 com escrita no SAP" in resultado.resumo

    def test_ciclo_que_nao_fez_nada_diz_isso(self) -> None:
        """O caso que motivou a mudança: janela cheia, nada a fazer."""
        resultado = ResultadoExecucao(processados=719, sucessos=719)
        assert "0 com ação" in resultado.resumo
        assert "nenhum erro" in resultado.resumo

    def test_erro_aparece_no_lugar_do_nenhum(self) -> None:
        resultado = ResultadoExecucao(processados=10, sucessos=9, erros=1, com_acao=3, escritas=3)
        assert "1 com erro" in resultado.resumo
        assert "nenhum erro" not in resultado.resumo

    def test_acao_sem_escrita_e_visivel(self) -> None:
        """Nem toda ação escreve: um documento sem valor é decidido e não enviado."""
        resultado = ResultadoExecucao(processados=5, sucessos=5, com_acao=2, escritas=0)
        assert "2 com ação (0 com escrita no SAP)" in resultado.resumo
