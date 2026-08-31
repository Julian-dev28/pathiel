"""No module may define the same top-level name twice.

Python takes the last definition and says nothing. That is how a rewritten
`alive()` in scripts/supervise_processes.py sat unused below its own
replacement on 2026-08-31: the new function was correct, the tests exercised
the old one, and the only symptom was a test failure that made no sense against
the code being read.

Cheap to check, and it catches the whole class: a half-finished refactor, a
merge that duplicated a helper, a copy-paste that redefined a constant's
function. Anything that makes the code you are reading not the code that runs.
"""
from __future__ import annotations

import ast
import collections
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP = (".venv", "__pycache__", "build", "node_modules", ".git")


def _python_files():
    for p in ROOT.rglob("*.py"):
        if not any(part in SKIP for part in p.parts):
            yield p


def test_no_module_defines_the_same_name_twice():
    offenders = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue                      # syntax is another test's job
        seen = collections.defaultdict(list)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                seen[node.name].append(node.lineno)
        for name, lines in seen.items():
            if len(lines) > 1:
                offenders.append(
                    f"{path.relative_to(ROOT)}: {name} defined at lines "
                    f"{lines} — only the last one runs")
    assert not offenders, "\n".join(offenders)


def test_the_check_actually_detects_a_shadowed_definition(tmp_path):
    """A guard that cannot fail is not a guard."""
    src = "def f():\n    return 1\n\n\ndef f():\n    return 2\n"
    tree = ast.parse(src)
    seen = collections.defaultdict(list)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            seen[node.name].append(node.lineno)
    assert seen["f"] == [1, 5]
