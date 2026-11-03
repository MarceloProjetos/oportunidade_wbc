"""Prove a diff only touched docstrings and function annotations.

Compares the AST of every modified ``.py`` against ``git show <ref>:<path>`` with docstrings and
function argument/return annotations stripped. Class-level annotations (dataclass and pydantic
fields) are kept on purpose: changing them changes behavior. Exit code 1 lists the files whose code
changed.

Usage: ``python scripts/check_docs_only_diff.py [--ref HEAD] [paths...]``
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _strip(tree: ast.AST) -> str:
    """Dump ``tree`` without docstrings and without function annotations."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.returns = None
            a = node.args
            for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg):
                if arg is not None:
                    arg.annotation = None
    return ast.dump(tree, include_attributes=False)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("paths", nargs="*")
    args = ap.parse_args(argv)

    paths = args.paths or [p for p in _git("diff", "--name-only", args.ref, "--", "*.py").splitlines()
                           if (ROOT / p).exists()]
    changed: list[str] = []
    for rel in paths:
        new = (ROOT / rel).read_text(encoding="utf-8")
        try:
            old = _git("show", f"{args.ref}:{Path(rel).as_posix()}")
        except subprocess.CalledProcessError:
            changed.append(f"{rel}: new file")
            continue
        if _strip(ast.parse(old)) != _strip(ast.parse(new)):
            changed.append(rel)
    for rel in changed:
        print(f"CODE CHANGED: {rel}")
    print(f"{len(paths) - len(changed)}/{len(paths)} files: docstrings/annotations only")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
