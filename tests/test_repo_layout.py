"""O que o git leva para a .11 tem de ser o que roda aqui.

Em 08/09/2026 o pacote `wbcpython/` chegou ao servidor SEM nenhum `__init__.py` nem o
`__main__.py`: a regra `_*.py` do `.gitignore` (arquivos temporários) casa com os dois
nomes, e localmente tudo funcionava porque os arquivos existiam no disco. Na .11,
`python -m wbcpython` respondia "No module named wbcpython.__main__".

Este teste pergunta ao git, não ao disco: todo diretório de `wbcpython/` e `tests/wbc/`
que tenha `.py` versionado precisa ter o `__init__.py` versionado, e o `__main__.py`
do pacote precisa estar lá. Fora de um clone git (zip, cópia) o teste é pulado.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _versionados() -> list[str]:
    if shutil.which('git') is None:
        pytest.skip('git não disponível')
    proc = subprocess.run(['git', 'ls-files', 'wbcpython', 'tests/wbc'], cwd=RAIZ,
                          capture_output=True, text=True, encoding='utf-8')
    if proc.returncode != 0:
        pytest.skip('fora de um clone git')
    return [linha.strip() for linha in proc.stdout.splitlines() if linha.strip()]


def test_todo_pacote_versionado_leva_o_seu_init():
    arquivos = _versionados()
    assert arquivos, 'git ls-files não devolveu nada para wbcpython/ e tests/wbc/'
    pastas_com_py = {os.path.dirname(a) for a in arquivos if a.endswith('.py')}
    sem_init = sorted(p for p in pastas_com_py if f'{p}/__init__.py' not in arquivos)
    assert not sem_init, (
        f'pasta(s) com .py versionado e SEM __init__.py versionado: {sem_init} — '
        'confira as exceções !__init__.py / !__main__.py do .gitignore'
    )


def test_o_entrypoint_python_m_wbcpython_esta_versionado():
    assert 'wbcpython/__main__.py' in _versionados()
