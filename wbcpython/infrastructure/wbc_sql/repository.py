"""Repositório do WBC — acesso **somente leitura** ao banco WBCCAD.

Regra 5 do projeto (`ai_spec/04_environment_constraints.md`): a integração nunca
escreve no SQL Server do WBC, em ambiente nenhum. Quem escreve no WBC é o
próprio WBC.

Essa promessa é sustentada aqui por três camadas:

1. **A interface não oferece escrita.** `RepositorioOrcamentosWbc` declara apenas
   métodos de consulta — não há um `salvar()` para alguém chamar por engano.
2. **Toda consulta passa pela trava.** `_executar()` chama
   `assert_read_only_sql()` antes de enviar qualquer coisa ao banco.
3. **Recomenda-se um usuário de banco com permissão apenas de leitura**
   (`db_datareader`) como camada extra, do lado do servidor.

Estado de processamento — inclusive "este orçamento já foi integrado" — pertence
ao banco de tracking da própria solução, nunca ao WBCCAD.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from sqlalchemy import Engine, bindparam, create_engine, text
from sqlalchemy.engine import URL, Row

from wbcpython.config import WbcSqlSettings
from wbcpython.infrastructure.wbc_sql import queries
from wbcpython.infrastructure.wbc_sql.models import (
    DadosImpressaoWbc,
    ItemArvoreWbc,
    ItemOrcamentoWbc,
    OrcamentoWbc,
)
from wbcpython.safety import assert_read_only_sql

logger = logging.getLogger(__name__)

#: Orçamentos por consulta ao ler situações em lote. O SQL Server tem limite de
#: parâmetros por comando, e um `IN` muito grande piora o plano de execução.
TAMANHO_DO_LOTE = 1000

FONTE = "SQL Server do WBC (WBCCAD)"


class RepositorioOrcamentosWbc(Protocol):
    """Contrato de leitura dos orçamentos do WBC.

    Deliberadamente **sem métodos de escrita**. Se em algum momento parecer
    necessário gravar algo no WBC, isso é sinal de que o estado pertence ao
    banco de tracking — não de que falta um método aqui.
    """

    def buscar_orcamento(self, orcnum: str) -> OrcamentoWbc | None: ...

    def situacao_atual(self, orcnum: str) -> tuple[int, str] | None:
        """Devolve `(sitcode, revisao)` sem carregar os itens."""
        ...

    def situacoes_atuais(self, orcnums: Sequence[str]) -> dict[str, tuple[int, str]]:
        """O mesmo, para vários orçamentos de uma vez."""
        ...

    def pesos_por_item(self, orcnum: str) -> dict[int, Decimal]:
        """Peso (nível 1 da árvore) de cada item, para o `Weight1` do pedido."""
        ...

    def orcamentos_alterados_desde(
        self, desde: datetime, *, sitcode_minimo: int = 10
    ) -> list[str]: ...


def _decimal(valor: Any) -> Decimal:
    """Converte para Decimal sem propagar dado sujo como exceção obscura."""
    if valor is None:
        return Decimal(0)
    if isinstance(valor, Decimal):
        return valor
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        logger.warning("Valor numérico inesperado no WBC: %r — assumido 0.", valor)
        return Decimal(0)


def _decimal_opcional(valor: Any) -> Decimal | None:
    """Como `_decimal`, mas preserva o nulo em vez de assumir zero.

    Serve `ORCPRDQTD`, onde "não informado" e "zero" precisam continuar
    distintos: a regra de negócio manda usar 1 nos dois casos, mas é ela quem
    decide isso — não a leitura. Achatar aqui esconderia que a coluna é nula em
    100% das linhas do WBC.
    """
    return None if valor is None else _decimal(valor)


def _texto(valor: Any) -> str:
    return "" if valor is None else str(valor)


#: Marcadores de formatação que o WBC embute nos textos longos. Não são
#: caracteres de controle — são estas sequências literais, escritas no dado.
MARCADORES_WBC = ("[CR]", "[TAB]")


def _texto_wbc(valor: Any) -> str:
    """Texto longo do WBC, com os marcadores de formatação trocados por espaço.

    Campos como `ORCIMP_ACABAMENTO` e `ORCPGT` vêm com `[CR]` e `[TAB]`
    literais no meio do texto — resquício de como o WBC monta a impressão.
    O legado os troca por espaço na própria consulta
    (`replace(replace(..., '[CR]', ' '), '[TAB]', ' ')`); aqui é feito na
    leitura, para valer em qualquer consulta que traga o campo.

    Sem isso, o acabamento chega ao SAP como
    `"[CR][CR][CR][CR]Cinza Padrão Altamira..."` — visualmente quebrado para
    quem abre a cotação.
    """
    texto = _texto(valor)
    for marcador in MARCADORES_WBC:
        texto = texto.replace(marcador, " ")
    return texto.strip()


def _revisao(valor: Any) -> str:
    """Normaliza a revisão: sem espaços e **sempre maiúscula**.

    A revisão é a letra que identifica a versão do orçamento (`A`, `B`, `C`…),
    e é usada em dois lugares onde o caso importa:

    * vai para o UDF `U_INO_VERSAOWBC` do documento no SAP, que é lido por
      pessoas e comparado por consultas — `"a"` e `"A"` não podem coexistir;
    * alimenta `ordem_revisao()`, cuja fórmula ASCII-64 pressupõe maiúscula
      (minúscula produziria um número muito maior, fazendo uma revisão antiga
      parecer mais nova e disparando cancelamento e recriação de documento).

    Nos dados atuais a coluna vem nula ou já em maiúscula, então isto é uma
    salvaguarda — do tipo que só se percebe que faltava depois do estrago.
    """
    return _texto(valor).strip().upper()


class RepositorioOrcamentosWbcSql:
    """Implementação sobre SQL Server (ou qualquer banco suportado pelo SQLAlchemy).

    Aceita um `Engine` pronto, o que permite testar contra SQLite em memória sem
    nenhum SQL Server envolvido.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------ construção

    @classmethod
    def a_partir_de(cls, settings: WbcSqlSettings) -> RepositorioOrcamentosWbcSql:
        """Cria o repositório a partir da configuração, em modo somente leitura."""
        return cls(create_engine(cls.montar_url(settings), pool_pre_ping=True))

    @staticmethod
    def montar_url(settings: WbcSqlSettings) -> URL:
        """Monta a URL de conexão.

        Usa `URL.create()` em vez de formatar a string à mão. Não é preciosismo:
        com interpolação direta, uma senha contendo `@` fazia o trecho seguinte
        virar **hostname** (conectando no servidor errado e vazando parte da
        senha em qualquer log da URL "mascarada"), e um `%` era interpretado
        como escape de URL, autenticando com uma senha diferente da real — em
        silêncio. `URL.create()` trata a senha como dado, não como texto de URL.

        O objeto `URL` também protege melhor: seu `repr()` mascara a senha,
        enquanto uma string a exporia inteira a quem a registrasse em log.

        **Sobre `ApplicationIntent=ReadOnly`:** foi tentado aqui como defesa em
        profundidade, mas o `pymssql` **não aceita** esse parâmetro — o
        SQLAlchemy o repassa ao driver, que rejeita com
        `TypeError: connect() got an unexpected keyword argument`. Ele funciona
        com `pyodbc`, não com este driver. Descoberto ao conectar no ambiente
        real; deixá-lo aqui quebraria toda leitura do WBC.

        A garantia de somente leitura não depende disso: está no código
        (`assert_read_only_sql`, aplicado no ponto único de execução) e na
        interface, que não expõe métodos de escrita. Como camada extra **no
        servidor**, use um usuário de banco com permissão apenas de leitura
        (`db_datareader`) — o que é mais eficaz que qualquer flag de conexão.
        """
        return URL.create(
            "mssql+pymssql",
            username=settings.username,
            password=settings.password.get_secret_value(),
            host=settings.host,
            port=settings.port,
            database=settings.database,
        )

    # -------------------------------------------------------------- execução

    def _executar(
        self, sql: str, *, expandir: tuple[str, ...] = (), **parametros: Any
    ) -> Sequence[Row[Any]]:
        """Ponto único de execução — é aqui que a trava de leitura é aplicada.

        `expandir` nomeia os parâmetros que recebem uma lista e viram um `IN`
        com um marcador por item. Quem monta a lista é o SQLAlchemy: o texto da
        consulta continua fixo, e a trava de leitura o inspeciona antes de
        qualquer expansão.
        """
        assert_read_only_sql(sql, fonte=FONTE)
        consulta = text(sql)
        if expandir:
            consulta = consulta.bindparams(*(bindparam(nome, expanding=True) for nome in expandir))
        with self._engine.connect() as conexao:
            return conexao.execute(consulta, parametros).fetchall()

    # -------------------------------------------------------------- consultas

    def buscar_orcamento(self, orcnum: str) -> OrcamentoWbc | None:
        linhas = self._executar(queries.ORCAMENTO_POR_NUMERO, orcnum=orcnum)
        if not linhas:
            return None
        orcamento = self._montar_orcamento(linhas)
        return orcamento.model_copy(update={"arvore": tuple(self.buscar_linhas_arvore(orcnum))})

    def buscar_linhas_arvore(self, orcnum: str) -> list[ItemArvoreWbc]:
        """Árvore de produtos do orçamento — vazia quando não há detalhamento.

        Consulta separada de propósito: a árvore tem cardinalidade própria (um
        orçamento com 20 linhas de árvore para 2 itens é comum) e juntá-la à
        consulta do cabeçalho multiplicaria os itens.
        """
        linhas = self._executar(queries.LINHAS_DA_ARVORE, orcnum=orcnum)
        return [
            ItemArvoreWbc(
                orcitm=int(d["orcitm"] or 0),
                grupo=int(d["grupo"] or 0),
                subgrupo=int(d["subgrupo"] or 0),
                produto=_texto(d["produto"]).strip(),
                nivel=int(d["nivel"] or 0),
                cor=_texto(d["cor"]).strip(),
                descricao=_texto(d["descricao"]).strip(),
                quantidade=_decimal(d["quantidade"]),
                total=_decimal(d["total"]),
                peso=_decimal(d["peso"]),
                id_integracao=int(d["id_integracao"] or 0),
            )
            for d in (linha._mapping for linha in linhas)
        ]

    def pesos_por_item(self, orcnum: str) -> dict[int, Decimal]:
        """Peso de cada item do orçamento, para o `Weight1` do pedido.

        Só o nível 1 da árvore — ver `queries.PESOS_NIVEL_1_POR_ITEM` para o
        porquê (somar os níveis conta a mesma massa duas ou três vezes).

        Item sem árvore simplesmente **não aparece** no dicionário. É a
        diferença entre "não sei o peso" e "o peso é zero", e quem monta a linha
        precisa dela: sem peso conhecido o campo não é enviado, e o SAP mantém o
        do cadastro do item.
        """
        linhas = self._executar(queries.PESOS_NIVEL_1_POR_ITEM, orcnum=orcnum)
        pesos: dict[int, Decimal] = {}
        for linha in linhas:
            dados = linha._mapping
            peso = _decimal(dados["peso"])
            if peso > 0:
                pesos[int(dados["orcitm"] or 0)] = peso
        return pesos

    def situacao_atual(self, orcnum: str) -> tuple[int, str] | None:
        linhas = self._executar(queries.SITUACAO_POR_NUMERO, orcnum=orcnum)
        if not linhas:
            return None
        linha = linhas[0]._mapping
        return int(linha["sitcode"] or 0), _revisao(linha["revisao"])

    def situacoes_atuais(self, orcnums: Sequence[str]) -> dict[str, tuple[int, str]]:
        """`{orcnum: (sitcode, revisao)}` para vários orçamentos de uma vez.

        Os orçamentos vão em lotes porque o SQL Server recusa consultas com
        milhares de parâmetros — e porque um `IN` gigantesco degrada o plano de
        execução. Mil por vez resolve a janela atual em duas idas ao banco.

        Ausente do resultado significa "não existe no WBC": quem chama trata
        isso como erro do orçamento, não como situação zero — confundir os dois
        faria um orçamento inexistente parecer "abaixo do mínimo" e sumir em
        silêncio.
        """
        situacoes: dict[str, tuple[int, str]] = {}
        for inicio in range(0, len(orcnums), TAMANHO_DO_LOTE):
            lote = tuple(orcnums[inicio : inicio + TAMANHO_DO_LOTE])
            if not lote:
                continue
            linhas = self._executar(
                queries.SITUACOES_EM_LOTE, expandir=("orcnums",), orcnums=list(lote)
            )
            for linha in linhas:
                dados = linha._mapping
                situacoes[_texto(dados["orcnum"])] = (
                    int(dados["sitcode"] or 0),
                    _revisao(dados["revisao"]),
                )
        return situacoes

    def orcamentos_alterados_desde(self, desde: datetime, *, sitcode_minimo: int = 10) -> list[str]:
        linhas = self._executar(
            queries.ORCAMENTOS_ALTERADOS_DESDE,
            desde=desde,
            sitcode_minimo=sitcode_minimo,
        )
        return [_texto(linha._mapping["orcnum"]) for linha in linhas]

    # ------------------------------------------------------------- montagem

    @staticmethod
    def _montar_orcamento(linhas: Iterable[Row[Any]]) -> OrcamentoWbc:
        """Reconstitui cabeçalho + itens a partir do resultado achatado do join.

        O `LEFT JOIN` com os itens faz com que um orçamento sem itens venha como
        uma única linha com os campos de item nulos — daí o descarte de linhas
        cujo `id_integracao` e `orcitm` sejam ambos zerados.
        """
        linhas = list(linhas)
        cabecalho = linhas[0]._mapping

        itens: list[ItemOrcamentoWbc] = []
        ja_vistos: set[tuple[int, int]] = set()

        for linha in linhas:
            dados = linha._mapping
            sem_item = not dados["id_integracao"] and not dados["orcitm"]
            if sem_item:
                continue

            # Deduplicação obrigatória: o orçamento se junta a ORCIMP e ORCCAB
            # por ORCNUM, e nada garante uma única linha nessas tabelas. Havendo
            # duas, o produto cartesiano do join repete **cada item**, dobrando
            # silenciosamente a quantidade que alimenta a cotação e o pedido no
            # SAP. Era exatamente isso que o GROUP BY do legado escondia.
            chave = (int(dados["id_integracao"] or 0), int(dados["orcitm"] or 0))
            if chave in ja_vistos:
                continue
            ja_vistos.add(chave)

            itens.append(
                ItemOrcamentoWbc(
                    orcitm=int(dados["orcitm"] or 0),
                    grupo=int(dados["grupo"] or 0),
                    subgrupo=int(dados["subgrupo"] or 0),
                    produto=_texto(dados["produto"]),
                    quantidade=_decimal_opcional(dados["quantidade"]),
                    texto=_texto(dados["texto"]),
                    valor=_decimal(dados["valor"]),
                    ipi=_decimal(dados["ipi"]),
                    icms=_decimal(dados["icms"]),
                    id_integracao=int(dados["id_integracao"] or 0),
                )
            )

        return OrcamentoWbc(
            orcnum=_texto(cabecalho["orcnum"]),
            revisao=_revisao(cabecalho["revisao"]),
            sitcode=int(cabecalho["sitcode"] or 0),
            cliente_nome=_texto(cabecalho["cliente_nome"]),
            representante=_texto(cabecalho["representante"]),
            municipio=_texto(cabecalho["municipio"]),
            uf=_texto(cabecalho["uf"]),
            data_orcamento=cabecalho["data_orcamento"],
            data_alteracao=cabecalho["data_alteracao"],
            percentual_comissao=_decimal(cabecalho["percentual_comissao"]),
            valor_comissao=_decimal(cabecalho["valor_comissao"]),
            base2=_decimal(cabecalho["base2"]),
            data_ultima_alteracao_impressao=cabecalho["data_ultima_alteracao_impressao"],
            retorno=_decimal(cabecalho["retorno"]),
            negociacao=_decimal(cabecalho["negociacao"]),
            indice_vendas=_decimal(cabecalho["indice_vendas"]),
            impressao=RepositorioOrcamentosWbcSql._montar_impressao(cabecalho),
            itens=tuple(itens),
        )

    @staticmethod
    def _montar_impressao(cabecalho: Any) -> DadosImpressaoWbc:
        """Reúne os campos de impressão que alimentam o snapshot do OrcDetalhe."""
        return DadosImpressaoWbc(
            valor_venda=_decimal(cabecalho["imp_valor_venda"]),
            valor_lista=_decimal(cabecalho["imp_valor_lista"]),
            valor_investimento=_decimal(cabecalho["imp_valor_investimento"]),
            valor_lucro=_decimal(cabecalho["imp_valor_lucro"]),
            valor_expedicao=_decimal(cabecalho["imp_valor_expedicao"]),
            valor_comissao=_decimal(cabecalho["imp_valor_comissao"]),
            percentual_comissao=_decimal(cabecalho["imp_percentual_comissao"]),
            valor_transporte=_decimal(cabecalho["imp_valor_transporte"]),
            valor_embalagem=_decimal(cabecalho["imp_valor_embalagem"]),
            valor_montagem=_decimal(cabecalho["imp_valor_montagem"]),
            base1=_decimal(cabecalho["imp_base1"]),
            base2=_decimal(cabecalho["imp_base2"]),
            base3=_decimal(cabecalho["imp_base3"]),
            cliente_codigo=int(cabecalho["imp_cliente_codigo"] or 0),
            contato_codigo=int(cabecalho["imp_contato_codigo"] or 0),
            contato=_texto(cabecalho["imp_contato"]).strip(),
            pagamento_codigo=_texto_wbc(cabecalho["imp_pagamento_codigo"]),
            pagamento_texto=_texto_wbc(cabecalho["imp_pagamento_texto"]),
            montagem_tipo=_texto_wbc(cabecalho["imp_montagem_tipo"]),
            prazo_entrega=int(cabecalho["imp_prazo_entrega"] or 0),
            revisao=_revisao(cabecalho["imp_revisao"]),
            email=_texto(cabecalho["imp_email"]).strip(),
            fone=_texto(cabecalho["imp_fone"]).strip(),
            cidade=_texto(cabecalho["imp_cidade"]).strip(),
            uf=_texto(cabecalho["imp_uf"]).strip(),
            tipo_venda=_texto(cabecalho["imp_tipo_venda"]).strip(),
            transporte=_texto_wbc(cabecalho["imp_transporte"]),
            acabamento=_texto_wbc(cabecalho["imp_acabamento"]),
            montagem=_texto_wbc(cabecalho["imp_montagem"]),
            tabela_preco=_texto(cabecalho["imp_tabela_preco"]).strip(),
        )
