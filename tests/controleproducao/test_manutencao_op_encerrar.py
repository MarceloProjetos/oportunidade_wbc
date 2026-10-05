"""Testes de `manutencao-op buscar` e `manutencao-op encerrar` (módulo 3, 17/09/2026).

Porte de `ManutencaoOp.buscar()` (linha ~99) e de `Button4_ClickAfter` (linha ~399) com
`corrigeOP` / `SaidaEnsumo` / `EntradaProduto`.

O encerramento é a única operação irreversível do módulo — cria saída e entrada de
mercadoria. Por isso os testes aqui cobrem principalmente o que NÃO deve acontecer:
encerrar uma OP cuja movimentação falhou, movimentar uma OP já apontada, ou agir sem
confirmação.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from controleproducao.cli import app
from controleproducao.modules.manutencao_op import service as svc


def _op(doc_entry=101, doc_num=9001, status="P", planejada=10, apontada=0):
    return {
        "doc_entry": doc_entry, "doc_num": doc_num, "status": status, "item_code": "PROD1",
        "planejada": planejada, "apontada": apontada, "pedido": 84245,
    }


# ---------------------------------------------------------------------------
# buscar — montagem do WhereQuery (`buscar()`, linha ~99)
# ---------------------------------------------------------------------------
def test_filtro_com_um_limite_vira_igualdade():
    """Regra do original preservada: só o limite inferior => `=`, nunca `between`."""
    assert svc._monta_filtro("9001", None, None, None) == (' AND (T1."DocNum" = ?)', [9001])
    assert svc._monta_filtro(None, None, "P", None) == (' AND (T1."Status" = ?)', ["P"])


def test_filtro_com_dois_limites_vira_between():
    filtro, params = svc._monta_filtro("9001", "9010", "P", "R")
    assert '(T1."DocNum" BETWEEN ? AND ?)' in filtro
    assert '(T1."Status" BETWEEN ? AND ?)' in filtro
    assert params == [9001, 9010, "P", "R"]


def test_op_nao_numerica_e_recusada_na_borda():
    """F7 (28/09/2026): values are bound parameters; a typo is refused in Portuguese here
    instead of reaching HANA as a conversion error."""
    with pytest.raises(ValueError):
        svc._monta_filtro("90O1", None, None, None)
    with pytest.raises(ValueError):
        svc.buscar_ops(MagicMock(), "84245; --")


def test_limite_superior_sozinho_e_recusado():
    """No legado o `if` externo testava só o limite inferior, então `--op-ate` sozinho era
    silenciosamente ignorado. Numa CLI isso passaria despercebido; aqui é erro."""
    with pytest.raises(ValueError):
        svc._monta_filtro(None, "9010", None, None)
    with pytest.raises(ValueError):
        svc._monta_filtro(None, None, None, "R")


def test_status_invalido_nao_chega_na_query():
    """O legado interpolava a caixa de texto direto no SQL."""
    with pytest.raises(ValueError):
        svc._monta_filtro(None, None, "'; DROP", None)


def test_pedido_e_obrigatorio():
    with pytest.raises(ValueError):
        svc.buscar_ops(MagicMock(), "")


def test_busca_ordena_por_docnum_desc_e_traduz_status():
    leitor = MagicMock()
    leitor.fetch_all.return_value = [{
        "Número OP": 9001, "Status": "P", "Cód. Produto": "X", "Produto": "Peça",
        "Qtde. Planejada": 10, "Qtde. Apontada": 0, "Qtde. Restante": 10,
        "Data Pedido": None, "Data inicio": None, "Data Vencimento": None,
        "Cod. Cliente": "C1", "Cliente": "ACME", "Selecionar": "N",
    }]

    resultado = svc.buscar_ops(leitor, "84245", op_de="9001", status_de="P")

    sql, params = leitor.fetch_all.call_args.args
    assert sql.rstrip().endswith('ORDER BY T1."DocNum" DESC')
    # F7: the order number and the filters travel as parameters, never inside the text.
    assert params == (84245, 9001, "P")
    assert "84245" not in sql and "9001" not in sql
    assert resultado[0]["Status (descrição)"] == "Planejada"
    # A coluna da caixa de seleção da grade não faz sentido na CLI.
    assert "Selecionar" not in resultado[0]


# ---------------------------------------------------------------------------
# corrigeOP / SaidaEnsumo / EntradaProduto
# ---------------------------------------------------------------------------
def test_corrige_op_libera_e_forca_baixa_manual():
    """`corrigeOP` (linha ~364): sem `im_Manual` o SAP tenta backflush e recusa a saída."""
    sl = AsyncMock()
    sl.get_by_key.return_value = {"ProductionOrderLines": [{"LineNumber": 0}, {"LineNumber": 1}]}

    asyncio.run(svc.corrige_op(sl, 101))

    campos = sl.update_entity.await_args.args[2]
    assert campos["ProductionOrderStatus"] == "boposReleased"
    assert campos["ProductionOrderLines"] == [
        {"LineNumber": 0, "ProductionOrderIssueType": "im_Manual"},
        {"LineNumber": 1, "ProductionOrderIssueType": "im_Manual"},
    ]


def test_saida_usa_linenum_real_e_pula_linhas_ja_baixadas():
    """Duas correções sobre o C#: `BaseLine` é o `LineNum` da OP (o original usava o índice
    do recordset) e linhas sem saldo não viram linha de saída."""
    sl = AsyncMock()
    leitor = MagicMock()
    leitor.fetch_all.return_value = [
        {"LineNum": 0, "ItemCode": "A", "Faltante": 0},
        {"LineNum": 3, "ItemCode": "B", "Faltante": 5},
    ]

    asyncio.run(svc.saida_insumo(sl, leitor, 101, filial=1, serie=20))

    corpo = sl.create_entity.await_args.args[1]
    assert sl.create_entity.await_args.args[0] == "InventoryGenExits"
    assert corpo["DocumentLines"] == [
        {"BaseType": svc.TIPO_ORDEM_PRODUCAO, "BaseEntry": 101, "BaseLine": 3}
    ]
    assert corpo["BPL_IDAssignedToInvoice"] == 1, (
        "o campo da filial é BPL_IDAssignedToInvoice; `BPLId` não existe na entidade e a "
        "Service Layer o ignora em silêncio (seção 7.29)"
    )
    assert corpo["Series"] == 20, "e recusa também sem a série (seção 7.28)"
    assert "BPLId" not in corpo, "enviar BPLId dá a impressão de estar preenchido e não está"


def test_saida_sem_pendencia_nao_cria_documento():
    sl = AsyncMock()
    leitor = MagicMock()
    leitor.fetch_all.return_value = [{"LineNum": 0, "ItemCode": "A", "Faltante": 0}]

    assert asyncio.run(svc.saida_insumo(sl, leitor, 101, filial=1, serie=20)) == {}
    sl.create_entity.assert_not_awaited()


def test_entrada_aponta_para_a_op_sem_informar_item():
    """`EntradaProduto` (linha ~547): item, quantidade e depósito vêm do documento base."""
    sl = AsyncMock()
    asyncio.run(svc.entrada_produto(sl, 101, filial=1, serie=19))

    entidade, corpo = sl.create_entity.await_args.args
    assert entidade == "InventoryGenEntries"
    assert corpo["DocumentLines"] == [{"BaseType": svc.TIPO_ORDEM_PRODUCAO, "BaseEntry": 101}]
    assert corpo["BPL_IDAssignedToInvoice"] == 1
    assert corpo["Series"] == 19, "a entrada usa a série do ObjectCode 59, não a da saída"


# ---------------------------------------------------------------------------
# finalizar_ops — a orquestração
# ---------------------------------------------------------------------------
def _leitor_com_filial(
    linhas_faltantes=None, filiais_ativas=(1,), candidatos=None, series=None,
    componentes=None,
):
    """HANA simulado: responde pela query que chega, não pela ordem das chamadas.

    Despachar por conteúdo da query (e não com `side_effect` posicional) mantém o teste
    válido quando a ordem das leituras mudar — foi o que aconteceu ao acrescentar a
    resolução de filial em 21/09/2026.
    """
    leitor = MagicMock()
    faltantes = linhas_faltantes if linhas_faltantes is not None else [
        {"LineNum": 0, "ItemCode": "A", "Faltante": 5}
    ]
    padrao_candidatos = candidatos if candidatos is not None else {
        "filial_deposito_op": 1, "filial_componentes": 1, "filial_pedido": 1
    }

    # Séries por ObjectCode: 60 = saída, 59 = entrada (valores reais da Altamira).
    padrao_series = series if series is not None else {"60": 20, "59": 19}

    def fetch_all(sql, params=(), *_a, **_kw):
        if "OBPL" in sql:
            return [{"BPLId": f} for f in filiais_ativas]
        if "filial_deposito_op" in sql:
            return [padrao_candidatos]
        if "NNM1" in sql:
            # Since F7 the ObjectCode travels as a parameter, not inside the text.
            for object_code, numero in padrao_series.items():
                if object_code in (str(p) for p in params):
                    return [{"Series": numero, "SeriesName": "Primário", "filial": 0}]
            return []
        # As duas consultas a WOR1 se distinguem pelo alias "Faltante": uma traz o saldo
        # pendente de baixa, a outra os componentes para montar a hierarquia.
        if "Faltante" in sql:
            return faltantes
        if "COMPONENTES" in sql or ('FROM WOR1' in sql and '"DocEntry" IN' in sql):
            return componentes or []
        return faltantes

    leitor.fetch_all = MagicMock(side_effect=fetch_all)
    return leitor


def _finaliza(ops, falha_em=None, leitor=None):
    """Roda `finalizar_ops` com a Service Layer simulada; `falha_em` derruba uma etapa."""
    sl = AsyncMock()
    sl.get_by_key.return_value = {"ProductionOrderLines": [{"LineNumber": 0}]}
    leitor = leitor or _leitor_com_filial()
    chamadas = []

    async def create_entity(entidade, _corpo):
        chamadas.append(entidade)
        if falha_em == entidade:
            raise RuntimeError("recusado pelo SAP")
        return {"DocEntry": 777}

    async def update_entity(entidade, _chave, campos, **_kw):
        chamadas.append(f"{entidade}:{campos.get('ProductionOrderStatus')}")

    sl.create_entity = AsyncMock(side_effect=create_entity)
    sl.update_entity = AsyncMock(side_effect=update_entity)

    return asyncio.run(svc.finalizar_ops(sl, leitor, ops)), chamadas


def test_ordem_das_etapas_segue_o_original():
    resultado, chamadas = _finaliza([_op()])

    assert chamadas == [
        "ProductionOrders:boposReleased",   # corrigeOP
        "InventoryGenExits",                # SaidaEnsumo
        "InventoryGenEntries",              # EntradaProduto
        "ProductionOrders:boposClosed",     # mudaStatus("f")
    ]
    assert len(resultado["finalizadas"]) == 1
    assert resultado["finalizadas"][0]["entrada_docentry"] == 777


def test_op_nao_e_encerrada_se_a_movimentacao_falhar():
    """Divergência consciente: o legado chamava `mudaStatus("f")` no fim sobre TODAS as
    linhas marcadas, fechando inclusive OPs cuja saída havia falhado."""
    resultado, chamadas = _finaliza([_op()], falha_em="InventoryGenExits")

    assert "ProductionOrders:boposClosed" not in chamadas
    assert "InventoryGenEntries" not in chamadas
    assert resultado["finalizadas"] == []
    assert resultado["com_erro"][0]["etapa"] == "saída de insumo"


def test_op_ja_apontada_e_ignorada_sem_movimentar_estoque():
    """`if (qtdAD < qtdPD)` do original."""
    resultado, chamadas = _finaliza([_op(planejada=10, apontada=10)])

    assert chamadas == []
    assert resultado["ignoradas"] and resultado["finalizadas"] == []


def test_erro_numa_op_nao_impede_as_demais():
    ops = [_op(101, 9001), _op(102, 9002, planejada=10, apontada=10), _op(103, 9003)]
    resultado, _chamadas = _finaliza(ops)

    assert [op["doc_num"] for op in resultado["finalizadas"]] == [9001, 9003]
    assert [op["doc_num"] for op in resultado["ignoradas"]] == [9002]


def test_interromper_so_entre_ops_a_op_em_curso_termina_a_cadeia():
    """D5 (29/09/2026): "Interromper" pressed while the first OP is between its material
    issue and its product receipt. Cutting there would leave stock taken out and no product
    put in; the OP must finish its chain, and the next ones must not start."""
    parada = {"pedida": False}
    sl = AsyncMock()
    sl.get_by_key.return_value = {"ProductionOrderLines": [{"LineNumber": 0}]}
    chamadas = []

    async def create_entity(entidade, _corpo):
        chamadas.append(entidade)
        if entidade == "InventoryGenExits":
            parada["pedida"] = True        # the click lands right after the issue
        return {"DocEntry": 777}

    async def update_entity(_entidade, chave, campos, **_kw):
        chamadas.append(f"{chave}:{campos.get('ProductionOrderStatus')}")

    sl.create_entity = AsyncMock(side_effect=create_entity)
    sl.update_entity = AsyncMock(side_effect=update_entity)
    ops = [_op(101, 9001), _op(102, 9002), _op(103, 9003)]

    resultado = asyncio.run(svc.finalizar_ops(
        sl, _leitor_com_filial(), ops, deve_parar=lambda: parada["pedida"]
    ))

    assert chamadas == ["101:boposReleased", "InventoryGenExits", "InventoryGenEntries", "101:boposClosed"]
    assert [o["doc_num"] for o in resultado["finalizadas"]] == [9001]
    assert [o["doc_num"] for o in resultado["interrompidas"]] == [9002, 9003]
    assert "interrompida" in resultado["interrompidas"][0]["motivo"]


def test_sem_pedido_de_parada_nada_fica_de_fora():
    resultado, _chamadas = _finaliza([_op(101, 9001), _op(102, 9002)])
    assert resultado["interrompidas"] == []


# ---------------------------------------------------------------------------
# CLI — as guardas do comando irreversível
# ---------------------------------------------------------------------------
def _executa_cli(ops, argumentos):
    criados = []

    with patch("controleproducao.cli.get_settings") as cfg, patch("controleproducao.cli.HanaDirectReader") as leitor, \
            patch("controleproducao.cli.ServiceLayerClient") as cliente_sl:
        # Sem isto, `settings.is_production` seria um MagicMock (verdadeiro) e a trava de
        # escrita em produção recusaria o comando antes de ele fazer qualquer coisa.
        cfg.return_value.is_production = False
        # O leitor precisa responder também às queries de filial e de série, senão a cadeia
        # para em "determinar filial e séries" e o teste mede a coisa errada. Despacha por
        # conteúdo da query, igual a `_leitor_com_filial`.
        leitor.return_value.fetch_all = MagicMock(
            side_effect=_leitor_com_filial().fetch_all.side_effect
        )
        leitor.return_value.close = MagicMock()
        with patch("controleproducao.cli.manutencao_op_service.levanta_ops", return_value=ops):
            sl = cliente_sl.return_value.__aenter__.return_value
            sl.get_by_key = AsyncMock(return_value={"ProductionOrderLines": []})
            sl.update_entity = AsyncMock()

            async def create_entity(entidade, _corpo):
                criados.append(entidade)
                return {"DocEntry": 1}

            sl.create_entity = AsyncMock(side_effect=create_entity)
            resultado = CliRunner().invoke(app, ["manutencao-op", "encerrar"] + argumentos)
    return resultado, criados


def test_cli_exige_confirmacao_antes_de_movimentar():
    _resultado, criados = _executa_cli([_op()], ["9001"])
    assert criados == []


def test_cli_nao_movimenta_op_ja_apontada():
    resultado, criados = _executa_cli([_op(planejada=10, apontada=10)], ["9001", "--sim"])
    assert criados == []
    assert resultado.exit_code == 0


def test_cli_recusa_ops_e_pedido_ao_mesmo_tempo():
    resultado, criados = _executa_cli([], ["9001", "--pedido", "84245", "--sim"])
    assert criados == []
    assert resultado.exit_code == 1


# ---------------------------------------------------------------------------
# Faixas invertidas (21/09/2026) — armadilha encontrada no primeiro uso real
# ---------------------------------------------------------------------------
def test_faixa_de_status_invertida_e_recusada():
    """`--status-de P --status-ate C` parece "de Planejada até Cancelada", mas o `between`
    do original compara os códigos como TEXTO: a faixa é alfabética (C < L < P < R) e o
    intervalo fica invertido, devolvendo zero linhas — indistinguível de "o pedido não tem
    OP". Aconteceu no primeiro uso real do comando."""
    with pytest.raises(ValueError) as erro:
        svc._monta_filtro(None, None, "P", "C")
    assert "invertida" in str(erro.value)
    # A mensagem tem que ensinar a ordem real e oferecer a saída.
    assert "C < L < P < R" in str(erro.value)
    assert "--status-de C --status-ate P" in str(erro.value)


