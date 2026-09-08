"""Travas de segurança de ambiente.

Implementa, em código executável, as regras descritas em
`ai_spec/04_environment_constraints.md`:

Regra 1 — produção é imutável:
    Nenhuma operação de ESCRITA (POST/PATCH/PUT/DELETE, ou SQL de escrita) pode
    atingir a company DB de produção (SBOALTAMIRAPROD). Todo desenvolvimento e
    teste usa exclusivamente SBOALTAMIRAHOMOLOG.

Regra 5 — o SQL Server do WBC é somente leitura:
    Nenhuma operação de escrita pode ser executada no SQL Server do WBC, em
    ambiente nenhum. A integração apenas consome dados do WBC; quem escreve no
    WBC é o próprio sistema WBC. Diferentemente da Regra 1, esta trava **não
    tem chave de desligamento** — a diretiva é absoluta.

As regras ficam aqui, num único ponto, para que os adaptadores de
infraestrutura (Service Layer, SQL Server, HANA) não precisem cada um
reimplementar a verificação — e para que sejam testáveis isoladamente.
"""

from __future__ import annotations

import re

# Métodos HTTP considerados de escrita no Service Layer.
WRITE_METHODS: frozenset[str] = frozenset({"POST", "PATCH", "PUT", "DELETE", "MERGE"})

# Palavras-chave que caracterizam SQL de escrita.
_SQL_WRITE_KEYWORDS: frozenset[str] = frozenset(
    {
        "insert",
        "update",
        "delete",
        "merge",
        "upsert",
        "truncate",
        "drop",
        "alter",
        "create",
        "replace",
        "grant",
        "revoke",
        "call",
        "exec",
        "execute",
        "into",  # 'SELECT ... INTO nova_tabela' cria tabela: é escrita.
    }
)

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


class SafetyViolation(RuntimeError):
    """Base das violações de regra de segurança do projeto.

    Estas exceções NÃO devem ser capturadas e silenciadas por código de
    aplicação: sinalizam violação de uma regra do projeto, não falha
    transitória passível de retry.
    """


class ProductionWriteBlocked(SafetyViolation):
    """Levantada quando uma operação de escrita tentaria atingir a produção."""


class ReadOnlyViolation(SafetyViolation):
    """Levantada quando se tenta escrever numa fonte declarada somente leitura.

    Hoje se aplica ao SQL Server do WBC (Regra 5) e, por precaução, ao acesso
    direto ao HANA, que na arquitetura atual só consome views de leitura.
    """


def is_production(target: str | None, production_company_db: str) -> bool:
    """Diz se `target` (company DB ou schema) é o ambiente de produção.

    A comparação é case-insensitive e ignora espaços em volta, porque o mesmo
    nome aparece com grafias diferentes entre config, URL e SQL.
    """
    if target is None:
        return False
    return target.strip().casefold() == production_company_db.strip().casefold()


