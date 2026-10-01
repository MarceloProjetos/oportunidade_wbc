"""A minimal stand-in for ``mcp.server.fastmcp`` (SDK 1.x), for machines with SDK 2.x.

Why (01/10/2026 review): ``mcp/mcp_server.py`` is written against the 1.x ``FastMCP`` that
the .11 runs; the notebook has ``mcp`` 2.x, where that module is gone, so the three
``test_mcp_*`` files were skipped silently and any change to the facade reached the .11
untested. The tools themselves are plain functions that call the 8077 API over HTTP —
only the registration needs the SDK. This stub keeps exactly what the facade and its tests
use: ``FastMCP(name, instructions=…)``, ``.tool(annotations=…)`` and ``.resource(uri, …)``
(both return the function unchanged, as 1.x does), ``.instructions``, ``.run()`` and
``await .list_tools()`` (name, description, annotations).

Installed by ``tests/conftest.py`` ONLY when the real module does not import: where the 1.x
SDK exists (the .11), the tests run against it.
"""
from __future__ import annotations

import inspect
import sys
import types
from collections.abc import Callable
from typing import Any


class FastMCP:
    def __init__(self, name: str, instructions: str | None = None, **_kwargs: Any) -> None:
        self.name = name
        self.instructions = instructions
        self._tools: dict[str, types.SimpleNamespace] = {}
        self._resources: dict[str, Callable[..., Any]] = {}

    def tool(self, name: str | None = None, description: str | None = None,
             annotations: Any = None, **_kwargs: Any) -> Callable[[Callable], Callable]:
        def registrar(fn: Callable) -> Callable:
            nome = name or fn.__name__
            self._tools[nome] = types.SimpleNamespace(
                name=nome, description=description or inspect.getdoc(fn) or "",
                annotations=annotations, fn=fn,
            )
            return fn
        return registrar

    def resource(self, uri: str, **_kwargs: Any) -> Callable[[Callable], Callable]:
        def registrar(fn: Callable) -> Callable:
            self._resources[uri] = fn
            return fn
        return registrar

    async def list_tools(self) -> list[types.SimpleNamespace]:
        return list(self._tools.values())

    def run(self, *_args: Any, **_kwargs: Any) -> None:  # pragma: no cover - never served
        raise RuntimeError("stub do FastMCP: só para testes")


class ToolAnnotations(types.SimpleNamespace):
    """1.x keeps the camelCase names (``readOnlyHint``); 2.x renamed them to snake_case."""


def instalar() -> None:
    """Register the stub as ``mcp.server.fastmcp`` and ``mcp.types`` (the 2.x ``mcp.types``
    exists but its ``ToolAnnotations`` has other attribute names)."""
    modulo = types.ModuleType("mcp.server.fastmcp")
    modulo.FastMCP = FastMCP
    sys.modules["mcp.server.fastmcp"] = modulo
    tipos = types.ModuleType("mcp.types")
    tipos.ToolAnnotations = ToolAnnotations
    sys.modules["mcp.types"] = tipos