def test_faixa_de_status_na_ordem_alfabetica_passa():
    assert svc._monta_filtro(None, None, "C", "R") == (' AND (T1."Status" BETWEEN ? AND ?)', ["C", "R"])


def test_faixa_de_status_de_um_elemento_passa():
    """De/até iguais é intervalo válido de um item, não inversão."""
    assert svc._monta_filtro(None, None, "P", "P") == (' AND (T1."Status" BETWEEN ? AND ?)', ["P", "P"])


def test_faixa_de_op_invertida_e_recusada():
    """Mesma armadilha, com números — aqui a ordem é a natural, mas inverter ainda zera."""
    with pytest.raises(ValueError):
        svc._monta_filtro("9010", "9001", None, None)


def test_ordem_de_status_e_alfabetica_nao_do_ciclo_de_vida():
    """Se alguém "consertar" isso para a ordem do ciclo de vida (P -> R -> L), o filtro
    passa a divergir do legado silenciosamente. A ordem aqui é a do banco."""
    assert svc._ORDEM_STATUS == ("C", "L", "P", "R")


# ---------------------------------------------------------------------------
# Filial (BPLId) dos lançamentos — descoberto no primeiro `encerrar` real (21/09/2026)
# ---------------------------------------------------------------------------
def test_filial_vem_do_deposito_da_op_por_preferencia():
    """Um lançamento de estoque acontece na filial do depósito, e o B1 exige essa
    coerência — por isso o depósito vence o pedido de origem."""
    leitor = _leitor_com_filial(
        filiais_ativas=(1, 3),
        candidatos={"filial_deposito_op": 3, "filial_componentes": 1, "filial_pedido": 1},
    )
    assert svc._filial_do_movimento(leitor, 101) == 3


