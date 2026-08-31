"""Every module the live loop imports must actually import.

On 2026-08-30 commit 0df9b66 deleted mover_recorders.py while
news_surge_short_live.py still imported `_macro_regime` from it. That made one
of the four LIVE books unimportable, and scripts/trading_loop.py imports it at
module scope — so the trading loop could not start at all.

The suite was 1001 tests green. None of them imported that module, so nothing
noticed. The AST scope-check used on trading_loop.py cannot catch it either: the
broken import lives in a DIFFERENT module.

This is the check. It parses trading_loop.py's import statements without
importing it (importing that module STARTS the live loop) and imports each
target for real.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOOP = ROOT / "scripts" / "trading_loop.py"


def _loop_module_imports() -> list[str]:
    """Module-scope imports of first-party packages, read from the AST.

    Module scope only: a lazy import inside a function fails at call time, not
    at start-up, and is a different (smaller) problem.
    """
    tree = ast.parse(LOOP.read_text())
    out: list[str] = []
    for node in tree.body:                       # top level only
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in {"pathia", "services"}:
                out.append(node.module)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in {"pathia", "services"}:
                    out.append(a.name)
    return sorted(set(out))


def test_the_loop_has_first_party_imports_to_check():
    """Guard the guard: if the parse silently returns nothing, every assertion
    below passes vacuously."""
    assert len(_loop_module_imports()) >= 10


@pytest.mark.parametrize("module", _loop_module_imports())
def test_every_module_the_loop_imports_is_importable(module):
    """A ModuleNotFoundError here means the live loop cannot start."""
    importlib.import_module(module)


def test_every_name_the_loop_imports_actually_exists():
    """Not just the module — the NAME. `from x import y` where y is gone fails
    exactly the same way, and that is the shape the 0df9b66 bug took."""
    tree = ast.parse(LOOP.read_text())
    missing = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if node.module.split(".")[0] not in {"pathia", "services"}:
            continue
        mod = importlib.import_module(node.module)
        for alias in node.names:
            if not hasattr(mod, alias.name):
                missing.append(f"{node.module}.{alias.name}")
    assert not missing, f"the loop imports names that do not exist: {missing}"


def test_every_live_book_module_is_importable():
    """The books are what spend money. Each must load."""
    for mod in ("pathia.agents.news_surge_short_live",
                "pathia.agents.news_surge_multi",
                "pathia.agents.social_trending_recorder",
                "pathia.agents.unlock_short_live"):
        importlib.import_module(mod)
