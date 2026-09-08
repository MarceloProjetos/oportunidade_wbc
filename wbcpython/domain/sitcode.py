"""Máquina de estados da integração — decisão a partir do SitCode.

Este módulo é o coração da regra de negócio. Ele é **puro**: recebe um retrato
do estado (o que o WBC diz, o que o SAP já tem) e devolve a lista de ações a
executar. Não faz I/O, não conhece Service Layer nem banco — e por isso é
inteiramente testável sem ambiente.

Toda a lógica foi extraída do encadeamento de `if/else` do `Main` do
`WBCServConsole` legado (`Program.cs`), com as consultas de apoio
(`Querys.resx`) usadas para descobrir o significado real de cada campo.

--------------------------------------------------------------------------
Campos do legado e seus equivalentes aqui
--------------------------------------------------------------------------
| Legado                                   | Aqui                          |
|------------------------------------------|-------------------------------|
| `oRs.Fields.Item(0)` = `U_ORCNUM_WBC`     | `EstadoIntegracao.orcamento`  |
| `oRs.Fields.Item(1)` = `CardCode`         | `parceiro_atual`              |
| `oRs.Fields.Item(2)` = `OpprId`           | `oportunidade`                |
| `oRs.Fields.Item(3)` = `U_INO_StatusWBC`  | `sitcode_sap`                 |
| `oRs.Fields.Item(4)` = `U_INO_Update`     | `alterado` (desvio p/ troca)  |
| `oRs.Fields.Item(5)` = `U_INO_PN_Correc`  | `parceiro_novo`               |
| `GetIntIdWBCOrcamentos` (COUNT em OQUT)   | `tem_cotacao`                 |
| `GetIdOrcamentosPedido` (COUNT em ORDR)   | `tem_pedido`                  |
| `GetStatusAtualCot` (`U_INO_VERSAOWBC`)   | `revisao_cotacao_sap`         |
| `item.SitCode` (WBC `SITCOD`)             | `sitcode_wbc`                 |
| `item.versao` (WBC `REVISAO`)             | `revisao_wbc`                 |

Atenção a duas leituras que eram fáceis de errar: `GetIntIdWBCOrcamentos` e
`GetIdOrcamentosPedido` são `SELECT COUNT(*)` — o `"0"` comparado no legado
significa "não existe cotação/pedido não-cancelado", e **não** um DocEntry.

--------------------------------------------------------------------------
Defeitos do legado tratados aqui (ver DEFEITOS_LEGADO.md)
--------------------------------------------------------------------------
1. Duas condições do legado são tautologias — `(x != 30 || x != 60 || ...)` é
   sempre verdadeiro, porque um número não pode ser diferente de todos e igual
   a algum ao mesmo tempo. Aqui está implementado o comportamento **efetivo**
   (o que a tautologia de fato produz), com o ramo nomeado e documentado.
2. Exclusões de oportunidades específicas por número, escritas no código
   (`OportunidadeId != "00118376"`), **não foram portadas**: são exceções
   pontuais de dados, não regra de negócio.
3. No legado, as ações dentro do ramo final se acumulam (são `if` sequenciais,
   não `else if`). Por isso a decisão aqui devolve uma **lista ordenada** de
   ações, não uma ação única.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from wbcpython.domain.revisao import revisao_wbc_e_mais_nova

# SitCode abaixo ou igual a este valor não é processado pela integração.
# No legado: `if (oRsOther.RecordCount > 0 && item.SitCode > 10)`.
SITCODE_MINIMO = 10

SITCODE_ORCAMENTO_EMITIDO = 30
SITCODES_REVISAO = frozenset({40, 55})
SITCODE_PEDIDO_FECHADO = 60
SITCODES_ENCERRAMENTO = frozenset({70, 90, 99})

#: Valores de `OOPR."Status"` que significam oportunidade fechada — perdida
#: ('L') ou vendida ('W'). Aberta é 'O'.
STATUS_OPORTUNIDADE_FECHADA = frozenset({"L", "W"})


class Acao(Enum):
    """Ação de integração a executar no SAP."""

    CRIAR_COTACAO = "criar_cotacao"
    ATUALIZAR_COTACAO = "atualizar_cotacao"
    CANCELAR_E_RECRIAR_COTACAO = "cancelar_e_recriar_cotacao"
    CRIAR_PEDIDO = "criar_pedido"
    ATUALIZAR_PEDIDO = "atualizar_pedido"
    CANCELAR_E_RECRIAR_PEDIDO = "cancelar_e_recriar_pedido"
    #: Grava `Status = 'sos_Missed'` e o SitCode na oportunidade. **Não
    #: cancela** a oportunidade: no SAP não há cancelamento de oportunidade,
    #: só a mudança de status.
    #:
    #: Chamava-se `FECHAR_CANCELAR_OPORTUNIDADE`, herdado do
    #: `cancelaFechaOportunidade` do legado — que também só mudava o status. O
    #: nome dizia "cancelar" e o código não cancelava, e isso aparecia no painel
    #: e no histórico de cada oportunidade. Renomeado na confirmação da regra
    #: com o usuário, sem mudança de comportamento.
    MARCAR_OPORTUNIDADE_PERDIDA = "marcar_oportunidade_perdida"
    CANCELAR_COTACAO_NO_ENCERRAMENTO = "cancelar_cotacao_no_encerramento"
    ATUALIZAR_STATUS_OPORTUNIDADE = "atualizar_status_oportunidade"
    VINCULAR_DOCUMENTO_A_OPORTUNIDADE = "vincular_documento_a_oportunidade"


@dataclass(frozen=True, slots=True)
class EstadoIntegracao:
    """Retrato do que o WBC diz e do que o SAP já tem, para um orçamento."""

    orcamento: str
    sitcode_wbc: int

    # Lado SAP
    sitcode_sap: str = ""
    #: `Status` da OOPR: 'O' aberta, 'L' perdida, 'W' vendida. É a **única**
    #: prova de que o encerramento aconteceu. O `sitcode_sap` não serve para
    #: isso: `atualizar_status` o espelha a cada ciclo, muito antes de qualquer
    #: encerramento — e enquanto o `encerrar()` mandava um `sos_Lost`
    #: inexistente, todas as oportunidades tinham o SitCode espelhado e
    #: continuavam abertas.
    status_oportunidade: str = ""
    tem_cotacao: bool = False
    tem_pedido: bool = False
    #: `DocStatus = 'C'` no pedido vigente: o SAP não aceita alteração nem
    #: cancelamento. Ver `pedido_fechado_no_sap`.
    pedido_fechado: bool = False
    revisao_cotacao_sap: str = ""
    revisao_pedido_sap: str = ""
    #: `CardCode` do pedido vigente no SAP. É o equivalente do `ChecaPNPedido`
    #: legado: comparado com `parceiro_novo`, diz se a troca já foi aplicada.
    parceiro_pedido_sap: str = ""

    # Revisão corrente no WBC
    revisao_wbc: str = ""

    #: `U_INO_Update = 'Y'`: o operador pediu a troca de PN do pedido.
    #:
    #: **Onde o campo mora:** é um UDF da *oportunidade no SAP*
    #: (`SalesOpportunities` no Service Layer, `OOPR` no HANA) — não um campo do
    #: WBC. A documentação daqui já disse o contrário, e o usuário corrigiu.
    #: A integração o lê junto com o resto da oportunidade, na mesma consulta.
    #:
    #: **É um desvio entre dois caminhos, e não uma autorização para mexer.**
    #: No legado, dentro do SitCode 60 com pedido existente: `!= 'Y'` compara a
    #: revisão carimbada no pedido e atualiza (`Program.cs:249`); `== 'Y'`
    #: desvia para cancelar e recriar no PN novo (`Program.cs:280`). Congelar o
    #: pedido em `'N'` — que esta integração chegou a fazer — inverte isso e
    #: deixa a atualização por revisão sem caminho nenhum.
    #:
    #: Quem congela o pedido é a revisão carimbada nele (`U_INO_VERSAOWBC` no
    #: `ORDR`, aqui `revisao_pedido_sap`) e o `DocStatus` fechado.
    #:
    #: A troca em si é decidida por `troca_de_parceiro_pendente`, que olha o
    #: pedido; a exigência de `'Y'` vem de `pedido_corrigido_a_mao`, que roda
    #: antes. Exigir `alterado` *dentro* da troca já foi um erro, e fez a troca
    #: deixar de acontecer.
    #:
    #: **A integração escreve neste campo, num caso só:** baixa para `'N'` ao
    #: vincular o **pedido**, no mesmo PATCH do estágio — ver
    #: `service_layer/oportunidades.py`. É o que o legado faz
    #: (`ServiceProcess.cs:198` e `:210`, ambas em `AddPedidoOportunidade`), e
    #: fecha o ciclo pedido pelo negócio: o operador marca `'Y'`, o pedido novo
    #: é criado e vinculado, a marca volta a `'N'`. O que a integração **não**
    #: faz é baixar a marca como passo avulso depois da troca — ver
    #: `processar.py`.
    alterado: bool = False

    # Troca de parceiro de negócios (PN)
    parceiro_atual: str = ""
    parceiro_novo: str = ""

    @property
    def pedido_fechado_no_sap(self) -> bool:
        """Pedido fechado: nada a escrever nele, e nada a cancelar.

        Um pedido fechado (`DocStatus = 'C'`) já foi entregue ou faturado. O SAP
        recusa alteração e recusa cancelamento — tentar não é só inútil, é um
        erro por ciclo, para sempre, no histórico de cada orçamento nessa
        situação.

        Reportado pelo usuário no orçamento `00124268`: pedido `83988`, fechado,
        com revisão `'A'` no SAP e revisão mais nova no WBC. A regra caía em
        `atualiza_pedido` e o SAP recusava a gravação.

        O tamanho disso surpreende: em produção, **2.452 dos 2.568** pedidos
        vigentes estão fechados. É o estado normal de um pedido que seguiu seu
        curso, não a exceção — e a integração não tinha como saber, porque
        `DocStatus` não era lido.

        `tem_pedido` continua valendo: existe pedido, então não se cria outro.
        "Fechado" não é "inexistente".
        """
        return self.tem_pedido and self.pedido_fechado

    @property
    def pedido_corrigido_a_mao(self) -> bool:
        """`U_INO_PN_Correc` preenchido com `U_INO_Update = 'N'`: não mexer.

        Segundo o negócio, essa combinação significa que **o pedido já está
        correto, em nome de outro PN**, e que ninguém pediu alteração de
        valores. A integração não tem o que fazer ali — nem trocar o parceiro,
        nem atualizar o pedido.

        A segunda metade é a que mordeu de verdade. O orçamento `00124045` tem
        o pedido vigente `84022` no `PN_Correc` (`C011608`), com o pedido antigo
        `83949` já cancelado no PN da oportunidade: a correção **está feita**.
        Como `troca_de_parceiro_pendente` é falsa nesse ponto,
        `parceiro_do_pedido` devolvia o `CardCode` da *oportunidade*
        (`C008483`), e `campos_comuns` envia `CardCode` **sempre** — então
        `atualizar_pedido` desfazia a correção, devolvendo o pedido ao parceiro
        errado.

        Medido em produção: 592 oportunidades têm essa combinação. Das que a
        regra antiga ainda consideraria com troca pendente, **todas as 4** são
        casos antigos resolvidos à mão, com o pedido vivo num terceiro parceiro
        — nenhuma é trabalho legítimo. Ver `DECISOES.md`.

        Consequência que precisa ficar dita: com esta guarda, a troca de PN só
        acontece com `U_INO_Update = 'Y'`. Se o `PN_Correc` for preenchido na
        oportunidade sem que `Update` vá a `'Y'`, a troca não acontece — os dois
        são UDFs da oportunidade no SAP, e quem os preenche é de fora desta
        integração. Pergunta em aberto (`RETOMADA.md`).

        `tem_pedido` faz parte da pergunta, como nas duas propriedades irmãs:
        sem pedido não há nada corrigido à mão para preservar. A primeira versão
        não o verificava e devolvia `True` com pedido nenhum. Não chegou a
        causar defeito porque o `_decidir_pedido` já tinha saído antes, na
        criação — mas isso é ordem de statement, não invariante da propriedade,
        e quem reusasse a propriedade fora dali receberia a resposta errada.
        """
        return self.tem_pedido and bool(self.parceiro_novo) and not self.alterado

    @property
    def troca_de_parceiro_pendente(self) -> bool:
        """`U_INO_PN_Correc` preenchido e o pedido ainda não está nele.

        **A guarda é o pedido, não uma marca.** É o `ChecaPNPedido` do legado
        (`SELECT count(*) FROM ORDR WHERE CardCode = PNNew AND U_INO_COTWBC =
        orçamento AND CANCELED = 'N'`): havendo pedido não cancelado já no
        parceiro novo, não há o que fazer.

        Isso torna a regra idempotente por construção, e é o que permite agir
        **sempre** que o `PN_Correc` está preenchido, sem depender de ninguém
        baixar uma marca depois. O campo fica preenchido para sempre — 578
        oportunidades em homologação o têm — e isso é inofensivo: o pedido
        dessas já está no parceiro novo.

        `U_INO_Update` **não** entra nesta propriedade: aqui a pergunta é só
        "o pedido já está no parceiro novo?". Quem usa o campo é
        `pedido_corrigido_a_mao`, uma guarda que roda **antes** desta no
        `_decidir_pedido` — e a diferença entre as duas importa. Exigir
        `alterado` *para trocar* foi um engano meu, corrigido depois de reler
        `Program.cs:280-300`; o que o negócio esclareceu depois é o contrário
        disso: `Update = 'N'` com `PN_Correc` preenchido é sinal positivo de que
        alguém **já resolveu** o caso à mão.
        """
        if not self.parceiro_novo or not self.tem_pedido:
            return False
        return self.parceiro_pedido_sap != self.parceiro_novo

    @property
    def parceiro_do_pedido(self) -> str:
        """O parceiro que o **pedido** deve levar.

        Só o `PN_Correc` quando a troca está pendente; fora disso, o da
        oportunidade — como no legado, em que `CriaPedido` recebe
        `oRs.Fields.Item(1)` (o `CardCode` da oportunidade) no caminho normal e
        `PNNew` apenas no ramo da troca.
        """
        return self.parceiro_novo if self.troca_de_parceiro_pendente else self.parceiro_atual

    @property
    def status_ja_espelhado(self) -> bool:
        """O SAP já registra o SitCode corrente do WBC.

        `U_INO_StatusWBC` é uma cópia do SitCode. Quando as duas pontas já
        concordam, regravar o mesmo valor é uma escrita sem efeito — e não é
        barata: consome o teto de escrita do ciclo, gera evento no histórico de
        cada oportunidade e faz o resumo anunciar trabalho que não existiu. Em
        homologação eram 154 das 160 ações de espelhamento.

        A comparação é textual porque o campo é texto no SAP — ver
        `atualizar_status`, que grava `str(sitcode)` de propósito.
        """
        return self.sitcode_sap.strip() == str(self.sitcode_wbc)

    @property
    def oportunidade_encerrada(self) -> bool:
        """A oportunidade já está fechada no SAP (perdida ou vendida)."""
        return self.status_oportunidade.strip().upper() in STATUS_OPORTUNIDADE_FECHADA

    @property
    def processavel(self) -> bool:
        """A integração só considera orçamentos acima do SitCode mínimo."""
        return self.sitcode_wbc > SITCODE_MINIMO


@dataclass(frozen=True, slots=True)
class Decisao:
    """Resultado da máquina de estados.

    `motivos` acompanha `acoes` para que o dashboard e o log expliquem *por que*
    cada ação foi decidida — algo que o sistema legado não registrava.
    """

    acoes: tuple[Acao, ...] = ()
    motivos: tuple[str, ...] = ()
    regra: str = ""

    @property
    def tem_acao(self) -> bool:
        return bool(self.acoes)


@dataclass
class _Acumulador:
    acoes: list[Acao] = field(default_factory=list)
    motivos: list[str] = field(default_factory=list)

    def registrar(self, motivo: str, *acoes: Acao) -> None:
        self.acoes.extend(acoes)
        self.motivos.append(motivo)


def decidir(estado: EstadoIntegracao) -> Decisao:
    """Decide as ações de integração para um orçamento.

    Reproduz a ordem de avaliação do `Main` legado, que é significativa: os
    ramos são mutuamente exclusivos até o último, e dentro dele as ações se
    acumulam.
    """
    if not estado.processavel:
        return Decisao(
            regra="sitcode_abaixo_do_minimo",
            motivos=(
                (
                    f"SitCode {estado.sitcode_wbc} não é processado "
                    f"(mínimo: acima de {SITCODE_MINIMO})."
                ),
            ),
        )

    # --- Ramo 1: orçamento emitido para o cliente -------------------------
    if estado.sitcode_wbc == SITCODE_ORCAMENTO_EMITIDO:
        return _ramo_orcamento_emitido(estado)

    # --- Ramos 2 e 3: revisão de orçamento (SitCode 40/55) ----------------
    if estado.tem_cotacao and estado.sitcode_wbc in SITCODES_REVISAO:
        if estado.sitcode_sap == "30":
            return Decisao(
                regra="revisao_sobre_cotacao_emitida",
                acoes=(Acao.ATUALIZAR_COTACAO, Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE),
                motivos=(
                    (
                        f"WBC em revisão (SitCode {estado.sitcode_wbc}) e SAP ainda em 30: "
                        f"atualiza a cotação existente."
                    ),
                ),
            )
        if estado.sitcode_sap == "40":
            return _ramo_revisao_ja_registrada(estado)
        # Demais valores de sitcode_sap caem no ramo final.

    # --- Ramo 3.5: orçamento encerrado no WBC -----------------------------
    # Precisa vir **antes** do ramo "não há cotação", e a ordem é consequência
    # direta de passarmos a cancelar a cotação no encerramento.
    #
    # No legado a ordem era indiferente: a cotação nunca era cancelada, então um
    # orçamento encerrado sempre tinha `tem_cotacao=True` e jamais alcançava o
    # ramo de criação. Cancelando, `tem_cotacao` vira False na execução seguinte
    # — e o ramo 4 recriaria a cotação de um orçamento perdido, para cancelá-la
    # no ciclo seguinte, indefinidamente.
    if estado.sitcode_wbc in SITCODES_ENCERRAMENTO:
        return _ramo_pedido_e_encerramento(estado)

    # --- Ramo 4: não há cotação ------------------------------------------
    # No legado esta condição vinha acompanhada de uma tautologia
    # `(SitCode != 30 || != 60 || ...)`, que é sempre verdadeira; o efeito real
    # é simplesmente "não existe cotação".
    if not estado.tem_cotacao:
        return Decisao(
            regra="sem_cotacao_cria",
            acoes=(Acao.CRIAR_COTACAO, Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE),
            motivos=(
                (
                    f"Não existe cotação para o orçamento e o SitCode é "
                    f"{estado.sitcode_wbc}: cria a cotação."
                ),
            ),
        )

    # --- Ramo 5: cotação existe; trata pedido e encerramento --------------
    return _ramo_pedido_e_encerramento(estado)


def _ramo_orcamento_emitido(estado: EstadoIntegracao) -> Decisao:
    """SitCode 30 — orçamento emitido para o cliente."""
    if not estado.tem_cotacao:
        return Decisao(
            regra="emitido_sem_cotacao",
            acoes=(Acao.CRIAR_COTACAO, Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE),
            motivos=("SitCode 30 e nenhuma cotação no SAP: cria a cotação.",),
        )

    if estado.sitcode_sap in ("40", "55"):
        # O WBC voltou para 30 enquanto o SAP já registrava revisão: o
        # documento no SAP está desatualizado e precisa ser refeito.
        return Decisao(
            regra="emitido_apos_revisao_no_sap",
            acoes=(Acao.CANCELAR_E_RECRIAR_COTACAO, Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE),
            motivos=(
                (
                    f"WBC voltou ao SitCode 30 mas o SAP registra {estado.sitcode_sap}: "
                    f"cancela a cotação e recria."
                ),
            ),
        )

    return Decisao(
        regra="emitido_ja_sincronizado",
        motivos=("SitCode 30 com cotação já existente e SAP sincronizado: nada a fazer.",),
    )


def _ramo_revisao_ja_registrada(estado: EstadoIntegracao) -> Decisao:
    """SitCode 40/55 com o SAP já em 40 — decide pela comparação de revisão."""
    if revisao_wbc_e_mais_nova(estado.revisao_wbc, estado.revisao_cotacao_sap):
        return Decisao(
            regra="revisao_mais_nova_recria_cotacao",
            acoes=(Acao.CANCELAR_E_RECRIAR_COTACAO, Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE),
            motivos=(
                (
                    f"Revisão do WBC ({estado.revisao_wbc or '-'}) é mais nova que a do SAP "
                    f"({estado.revisao_cotacao_sap or '-'}): cancela e recria a cotação."
                ),
            ),
        )
    return Decisao(
        regra="revisao_congelada",
        motivos=(
            (
                f"Revisão do WBC ({estado.revisao_wbc or '-'}) não é mais nova que a do SAP "
                f"({estado.revisao_cotacao_sap or '-'}): documento congelado, nada a fazer."
            ),
        ),
    )


def _ramo_pedido_e_encerramento(estado: EstadoIntegracao) -> Decisao:
    """Ramo final do legado, onde as ações se acumulam."""
    acumulador = _Acumulador()
    regras: list[str] = []

    if estado.sitcode_wbc == SITCODE_PEDIDO_FECHADO:
        _decidir_pedido(estado, acumulador, regras)

    if estado.sitcode_wbc in SITCODES_ENCERRAMENTO:
        _decidir_encerramento(estado, acumulador, regras)

    # No legado, esta condição vinha com uma tautologia; o efeito real era
    # "existe cotação", e o status era reespelhado a cada passagem — inclusive
    # quando o SAP já tinha o valor. **Divergência deliberada:** aqui só espelha
    # quando as duas pontas discordam de fato.
    #
    # O legado podia se dar a esse luxo; nós não. Cada PATCH desses consome o
    # teto de escrita do ciclo, deixa um evento no histórico da oportunidade e
    # entra no resumo como trabalho feito. Na janela de homologação eram 154 de
    # 160 espelhamentos regravando o valor que já estava lá.
    #
    # O encerramento é excluído porque `encerrar()` grava `U_INO_StatusWBC`
    # junto com o `Status` — espelhar também seria o mesmo PATCH duas vezes.
    if (
        estado.tem_cotacao
        and not estado.status_ja_espelhado
        and Acao.MARCAR_OPORTUNIDADE_PERDIDA not in acumulador.acoes
    ):
        acumulador.registrar(
            f"Reflete o SitCode {estado.sitcode_wbc} no status da oportunidade no SAP "
            f"(o SAP registra '{estado.sitcode_sap or '-'}').",
            Acao.ATUALIZAR_STATUS_OPORTUNIDADE,
        )
        regras.append("espelha_status")

    return Decisao(
        acoes=tuple(acumulador.acoes),
        motivos=tuple(acumulador.motivos),
        regra="+".join(regras) if regras else "sem_acao",
    )


def _decidir_encerramento(
    estado: EstadoIntegracao,
    acumulador: _Acumulador,
    regras: list[str],
) -> None:
    """SitCode 70/90/99 — o orçamento foi perdido/cancelado no WBC.

    O que acontece com a **oportunidade**: ela é marcada como perdida
    (`Status = 'sos_Missed'`) e recebe o SitCode em `U_INO_StatusWBC`. Ela
    **não** é cancelada — no SAP não existe cancelar oportunidade, só mudar o
    status —, e isso é igual ao legado (`cancelaFechaOportunidade`,
    `ServiceProcess.cs:91-122`). Confirmado com o usuário.

    O que acontece com a **cotação** é a divergência, e está abaixo.

    **Divergência deliberada do legado.** O `cancelaFechaOportunidade` do
    `WBCServConsole` (`ServiceProcess.cs:91-122`) só gravava o
    `U_INO_StatusWBC` e punha a oportunidade em `sos_Missed`; nunca tocava nos
    documentos. O efeito em produção é mensurável: de 714 oportunidades
    encerradas, **686 (96%) continuam com a cotação aberta** — cotações que
    ninguém vai honrar, poluindo o pipeline de vendas do SAP.

    Aqui a cotação é cancelada junto com o encerramento, e **antes** dele: se o
    cancelamento falhar, a oportunidade não fica encerrada com um documento
    aberto pendurado, e o ciclo seguinte tenta de novo.

    Só a cotação. O pedido não é cancelado por dois motivos: encerramento com
    pedido existente é um estado que o WBC não deveria produzir (o pedido nasce
    no SitCode 60), e um pedido já pode ter movimentado estoque ou faturamento —
    cancelá-lo automaticamente é irreversível e caro. Havendo pedido, a cotação
    também é preservada, porque nesse caso ela foi convertida e o SAP recusaria
    o cancelamento.
    """
    if estado.tem_cotacao and not estado.tem_pedido:
        acumulador.registrar(
            f"SitCode {estado.sitcode_wbc}: cancela a cotação vinculada ao orçamento.",
            Acao.CANCELAR_COTACAO_NO_ENCERRAMENTO,
        )
        regras.append("cancela_cotacao_no_encerramento")

    if estado.oportunidade_encerrada:
        # A oportunidade já está fechada no SAP: marcar de novo é gravar o
        # mesmo valor. Sem esta guarda, toda execução repete o PATCH — ruído
        # permanente no histórico de cada oportunidade perdida da janela, e
        # teto de escrita consumido por trabalho que não existe.
        #
        # A condição **não** olha a cotação, e essa foi a correção: exigir
        # `not tem_cotacao` deixava passar todo encerramento que ainda tivesse
        # cotação vinculada. No `00123425` (Status `'L'`, SitCode espelhado,
        # cotação e pedido existentes) a oportunidade era remarcada como
        # perdida a cada ciclo — e a cotação nem chegava a ser cancelada,
        # porque há pedido. Nada acontecia, para sempre.
        #
        # O cancelamento da cotação, quando cabe, já foi registrado acima: a
        # guarda pula só a marcação. E se o `U_INO_StatusWBC` estiver
        # desatualizado, o espelhamento no fim de `_ramo_pedido_e_encerramento`
        # cuida disso — ele só é suprimido quando a marcação acontece.
        regras.append("encerramento_ja_aplicado")
        return

    acumulador.registrar(
        f"SitCode {estado.sitcode_wbc}: marca a oportunidade como perdida "
        f"(Status = 'sos_Missed'). A oportunidade **não** é cancelada.",
        Acao.MARCAR_OPORTUNIDADE_PERDIDA,
    )
    regras.append("marca_perdida")


def _decidir_pedido(
    estado: EstadoIntegracao,
    acumulador: _Acumulador,
    regras: list[str],
) -> None:
    """SitCode 60 — pedido fechado."""
    if not estado.tem_pedido:
        acumulador.registrar(
            "SitCode 60 e nenhum pedido no SAP: atualiza a cotação e cria o pedido.",
            Acao.ATUALIZAR_COTACAO,
            Acao.CRIAR_PEDIDO,
            Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE,
        )
        regras.append("cria_pedido")
        return

    # Pedido fechado no SAP: não aceita alteração nem cancelamento. Vem antes
    # de tudo porque é o fato mais duro — as outras guardas são regra de
    # negócio, esta é o SAP recusando a escrita.
    if estado.pedido_fechado_no_sap:
        # Uma troca de parceiro pedida sobre pedido fechado é pedido de negócio
        # que **nunca** poderá ser atendido: não dá para cancelar o pedido nem
        # refazê-lo. Congelar em silêncio deixaria esse caso indistinguível dos
        # 243 congelamentos benignos da janela; ele precisa de gente.
        if estado.troca_de_parceiro_pendente:
            acumulador.registrar(
                f"Troca de parceiro pedida ({estado.parceiro_pedido_sap or '-'} → "
                f"{estado.parceiro_novo}), mas o pedido está fechado no SAP: não é "
                f"possível cancelar nem refazer. Precisa de tratamento manual."
            )
            regras.append("troca_de_pn_impossivel_pedido_fechado")
            return
        acumulador.registrar("Pedido fechado no SAP: não aceita alteração nem cancelamento.")
        regras.append("pedido_fechado_no_sap")
        return

    # Antes de qualquer coisa sobre o pedido: `PN_Correc` preenchido com
    # `U_INO_Update = 'N'` significa que o pedido já está certo em nome de outro
    # parceiro, e que a troca pedida já foi concluída (a marca volta a `'N'` no
    # vínculo do pedido novo). Nada a fazer.
    #
    # A guarda precisa vir aqui, e não só no ramo da troca, porque o dano do
    # `00124045` veio pelo **outro** caminho: a correção já estava aplicada, a
    # troca não estava pendente, e era `atualizar_pedido` que reenviava o
    # `CardCode` da oportunidade e desfazia a correção.
    if estado.pedido_corrigido_a_mao:
        acumulador.registrar(
            f"Pedido já corrigido em nome de outro parceiro ({estado.parceiro_novo}) "
            f"e sem alteração pedida no WBC: nada a fazer no pedido."
        )
        regras.append("pedido_corrigido_a_mao")
        return

    # A troca de parceiro vem **antes** da comparação de revisão: refazer o
    # pedido já traz a revisão corrente junto, e atualizar um pedido que será
    # cancelado em seguida é trabalho jogado fora.
    if estado.troca_de_parceiro_pendente:
        acumulador.registrar(
            f"Parceiro corrigido no WBC ({estado.parceiro_pedido_sap or '-'} → "
            f"{estado.parceiro_novo}): cancela o pedido e recria com o novo PN.",
            Acao.CANCELAR_E_RECRIAR_PEDIDO,
            Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE,
        )
        regras.append("troca_de_pn")
        return

    # Quem congela o pedido é a **revisão carimbada nele**, `U_INO_VERSAOWBC`
    # no `ORDR` (`GetStatusAtualPedido` no legado) — e, desde a guarda acima, o
    # `DocStatus` fechado. Não o `U_INO_Update`.
    #
    # Chegamos a congelar por `U_INO_Update = 'N'`, e estava invertido: no
    # legado o campo é o **desvio entre dois caminhos**, não uma autorização.
    # `'N'` é justamente a condição do caminho da revisão
    # (`Program.cs:249`: `count != "0" && SitCode == 60 && alterado != "Y"`),
    # e `'Y'` desvia para a troca de PN (`Program.cs:280`). Congelar em `'N'`
    # deixava a atualização por revisão sem caminho nenhum: com `'Y'` ela cai
    # no outro ramo e retorna antes de chegar aqui.
    if revisao_wbc_e_mais_nova(estado.revisao_wbc, estado.revisao_pedido_sap):
        acumulador.registrar(
            f"Pedido existente e revisão do WBC ({estado.revisao_wbc or '-'}) mais nova "
            f"que a carimbada no pedido ({estado.revisao_pedido_sap or '-'}): "
            f"atualiza o pedido.",
            Acao.ATUALIZAR_PEDIDO,
            Acao.VINCULAR_DOCUMENTO_A_OPORTUNIDADE,
        )
        regras.append("atualiza_pedido")
    else:
        regras.append("pedido_congelado")
