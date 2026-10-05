"""Lógica de negócio do módulo Romaneio — **ESQUELETO, não implementado**.

Referência: `Views/Romaneio.b1f.cs` (ver seção 4.6 do migration_guide.md).
Prioridade 4 (última) na ordem de migração acordada com o usuário.

Funções a implementar:

- `buscar_disponivel(doc_num)`            <- Romaneio.busca() (linha ~132)
- `criar_item_composto(item_code, nome, ncm, itens_estrutura)`
      <- Romaneio.criacaoItem (linha ~822) — cria item SAP dinâmico + ProductTrees
- `criar_op_composto(itens, item_pai, linha_base, deposito)`
      <- Romaneio.criaOp (linha ~280)
- `saida_insumo(doc_entry)` / `entrada_mercadoria(item_code, custo, doc_entry)`
      <- Romaneio.SaidaEnsumo (linha ~741) / EntradaMercadoria (linha ~1112)
        (mesma lógica de manutencao_op.service — considerar extrair para core/ comum
        se, ao portar, o comportamento for de fato idêntico)
- `atualizar_pedido(item_code, nova_linha, custo_total, origem, quantidade_remessa)`
      <- Romaneio.AtualizaPedido (linha ~1011)
- `finalizar_remessa(doc_num, itens_selecionados)`
      <- Romaneio.Button5_ClickAfter (linha ~589) — orquestra as funções acima,
         agrupando por (Item de Origem, Código de Origem) em "remessas".

⚠️ NÃO portar `FechaPedido` (linha ~885) nem `AtualizaLinhaAntiga` (linha ~981) sem
antes confirmar com o usuário — ambos aparecem com o corpo real comentado/não chamado
no fluxo atual (código morto no legado, ver "Débitos técnicos" item 7 do
migration_guide.md), mas os nomes sugerem que a intenção de negócio (fechar pedido
quando não sobrar OP pendente; abater peso da linha de origem) pode não estar
totalmente implementada mesmo no legado.
"""
from __future__ import annotations

from controleproducao.core.service_layer_client import ServiceLayerClient


async def buscar_disponivel(sl: ServiceLayerClient, doc_num: str) -> list[dict]:
    raise NotImplementedError


async def finalizar_remessa(sl: ServiceLayerClient, doc_num: str, itens_selecionados: list[dict]) -> dict:
    raise NotImplementedError