def test_cai_para_os_componentes_quando_o_deposito_da_op_nao_tem_filial():
    leitor = _leitor_com_filial(
        filiais_ativas=(1,),
        candidatos={"filial_deposito_op": 0, "filial_componentes": 1, "filial_pedido": 1},
    )
    assert svc._filial_do_movimento(leitor, 101) == 1


def test_cai_para_o_pedido_de_origem_em_ultimo_caso_derivado():
    leitor = _leitor_com_filial(
        filiais_ativas=(2,),
        candidatos={"filial_deposito_op": 0, "filial_componentes": 0, "filial_pedido": 2},
    )
    assert svc._filial_do_movimento(leitor, 101) == 2


def test_env_e_o_ultimo_recurso():
    leitor = _leitor_com_filial(
        filiais_ativas=(7,),
        candidatos={"filial_deposito_op": 0, "filial_componentes": 0, "filial_pedido": 0},
    )
    assert svc._filial_do_movimento(leitor, 101, filial_configurada=7) == 7


def test_filial_desabilitada_e_ignorada():
    """O caso real da Altamira: a filial 2 existe mas está `Disabled='Y'`. A mensagem do
    SAP fala em filial ATIVA, então existir não basta — e derivar uma desabilitada daria
    a mesma recusa, só mais tarde e com pior diagnóstico."""
    leitor = _leitor_com_filial(
        filiais_ativas=(1,),
        candidatos={"filial_deposito_op": 2, "filial_componentes": 1, "filial_pedido": 2},
    )
    assert svc._filial_do_movimento(leitor, 101) == 1


