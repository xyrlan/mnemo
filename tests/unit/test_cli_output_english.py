"""The queue, session, deliver and land commands print English (#355).

These files drifted into Portuguese one string at a time, beside English ones.
No i18n layer exists, so the guard reads the printed literals themselves:
every string constant that is not a docstring. Comments are not constants, and
docstrings are skipped, so only what a user can see is checked.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mnemo.core.sessions.render import plural

SRC = Path(__file__).resolve().parents[2] / "src" / "mnemo"
FILES = [
    "core/sessions/render.py",
    "cli/commands/sessions.py",
    "cli/commands/session.py",
    "cli/commands/deliver.py",
    "cli/commands/land.py",
]
# Portuguese diacritics. None of them occur in the English these files print.
ACCENTED = set("ãõçáéíóúâêôàÃÕÇÁÉÍÓÚÂÊÔÀª")


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


@pytest.mark.parametrize("rel", FILES)
def test_printed_literals_carry_no_portuguese(rel: str) -> None:
    tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
    skip = _docstrings(tree)
    found = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in skip and ACCENTED & set(node.value)
    ]
    assert found == []


@pytest.mark.parametrize("count, expected", [
    (0, "0 uses"), (1, "1 use"), (2, "2 uses"),
])
def test_plural(count: int, expected: str) -> None:
    assert plural(count, "use") == expected