def assert_write_allowed(
    target_company_db: str | None,
    *,
    production_company_db: str,
    block_production_writes: bool = True,
    operation: str = "escrita",
) -> None:
    """Garante que uma operação de escrita não está mirando a produção.

    A trava **falha fechada**: se não dá para saber com segurança qual é o
    destino, ou qual nome conta como produção, a operação é recusada. O
    contrário — deixar passar quando a configuração está incompleta — já foi o
    comportamento deste código e é justamente a forma mais silenciosa de
    contornar a regra: bastava `WBC_PRODUCTION_COMPANY_DB` vazio, ou um erro de
    digitação nele, para nenhum destino ser reconhecido como produção.

    Args:
        target_company_db: company DB (ou schema HANA) que a operação vai atingir.
        production_company_db: nome da company DB tratada como produção.
        block_production_writes: trava ligada (padrão). Só deve ser desligada
            mediante autorização explícita e consciente — ver ai_spec/04.
        operation: descrição curta da operação, usada na mensagem de erro.

    Raises:
        ProductionWriteBlocked: se a operação atingiria a produção, ou se a
            configuração não permite afirmar que ela **não** atinge.
    """
    if not block_production_writes:
        return

    if not production_company_db or not production_company_db.strip():
        raise ProductionWriteBlocked(
            f"Operação de {operation} bloqueada: o nome da company DB de produção não está "
            f"configurado (WBC_PRODUCTION_COMPANY_DB vazio). Sem ele, a trava não tem como "
            f"reconhecer a produção — e deixar passar seria pior. Configure a variável antes "
            f"de qualquer escrita."
        )

    if target_company_db is None or not target_company_db.strip():
        raise ProductionWriteBlocked(
            f"Operação de {operation} bloqueada: o destino não foi informado. A trava recusa "
            f"escritas de destino desconhecido, porque não teria como garantir que não são "
            f"em produção."
        )

    if is_production(target_company_db, production_company_db):
        raise ProductionWriteBlocked(
            f"Operação de {operation} bloqueada: o destino '{target_company_db}' é a "
            f"company DB de produção. Regra do projeto (ai_spec/04_environment_constraints.md): "
            f"toda escrita deve usar o ambiente de homologação. "
            f"Se esta operação for realmente necessária, ela exige autorização humana "
            f"explícita — não contorne esta trava em código."
        )


def assert_http_call_allowed(
    method: str,
    target_company_db: str | None,
    *,
    production_company_db: str,
    block_production_writes: bool = True,
) -> None:
    """Aplica a trava a uma chamada HTTP do Service Layer.

    Leituras (GET) contra produção são permitidas — a regra do projeto proíbe
    alterações, não consultas. Qualquer método de escrita contra produção é
    bloqueado.
    """
    if method.strip().upper() not in WRITE_METHODS:
        return
    assert_write_allowed(
        target_company_db,
        production_company_db=production_company_db,
        block_production_writes=block_production_writes,
        operation=f"escrita ({method.strip().upper()})",
    )


def _strip_noise(sql: str) -> str:
    """Remove comentários, literais e identificadores citados do SQL.

    Sobra apenas a estrutura do comando, que é o que interessa para decidir se
    há escrita. Isso evita o falso positivo clássico
    ``SELECT * FROM t WHERE obs = 'favor update'``, em que ``update`` aparece só
    dentro de um literal.

    Precisa ser um **scanner de estado**, e não uma sequência de expressões
    regulares. Uma versão anterior removia comentários antes dos literais, e por
    isso um `--` dentro de um literal apagava o resto do comando — o que deixava
    passar coisas como::

        SELECT 'a--b' FROM t; DROP TABLE Alvo

    O `DROP` ficava invisível para a análise. Percorrer o texto uma vez,
    sabendo a cada caractere se está dentro de literal, de comentário de linha,
    de comentário de bloco ou de identificador citado, elimina essa classe de
    problema de uma vez.

    Identificadores entre aspas duplas (comuns no HANA, ex.: ``"VW_ALGO"``)
    viram um marcador neutro, para que um identificador que por acaso se chame
    ``insert`` não seja confundido com o comando.
    """
    NORMAL, LITERAL, COMENTARIO_LINHA, COMENTARIO_BLOCO, IDENTIFICADOR = range(5)

    saida: list[str] = []
    estado = NORMAL
    i = 0
    total = len(sql)

    while i < total:
        atual = sql[i]
        proximo = sql[i + 1] if i + 1 < total else ""

        if estado == NORMAL:
            if atual == "'":
                estado = LITERAL
                saida.append(" '' ")
                i += 1
            elif atual == '"':
                estado = IDENTIFICADOR
                saida.append(" ident ")
                i += 1
            elif atual == "-" and proximo == "-":
                estado = COMENTARIO_LINHA
                i += 2
            elif atual == "/" and proximo == "*":
                estado = COMENTARIO_BLOCO
                i += 2
            else:
                saida.append(atual)
                i += 1

        elif estado == LITERAL:
            if atual == "'":
                # '' dentro de um literal é uma aspas escapada, não o fim dele.
                if proximo == "'":
                    i += 2
                else:
                    estado = NORMAL
                    i += 1
            else:
                i += 1

        elif estado == IDENTIFICADOR:
            if atual == '"':
                if proximo == '"':
                    i += 2
                else:
                    estado = NORMAL
                    i += 1
            else:
                i += 1

        elif estado == COMENTARIO_LINHA:
            if atual == "\n":
                estado = NORMAL
                saida.append(" ")
            i += 1

        else:  # COMENTARIO_BLOCO
            if atual == "*" and proximo == "/":
                estado = NORMAL
                saida.append(" ")
                i += 2
            else:
                i += 1

    return " ".join("".join(saida).split()).casefold()