def test_sem_filial_ativa_determinavel_e_erro_legivel():
    leitor = _leitor_com_filial(
        filiais_ativas=(1,),
        candidatos={"filial_deposito_op": 9, "filial_componentes": 0, "filial_pedido": 0},
    )
    with pytest.raises(ValueError) as erro:
        svc._filial_do_movimento(leitor, 101)
    assert "filial ATIVA" in str(erro.value)
    assert "SL_BUSINESS_PLACE_ID" in str(erro.value)


def test_sem_nenhuma_filial_ativa_no_cadastro():
    leitor = _leitor_com_filial(filiais_ativas=())
    with pytest.raises(ValueError) as erro:
        svc._filial_do_movimento(leitor, 101)
    assert "Nenhuma filial ativa" in str(erro.value)


def test_filial_e_resolvida_antes_de_liberar_a_op():
    """A guarda que mais importa: sem filial determinável, a OP NÃO deve ser liberada.
    No primeiro teste real a cadeia parou na saída, deixando a OP Liberada com baixa
    manual — estado parcial que precisou de `replanejar` para desfazer. Resolver a filial
    primeiro elimina essa classe de meio-caminho."""
    leitor = _leitor_com_filial(
        filiais_ativas=(1,),
        candidatos={"filial_deposito_op": 0, "filial_componentes": 0, "filial_pedido": 0},
    )
    resultado, chamadas = _finaliza([_op()], leitor=leitor)

    assert chamadas == [], "nada pode ter sido escrito no SAP"
    assert resultado["com_erro"][0]["etapa"] == "determinar filial e séries"


