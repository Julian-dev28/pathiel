"""Static gate: `scripts/paper_breakout.py` (the oil breakout-entry PAPER
test, log only, no money -- docs/strategy/BREAKOUT_ENTRY_TEST_2026-11-19.md)
must never import or call a live order-placing symbol. AST-only: no import,
no execution, <1s.

Scope, stated plainly: this checks the script's OWN source, not the full
transitive import graph. `live_params.live_brent_params` (which the script
does import, legitimately, for the exact live Params) reaches
`next_trade.py`, which imports `pathiel.agents.bracket` for pure math
(`check_leverage`/`check_levels`/`DEFAULT_MIN_R`) -- loading that module
defines `place_bracket` et al. as names but never calls them. Forbidding the
whole transitive closure would flag every eval in `services/strategies/
evals/` that already builds a "live" baseline this same way; the thing that
actually matters, and the thing this test enforces, is that THIS script
never itself names an order-placing symbol in an import or a call."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "paper_breakout.py"

# Every live order-mutating function in this codebase (pathiel/agents/
# bracket.py, pathiel/server.py's MCP tool names, pathiel/client/exchange.py)
# plus the MCP tool names that wrap them. A name match, not a module match:
# catches `from pathiel.agents.bracket import place_bracket` AND
# `bracket.place_bracket(...)`/`b.place_bracket(...)` call forms alike.
FORBIDDEN_NAMES = {
    "place_bracket", "close_position", "cancel_order", "set_stop", "set_target", "set_leverage",
    "sweep_brackets", "sweep", "submit_desk_call", "refresh_desk", "update_config", "consolidate_protection",
    "handle_place_bracket",
}
# Modules known to hold live order/account-mutating code. A direct import
# from one of these (even of an unlisted name) is refused outright: the
# script has no legitimate reason to reach into them at all.
FORBIDDEN_MODULE_PREFIXES = (
    "pathiel.agents.bracket", "pathiel.agents.rebalancer", "pathiel.client.exchange", "pathiel.server",
    "scripts.pathiel-mcp-server", "scripts.pathiel_mcp_server",
)


def _tree() -> ast.Module:
    return ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))


def test_script_exists():
    assert SCRIPT.is_file()


def test_no_forbidden_module_imports():
    offenders = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ImportFrom) and node.module:
            if any(node.module == p or node.module.startswith(p + ".") for p in FORBIDDEN_MODULE_PREFIXES):
                offenders.append(f"from {node.module} import ... (line {node.lineno})")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if any(alias.name == p or alias.name.startswith(p + ".") for p in FORBIDDEN_MODULE_PREFIXES):
                    offenders.append(f"import {alias.name} (line {node.lineno})")
    assert not offenders, f"paper_breakout.py imports a live order module: {offenders}"
    # also catch "mcp" anywhere in a dotted import path (server/tool wiring)
    mcp_hits = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ImportFrom) and node.module and "mcp" in node.module.lower():
            mcp_hits.append(f"from {node.module} import ... (line {node.lineno})")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "mcp" in alias.name.lower():
                    mcp_hits.append(f"import {alias.name} (line {node.lineno})")
    assert not mcp_hits, f"paper_breakout.py imports an MCP module: {mcp_hits}"


def test_no_forbidden_imported_names():
    offenders = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                if alias.name in FORBIDDEN_NAMES or name in FORBIDDEN_NAMES:
                    offenders.append(f"{node.module}.{alias.name} (line {node.lineno})")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                tail = alias.name.rsplit(".", 1)[-1]
                if tail in FORBIDDEN_NAMES:
                    offenders.append(f"import {alias.name} (line {node.lineno})")
    assert not offenders, f"paper_breakout.py imports a live order function: {offenders}"


def test_no_forbidden_calls():
    """Defense in depth: even a module-qualified call (`bracket.
    place_bracket(...)`, `exchange.close_position(...)`) with no matching
    import statement (e.g. via `getattr` or a re-exported alias) is caught
    by name at the call site."""
    offenders = []
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in FORBIDDEN_NAMES:
            offenders.append(f"{name}(...) (line {node.lineno})")
    assert not offenders, f"paper_breakout.py calls a live order function: {offenders}"


def _code_string_constants(tree: ast.Module) -> list:
    """Every string literal in the AST that is NOT a module/function/class
    docstring -- so a prose mention in a comment/docstring ("never opens
    .env.local") cannot trip this check, only an actual string literal used
    in code (a path passed to open(), os.path.join(), etc.)."""
    docstring_ids = set()
    if (tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)):
        docstring_ids.add(id(tree.body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                docstring_ids.add(id(node.body[0].value))
    return [n.value for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstring_ids]


def test_no_env_or_agent_config_files_opened():
    """Hard rule: never open .env.local or ~/.pathiel. Checks actual code
    string literals (not docstrings/comments, which freely document that
    this script does NOT touch either path) -- this script has no
    legitimate reason to reference either (it reads $PATHIEL_STATE_DIR and
    the public venue API only)."""
    strings = _code_string_constants(_tree())
    assert not any(".env.local" in s for s in strings)
    assert not any(".pathiel" in s for s in strings)