def _tokens(texto: str) -> list[str]:
    return re.findall(r"[a-z_]+", texto)


def looks_like_write_sql(sql: str) -> bool:
    """Heurística: diz se um comando SQL contém alguma operação de escrita.

    Usada para proteger os acessos a SQL Server e HANA. É deliberadamente
    conservadora — na dúvida, classifica como escrita —, porque o custo de um
    falso positivo (um SELECT recusado) é muito menor que o de um falso
    negativo (uma escrita indevida).

    Trata cada comando separado por ``;`` de forma independente: basta um ser
    de escrita para o conjunto ser considerado escrita. Isso bloqueia tentativas
    do tipo ``SELECT 1; DROP TABLE T``.
    """
    limpo = _strip_noise(sql)
    if not limpo:
        return False

    for comando in limpo.split(";"):
        comando = comando.strip()
        if not comando:
            continue

        tokens = _tokens(comando)
        if not tokens:
            continue

        primeiro = tokens[0]
        if primeiro in _SQL_WRITE_KEYWORDS:
            return True

        if primeiro in ("select", "with"):
            # Um SELECT/CTE ainda pode desembocar em INSERT/UPDATE/... ou em
            # 'SELECT ... INTO nova_tabela'. Verifica o corpo inteiro.
            if any(token in _SQL_WRITE_KEYWORDS for token in tokens[1:]):
                return True
            continue

        # Comando que não começa com verbo de leitura conhecido: por precaução,
        # trata como escrita.
        if primeiro not in ("explain", "describe", "show", "values", "table"):
            return True

    return False


def assert_read_only_sql(sql: str, *, fonte: str = "SQL Server do WBC") -> None:
    """Garante que um comando SQL é de leitura numa fonte somente leitura.

    Aplica a Regra 5 de `ai_spec/04_environment_constraints.md`. Diferente das
    demais travas, **não aceita desligamento**: a diretiva é que a integração
    nunca escreve no SQL Server do WBC, em ambiente nenhum.

    Raises:
        ReadOnlyViolation: se o comando contiver qualquer operação de escrita.
    """
    if looks_like_write_sql(sql):
        raise ReadOnlyViolation(
            f"Operação de escrita bloqueada: {fonte} é acesso SOMENTE LEITURA "
            f"por diretiva do projeto (ai_spec/04_environment_constraints.md, Regra 5). "
            f"A integração apenas consome dados do WBC — quem escreve no WBC é o próprio "
            f"sistema WBC. Esta trava não possui chave de desligamento; se houver "
            f"necessidade real de gravar, isso exige decisão humana e mudança de escopo."
        )


def assert_sql_allowed(
    sql: str,
    target_schema: str | None,
    *,
    production_company_db: str,
    block_production_writes: bool = True,
) -> None:
    """Aplica a trava a um comando SQL (SQL Server ou HANA).

    Bloqueia SQL de escrita cujo destino seja o schema/base de produção.
    SELECTs continuam permitidos — as views HANA de leitura dependem disso
    (ver ai_spec/02_data_model.md, seção 3).
    """
    if not looks_like_write_sql(sql):
        return
    assert_write_allowed(
        target_schema,
        production_company_db=production_company_db,
        block_production_writes=block_production_writes,
        operation="escrita SQL",
    )