def test_saida_e_entrada_saem_na_mesma_filial():
    """Resolvida uma vez por OP, de propósito: saída e entrada em filiais diferentes
    deixariam o estoque inconsistente entre elas."""
    sl = AsyncMock()
    sl.get_by_key.return_value = {"ProductionOrderLines": [{"LineNumber": 0}]}
    corpos = []

    async def create_entity(entidade, corpo):
        corpos.append((entidade, corpo.get("BPL_IDAssignedToInvoice")))
        return {"DocEntry": 1}

    sl.create_entity = AsyncMock(side_effect=create_entity)
    sl.update_entity = AsyncMock()

    asyncio.run(svc.finalizar_ops(sl, _leitor_com_filial(), [_op()]))

    filiais = {filial for _e, filial in corpos}
    assert filiais == {1}, f"esperava uma única filial, veio {filiais}"


# ---------------------------------------------------------------------------
# Série de numeração (21/09/2026) — segunda metade da lacuna do BPLId
# ---------------------------------------------------------------------------
def test_serie_da_filial_vence_a_serie_sem_filial():
    """Hoje a Altamira tem série sem filial, mas isso pode mudar sem aviso: se alguém
    cadastrar uma série específica da filial, ela tem que vencer."""
    leitor = MagicMock()
    leitor.fetch_all = MagicMock(return_value=[
        {"Series": 31, "SeriesName": "Filial 1", "filial": 1},
        {"Series": 20, "SeriesName": "Primário", "filial": 0},
    ])
    # A ordenação é responsabilidade da query (BPLId da filial primeiro); a função pega
    # a primeira linha. Este teste trava esse contrato.
    assert svc._serie_do_documento(leitor, "60", filial=1) == 31


def test_query_de_serie_exclui_bloqueadas_e_outras_filiais():
    from controleproducao.modules.manutencao_op import queries as q

    sql = q.SERIE_DO_DOCUMENTO
    assert """IFNULL(T0."Locked",'N') <> 'Y'""" in sql
    assert '''T0."BPLId" IS NULL OR T0."BPLId" = ?''' in sql
    assert '''T0."ObjectCode" = ?''' in sql
    # Determinismo: sem ORDER BY, séries diferentes a cada execução espalhariam a
    # numeração dos documentos sem explicação.
    assert "ORDER BY" in sql


def test_sem_serie_ativa_e_erro_legivel():
    leitor = MagicMock()
    leitor.fetch_all = MagicMock(return_value=[])
    with pytest.raises(ValueError) as erro:
        svc._serie_do_documento(leitor, "60", filial=1)
    assert "série" in str(erro.value).lower()


def test_saida_e_entrada_usam_series_diferentes():
    """São tipos de documento distintos (60 e 59) com numerações próprias. Usar a mesma
    série nos dois seria recusado pelo SAP — ou pior, aceito na numeração errada."""
    sl = AsyncMock()
    sl.get_by_key.return_value = {"ProductionOrderLines": [{"LineNumber": 0}]}
    corpos = {}

    async def create_entity(entidade, corpo):
        corpos[entidade] = corpo
        return {"DocEntry": 1}

    sl.create_entity = AsyncMock(side_effect=create_entity)
    sl.update_entity = AsyncMock()

    asyncio.run(svc.finalizar_ops(sl, _leitor_com_filial(), [_op()]))

    assert corpos["InventoryGenExits"]["Series"] == 20
    assert corpos["InventoryGenEntries"]["Series"] == 19


def test_as_duas_series_sao_resolvidas_antes_de_qualquer_escrita():
    """A guarda que mais importa agora: descobrir que falta a série da ENTRADA depois de a
    saída estar lançada deixaria baixa de insumo sem entrada correspondente — o pior
    estado da cadeia, que só se desfaz cancelando o documento no SAP à mão."""
    leitor = _leitor_com_filial(series={"60": 20})  # a série da entrada (59) não existe
    resultado, chamadas = _finaliza([_op()], leitor=leitor)

    assert chamadas == [], "nada pode ter sido escrito"
    assert resultado["com_erro"][0]["etapa"] == "determinar filial e séries"


def test_log_de_encerramento_reflete_o_status_apos_corrige_op():
    """`corrige_op` libera a OP, então o status do levantamento fica velho. Sem atualizar,
    o log fecha com "Planejada -> Encerrada" e esconde a passagem por Liberada — que é
    exatamente o estado que sobra quando a cadeia falha depois desse ponto."""
    sl = AsyncMock()
    sl.get_by_key.return_value = {"ProductionOrderLines": [{"LineNumber": 0}]}
    sl.create_entity = AsyncMock(return_value={"DocEntry": 1})
    vistos = []

    async def update_entity(entidade, chave, campos, **_kw):
        vistos.append((entidade, chave, campos.get("ProductionOrderStatus")))

    sl.update_entity = AsyncMock(side_effect=update_entity)

    resultado = asyncio.run(
        svc.finalizar_ops(sl, _leitor_com_filial(), [_op(status="P")])
    )

    assert resultado["finalizadas"][0]["status"] == "R", (
        "a OP registrada como finalizada passou por Liberada, não saiu de Planejada"
    )


