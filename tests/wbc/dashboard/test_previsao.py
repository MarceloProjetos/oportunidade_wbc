"""Testes da aba "Próximo ciclo" — agregações e filtros, sem subir o Streamlit.

O que se protege aqui é a leitura que o painel faz do retrato: se a agregação
divergir do que o ciclo faria, a tela deixa de ser rede de segurança e vira
falsa confiança.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from wbcpython.application.previsao import LinhaDePrevisao, Previsao, de_json, para_json
from wbcpython.dashboard import previsao as prev


def _linha(**campos) -> LinhaDePrevisao:
    base = {
        "orcnum": "00125536",
        "cliente": "HOFFMANN",
        "parceiro": "C008795",
        "sitcode_wbc": 40,
        "status_oportunidade": "Aberta",
        "regra": "revisao_congelada",
    }
    base.update(campos)
    return LinhaDePrevisao(**base)


class TestClassificacaoDaLinha:
    """`escreve` e `toca_documento` são o eixo da tela inteira."""

    def test_sem_acao_nao_escreve(self) -> None:
        assert not _linha().escreve
        assert not _linha().toca_documento

    def test_espelhar_status_escreve_mas_nao_toca_documento(self) -> None:
        """A distinção é o ponto: espelhar status é barato e reversível;
        criar ou cancelar documento não é."""
        linha = _linha(acoes=("atualizar_status_oportunidade",))
        assert linha.escreve
        assert not linha.toca_documento

    def test_criar_cotacao_toca_documento(self) -> None:
        linha = _linha(acoes=("criar_cotacao", "vincular_documento_a_oportunidade"))
        assert linha.escreve
        assert linha.toca_documento

    def test_documento_sem_valor_e_sinalizado(self) -> None:
        """O SAP recusa com -5002; a tela não pode prometer uma criação que
        não vai acontecer."""
        linha = _linha(acoes=("criar_cotacao",), valor_do_documento=Decimal(0))
        assert linha.sem_valor

    def test_valor_positivo_nao_e_sinalizado(self) -> None:
        linha = _linha(acoes=("criar_cotacao",), valor_do_documento=Decimal("10.5"))
        assert not linha.sem_valor


class TestKpis:
    def test_soma_bate_com_o_total(self) -> None:
        linhas = [
            _linha(),
            _linha(acoes=("atualizar_status_oportunidade",)),
            _linha(acoes=("criar_pedido",)),
        ]
        kpis = prev.calcular_kpis(linhas)
        assert kpis.total == 3
        assert kpis.escreve == 2
        assert kpis.toca_documento == 1
        assert kpis.so_status == 1
        assert kpis.sem_acao == 1
        # A conta que o leitor faz de cabeça tem de fechar.
        assert kpis.toca_documento + kpis.so_status == kpis.escreve
        assert kpis.escreve + kpis.sem_acao == kpis.total

    def test_conta_problemas_e_trocas(self) -> None:
        linhas = [
            _linha(problema="não encontrado no WBC — viraria erro no ciclo."),
            _linha(troca_de_parceiro=True),
        ]
        kpis = prev.calcular_kpis(linhas)
        assert kpis.problemas == 1
        assert kpis.troca_de_parceiro == 1


class TestContagens:
    def test_uma_linha_com_duas_acoes_conta_nas_duas(self) -> None:
        linhas = [_linha(acoes=("criar_cotacao", "vincular_documento_a_oportunidade"))]
        assert dict(prev.contar_acoes(linhas)) == {
            "Criar cotação": 1,
            "Vincular documento à oportunidade": 1,
        }

    def test_acao_desconhecida_ainda_aparece_legivel(self) -> None:
        """Uma ação nova no domínio não pode sumir da tela por falta de rótulo."""
        assert prev.rotulo_da_acao("acao_inedita") == "acao inedita"

    def test_distribuicao_por_campo(self) -> None:
        linhas = [_linha(), _linha(status_oportunidade="Vendida")]
        assert dict(prev.contar(linhas, "status_oportunidade")) == {"Aberta": 1, "Vendida": 1}


class TestFiltros:
    def test_busca_encontra_por_documento(self) -> None:
        linhas = [_linha(cotacao_docnum=77829), _linha(orcnum="00000001")]
        assert [linha.orcnum for linha in prev.filtrar(linhas, busca="77829")] == ["00125536"]

    def test_apenas_documento_e_mais_restrito_que_apenas_com_acao(self) -> None:
        linhas = [
            _linha(acoes=("atualizar_status_oportunidade",)),
            _linha(orcnum="00000002", acoes=("criar_pedido",)),
        ]
        assert len(prev.filtrar(linhas, apenas_com_acao=True)) == 2
        assert [x.orcnum for x in prev.filtrar(linhas, apenas_documento=True)] == ["00000002"]

    def test_filtra_por_sitcode_e_status(self) -> None:
        linhas = [
            _linha(),
            _linha(orcnum="00000003", sitcode_wbc=60, status_oportunidade="Vendida"),
        ]
        assert [x.orcnum for x in prev.filtrar(linhas, sitcode=60)] == ["00000003"]
        assert [x.orcnum for x in prev.filtrar(linhas, status="Aberta")] == ["00125536"]


class TestRetrato:
    def test_ida_e_volta_preserva_a_decisao(self) -> None:
        """O JSON é o contrato entre o comando e o painel: o que a prévia
        decidiu tem de chegar à tela intacto."""
        original = Previsao(
            gerado_em=datetime(2026, 9, 1, 13, 0, tzinfo=UTC),
            company_db="SBOALTAMIRAHOMOLOG",
            corte="2026-06-01",
            meses_de_janela=3,
            limite_de_escrita=200,
            linhas=(_linha(acoes=("criar_cotacao",), valor_do_documento=Decimal("10.50")),),
        )
        voltou = de_json(json.loads(json.dumps(para_json(original))))

        assert voltou.company_db == original.company_db
        assert voltou.gerado_em == original.gerado_em
        assert voltou.linhas[0].acoes == ("criar_cotacao",)
        assert voltou.linhas[0].valor_do_documento == Decimal("10.50")
        assert voltou.linhas[0].toca_documento

    def test_arquivo_ausente_tem_erro_proprio(self, tmp_path: Path) -> None:
        """'Nada a fazer' e 'ninguém gerou o retrato' não podem parecer a mesma
        coisa na tela."""
        with pytest.raises(prev.RetratoAusente):
            prev.carregar(tmp_path / "nao_existe.json")

    def test_carrega_do_disco(self, tmp_path: Path) -> None:
        destino = tmp_path / "previsao.json"
        destino.write_text(
            json.dumps(
                para_json(
                    Previsao(
                        gerado_em=datetime(2026, 9, 1, tzinfo=UTC),
                        company_db="SBOALTAMIRAHOMOLOG",
                        corte="2026-06-01",
                        meses_de_janela=3,
                        limite_de_escrita=200,
                        linhas=(_linha(),),
                    )
                )
            ),
            encoding="utf-8",
        )
        assert prev.carregar(destino).total == 1

    def test_idade_em_texto(self) -> None:
        previsao = Previsao(
            gerado_em=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
            company_db="X",
            corte="2026-06-01",
            meses_de_janela=3,
            limite_de_escrita=200,
        )
        assert prev.idade(previsao, agora=datetime(2026, 9, 1, 12, 30, tzinfo=UTC)) == "há 30 min"
        assert prev.idade(previsao, agora=datetime(2026, 9, 2, 12, 30, tzinfo=UTC)) == "há 1 dia(s)"
