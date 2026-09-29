"""Instância única do Jinja2, com o contexto de ambiente sempre disponível (22/09/2026).

Antes cada router criava o seu `Jinja2Templates` e cabia à rota passar o ambiente no
contexto. Resultado imediato: a faixa "gravando em PRODUÇÃO" apareceu numa tela e **não**
nas demais, porque aquelas rotas não passavam nada.

Um aviso de ambiente que depende de cada rota lembrar de incluí-lo não é um aviso — é uma
armadilha, e falha exatamente na tela que ninguém revisou. Aqui ele vira global do Jinja:
está em toda página por construção, e uma rota nova não tem como esquecer.

O ambiente é do processo, não do request (vem do `.env`), então ser global é correto e não
só conveniente. A função é reavaliada a cada render para o `get_settings` em cache ainda
poder ser trocado em teste.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from controleproducao.config import get_settings
from controleproducao.core.formato import numero_br, quantidade_br
from controleproducao.core.tarefas import CLASSE_DA_BARRA, CLASSE_DA_PILULA
from controleproducao.core.web import contexto_do_ambiente

# Caminho absoluto, derivado do próprio arquivo: a aplicação precisa subir de qualquer
# diretório. Com `directory="controleproducao/templates"` ela só funcionava quando o `uvicorn` era
# chamado de dentro de `python_app/` — de outro diretório, ou de um systemd sem
# `WorkingDirectory`, morria antes de servir a primeira página. `config.py` e
# `listas_fixas.py` já resolviam assim; estes dois pontos é que ficaram para trás.
_RAIZ_DO_APP = Path(__file__).resolve().parent.parent

templates = Jinja2Templates(directory=_RAIZ_DO_APP / "templates")

# Nome distinto da chave `ambiente` que o próprio dicionário carrega: registrado como
# `ambiente`, um contexto de rota com essa chave sombreava a função e o template quebrava
# com "'str' object is not callable". Nome ambíguo em variável global custa caro.
templates.env.globals["amb_atual"] = contexto_do_ambiente

# Whether the screen asks for the key (OS_API_KEY set): the "Sair" button only makes sense
# then. Evaluated per render, like the environment, so tests can swap the settings.
templates.env.globals["exige_chave"] = lambda: bool(get_settings().os_api_key.get_secret_value())

# Formatação pt-BR como filtro, não como função chamada em cada template: filtro é o que
# o Jinja oferece para "transformar na hora de exibir", e deixa o valor cru disponível
# para qualquer outro uso no mesmo template.
templates.env.filters["moeda"] = numero_br
templates.env.filters["qtd"] = quantidade_br

# `tojson` embeds a finished task's state in its page (tarefa.html): a Decimal/datetime in a
# service result must become text, not a 500 on a page that only shows a result.
templates.env.policies["json.dumps_kwargs"] = {"sort_keys": True, "default": str}

# One map from a task outcome to its CSS classes (see `core/tarefas.py`).
templates.env.globals["CLASSE_DA_PILULA"] = CLASSE_DA_PILULA
templates.env.globals["CLASSE_DA_BARRA"] = CLASSE_DA_BARRA