def test_encerrar_aceita_liberada_e_nao_exige_planejada():
    """Registro de uma correção de entendimento (21/09/2026): o `encerrar` NÃO exige que a
    OP esteja Planejada. `corrigeOP` libera a OP de todo jeito, e o legado só checava a
    quantidade apontada. Durante os testes reais foi pedido um `replanejar` desnecessário
    entre as tentativas por causa dessa suposição errada."""
    resultado, criados = _executa_cli([_op(status="R")], ["9001", "--sim"])
    assert criados, "uma OP Liberada tem que ser processada"
    assert resultado.exit_code == 0


def test_encerrar_recusa_cancelada():
    """Cancelada não pode ser liberada, e `corrigeOP` é o primeiro passo — a cadeia
    falharia no início com mensagem obscura do SAP."""
    resultado, criados = _executa_cli([_op(status="C")], ["9001", "--sim"])
    assert criados == []
    assert "cancelada" in resultado.output
    assert resultado.exit_code == 0


# ---------------------------------------------------------------------------
# Regra de negócio: OP só é apontada estando liberada (21/09/2026)
# ---------------------------------------------------------------------------
def test_planejada_e_liberada_antes_do_apontamento():
    """Regra informada pelo Anderson: uma OP só pode ser apontada estando LIBERADA.
    Encerrar envolve apontar (saída + entrada), então a Planejada é liberada primeiro."""
    sl = AsyncMock()
    sl.get_by_key.return_value = {"ProductionOrderLines": [{"LineNumber": 0}]}

    liberou = asyncio.run(svc.corrige_op(sl, 101, status_atual="P"))

    campos = sl.update_entity.await_args.args[2]
    assert liberou is True
    assert campos["ProductionOrderStatus"] == "boposReleased"


def test_ja_liberada_nao_recebe_novo_status():
    """Reenviar o status que a OP já tem é escrita sem efeito — e a Service Layer é o
    recurso mais caro da execução (52% do tempo, seção 7.14)."""
    sl = AsyncMock()
    sl.get_by_key.return_value = {"ProductionOrderLines": [{"LineNumber": 0}]}

    liberou = asyncio.run(svc.corrige_op(sl, 101, status_atual="R"))

    campos = sl.update_entity.await_args.args[2]
    assert liberou is False
    assert "ProductionOrderStatus" not in campos


def test_baixa_manual_vai_mesmo_quando_ja_liberada():
    """O `im_Manual` não depende do status: é ele que permite a baixa manual dos insumos,
    e precisa estar nas linhas em qualquer caso."""
    sl = AsyncMock()
    sl.get_by_key.return_value = {
        "ProductionOrderLines": [{"LineNumber": 0}, {"LineNumber": 1}]
    }

    asyncio.run(svc.corrige_op(sl, 101, status_atual="R"))

    campos = sl.update_entity.await_args.args[2]
    assert campos["ProductionOrderLines"] == [
        {"LineNumber": 0, "ProductionOrderIssueType": "im_Manual"},
        {"LineNumber": 1, "ProductionOrderIssueType": "im_Manual"},
    ]


def test_resultado_registra_se_a_op_foi_liberada_no_caminho():
    """Para o relatório poder dizer o que aconteceu de verdade, em vez de supor."""
    sl = AsyncMock()
    sl.get_by_key.return_value = {"ProductionOrderLines": [{"LineNumber": 0}]}
    sl.create_entity = AsyncMock(return_value={"DocEntry": 1})
    sl.update_entity = AsyncMock()

    planejada = asyncio.run(svc.finalizar_ops(sl, _leitor_com_filial(), [_op(status="P")]))
    liberada = asyncio.run(svc.finalizar_ops(sl, _leitor_com_filial(), [_op(status="R")]))

    assert planejada["finalizadas"][0]["foi_liberada"] is True
    assert liberada["finalizadas"][0]["foi_liberada"] is False


def _texto(resultado):
    """Normaliza espaços na saída antes de procurar frases nela.

    ⚠️ **Não afirme sobre conteúdo de célula de tabela.** O Rich quebra a linha dentro da
    célula conforme a largura disponível, e a largura muda quando uma coluna é acrescentada
    — a frase `liberar + saída + entrada + encerrar` aparece cortada por bordas em pedaços
    imprevisíveis. Isso quebrou três testes ao longo de 21/09, sempre por formatação e
    nunca por comportamento. As asserções abaixo olham só a **prosa fora da tabela**, que é
    estável, e palavras isoladas.
    """
    return " ".join(resultado.output.split())


def test_cli_avisa_que_vai_liberar_antes():
    """O passo tem que aparecer antes da confirmação: é o estado que sobra se a cadeia
    falhar depois dele — aconteceu quatro vezes nos testes reais."""
    resultado, _criados = _executa_cli([_op(status="P")], ["9001", "--sim"])
    texto = _texto(resultado)
    assert "está Planejada e será LIBERADA" in texto
    assert "só pode ser apontada estando liberada" in texto
    assert "liberar" in texto  # palavra isolada sobrevive à quebra de célula


