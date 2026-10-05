"""Testes do de-para @INO_GRP_PRODUTOS lido do Service Layer."""

from __future__ import annotations

from typing import Any

from wbcpython.infrastructure.service_layer.grupo_produtos import (
    RECURSO,
    RepositorioGrupoProdutosServiceLayer,
)

RESPOSTA = {
    "value": [
        {"Code": "1", "U_INO_Descricao": "Estantes", "U_INO_ItemSAP": "I000002"},
        {"Code": "2", "U_INO_Descricao": "Porta-Paletes", "U_INO_ItemSAP": "I000003"},
    ]
}


class ClienteFalso:
    def __init__(self, resposta: Any) -> None:
        self.resposta = resposta
        self.chamadas: list[str] = []

    def get_json(self, caminho: str, **_: Any) -> Any:
        self.chamadas.append(caminho)
        return self.resposta


class TestLeitura:
    def test_le_do_recurso_com_prefixo_u(self) -> None:
        """Sem o `U_`, o Service Layer responde -1002 Service Not Found."""
        cliente = ClienteFalso(RESPOSTA)
        RepositorioGrupoProdutosServiceLayer(cliente).carregar()
        assert cliente.chamadas == [RECURSO]
        assert RECURSO.startswith("U_")

    def test_monta_o_mapa_por_codigo(self) -> None:
        mapa = RepositorioGrupoProdutosServiceLayer(ClienteFalso(RESPOSTA)).carregar()
        assert mapa["2"].item_sap == "I000003"
        assert mapa["2"].descricao == "Porta-Paletes"

    def test_le_uma_vez_so(self) -> None:
        """A tabela é estática; reler por linha seria uma ida à rede por item."""
        cliente = ClienteFalso(RESPOSTA)
        repo = RepositorioGrupoProdutosServiceLayer(cliente)
        repo.carregar()
        repo.carregar()
        repo.carregar()
        assert len(cliente.chamadas) == 1


class TestCadastroIncompleto:
    def test_grupo_sem_item_e_descartado(self) -> None:
        """Um grupo sem item não aponta para lugar nenhum — cair no fallback é
        melhor que enviar `ItemCode` vazio e receber um erro obscuro do SAP."""
        resposta = {
            "value": [
                {"Code": "1", "U_INO_ItemSAP": "  "},
                {"Code": "2", "U_INO_ItemSAP": "I000003"},
            ]
        }
        mapa = RepositorioGrupoProdutosServiceLayer(ClienteFalso(resposta)).carregar()
        assert "1" not in mapa
        assert "2" in mapa

    def test_resposta_vazia_nao_estoura(self) -> None:
        mapa = RepositorioGrupoProdutosServiceLayer(ClienteFalso({})).carregar()
        assert mapa == {}
