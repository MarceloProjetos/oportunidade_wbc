"""Single Jinja2 instance, with the environment context always available (22/09/2026).

Before, each router created its own `Jinja2Templates` and it was up to the route to pass the
environment in the context. Immediate result: the "writing to PRODUCTION" banner showed on one
screen and **not** on the others, because those routes passed nothing.

An environment notice that depends on each route remembering to include it is not a notice —
it is a trap, and it fails on exactly the screen nobody reviewed. Here it becomes a Jinja
global: it is on every page by construction, and a new route cannot forget it.

The environment belongs to the process, not to the request (it comes from the `.env`), so being
global is correct and not just convenient. The function is re-evaluated on every render so the
cached `get_settings` can still be swapped in tests.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

import casa
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

# The shared shell of the .11 screens (top bar, palette, theme cookie): `casa/...` templates
# and the globals its macros read. The app's own templates keep priority.
casa.instalar(templates.env)

# Where each screen of the shared bar lives, seen from this app: its own pages, and the
# redirect routes of core/acesso.py for the other two processes (they resolve host/port).
templates.env.globals["CASA_HREFS"] = {
    "integracao": "/painel-wbc",
    "pedidos": "/pedidos-wbc",
    "ops": "/manutencao-op",
    "sincronizacao": "/sincronizacao",
    "tarefas": "/tarefas",
}

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