def test_cli_nao_avisa_liberacao_para_op_ja_liberada():
    resultado, _criados = _executa_cli([_op(status="R")], ["9001", "--sim"])
    texto = _texto(resultado)
    assert "LIBERADA antes" not in texto
    assert "só pode ser apontada estando liberada" not in texto


# ---------------------------------------------------------------------------
# Reverter a liberação quando nada foi lançado (21/09/2026)
# ---------------------------------------------------------------------------
def _finaliza_com_falha(falha_em, status="P"):
    """Roda `finalizar_ops` derrubando uma etapa; devolve (resultado, status_enviados)."""
    sl = AsyncMock()
    sl.get_by_key.return_value = {"ProductionOrderLines": [{"LineNumber": 0}]}
    status_enviados = []

    async def create_entity(entidade, _corpo):
        if falha_em == entidade:
            raise RuntimeError("recusado pelo SAP")
        return {"DocEntry": 1}

    async def update_entity(_entidade, _chave, campos, **_kw):
        if "ProductionOrderStatus" in campos:
            status_enviados.append(campos["ProductionOrderStatus"])

    sl.create_entity = AsyncMock(side_effect=create_entity)
    sl.update_entity = AsyncMock(side_effect=update_entity)

    resultado = asyncio.run(
        svc.finalizar_ops(sl, _leitor_com_filial(), [_op(status=status)])
    )
    return resultado, status_enviados


def test_falha_na_saida_devolve_a_op_para_planejada():
    """Nos testes reais de 21/09, cinco falhas seguidas (filial, série, nome de campo,
    custo de item) deixaram a OP Liberada e desfazer isso virou trabalho manual repetido.
    Quando nada foi lançado, reverter é seguro."""
    resultado, status = _finaliza_com_falha("InventoryGenExits")

    assert resultado["com_erro"][0]["liberacao"] == "desfeita"
    assert status == ["boposReleased", "boposPlanned"]


def test_falha_na_entrada_NAO_devolve_a_op():
    """Com a saída já lançada, reverter o status deixaria estoque movimentado numa OP
    Planejada — pior que o estado atual. A OP fica Liberada, de propósito."""
    resultado, status = _finaliza_com_falha("InventoryGenEntries")

    assert resultado["com_erro"][0]["liberacao"] == "mantida (saída já lançada)"
    assert "boposPlanned" not in status


def test_op_que_ja_estava_liberada_nao_e_rebaixada():
    """Só se desfaz o que este comando fez. Uma OP que chegou Liberada continua Liberada —
    rebaixá-la seria alterar um estado que não era nosso."""
    resultado, status = _finaliza_com_falha("InventoryGenExits", status="R")

    assert "liberacao" not in resultado["com_erro"][0]
    assert status == []


def test_falha_na_reversao_nao_esconde_o_erro_original():
    sl = AsyncMock()
    sl.get_by_key.return_value = {"ProductionOrderLines": [{"LineNumber": 0}]}
    sl.create_entity = AsyncMock(side_effect=RuntimeError("custo do item não encontrado"))

    async def update_entity(_entidade, _chave, campos, **_kw):
        if campos.get("ProductionOrderStatus") == "boposPlanned":
            raise RuntimeError("SAP recusou o replanejamento")

    sl.update_entity = AsyncMock(side_effect=update_entity)

    resultado = asyncio.run(svc.finalizar_ops(sl, _leitor_com_filial(), [_op(status="P")]))

    erro = resultado["com_erro"][0]
    assert "custo do item não encontrado" in erro["motivo"], "o erro original é o principal"
    assert erro["liberacao"].startswith("falhou")


# ---------------------------------------------------------------------------
# Encerrar o pedido inteiro, respeitando a hierarquia (21/09/2026)
# ---------------------------------------------------------------------------
def _op_h(entry, num, item, planejada=1):
    return {
        "doc_entry": entry, "doc_num": num, "status": "P", "item_code": item,
        "planejada": planejada, "apontada": 0, "pedido": 84348,
    }


def test_filha_vem_antes_da_mae():
    """A razão é física: a saída de insumo da mãe consome o item que a filha produz.
    Encerrar de cima para baixo falharia por falta de estoque."""
    # A (produz PAI) consome FILHO; B (produz FILHO) consome MATERIA.
    ops = [_op_h(1, 9001, "PAI"), _op_h(2, 9002, "FILHO")]
    componentes = {1: {"FILHO"}, 2: {"MATERIA"}}

    ordenadas, ciclo = svc.ordena_por_dependencia(ops, componentes)

    assert [op["doc_entry"] for op in ordenadas] == [2, 1]
    assert ciclo == []


def test_cascata_de_tres_niveis():
    ops = [_op_h(1, 9001, "N1"), _op_h(2, 9002, "N2"), _op_h(3, 9003, "N3")]
    componentes = {1: {"N2"}, 2: {"N3"}, 3: {"ACO"}}

    ordenadas, _ciclo = svc.ordena_por_dependencia(ops, componentes)

    assert [op["doc_entry"] for op in ordenadas] == [3, 2, 1]


