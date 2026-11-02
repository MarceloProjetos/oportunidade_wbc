"""The office's org chart: who handles what, how an agent introduces itself, where things
escalate (F1 of docs/PLANO_ORGANOGRAMA.md, 02/11/2026).

THE RULE (owner, 01/11/2026): **a role never grants anything.** This module is read by people
and by the agents' prompts; it is never read by an authorisation path. Scopes stay in
``seguranca.credenciais`` (changed only with ``python -m seguranca``), approval rights stay in
``X-SIS-Papel`` + ``acoes_agente.pode_aprovar``, and the agent rules in ``seguranca.agente``.
``tests/test_organograma.py`` checks by AST that ``seguranca/``, the MCP's access layer, the
CP's and the screens' login and the API's ``requer_chave`` never import it.

Consequences:

- ``conferir`` compares the chart with the credentials and **only reports**. A chart that
  promises more than the credential holds is a ``falta`` (the action would be refused — the
  credential wins); a credential holding more than the role expects is a ``sobra`` (a warning:
  "security adds, never removes a function" — removing a scope is the owner's call, by hand).
- ``aprova`` describes who decides what, so an agent knows whom to ask; it is not checked
  against anything at approval time.

The chart is ``operacao/organograma.toml`` (versioned, reviewed in git). The repository is
public, so the validation refuses anything that looks like a contact (``@``, a phone number)
and a person's surname. An invalid edit never leaves the agents without a role: ``carregar``
keeps serving the last valid version of the file and ``situacao`` says what is wrong.

    python -m operacao.organograma                 # the chart, as tables
    python -m operacao.organograma conferir        # chart x credentials (exit 1 = a falta)
    python -m operacao.organograma tema ti_servidor
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import threading
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from operacao.acoes_agente import CATALOGO
from seguranca import credenciais

logger = logging.getLogger(__name__)

ARQUIVO_PADRAO = Path(__file__).with_name("organograma.toml")
VERSAO = 1
TIPOS = ("agente", "pessoa", "equipe")
#: Where a question that matches no topic goes. Must exist and be owned by a person.
TEMA_GERAL = "outro"

_ID = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
#: A nickname resolved in the .90's .env -- no digits, so a number cannot hide in it.
_CONTATO = re.compile(r"^[a-z_-]{1,30}$")
_CREDENCIAL = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
#: A Brazilian phone in free text: optional +55, area code, 8-9 digits, usual separators.
#: Narrow on purpose -- order ranges ("84000-84010") and SAP codes must pass.
_TELEFONE = re.compile(r"(?<!\d)(?:\+?55[\s.-]?)?\(?\d{2}\)?[\s.-]?9?\d{4}[\s.-]?\d{4}(?!\d)")
#: Short fields that name someone: there, 10+ digits in total is refused however split.
_CAMPOS_DE_IDENTIDADE = {"id", "nome", "cargo", "contato", "apresentacao", "credencial", "dono"}

_CHAVES_TOPO = {"versao", "membro", "tema"}
_CHAVES_COMUNS = {"id", "tipo", "nome", "cargo", "cuida_de", "nao_faz", "escala_para"}
_CHAVES_AGENTE = {"credencial", "pede", "escopos_esperados", "apresentacao"}
_CHAVES_HUMANO = {"aprova", "contato"}
_CHAVES_TEMA = {"id", "dono", "substitutos", "descricao", "exemplos"}


class OrganogramaInvalido(ValueError):
    """The chart breaks a rule; ``erros`` lists every problem found, not just the first."""

    def __init__(self, erros: list[str]) -> None:
        self.erros = list(erros)
        super().__init__("; ".join(self.erros))


@dataclass(frozen=True)
class Membro:
    id: str
    tipo: str
    nome: str
    cargo: str
    cuida_de: tuple[str, ...] = ()
    nao_faz: tuple[str, ...] = ()
    escala_para: tuple[str, ...] = ()
    credencial: str | None = None
    pede: tuple[str, ...] = ()
    escopos_esperados: tuple[str, ...] = ()
    apresentacao: str | None = None
    aprova: tuple[str, ...] = ()
    contato: str | None = None

    @property
    def agente(self) -> bool:
        return self.tipo == "agente"

    def como_dict(self) -> dict[str, Any]:
        base: dict[str, Any] = {"id": self.id, "tipo": self.tipo, "nome": self.nome, "cargo": self.cargo,
                                "cuida_de": list(self.cuida_de), "nao_faz": list(self.nao_faz),
                                "escala_para": list(self.escala_para)}
        if self.agente:
            base |= {"credencial": self.credencial, "pede": list(self.pede),
                     "escopos_esperados": list(self.escopos_esperados), "apresentacao": self.apresentacao}
        else:
            base |= {"aprova": list(self.aprova), "contato": self.contato}
        return base


@dataclass(frozen=True)
class Tema:
    id: str
    dono: str
    descricao: str
    substitutos: tuple[str, ...] = ()
    exemplos: tuple[str, ...] = ()

    def como_dict(self) -> dict[str, Any]:
        return {"id": self.id, "dono": self.dono, "substitutos": list(self.substitutos),
                "descricao": self.descricao, "exemplos": list(self.exemplos)}


@dataclass(frozen=True)
class Organograma:
    """Shared by every caller (cached): the mappings are read-only on purpose."""

    versao: int
    membros: Mapping[str, Membro]
    temas: Mapping[str, Tema]

    def agentes(self) -> list[Membro]:
        return [m for m in self.membros.values() if m.agente]

    def quem_cuida_de(self, tema: str) -> Tema:
        """The topic, or the catch-all one when nobody owns it: a question is never ownerless."""
        return self.temas.get(tema) or self.temas[TEMA_GERAL]

    def como_dict(self) -> dict[str, Any]:
        return {"versao": self.versao,
                "membros": [m.como_dict() for m in self.membros.values()],
                "temas": [t.como_dict() for t in self.temas.values()]}


# --- reading ----------------------------------------------------------------------------

def _texto(dados: dict, chave: str, onde: str, erros: list[str], *, obrigatorio: bool) -> str | None:
    valor = dados.get(chave)
    if valor is None:
        if obrigatorio:
            erros.append(f"{onde}: falta '{chave}'")
        return None
    if not isinstance(valor, str) or not valor.strip():
        erros.append(f"{onde}: '{chave}' tem de ser um texto não vazio")
        return None
    return valor.strip()


def _lista(dados: dict, chave: str, onde: str, erros: list[str]) -> tuple[str, ...]:
    valor = dados.get(chave, [])
    if not isinstance(valor, list) or not all(isinstance(v, str) and v.strip() for v in valor):
        erros.append(f"{onde}: '{chave}' tem de ser uma lista de textos")
        return ()
    itens = tuple(v.strip() for v in valor)
    if len(set(itens)) != len(itens):
        erros.append(f"{onde}: '{chave}' tem item repetido")
    return itens


def _membro(dados: Any, n: int, erros: list[str]) -> Membro | None:
    onde = f"membro #{n}"
    if not isinstance(dados, dict):
        erros.append(f"{onde}: tem de ser uma tabela [[membro]]")
        return None
    id_ = _texto(dados, "id", onde, erros, obrigatorio=True)
    if id_:
        onde = f"membro '{id_}'"
        if not _ID.match(id_):
            erros.append(f"{onde}: id só com minúsculas, números e '_' (até 30)")
    tipo = _texto(dados, "tipo", onde, erros, obrigatorio=True)
    if tipo and tipo not in TIPOS:
        erros.append(f"{onde}: tipo '{tipo}' inválido (use {', '.join(TIPOS)})")
        tipo = None
    permitidas = _CHAVES_COMUNS | (_CHAVES_AGENTE if tipo == "agente" else _CHAVES_HUMANO if tipo else set())
    for chave in sorted(set(dados) - permitidas):
        if tipo == "agente" and chave == "aprova":
            erros.append(f"{onde}: um agente nunca aprova — tire 'aprova'")
        elif tipo:
            erros.append(f"{onde}: chave '{chave}' não vale para tipo '{tipo}'")
    nome = _texto(dados, "nome", onde, erros, obrigatorio=True)
    cargo = _texto(dados, "cargo", onde, erros, obrigatorio=True)
    if not (id_ and tipo and nome and cargo):
        return None
    agente = tipo == "agente"
    return Membro(
        id=id_, tipo=tipo, nome=nome, cargo=cargo,
        cuida_de=_lista(dados, "cuida_de", onde, erros),
        nao_faz=_lista(dados, "nao_faz", onde, erros),
        escala_para=_lista(dados, "escala_para", onde, erros),
        credencial=_texto(dados, "credencial", onde, erros, obrigatorio=agente) if agente else None,
        pede=_lista(dados, "pede", onde, erros) if agente else (),
        escopos_esperados=_lista(dados, "escopos_esperados", onde, erros) if agente else (),
        apresentacao=_texto(dados, "apresentacao", onde, erros, obrigatorio=False) if agente else None,
        aprova=_lista(dados, "aprova", onde, erros) if not agente else (),
        contato=_texto(dados, "contato", onde, erros, obrigatorio=False) if not agente else None,
    )


def _tema(dados: Any, n: int, erros: list[str]) -> Tema | None:
    onde = f"tema #{n}"
    if not isinstance(dados, dict):
        erros.append(f"{onde}: tem de ser uma tabela [[tema]]")
        return None
    id_ = _texto(dados, "id", onde, erros, obrigatorio=True)
    if id_:
        onde = f"tema '{id_}'"
        if not _ID.match(id_):
            erros.append(f"{onde}: id só com minúsculas, números e '_' (até 30)")
    for chave in sorted(set(dados) - _CHAVES_TEMA):
        erros.append(f"{onde}: chave '{chave}' desconhecida")
    dono = _texto(dados, "dono", onde, erros, obrigatorio=True)
    descricao = _texto(dados, "descricao", onde, erros, obrigatorio=True)
    if not (id_ and dono and descricao):
        return None
    return Tema(id=id_, dono=dono, descricao=descricao,
                substitutos=_lista(dados, "substitutos", onde, erros),
                exemplos=_lista(dados, "exemplos", onde, erros))


def _textos(dados: Any, onde: str, campo: str = ""):
    """Every string in the file with where it is (``membro 'felipe'.cargo``) and its key."""
    if isinstance(dados, str):
        yield onde, campo, dados
    elif isinstance(dados, dict):
        for chave, valor in dados.items():
            yield from _textos(valor, f"{onde}.{chave}" if onde else str(chave), str(chave))
    elif isinstance(dados, list):
        for n, valor in enumerate(dados, 1):
            id_ = valor.get("id") if isinstance(valor, dict) else None
            rotulo = f"{onde} '{id_}'" if isinstance(id_, str) else f"{onde}[{n}]"
            yield from _textos(valor, rotulo, campo)


def _sem_dado_pessoal(dados: dict, erros: list[str]) -> None:
    """The repository is public: a contact never goes into the file, however it is written.

    The message names WHERE, never the value: errors go to the log and, from F2 on, to the API.
    A surname on an agent or inside a sentence cannot be told apart by code -- review catches it.
    """
    for onde, campo, texto in _textos(dados, ""):
        if "@" in texto:
            erros.append(f"{onde}: dado de contato no arquivo (repo público): tem '@'")
        elif campo in _CAMPOS_DE_IDENTIDADE and sum(c.isdigit() for c in texto) >= 10:
            erros.append(f"{onde}: dado de contato no arquivo (repo público): número longo")
        elif _TELEFONE.search(texto):
            erros.append(f"{onde}: dado de contato no arquivo (repo público): parece telefone")


def _validar(org: Organograma, erros: list[str]) -> None:
    membros, temas = org.membros, org.temas
    humanos = {m.id for m in membros.values() if not m.agente}
    papeis = set().union(*(a.papeis for a in CATALOGO.values() if a.papeis))
    credenciais_vistas: dict[str, str] = {}

    for m in membros.values():
        onde = f"membro '{m.id}'"
        for destino in m.escala_para:
            if destino == m.id:
                erros.append(f"{onde}: escala para si mesmo")
            elif destino not in membros:
                erros.append(f"{onde}: escala para '{destino}', que não está no organograma")
        for tema in m.cuida_de:
            if tema not in temas:
                erros.append(f"{onde}: cuida de '{tema}', que não é um tema")
            elif temas[tema].dono != m.id:
                erros.append(f"{onde}: cuida de '{tema}', mas o dono do tema é '{temas[tema].dono}'")
        if m.tipo == "pessoa" and len(m.nome.split()) != 1:
            erros.append(f"{onde}: pessoa só com o primeiro nome (repo público)")
        for papel in m.aprova:
            if papel not in papeis:
                erros.append(f"{onde}: aprova '{papel}', papel que nenhuma ação usa ({', '.join(sorted(papeis))})")
        if m.contato and not _CONTATO.match(m.contato):
            erros.append(f"{onde}: contato é um apelido (minúsculas, números, '-', '_'), não o contato em si")
        if not m.agente:
            continue
        if not m.escala_para:
            erros.append(f"{onde}: agente sem 'escala_para'")
        if m.credencial:
            if not _CREDENCIAL.match(m.credencial):
                erros.append(f"{onde}: credencial '{m.credencial}' não é um nome de credencial")
            elif m.credencial in credenciais_vistas:
                erros.append(f"{onde}: credencial '{m.credencial}' já é de '{credenciais_vistas[m.credencial]}'")
            credenciais_vistas.setdefault(m.credencial, m.id)
        for escopo in m.escopos_esperados:
            if escopo in credenciais.PROIBIDOS_AO_AGENTE:
                erros.append(f"{onde}: escopo '{escopo}' é proibido a agente")
            elif escopo not in credenciais.ESCOPOS:
                erros.append(f"{onde}: escopo '{escopo}' não existe")
        for nome in m.pede:
            acao = CATALOGO.get(nome)
            if acao is None:
                erros.append(f"{onde}: pede '{nome}', que não está no acoes_agente.CATALOGO")
            elif acao.escopo not in m.escopos_esperados:
                erros.append(f"{onde}: pede '{nome}', que precisa do escopo '{acao.escopo}' "
                             "fora de 'escopos_esperados'")

    for t in temas.values():
        onde = f"tema '{t.id}'"
        if t.dono not in membros:
            erros.append(f"{onde}: dono '{t.dono}' não está no organograma")
        elif t.id not in membros[t.dono].cuida_de:
            erros.append(f"{onde}: o dono '{t.dono}' não lista o tema em 'cuida_de'")
        for s in t.substitutos:
            if s == t.dono:
                erros.append(f"{onde}: o dono não é substituto de si mesmo")
            elif s not in membros:
                erros.append(f"{onde}: substituto '{s}' não está no organograma")
        if not ({t.dono, *t.substitutos} & humanos):
            erros.append(f"{onde}: nenhuma pessoa ou equipe entre dono e substitutos")
    geral = temas.get(TEMA_GERAL)
    if geral is None:
        erros.append(f"falta o tema '{TEMA_GERAL}' (para onde vai o que não casa com nenhum)")
    elif geral.dono not in humanos:
        erros.append(f"tema '{TEMA_GERAL}': o dono tem de ser uma pessoa ou equipe")

    _validar_escalada(membros, humanos, erros)


def _validar_escalada(membros: dict[str, Membro], humanos: set[str], erros: list[str]) -> None:
    """Agents hand over among themselves without a loop, and every agent reaches a person.

    Among people a cycle is fine — that is a stand-in (the director and his substitutes); a
    cycle of agents is the ping-pong the plan forbids. Handing a question to the topic's owner
    (the F4 repasse) goes by ``tema.dono``, not by this chain, so it is not limited here.
    """
    agentes = {m.id: m for m in membros.values() if m.agente}

    def ciclo(origem: str, caminho: tuple[str, ...]) -> tuple[str, ...] | None:
        for destino in agentes[origem].escala_para:
            if destino in caminho:
                return (*caminho[caminho.index(destino):], destino)
            if destino in agentes:
                achado = ciclo(destino, (*caminho, destino))
                if achado:
                    return achado
        return None

    vistos: set[frozenset[str]] = set()
    for a in sorted(agentes):
        laco = ciclo(a, (a,))
        if laco and frozenset(laco) not in vistos:
            vistos.add(frozenset(laco))
            erros.append(f"escalada em laço entre agentes: {' -> '.join(laco)}")

    alcanca: dict[str, bool] = {}

    def chega_a_pessoa(origem: str, visitados: frozenset[str]) -> bool:
        if origem in alcanca:
            return alcanca[origem]
        for destino in agentes[origem].escala_para:
            if destino in humanos:
                return True
            if destino in agentes and destino not in visitados and chega_a_pessoa(destino, visitados | {destino}):
                return True
        return False

    for a in sorted(agentes):
        alcanca[a] = chega_a_pessoa(a, frozenset({a}))
        if not alcanca[a]:
            erros.append(f"membro '{a}': a escalada nunca chega a uma pessoa")


def de_dados(dados: Any) -> Organograma:
    """Build and validate the chart from the parsed TOML; every problem at once."""
    erros: list[str] = []
    if not isinstance(dados, dict):
        raise OrganogramaInvalido(["o arquivo não é uma tabela TOML"])
    for chave in sorted(set(dados) - _CHAVES_TOPO):
        erros.append(f"chave '{chave}' desconhecida no topo do arquivo")
    versao = dados.get("versao")
    if type(versao) is not int or versao != VERSAO:
        erros.append(f"versao tem de ser {VERSAO}")
    _sem_dado_pessoal(dados, erros)

    membros: dict[str, Membro] = {}
    lista_membros = dados.get("membro", [])
    if not isinstance(lista_membros, list) or not lista_membros:
        erros.append("nenhum [[membro]]")
        lista_membros = []
    for n, bruto in enumerate(lista_membros, 1):
        m = _membro(bruto, n, erros)
        if m and m.id in membros:
            erros.append(f"membro '{m.id}' repetido")
        elif m:
            membros[m.id] = m

    temas: dict[str, Tema] = {}
    lista_temas = dados.get("tema", [])
    if not isinstance(lista_temas, list) or not lista_temas:
        erros.append("nenhum [[tema]]")
        lista_temas = []
    for n, bruto in enumerate(lista_temas, 1):
        t = _tema(bruto, n, erros)
        if t and t.id in temas:
            erros.append(f"tema '{t.id}' repetido")
        elif t:
            temas[t.id] = t

    org = Organograma(versao=VERSAO, membros=MappingProxyType(membros), temas=MappingProxyType(temas))
    _validar(org, erros)
    if erros:
        raise OrganogramaInvalido(erros)
    return org


def ler(texto: str) -> Organograma:
    try:
        dados = tomllib.loads(texto)
    except tomllib.TOMLDecodeError as exc:
        raise OrganogramaInvalido([f"TOML inválido: {exc}"]) from None
    return de_dados(dados)


# --- loading with "last valid version" --------------------------------------------------

@dataclass
class _Estado:
    marca: tuple[int, int] | None = None
    valido: Organograma | None = None
    valido_desde: str | None = None
    erros: tuple[str, ...] = ()


_estados: dict[str, _Estado] = {}
_trava = threading.Lock()


def _arquivo(arquivo: Path | None) -> Path:
    if arquivo is not None:
        return Path(arquivo)
    return Path(os.environ.get("SIS_ORGANOGRAMA_ARQUIVO") or ARQUIVO_PADRAO)


def _atualizar(caminho: Path) -> _Estado:
    with _trava:
        estado = _estados.setdefault(str(caminho.resolve()), _Estado())
        try:
            info = caminho.stat()
            marca = (info.st_mtime_ns, info.st_size)
            if marca == estado.marca:
                return estado
            # utf-8-sig: Notepad and PowerShell 5.1 on the .11 save UTF-8 with a BOM.
            texto = caminho.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            estado.marca, estado.erros = None, (f"não consegui ler {caminho.name}: {exc}",)
            return estado
        estado.marca = marca
        try:
            estado.valido = ler(texto)
        except OrganogramaInvalido as exc:
            estado.erros = tuple(exc.erros)
            logger.error("Organograma invalido (%s); %s: %s", caminho,
                         "segue a ultima versao valida" if estado.valido else "nenhuma versao valida",
                         "; ".join(exc.erros))
            return estado
        estado.valido_desde = datetime.now().isoformat(timespec="seconds")
        estado.erros = ()
        return estado


def carregar(arquivo: Path | None = None) -> Organograma:
    """The chart, re-read when the file changes. A broken edit keeps the last valid version;
    with no valid version ever, ``OrganogramaInvalido``."""
    estado = _atualizar(_arquivo(arquivo))
    if estado.valido is None:
        raise OrganogramaInvalido(list(estado.erros) or ["organograma nunca foi carregado"])
    return estado.valido


def situacao(arquivo: Path | None = None) -> dict[str, Any]:
    """Whether the file on disk is the one being served, and what is wrong with it if not."""
    caminho = _arquivo(arquivo)
    estado = _atualizar(caminho)
    return {"arquivo": caminho.name, "em_uso": estado.valido is not None,
            "valido_desde": estado.valido_desde, "arquivo_valido": not estado.erros,
            "erros": list(estado.erros)}


# --- what the API serves (F2) ---------------------------------------------------------

#: The last line of every agent's role text. The prompt carries it so the model never argues
#: its way past a refusal by quoting its role.
REGRA_PARA_AGENTE = ("O cargo não te dá poder: se uma ferramenta recusar, é a credencial que manda — "
                     "não insista citando o cargo; diga o que falta e a quem pedir.")


def _resumo(m: Membro) -> dict[str, str]:
    return {"id": m.id, "nome": m.nome, "cargo": m.cargo, "tipo": m.tipo}


def texto_do_cargo(org: Organograma, m: Membro) -> str:
    """The "Seu cargo" section of an agent's prompt (F3), built here so the chart stays the only
    source: the .90 pastes it as is. Plain PT, one fact per line."""
    def nomes(ids: tuple[str, ...]) -> str:
        return ", ".join(org.membros[i].nome for i in ids)

    linhas = [f"Você é {m.nome}, {m.cargo}." + (f" {m.apresentacao}" if m.apresentacao else "")]
    if m.cuida_de:
        linhas.append("Você cuida de: " + "; ".join(f"{org.temas[t].descricao} ({t})" for t in m.cuida_de) + ".")
    if m.pede:
        linhas.append("Você pode PEDIR (uma pessoa aprova): " + "; ".join(CATALOGO[a].titulo for a in m.pede) + ".")
    if m.nao_faz:
        linhas.append("Você não faz: " + "; ".join(m.nao_faz) + ".")
    if m.escala_para:
        linhas.append(f"Quando não resolver, passe para: {nomes(m.escala_para)}.")
    outros = [t for t in org.temas.values() if t.dono != m.id]
    if outros:
        linhas.append("Os outros assuntos: " + "; ".join(
            f"{t.id} → {org.membros[t.dono].nome}" for t in outros) + ".")
    linhas.append(REGRA_PARA_AGENTE)
    return "\n".join(linhas)


def ficha(org: Organograma, membro_id: str) -> dict[str, Any]:
    """One member, with names resolved, and, for an agent, the ready role text. ``KeyError`` if
    the id is not in the chart."""
    m = org.membros[membro_id]
    dados = m.como_dict()
    dados["escala_para"] = [_resumo(org.membros[e]) for e in m.escala_para]
    if m.agente:
        dados["pede"] = [{"acao": a, "titulo": CATALOGO[a].titulo} for a in m.pede]
        dados["texto_cargo"] = texto_do_cargo(org, m)
    return {"membro": dados, "temas": [{"id": t.id, "dono": t.dono, "descricao": t.descricao}
                                       for t in org.temas.values()]}


def tema_publico(org: Organograma, tema_id: str) -> dict[str, Any]:
    """Who handles a topic. A topic that does not exist goes to the catch-all one (``casou``
    False), so the caller always gets an owner."""
    t = org.quem_cuida_de(tema_id)
    return {"pedido": tema_id, "casou": t.id == tema_id, "tema": t.id, "descricao": t.descricao,
            "exemplos": list(t.exemplos), "dono": _resumo(org.membros[t.dono]),
            "substitutos": [_resumo(org.membros[s]) for s in t.substitutos]}


# --- chart x credentials (read-only) ----------------------------------------------------

def conferir(org: Organograma, clientes: list[dict] | None = None) -> dict[str, Any]:
    """Compare each agent's role with its credential. Reads ``state/credenciais.json``, never
    writes it: nothing is granted for being in the chart, nothing is revoked for being missing
    from it.

    Per agent: ``faltam`` (the role expects, the credential lacks — the agent will be refused,
    and the credential wins), ``sobram`` (the credential holds, the role does not expect — a
    warning for the owner). ``credencial_sem_ficha``: an active agent credential nobody in the
    chart uses. ``ok`` is False only for what makes an agent fail or escape its rules: a
    ``falta``, a missing credential, or a credential not marked as agent (the off switch and
    the business-hours rule would not apply to it), or a scope no agent may ever hold
    (``PROIBIDOS_AO_AGENTE`` — only a hand edit of the JSON gets one there). For an agent,
    ``admin`` does not count as covering a ``falta``: it is itself the problem.
    """
    if clientes is None:
        clientes = credenciais.carregar()
    ativos = {str(c.get("nome")): c for c in clientes if c.get("ativo", True)}
    agentes = []
    ok = True
    for m in org.agentes():
        esperados = set(m.escopos_esperados)
        registro = ativos.get(m.credencial or "")
        item: dict[str, Any] = {"membro": m.id, "credencial": m.credencial}
        if registro is None:
            item |= {"situacao": "sem_credencial", "faltam": sorted(esperados), "sobram": [],
                     "proibidos": [], "marcada_como_agente": None}
            ok = False
        else:
            tem = {str(e) for e in registro.get("escopos", [])}
            faltam = sorted(esperados - tem)
            sobram = sorted(tem - esperados)
            proibidos = sorted(tem & credenciais.PROIBIDOS_AO_AGENTE)
            marcada = bool(registro.get("agente"))
            situacao_ = ("escopo_proibido" if proibidos else "credencial_nao_e_agente" if not marcada
                         else "faltam" if faltam else "sobram" if sobram else "ok")
            item |= {"situacao": situacao_, "faltam": faltam, "sobram": sobram, "proibidos": proibidos,
                     "marcada_como_agente": marcada}
            ok = ok and marcada and not faltam and not proibidos
        agentes.append(item)
    usadas = {m.credencial for m in org.agentes()}
    sem_ficha = sorted(n for n, c in ativos.items() if c.get("agente") and n not in usadas)
    return {"ok": ok, "agentes": agentes, "credencial_sem_ficha": sem_ficha,
            "regra": "A conferência só aponta: nada é concedido nem revogado por ela."}


# --- CLI --------------------------------------------------------------------------------

def _linha(*colunas: str) -> str:
    return "  ".join(colunas).rstrip()


def _mostrar(org: Organograma) -> None:
    print(f"Organograma (versão {org.versao}) — o cargo não dá permissão; quem manda é a credencial.\n")
    largura = max(len(m.nome) for m in org.membros.values())
    for m in org.membros.values():
        print(_linha(f"{m.nome:<{largura}}", f"[{m.tipo}]", m.cargo))
        if m.cuida_de:
            print(f"{'':<{largura}}  cuida de: {', '.join(m.cuida_de)}")
        if m.pede:
            print(f"{'':<{largura}}  pede: {', '.join(m.pede)}")
        if m.aprova:
            print(f"{'':<{largura}}  aprova (papel): {', '.join(m.aprova)}")
        if m.escala_para:
            print(f"{'':<{largura}}  escala para: {', '.join(org.membros[e].nome for e in m.escala_para)}")
    print("\nTemas")
    largura = max(len(t.id) for t in org.temas.values())
    for t in org.temas.values():
        subs = ", ".join(org.membros[s].nome for s in t.substitutos) or "—"
        print(_linha(f"{t.id:<{largura}}", f"{org.membros[t.dono].nome} (subst.: {subs})", f"— {t.descricao}"))


def _mostrar_conferencia(resultado: dict[str, Any]) -> None:
    for item in resultado["agentes"]:
        print(f"{item['membro']} ({item['credencial']}): {item['situacao']}")
        if item["situacao"] == "sem_credencial":
            print("   nenhuma credencial ativa com esse nome")
        if item["proibidos"]:
            print(f"   ESCOPO PROIBIDO A AGENTE na credencial: {', '.join(item['proibidos'])} — revise já")
        if item["marcada_como_agente"] is False:
            print("   credencial SEM a marca de agente: interruptor e horário não valem para ela")
        if item["faltam"]:
            print(f"   faltam (a ficha espera, a credencial não tem): {', '.join(item['faltam'])}")
        if item["sobram"]:
            print(f"   sobram (a credencial tem, o cargo não prevê): {', '.join(item['sobram'])}")
    if resultado["credencial_sem_ficha"]:
        print(f"credenciais de agente sem ficha: {', '.join(resultado['credencial_sem_ficha'])}")
    print(f"\n{resultado['regra']} Tirar escopo: python -m seguranca (decisão do Marcelo).")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m operacao.organograma",
                                description="Organograma do escritório (só leitura).")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("mostrar", help="o organograma e os temas (padrão)")
    sub.add_parser("conferir", help="ficha x credenciais; sai 1 se um agente vai ser recusado")
    t = sub.add_parser("tema", help="quem cuida de um tema")
    t.add_argument("tema")
    # The .11's console and the pipes PowerShell reads are not UTF-8: never die on a dash.
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(errors="replace")
    args = p.parse_args(argv)

    try:
        org = carregar()
    except OrganogramaInvalido as exc:
        print("Organograma inválido:", file=sys.stderr)
        for erro in exc.erros:
            print(f"  - {erro}", file=sys.stderr)
        return 2

    if args.cmd == "conferir":
        resultado = conferir(org)
        _mostrar_conferencia(resultado)
        return 0 if resultado["ok"] else 1
    if args.cmd == "tema":
        tema = org.quem_cuida_de(args.tema)
        if tema.id != args.tema:
            print(f"'{args.tema}' não é um tema; vai para '{tema.id}'.")
        subs = ", ".join(org.membros[s].nome for s in tema.substitutos) or "—"
        print(f"{tema.id}: {org.membros[tema.dono].nome} (substitutos: {subs}) — {tema.descricao}")
        return 0
    _mostrar(org)
    return 0


if __name__ == "__main__":
    sys.exit(main())
