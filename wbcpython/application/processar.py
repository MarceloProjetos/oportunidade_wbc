"""Caso de uso central: processar um orçamento.

É aqui que as peças se encontram — lê o estado (WBC + SAP), pede a decisão ao
domínio, executa as ações no SAP e registra tudo no acompanhamento.

A decisão de negócio **não mora aqui**: `domain.sitcode.decidir()` é quem
decide, e esta camada só executa. Essa separação é o que permite testar toda a
regra sem nenhum sistema externo, e é a diferença mais visível em relação ao
legado, onde regra e chamadas de API estavam entrelaçadas num `Main` de 380
linhas.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from wbcpython.application.documentos_lidos import DocumentosJaLidos
from wbcpython.domain import cotacao as regras_cotacao
from wbcpython.domain import pedido as regras_pedido
from wbcpython.domain.linhas import FATOR_DE_EMBARQUE, ResultadoLinhas
from wbcpython.domain.mapeamento import montar_payload_orcdetalhe
from wbcpython.domain.sitcode import Acao, Decisao, EstadoIntegracao, decidir
from wbcpython.infrastructure.service_layer.documentos import (
    RepositorioDocumentosVenda,
    RepositorioDocumentosVendaServiceLayer,
    TipoDocumento,
)
from wbcpython.infrastructure.service_layer.grupo_produtos import (
    RepositorioGrupoProdutos,
)
from wbcpython.infrastructure.service_layer.oportunidades import (
    CAMPO_CHAVE,
    RepositorioOportunidadesServiceLayer,
)
from wbcpython.infrastructure.service_layer.orcdetalhe import (
    RepositorioOrcDetalheServiceLayer,
)
from wbcpython.infrastructure.service_layer.parceiros import (
    RepositorioParceirosServiceLayer,
)
from wbcpython.infrastructure.wbc_sql.models import OrcamentoWbc
from wbcpython.infrastructure.wbc_sql.repository import RepositorioOrcamentosWbcSql
from wbcpython.tracking import RepositorioTracking, StatusIntegracao, TipoEvento

logger = logging.getLogger(__name__)

#: Filial (`BPL_IDAssignedToInvoice`) atribuída aos documentos.
#: `1` é `Altamira Indústria Metalúrgica Ltda.`, a única filial ativa; a de
#: código `2` ("Principal") está desabilitada. O legado fixava este mesmo valor
#: no código-fonte.
FILIAL_PADRAO = 1

#: Ações que criam ou alteram um documento de venda no SAP. Depois de
#: qualquer uma delas o `U_INO_StatusWBC` da oportunidade precisa refletir o
#: SitCode do WBC — ver `_espelhar_status_apos_documento`.
ACOES_DE_DOCUMENTO = frozenset(
    {
        Acao.CRIAR_COTACAO,
        Acao.ATUALIZAR_COTACAO,
        Acao.CANCELAR_E_RECRIAR_COTACAO,
        Acao.CRIAR_PEDIDO,
        Acao.ATUALIZAR_PEDIDO,
        Acao.CANCELAR_E_RECRIAR_PEDIDO,
    }
)

#: As ações de cada documento. A divisão não é organização de código: cotação e
#: pedido levam conjuntos **diferentes** de campos, no cabeçalho e nas linhas,
#: e cada um tem o seu procedimento (`_executar_cotacao`, `_executar_pedido`).
ACOES_DE_COTACAO = frozenset(
    {Acao.CRIAR_COTACAO, Acao.ATUALIZAR_COTACAO, Acao.CANCELAR_E_RECRIAR_COTACAO}
)
ACOES_DE_PEDIDO = frozenset(
    {Acao.CRIAR_PEDIDO, Acao.ATUALIZAR_PEDIDO, Acao.CANCELAR_E_RECRIAR_PEDIDO}
)

#: Ações que escrevem no SAP. É o que o teto por ciclo limita — ler a janela
#: inteira custa centésimos de segundo no HANA; criar documentos, não.
ACOES_DE_ESCRITA = ACOES_DE_DOCUMENTO | frozenset(
    {
        Acao.MARCAR_OPORTUNIDADE_PERDIDA,
        Acao.CANCELAR_COTACAO_NO_ENCERRAMENTO,
        Acao.ATUALIZAR_STATUS_OPORTUNIDADE,
        Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE,
    }
)

#: Ação executada → status a exibir no dashboard.
STATUS_POR_ACAO = {
    Acao.CRIAR_COTACAO: StatusIntegracao.COTACAO_CRIADA,
    Acao.ATUALIZAR_COTACAO: StatusIntegracao.COTACAO_ATUALIZADA,
    Acao.CANCELAR_E_RECRIAR_COTACAO: StatusIntegracao.COTACAO_CRIADA,
    Acao.CRIAR_PEDIDO: StatusIntegracao.PEDIDO_CRIADO,
    Acao.ATUALIZAR_PEDIDO: StatusIntegracao.PEDIDO_ATUALIZADO,
    Acao.CANCELAR_E_RECRIAR_PEDIDO: StatusIntegracao.PEDIDO_CRIADO,
    Acao.MARCAR_OPORTUNIDADE_PERDIDA: StatusIntegracao.ENCERRADA,
}


#: Folga na conferência do total gravado, em reais.
#:
#: O SAP arredonda em duas casas por linha e a soma pode diferir do centavo
#: calculado aqui. Um centavo de folga distingue arredondamento de linha que não
#: foi gravada — as divergências reais observadas são de milhares de reais.
TOLERANCIA_DE_TOTAL = Decimal("0.01")


class DocumentoSemValor(Exception):
    """O documento montado não tem valor — não há o que enviar ao SAP.

    Não é erro de integração: é um orçamento que ainda não tem o que virar
    documento. Ver `_exigir_valor`.
    """


def total_do_payload(dados: dict[str, Any]) -> Decimal:
    """Soma `Quantity * Price` das linhas do payload.

    É o mesmo cálculo que o SAP faz para o `DocTotal`, feito antes de enviar.
    Em `Decimal` de propósito: as linhas carregam `float` (é o que o JSON do
    Service Layer aceita), e somar floats para depois comparar com zero é
    justamente onde um total legítimo de centavos poderia virar zero.
    """
    total = Decimal(0)
    for linha in dados.get("DocumentLines") or ():
        quantidade = _decimal(linha.get("Quantity")) or Decimal(0)
        preco = _decimal(linha.get("Price")) or Decimal(0)
        total += quantidade * preco
    return total


@dataclass(frozen=True, slots=True)
class ResultadoProcessamento:
    orcnum: str
    decisao: Decisao
    acoes_executadas: tuple[Acao, ...] = ()
    erro: str = ""

    @property
    def sucesso(self) -> bool:
        return not self.erro


class ProcessadorDeOrcamento:
    """Orquestra o processamento de um orçamento, do WBC ao SAP."""

    def __init__(
        self,
        *,
        wbc: RepositorioOrcamentosWbcSql,
        orcdetalhe: RepositorioOrcDetalheServiceLayer,
        documentos: RepositorioDocumentosVendaServiceLayer,
        oportunidades: RepositorioOportunidadesServiceLayer,
        tracking: RepositorioTracking,
        grupo_produtos: RepositorioGrupoProdutos | None = None,
        parceiros: RepositorioParceirosServiceLayer | None = None,
        filial: int = FILIAL_PADRAO,
        gravar_snapshot: bool = True,
        fator_de_embarque: Decimal = FATOR_DE_EMBARQUE,
        somente_leitura: bool = False,
    ) -> None:
        self._wbc = wbc
        self._orcdetalhe = orcdetalhe
        self._documentos = documentos
        self._oportunidades = oportunidades
        self._tracking = tracking
        self._grupo_produtos = grupo_produtos
        self._parceiros = parceiros
        self._filial = filial
        self._gravar_snapshot = gravar_snapshot
        #: Folga de embalagem sobre o peso líquido da árvore — ver
        #: `domain.linhas._peso_unitario`. Vem da configuração no worker.
        self._fator_de_embarque = fator_de_embarque
        #: Ensaio: decide e registra tudo, mas não executa nenhuma ação.
        #:
        #: A garantia não vem de filtrar ações uma a uma — vem de o único
        #: caminho que escreve (`_executar`) não ser chamado. Não há como uma
        #: ação nova nascer fora do ensaio por esquecimento.
        self._somente_leitura = somente_leitura

    # ------------------------------------------------------------------ fluxo

    def processar(
        self,
        oportunidade: dict[str, Any],
        situacao: tuple[int, str] | None = None,
    ) -> ResultadoProcessamento:
        """Processa uma oportunidade do SAP contra o orçamento no WBC.

        Nunca propaga exceção: uma falha num orçamento vira erro registrado, e o
        laço do worker segue para o próximo. É a correção direta do defeito do
        legado, em que um registro problemático abortava a execução inteira.
        """
        orcnum = str(oportunidade.get("U_ORCNUM_WBC") or "").strip()
        if not orcnum:
            logger.warning(
                "Oportunidade %s sem número de orçamento — ignorada.", oportunidade.get(CAMPO_CHAVE)
            )
            return ResultadoProcessamento(
                orcnum="",
                decisao=Decisao(regra="sem_numero_de_orcamento"),
                erro="Oportunidade sem U_ORCNUM_WBC.",
            )

        try:
            return self._processar_orcamento(orcnum, oportunidade, situacao)
        except Exception as exc:
            logger.exception("Falha ao processar o orçamento %s.", orcnum)
            self._tracking.registrar_erro(orcnum, f"{type(exc).__name__}: {exc}")
            return ResultadoProcessamento(
                orcnum=orcnum, decisao=Decisao(regra="erro"), erro=str(exc)
            )

    def _processar_orcamento(
        self,
        orcnum: str,
        oportunidade: dict[str, Any],
        situacao: tuple[int, str] | None = None,
    ) -> ResultadoProcessamento:
        """Decide primeiro; só carrega o orçamento inteiro se houver ação.

        `situacao` é `(sitcode, revisao)` lido em lote pelo worker. Com ela, a
        decisão sai sem tocar nas linhas do orçamento — e carregar as linhas é
        caro: 279 ms contra 29 ms da situação, medido no ambiente real. Como a
        grande maioria dos orçamentos da janela não tem ação, evitar essa carga
        é a diferença entre um ciclo de oito minutos e um de um minuto.

        Quem tem ação carrega o orçamento completo e **refaz** estado e decisão
        sobre ele. Parece redundante, mas não é: garante que o documento seja
        montado a partir do mesmo retrato que autorizou a ação, e não de uma
        versão que mudou entre as duas leituras.
        """
        if situacao is not None:
            decisao_previa = self._decidir_com_situacao(orcnum, oportunidade, situacao)
            if decisao_previa is not None:
                return decisao_previa

        orcamento = self._wbc.buscar_orcamento(orcnum)
        if orcamento is None:
            mensagem = f"Orçamento {orcnum} não encontrado no WBC."
            self._tracking.registrar_erro(orcnum, mensagem)
            return ResultadoProcessamento(
                orcnum=orcnum, decisao=Decisao(regra="orcamento_inexistente"), erro=mensagem
            )

        estado = self._montar_estado(orcamento, oportunidade)
        decisao = decidir(estado)

        self._tracking.registrar_verificacao(
            orcnum,
            oppr_id=_inteiro(oportunidade.get(CAMPO_CHAVE)),
            cliente=orcamento.cliente_nome,
            vendedor=orcamento.representante,
            municipio=orcamento.municipio,
            uf=orcamento.uf,
            sitcode_wbc=orcamento.sitcode,
            revisao_wbc=orcamento.revisao,
            sitcode_sap=estado.sitcode_sap,
            regra=decisao.regra,
            data_abertura=_data(oportunidade.get("OpenDate")),
        )
        self._tracking.registrar_evento(
            orcnum,
            regra=decisao.regra,
            mensagem=" ".join(decisao.motivos),
            detalhes={"acoes": [a.value for a in decisao.acoes]},
        )

        if not decisao.tem_acao:
            self._tracking.registrar_verificacao(
                orcnum, status=StatusIntegracao.SEM_ACAO, regra=decisao.regra
            )
            return ResultadoProcessamento(orcnum=orcnum, decisao=decisao)

        if self._somente_leitura:
            return self._registrar_ensaio(orcnum, decisao)

        executadas = self._executar(estado, orcamento, oportunidade, decisao)
        return ResultadoProcessamento(orcnum=orcnum, decisao=decisao, acoes_executadas=executadas)

    def _registrar_ensaio(self, orcnum: str, decisao: Decisao) -> ResultadoProcessamento:
        """Fecha o orçamento no ensaio: registrado, decidido, não executado.

        O status **não** é tocado. Uma linha nova nasce `PENDENTE`, que é a
        verdade: avaliada, com ação identificada, ainda não feita. E uma linha
        que já traz o resultado de um ciclo real não pode ser rebaixada por um
        ensaio — o ensaio não desfez nada no SAP.

        O evento diz em voz alta que foi ensaio. Sem ele, semanas depois, a
        linha parada em "Pendente" seria indistinguível de uma que falhou em
        silêncio.
        """
        acoes = ", ".join(a.value for a in decisao.acoes)
        self._tracking.registrar_evento(
            orcnum,
            regra=decisao.regra,
            mensagem=(
                f"Simulação: {len(decisao.acoes)} ação(ões) decidida(s) e "
                f"**não** executada(s) ({acoes}). Nada foi escrito no SAP."
            ),
            detalhes={"simulacao": True, "acoes_previstas": [a.value for a in decisao.acoes]},
        )
        return ResultadoProcessamento(orcnum=orcnum, decisao=decisao)

    def _decidir_com_situacao(
        self,
        orcnum: str,
        oportunidade: dict[str, Any],
        situacao: tuple[int, str],
    ) -> ResultadoProcessamento | None:
        """Decide pela situação lida em lote. `None` significa "tem ação, siga".

        O registro do orçamento sem ação é parcial de propósito: cliente,
        vendedor e município moram nas linhas, que não foram carregadas. O
        acompanhamento preserva o que já sabia desses campos — e gravar `''`
        por cima seria pior do que não gravar.
        """
        sitcode, revisao = situacao
        estado = montar_estado(
            _OrcamentoResumido(orcnum=orcnum, sitcode=sitcode, revisao=revisao),
            oportunidade,
            fonte_de_documentos(oportunidade, self._documentos),
        )
        decisao = decidir(estado)
        if decisao.tem_acao:
            return None

        self._tracking.registrar_verificacao(
            orcnum,
            oppr_id=_inteiro(oportunidade.get(CAMPO_CHAVE)),
            sitcode_wbc=sitcode,
            revisao_wbc=revisao,
            sitcode_sap=estado.sitcode_sap,
            regra=decisao.regra,
            status=StatusIntegracao.SEM_ACAO,
            data_abertura=_data(oportunidade.get("OpenDate")),
        )
        self._tracking.registrar_evento(
            orcnum,
            regra=decisao.regra,
            mensagem=" ".join(decisao.motivos),
            detalhes={"acoes": []},
        )
        return ResultadoProcessamento(orcnum=orcnum, decisao=decisao)

    # ------------------------------------------------------------ montagem

    def _montar_estado(
        self, orcamento: OrcamentoWbc, oportunidade: dict[str, Any]
    ) -> EstadoIntegracao:
        return montar_estado(
            orcamento, oportunidade, fonte_de_documentos(oportunidade, self._documentos)
        )

    # ------------------------------------------------------------- execução

    def _executar(
        self,
        estado: EstadoIntegracao,
        orcamento: OrcamentoWbc,
        oportunidade: dict[str, Any],
        decisao: Decisao,
    ) -> tuple[Acao, ...]:
        orcnum = orcamento.orcnum
        oppr_id = _inteiro(oportunidade.get(CAMPO_CHAVE))
        executadas: list[Acao] = []
        ultimo_documento: dict[str, Any] | None = None
        tipo_ultimo: TipoDocumento | None = None

        # O snapshot vem **antes** dos documentos porque o seu `DocEntry` é o
        # que alimenta o UDF `U_INO_ORCAMENTO` da cotação/pedido. Ver
        # `_gravar_snapshot_orcdetalhe`.
        snapshot_id = self._gravar_snapshot_orcdetalhe(orcamento) if self._gravar_snapshot else None

        for acao in decisao.acoes:
            if acao is Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE:
                # Depende do documento gerado nas ações anteriores desta mesma
                # decisão; sem documento novo, não há o que vincular.
                if oppr_id and ultimo_documento and tipo_ultimo:
                    # DocEntry, não DocNum — ver `vincular_documento`.
                    doc_entry = _inteiro(ultimo_documento.get("DocEntry"))
                    total = _decimal(ultimo_documento.get("DocTotal"))
                    if doc_entry:
                        self._oportunidades.vincular_documento(
                            oppr_id,
                            tipo_ultimo,
                            doc_entry,
                            float(total) if total else None,
                        )
                        executadas.append(acao)
                continue

            if acao is Acao.ATUALIZAR_STATUS_OPORTUNIDADE:
                if oppr_id:
                    self._oportunidades.atualizar_status(oppr_id, orcamento.sitcode)
                    executadas.append(acao)
                continue

            if acao is Acao.CANCELAR_COTACAO_NO_ENCERRAMENTO:
                if self._cancelar_cotacao_no_encerramento(orcamento.orcnum, oportunidade):
                    executadas.append(acao)
                continue

            if acao is Acao.MARCAR_OPORTUNIDADE_PERDIDA:
                if oppr_id:
                    self._oportunidades.encerrar(oppr_id, orcamento.sitcode)
                    executadas.append(acao)
                continue

            try:
                documento, tipo = self._executar_documento(
                    acao, estado, orcamento, oportunidade, snapshot_id
                )
            except DocumentoSemValor:
                # A ação **não** entra em `executadas`: nada foi escrito, e
                # contá-la faria o espelhamento de status e o teto de escrita
                # tratarem um documento que não existe como se existisse.
                continue
            if documento is not None and tipo is not None:
                ultimo_documento, tipo_ultimo = documento, tipo
                self._registrar_documento(orcnum, tipo, documento)
            executadas.append(acao)

        self._espelhar_status_apos_documento(oppr_id, estado, orcamento, executadas)

        # Nada de "baixar a marca" **depois da troca**. Chegamos a fazer isso,
        # escrevendo `U_INO_Update = 'N'` e `U_INO_PN_Correc = ''`, e as duas
        # escritas estavam erradas: `U_INO_Update` sinaliza alteração de
        # **valores**, e não a troca de parceiro; e apagar o `PN_Correc`
        # destruiria o registro de qual correção foi pedida. A idempotência vem
        # do próprio pedido — `troca_de_parceiro_pendente` compara o parceiro
        # que ele já tem —, exatamente como o `ChecaPNPedido` do legado.
        #
        # Isto **não** quer dizer que a integração nunca toque no campo: ela
        # baixa `U_INO_Update` ao vincular o pedido, junto com o estágio, como
        # o legado (`ServiceProcess.cs:198`, `:210`). O que não existe é uma
        # escrita avulsa aqui, no fim da troca.

        status = self._status_final(executadas)
        if status is not None:
            self._tracking.registrar_verificacao(orcnum, status=status, regra=decisao.regra)
        self._tracking.registrar_evento(
            orcnum,
            tipo=TipoEvento.ACAO,
            regra=decisao.regra,
            mensagem="Ações executadas: " + ", ".join(a.value for a in executadas),
        )
        return tuple(executadas)

    def _cancelar_cotacao_no_encerramento(
        self,
        orcnum: str,
        oportunidade: dict[str, Any],
    ) -> bool:
        """Cancela a cotação do orçamento encerrado; falha não derruba o ciclo.

        A recusa do SAP é um resultado esperado, não um defeito: a cotação pode
        já ter sido convertida, fechada manualmente ou cancelada por alguém
        entre a leitura e a escrita. Nesses casos registra-se o aviso e o
        encerramento da oportunidade segue — que é o efeito que o legado já
        produzia.
        """
        doc_entry = fonte_de_documentos(oportunidade, self._documentos).doc_entry(
            TipoDocumento.COTACAO, orcnum
        )
        if doc_entry is None:
            return False
        try:
            self._documentos.cancelar(TipoDocumento.COTACAO, doc_entry)
        except Exception as erro:  # noqa: BLE001
            aviso = f"Não foi possível cancelar a cotação {doc_entry} no encerramento: {erro}"
            logger.warning("Orçamento %s: %s", orcnum, aviso)
            self._tracking.registrar_evento(orcnum, tipo=TipoEvento.ERRO, mensagem=aviso)
            return False
        return True

    def _espelhar_status_apos_documento(
        self,
        oppr_id: int | None,
        estado: EstadoIntegracao,
        orcamento: OrcamentoWbc,
        executadas: list[Acao],
    ) -> None:
        """Mexeu em documento, o `U_INO_StatusWBC` da oportunidade acompanha.

        Sem isto, uma oportunidade recém-integrada ficava com o status antigo
        (tipicamente `"0"`): as regras que criam ou atualizam documento —
        `sem_cotacao_cria`, `emitido_sem_cotacao`, `revisao_sobre_cotacao_emitida`
        — não trazem `atualizar_status_oportunidade` entre as ações, porque no
        legado o espelhamento vinha embutido na própria gravação da
        oportunidade (`AddCotacaoOportunidade` grava `U_INO_StatusWBC` junto com
        o vínculo).

        A consequência de esquecer é silenciosa e cara: na execução seguinte o
        SAP ainda diz `"0"`, a decisão volta a ser "criar", e a integração
        recria o documento — de novo, e de novo.

        Aqui é um passo explícito, aplicado depois de qualquer ação de
        documento. Três guardas evitam chamadas inúteis ou nocivas:
        não repetir o que `ATUALIZAR_STATUS_OPORTUNIDADE` já fez; não escrever
        se o SAP já reflete o SitCode; e não reabrir status numa oportunidade
        que acabou de ser encerrada.
        """
        if oppr_id is None or not executadas:
            return
        if Acao.ATUALIZAR_STATUS_OPORTUNIDADE in executadas:
            return
        if Acao.MARCAR_OPORTUNIDADE_PERDIDA in executadas:
            return
        if not any(acao in ACOES_DE_DOCUMENTO for acao in executadas):
            return
        if estado.sitcode_sap.strip() == str(orcamento.sitcode):
            return

        self._oportunidades.atualizar_status(oppr_id, orcamento.sitcode)
        logger.info(
            "Orçamento %s: status da oportunidade espelhado para %s após ação de documento.",
            orcamento.orcnum,
            orcamento.sitcode,
        )

    def _executar_documento(
        self,
        acao: Acao,
        estado: EstadoIntegracao,
        orcamento: OrcamentoWbc,
        oportunidade: dict[str, Any],
        snapshot_id: int | None = None,
    ) -> tuple[dict[str, Any] | None, TipoDocumento | None]:
        """Encaminha a ação para o procedimento do documento correspondente.

        Cotação e pedido têm procedimentos **separados** — `_executar_cotacao` e
        `_executar_pedido` — porque os dois documentos não levam os mesmos
        campos. Enquanto dividiam um caminho só com um parâmetro de tipo, foi
        fácil mandar campo de um no outro; o `U_INO_Composicao` chegou a sair em
        cotação, onde aparece na impressão do cliente.
        """
        if acao in ACOES_DE_COTACAO:
            return self._executar_cotacao(acao, estado, orcamento, oportunidade, snapshot_id)
        if acao in ACOES_DE_PEDIDO:
            return self._executar_pedido(acao, estado, orcamento, oportunidade, snapshot_id)
        return None, None

    def _parceiro_existe(self, estado: EstadoIntegracao, orcnum: str) -> bool:
        """O parceiro corrigido existe no SAP? (`ChecaPN` do legado.)

        Vale para os dois caminhos que gravam o pedido no `PN_Correc`: a troca
        (cancela e recria) e a criação já corrigida. Criar no parceiro da
        oportunidade "enquanto isso" seria pior do que não criar: o vínculo
        baixa o `U_INO_Update`, e o pedido ficaria no parceiro errado para
        sempre — ver `EstadoIntegracao.nasce_no_parceiro_corrigido`.

        Sem repositório de parceiros injetado, segue em frente: é o
        comportamento de antes, e vale para os testes que não exercitam troca.
        """
        if not estado.pedido_no_parceiro_corrigido or self._parceiros is None:
            return True
        if self._parceiros.existe(estado.parceiro_novo):
            return True

        aviso = (
            f"Parceiro corrigido {estado.parceiro_novo} não existe no SAP: "
            + (
                "o pedido não foi criado (criá-lo no parceiro da oportunidade o "
                "deixaria no parceiro errado para sempre)."
                if estado.nasce_no_parceiro_corrigido
                else "o pedido não foi refeito (cancelá-lo o deixaria sem substituto)."
            )
        )
        logger.warning("Orçamento %s: %s", orcnum, aviso)
        self._tracking.registrar_evento(orcnum, tipo=TipoEvento.ERRO, mensagem=aviso)
        return False

    def _exigir_valor(self, tipo: TipoDocumento, orcnum: str, dados: dict[str, Any]) -> None:
        """Recusa, antes de enviar, um documento que o SAP rejeitaria.

        O SAP responde `HTTP 400 | SAP -5002 | "Document total value must be
        zero or greater than zero"` a um documento sem valor. Chegar lá vira
        erro de ciclo, entra no dashboard como falha e some no meio do ruído —
        quando a causa é banal: o orçamento ainda não tem item no WBC (caso do
        00125275, com zero itens), ou os itens que tem não chegaram a preço.

        Isso não é falha da integração e não deve ser tratada como tal. Fica no
        log e no acompanhamento, e o ciclo segue; quando o orçamento ganhar
        itens, a execução seguinte cria o documento normalmente — a decisão
        depende de existir cotação, não de já termos tentado.
        """
        total = total_do_payload(dados)
        if total > 0:
            return
        linhas = len(dados.get("DocumentLines") or ())
        motivo = "sem itens" if linhas == 0 else f"{linhas} item(ns) somando {total}"
        aviso = (
            f"{tipo.rotulo.capitalize()} não criada: o orçamento está {motivo}, "
            f"e o SAP recusa documento sem valor."
        )
        logger.info("Orçamento %s: %s", orcnum, aviso)
        self._tracking.registrar_evento(orcnum, tipo=TipoEvento.DECISAO, mensagem=aviso)
        raise DocumentoSemValor(aviso)

    # ------------------------------------------------------------- cotação

    def _executar_cotacao(
        self,
        acao: Acao,
        estado: EstadoIntegracao,
        orcamento: OrcamentoWbc,
        oportunidade: dict[str, Any],
        snapshot_id: int | None,
    ) -> tuple[dict[str, Any] | None, TipoDocumento | None]:
        """Criação e manutenção da **cotação**."""
        dados = self._payload_cotacao(orcamento, estado, oportunidade, snapshot_id)
        tipo = TipoDocumento.COTACAO
        self._exigir_valor(tipo, orcamento.orcnum, dados)

        if acao is Acao.CRIAR_COTACAO:
            return self._documentos.criar(tipo, dados), tipo
        if acao is Acao.CANCELAR_E_RECRIAR_COTACAO:
            return (
                self._documentos.cancelar_e_recriar(tipo, orcamento.orcnum, dados),
                tipo,
            )
        if acao is Acao.ATUALIZAR_COTACAO:
            doc_entry = self._documentos.doc_entry(tipo, orcamento.orcnum)
            if doc_entry is not None:
                self._documentos.atualizar(tipo, doc_entry, dados)
                return self._conferir_cotacao(orcamento.orcnum, doc_entry, dados)
            return None, None
        return None, None

    def _conferir_cotacao(
        self, orcnum: str, doc_entry: int, dados: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, TipoDocumento | None]:
        """Relê a cotação atualizada e, se divergir do orçamento, refaz.

        **Um `204 No Content` não prova que as linhas mudaram.** Nas cotações
        `00125616` e `00125577` o `PATCH` foi aceito, o cabeçalho gravou, e as
        linhas continuaram como estavam — uma delas a preço zero, deixando o
        documento R$ 1.604,38 e R$ 39.802,60 abaixo do orçamento. A integração
        registrou sucesso e o erro chegou ao usuário pelo documento, não pelo
        painel. Ver `DECISOES.md`.

        Cancelar e recriar é o caminho que **comprovadamente** funciona: foi o
        que o usuário fez à mão no `00125616`, e a cotação nova saiu com os dois
        valores certos. Só vale para a cotação: o pedido tem guardas próprias
        (`U_INO_Congelado`, `DocStatus`) e recriá-lo tem outro peso.
        """
        esperado = total_do_payload(dados)
        try:
            gravado = self._documentos.total_das_linhas(TipoDocumento.COTACAO, doc_entry)
        except Exception as exc:  # noqa: BLE001 — a conferência não derruba o ciclo
            logger.warning(
                "Orçamento %s: não foi possível reler a cotação %s para conferir "
                "(%s: %s). A atualização foi mantida como está.",
                orcnum,
                doc_entry,
                type(exc).__name__,
                exc,
            )
            return None, None

        if abs(gravado - esperado) <= TOLERANCIA_DE_TOTAL:
            return None, None

        logger.warning(
            "Orçamento %s: a cotação %s ficou em %s depois da atualização, e o "
            "orçamento soma %s. O SAP aceitou o PATCH sem trocar as linhas — "
            "cancelando e recriando a cotação.",
            orcnum,
            doc_entry,
            gravado,
            esperado,
        )
        self._tracking.registrar_evento(
            orcnum,
            regra="cotacao_refeita_apos_divergencia",
            mensagem=(
                f"Atualização aceita pelo SAP mas sem efeito nas linhas: documento "
                f"ficou em {gravado} contra {esperado} do orçamento. Cotação "
                f"cancelada e recriada."
            ),
        )
        return (
            self._documentos.cancelar_e_recriar(TipoDocumento.COTACAO, orcnum, dados),
            TipoDocumento.COTACAO,
        )

    def _payload_cotacao(
        self,
        orcamento: OrcamentoWbc,
        estado: EstadoIntegracao,
        oportunidade: dict[str, Any] | None = None,
        snapshot_id: int | None = None,
    ) -> dict[str, Any]:
        """Corpo da cotação: cabeçalho de `domain.cotacao` mais as linhas."""
        payload = regras_cotacao.montar_payload(
            orcamento,
            # A cotação nunca muda de parceiro: no legado, `CriaCotacao` recebe
            # o `CardCode` da oportunidade, e `PNNew` só aparece no pedido.
            parceiro=estado.parceiro_atual,
            filial=self._filial,
            snapshot_id=snapshot_id,
            oportunidade=oportunidade,
        )
        linhas = self._montar_linhas(orcamento, regras_cotacao.linhas)
        if linhas:
            payload["DocumentLines"] = linhas
        return payload

    # -------------------------------------------------------------- pedido

    def _executar_pedido(
        self,
        acao: Acao,
        estado: EstadoIntegracao,
        orcamento: OrcamentoWbc,
        oportunidade: dict[str, Any],
        snapshot_id: int | None,
    ) -> tuple[dict[str, Any] | None, TipoDocumento | None]:
        """Criação e manutenção do **pedido de venda**."""
        dados = self._payload_pedido(orcamento, estado, oportunidade, snapshot_id)
        tipo = TipoDocumento.PEDIDO
        self._exigir_valor(tipo, orcamento.orcnum, dados)

        if acao is Acao.CRIAR_PEDIDO:
            # Pedido nascendo já no `PN_Correc` (`U_INO_Update = 'Y'` antes de
            # existir pedido): a mesma guarda `ChecaPN` da troca. Fora desse
            # caso `_parceiro_existe` devolve True sem ir à rede.
            if not self._parceiro_existe(estado, orcamento.orcnum):
                return None, None
            return self._documentos.criar(tipo, dados), tipo
        if acao is Acao.CANCELAR_E_RECRIAR_PEDIDO:
            # `cancelar_e_recriar` cancela **antes** de criar. Se o parceiro
            # corrigido não existir no SAP, o orçamento fica sem pedido nenhum
            # — pior que o estado inicial, e irreversível. O legado protegia
            # isso com `ChecaPN`, e a proteção vinha antes de tudo.
            if not self._parceiro_existe(estado, orcamento.orcnum):
                return None, None
            # O payload já prioriza o PN novo quando há troca de parceiro.
            return (
                self._documentos.cancelar_e_recriar(tipo, orcamento.orcnum, dados),
                tipo,
            )
        if acao is Acao.ATUALIZAR_PEDIDO:
            doc_entry = self._documentos.doc_entry(tipo, orcamento.orcnum)
            if doc_entry is not None:
                self._documentos.atualizar(
                    tipo, doc_entry, self._respeitar_congelamento(orcamento.orcnum, dados)
                )
            return None, None
        return None, None

    def _respeitar_congelamento(self, orcnum: str, dados: dict[str, Any]) -> dict[str, Any]:
        """`U_INO_Congelado = 'Y'` no pedido: manda o cabeçalho, não as linhas.

        Atualizar um pedido, aqui como no legado, **refaz as linhas**: elas são
        apagadas e reconstruídas a partir do orçamento do WBC
        (`ServiceProcess.cs:600-672`). Qualquer edição manual — item trocado,
        quantidade ajustada, texto adicional — some junto. O campo existe para
        impedir exatamente isso.

        Protege **só as linhas**, e essa parte importa: no legado o cabeçalho é
        atribuído antes do `if (Congelado != "Y")` e o `Update()` vem depois
        dele, então um pedido congelado continua recebendo o
        `U_INO_VERSAOWBC` novo. Não é detalhe cosmético — é o que impede o
        orçamento de ser escolhido de novo a cada ciclo: sem o carimbo, a
        revisão do WBC seguiria eternamente mais nova que a do pedido, e o
        ciclo tentaria atualizar para sempre, consumindo o teto de escrita.

        Em produção **os 2.581 pedidos vigentes estão em `'Y'`** — 2.461
        fechados e 120 abertos. Ou seja: na prática, atualização de pedido
        nunca refaz linha. Sem esta guarda, a integração destruiria a edição
        manual dos 120 abertos no primeiro ciclo que os alcançasse.
        """
        if not self._documentos.esta_congelado(TipoDocumento.PEDIDO, orcnum):
            return dados
        preservado = {k: v for k, v in dados.items() if k != "DocumentLines"}
        logger.info(
            "Pedido do orçamento %s está congelado (U_INO_Congelado = 'Y'): "
            "cabeçalho atualizado, %d linha(s) preservadas como estão.",
            orcnum,
            len(dados.get("DocumentLines") or ()),
        )
        return preservado

    def _payload_pedido(
        self,
        orcamento: OrcamentoWbc,
        estado: EstadoIntegracao,
        oportunidade: dict[str, Any] | None = None,
        snapshot_id: int | None = None,
    ) -> dict[str, Any]:
        """Corpo do pedido: cabeçalho de `domain.pedido` mais as linhas."""
        payload = regras_pedido.montar_payload(
            orcamento,
            parceiro=estado.parceiro_do_pedido,
            filial=self._filial,
            snapshot_id=snapshot_id,
            oportunidade=oportunidade,
            # Vale também para o pedido que nasce no `PN_Correc`: o contato da
            # oportunidade pertence ao parceiro antigo, e o SAP recusa contato
            # de outro parceiro (`-5002 Invalid contact person code`).
            trocando_de_parceiro=estado.pedido_no_parceiro_corrigido,
        )
        # O peso vai só no pedido — ver `domain.linhas`. A leitura é uma
        # consulta extra ao WBC, feita apenas nas ações que montam um pedido:
        # não é a cada avaliação da janela, é a cada pedido de verdade.
        pesos = self._pesos_do_orcamento(orcamento)
        linhas = self._montar_linhas(
            orcamento,
            lambda orc, de_para: regras_pedido.linhas(
                orc, de_para, pesos=pesos, fator_de_embarque=self._fator_de_embarque
            ),
        )
        if linhas:
            payload["DocumentLines"] = linhas
        return payload

    def _pesos_do_orcamento(self, orcamento: OrcamentoWbc) -> dict[int, Decimal]:
        """Peso por `ORCITM`, e um aviso para cada item que ficou sem.

        A falta de peso não impede o pedido — o SAP mantém o do cadastro do item
        — mas precisa aparecer. Entre os orçamentos que chegam a virar pedido a
        árvore cobre 314 de 316 linhas; quando um item cai nas duas restantes, é
        melhor que alguém saiba disso pelo log do que descubra pela expedição.
        """
        pesos = self._wbc.pesos_por_item(orcamento.orcnum)
        sem_peso = [item.orcitm for item in orcamento.itens if item.orcitm not in pesos]
        if sem_peso:
            logger.warning(
                "%s: sem peso na árvore para o(s) item(ns) %s — o SAP manterá o peso "
                "do cadastro nessas linhas.",
                orcamento.orcnum,
                ", ".join(str(i) for i in sem_peso),
            )
        return pesos

    def _montar_linhas(
        self,
        orcamento: OrcamentoWbc,
        resolver: Callable[[OrcamentoWbc, dict[str, Any]], ResultadoLinhas],
    ) -> list[dict[str, Any]]:
        """Resolve as linhas do documento e registra o que precisou ser suprido.

        Os avisos **não** ficam só no log: vão para o acompanhamento, porque um
        grupo fora do de-para significa que o documento foi criado com um item
        que ninguém escolheu conscientemente. Quem olha o dashboard precisa ver
        isso sem ter de abrir o log do servidor.
        """
        if self._grupo_produtos is None:
            logger.warning(
                "De-para de grupos indisponível: %s seguirá sem DocumentLines.",
                orcamento.orcnum,
            )
            return []

        resultado = resolver(orcamento, self._grupo_produtos.carregar())

        for aviso in resultado.avisos:
            logger.warning("%s: %s", orcamento.orcnum, aviso)
            self._tracking.registrar_evento(orcamento.orcnum, tipo=TipoEvento.ERRO, mensagem=aviso)

        if resultado.vazio and orcamento.itens:
            # Itens existem no WBC mas nenhuma linha sobreviveu. Deixar seguir
            # produziria um documento com total zero — recusado pelo SAP com uma
            # mensagem que não diz nada sobre a causa real.
            logger.error(
                "%s: %d item(ns) no WBC e nenhuma linha montada para o SAP.",
                orcamento.orcnum,
                len(orcamento.itens),
            )

        return list(resultado.linhas)

    def _gravar_snapshot_orcdetalhe(self, orcamento: OrcamentoWbc) -> int | None:
        """Grava o snapshot do OrcDetalhe e devolve o `DocEntry` criado.

        **É gravado antes dos documentos, não depois** — e a ordem não é
        indiferente: o `DocEntry` deste snapshot é o que vai no UDF
        `U_INO_ORCAMENTO` da cotação/pedido, criando o vínculo do documento de
        volta para o retrato do orçamento que o originou. Sem o snapshot em
        mãos, não há o que gravar nesse campo.

        O legado faz o mesmo, na mesma ordem (`ServiceProcess.cs:~967`), só que
        recupera o `DocEntry` com `SELECT max("DocEntry") FROM "@INO_ORCAM"` —
        sujeito a devolver o registro de outra execução concorrente. Aqui o
        `DocEntry` vem da própria resposta do POST, que é o único valor que
        certamente corresponde ao que acabou de ser criado.

        Continua sendo **auxiliar**: se falhar, o documento é criado assim
        mesmo, com `U_INO_ORCAMENTO` vazio — que é exatamente o que o legado
        faz quando não tem o valor (`DocEntryValdixonTable == "" ? "" : ...`).
        Perder o histórico é ruim; perder a cotação é pior.
        """
        try:
            criado = self._orcdetalhe.criar_snapshot(montar_payload_orcdetalhe(orcamento))
            return _inteiro((criado or {}).get("DocEntry"))
        except Exception as exc:  # noqa: BLE001 - o snapshot é auxiliar
            # O snapshot é registro histórico: falhar nele não pode desfazer os
            # documentos já criados no SAP, então vira aviso, não erro fatal.
            logger.warning(
                "Não foi possível gravar o snapshot do OrcDetalhe para %s: %s",
                orcamento.orcnum,
                exc,
            )
            self._tracking.registrar_evento(
                orcamento.orcnum,
                tipo=TipoEvento.ERRO,
                mensagem=f"Snapshot do OrcDetalhe não gravado: {exc}",
            )
            return None

    def _registrar_documento(
        self, orcnum: str, tipo: TipoDocumento, documento: dict[str, Any]
    ) -> None:
        self._tracking.registrar_documento(
            orcnum,
            tipo="cotacao" if tipo is TipoDocumento.COTACAO else "pedido",
            doc_entry=_inteiro(documento.get("DocEntry")),
            doc_num=_inteiro(documento.get("DocNum")),
            valor=_decimal(documento.get("DocTotal")),
        )

    @staticmethod
    def _status_final(executadas: list[Acao]) -> StatusIntegracao | None:
        """Status a exibir, considerando a ação mais significativa executada.

        A ordem da tabela importa: encerramento vence pedido, que vence cotação
        — senão um orçamento encerrado apareceria no dashboard como "cotação
        atualizada", que é o que aconteceu por último mas não é o que importa.
        """
        for acao in (
            Acao.MARCAR_OPORTUNIDADE_PERDIDA,
            Acao.CANCELAR_E_RECRIAR_PEDIDO,
            Acao.CRIAR_PEDIDO,
            Acao.ATUALIZAR_PEDIDO,
            Acao.CANCELAR_E_RECRIAR_COTACAO,
            Acao.CRIAR_COTACAO,
            Acao.ATUALIZAR_COTACAO,
        ):
            if acao in executadas:
                return STATUS_POR_ACAO[acao]
        return None


def _data(valor: Any) -> date | None:
    """`OOPR.OpenDate` vem do HANA como `datetime`; o acompanhamento guarda data.

    É a data que define a janela do worker — ver `Acompanhamento.data_abertura`.
    """
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return None


def _inteiro(valor: Any) -> int | None:
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _decimal(valor: Any) -> Decimal | None:
    try:
        return Decimal(str(valor)) if valor is not None else None
    except Exception:  # noqa: BLE001
        return None


@dataclass(frozen=True, slots=True)
class _OrcamentoResumido:
    """O mínimo que `montar_estado` precisa do WBC: número, situação e revisão.

    Existe para que a decisão possa ser tomada sem carregar as linhas do
    orçamento. Não é um `OrcamentoWbc` incompleto disfarçado: quem precisa das
    linhas carrega o objeto de verdade, e o tipo diferente impede que alguém
    passe este adiante por engano.
    """

    orcnum: str
    sitcode: int
    revisao: str


def fonte_de_documentos(oportunidade: dict[str, Any], repositorio: Any) -> Any:
    """De onde vêm cotação e pedido: da própria leitura ou do Service Layer.

    Quando a oportunidade chega do HANA, ela já traz os documentos resolvidos —
    e perguntar de novo ao SAP seriam quatro requisições por orçamento, para
    obter a mesma resposta. Quando chega do Service Layer (ou de um teste), o
    bloco não existe e vale o repositório de sempre.

    A escolha é por **presença de dado**, não por configuração: não há um
    interruptor que possa ficar no estado errado.
    """
    if oportunidade.get("documentos") is not None:
        return DocumentosJaLidos.da_oportunidade(oportunidade)
    return repositorio


def montar_estado(
    orcamento: OrcamentoWbc,
    oportunidade: dict[str, Any],
    documentos: RepositorioDocumentosVenda,
) -> EstadoIntegracao:
    """Reúne, num único retrato, o que o WBC diz e o que o SAP já tem.

    É função de módulo, e não método, de propósito: montar esse retrato é
    **somente leitura**, e a prévia (`wbcpython pendentes`) precisa dele sem
    instanciar o processador — que exige tracking e é capaz de escrever. Assim
    a prévia e o ciclo real enxergam exatamente o mesmo estado, sem risco de
    as duas versões divergirem com o tempo.
    """
    orcnum = orcamento.orcnum
    return EstadoIntegracao(
        orcamento=orcnum,
        sitcode_wbc=orcamento.sitcode,
        revisao_wbc=orcamento.revisao,
        sitcode_sap=str(oportunidade.get("U_INO_StatusWBC") or ""),
        status_oportunidade=str(oportunidade.get("Status") or ""),
        tem_cotacao=documentos.existe(TipoDocumento.COTACAO, orcnum),
        tem_pedido=documentos.existe(TipoDocumento.PEDIDO, orcnum),
        pedido_fechado=documentos.esta_fechado(TipoDocumento.PEDIDO, orcnum),
        revisao_cotacao_sap=documentos.revisao_aplicada(TipoDocumento.COTACAO, orcnum),
        revisao_pedido_sap=documentos.revisao_aplicada(TipoDocumento.PEDIDO, orcnum),
        parceiro_pedido_sap=documentos.parceiro_aplicado(TipoDocumento.PEDIDO, orcnum),
        alterado=str(oportunidade.get("U_INO_Update") or "N").upper() == "Y",
        parceiro_atual=str(oportunidade.get("CardCode") or ""),
        parceiro_novo=str(oportunidade.get("U_INO_PN_Correc") or ""),
    )