def test_item_produzido_por_varias_ops_precede_todas_as_consumidoras():
    """`PAR000PADRA000000000` tem 13 OPs no pedido 84348: todas precisam vir antes de
    qualquer OP que consuma esse item."""
    ops = [_op_h(1, 9001, "PAI"), _op_h(2, 9002, "PARAFUSO"), _op_h(3, 9003, "PARAFUSO")]
    componentes = {1: {"PARAFUSO"}, 2: set(), 3: set()}

    ordenadas = [op["doc_entry"] for op in svc.ordena_por_dependencia(ops, componentes)[0]]

    assert ordenadas.index(2) < ordenadas.index(1)
    assert ordenadas.index(3) < ordenadas.index(1)


def test_ordem_e_deterministica():
    """Desempate pelo DocNum: ordem diferente a cada execução tornaria um problema
    impossível de reproduzir."""
    ops = [_op_h(3, 9003, "A"), _op_h(1, 9001, "B"), _op_h(2, 9002, "C")]
    componentes = {1: set(), 2: set(), 3: set()}

    duas_vezes = [
        [op["doc_num"] for op in svc.ordena_por_dependencia(ops, componentes)[0]]
        for _ in range(2)
    ]
    assert duas_vezes[0] == duas_vezes[1] == [9001, 9002, 9003]


def test_ciclo_fica_de_fora_em_vez_de_entrar_em_ordem_arbitraria():
    """Numa operação irreversível de estoque, chutar a ordem é pior que recusar: uma delas
    consumiria o produto da outra antes de ele existir."""
    ops = [_op_h(1, 9001, "X"), _op_h(2, 9002, "Y")]
    componentes = {1: {"Y"}, 2: {"X"}}

    ordenadas, ciclo = svc.ordena_por_dependencia(ops, componentes)

    assert ordenadas == []
    assert {op["doc_entry"] for op in ciclo} == {1, 2}


def test_op_que_consome_o_que_ela_mesma_produz_nao_vira_ciclo():
    """Acontece em reprocesso/refugo. A OP não depende de si mesma."""
    ops = [_op_h(1, 9001, "X")]
    ordenadas, ciclo = svc.ordena_por_dependencia(ops, {1: {"X", "ACO"}})

    assert [op["doc_entry"] for op in ordenadas] == [1]
    assert ciclo == []


def test_dependentes_transitivos_alcancam_a_cadeia_inteira():
    ops = [_op_h(1, 9001, "N1"), _op_h(2, 9002, "N2"), _op_h(3, 9003, "N3")]
    componentes = {1: {"N2"}, 2: {"N3"}, 3: set()}

    deps = svc.dependentes_transitivos(ops, componentes)

    assert deps[3] == {2, 1}, "falhando a mais funda, as duas de cima ficam inalcançáveis"
    assert deps[1] == set()


def test_falha_pula_quem_depende_dela():
    """Sem isso, a mãe seria tentada e falharia por falta do insumo que a filha produziria
    — uma cascata de erros que esconde a causa real, lá embaixo."""
    sl = AsyncMock()
    sl.get_by_key.return_value = {"ProductionOrderLines": [{"LineNumber": 0}]}
    sl.update_entity = AsyncMock()
    tentadas = []

    async def create_entity(entidade, corpo):
        if entidade == "InventoryGenExits":
            tentadas.append(corpo["DocumentLines"][0]["BaseEntry"])
            if corpo["DocumentLines"][0]["BaseEntry"] == 2:
                raise RuntimeError("custo do item não encontrado")
        return {"DocEntry": 1}

    sl.create_entity = AsyncMock(side_effect=create_entity)

    ops = [_op_h(2, 9002, "FILHO"), _op_h(1, 9001, "PAI")]  # já na ordem certa
    componentes = {1: {"FILHO"}, 2: set()}
    deps = svc.dependentes_transitivos(ops, componentes)

    resultado = asyncio.run(
        svc.finalizar_ops(sl, _leitor_com_filial(), ops, dependentes=deps)
    )

    assert tentadas == [2], "a mãe não pode nem ter sido tentada"
    assert [op["doc_entry"] for op in resultado["puladas"]] == [1]
    assert "depende da OP DocEntry=2" in resultado["puladas"][0]["motivo"]


def test_falha_nao_pula_op_independente():
    """Só quem depende da que falhou é pulado — o resto do lote segue."""
    sl = AsyncMock()
    sl.get_by_key.return_value = {"ProductionOrderLines": [{"LineNumber": 0}]}
    sl.update_entity = AsyncMock()
    ok = []

    async def create_entity(entidade, corpo):
        if entidade == "InventoryGenExits":
            entry = corpo["DocumentLines"][0]["BaseEntry"]
            if entry == 2:
                raise RuntimeError("custo do item não encontrado")
            ok.append(entry)
        return {"DocEntry": 1}

    sl.create_entity = AsyncMock(side_effect=create_entity)

    ops = [_op_h(2, 9002, "FILHO"), _op_h(1, 9001, "PAI"), _op_h(9, 9009, "AVULSA")]
    componentes = {1: {"FILHO"}, 2: set(), 9: set()}
    deps = svc.dependentes_transitivos(ops, componentes)

    resultado = asyncio.run(
        svc.finalizar_ops(sl, _leitor_com_filial(), ops, dependentes=deps)
    )

    assert ok == [9]
    assert [op["doc_entry"] for op in resultado["finalizadas"]] == [9]
    assert [op["doc_entry"] for op in resultado["puladas"]] == [1]
