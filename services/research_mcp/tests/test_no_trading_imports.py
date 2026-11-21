"""The hard gate: the research MCP never imports order-placement or signing
code, and never touches .env.local / ~/.pathiel.

Two independent checks, because either one alone can be fooled:
  1. STATIC: parse every .py file under services/research_mcp/ (this
     server's own code, including its scripts/ entrypoint and its evals)
     with `ast`, and fail if any import statement names a forbidden module.
     Catches a forbidden import even if nothing ever calls it.
  2. DYNAMIC: in a FRESH subprocess (so another test file's own imports
     earlier in this same pytest session can't taint the answer), import
     only services.research_mcp.server and check sys.modules afterward.
     Catches a forbidden import that static parsing missed because it
     arrived transitively through a module this static check doesn't walk
     into (a third-party package, for instance).
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

RESEARCH_MCP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = RESEARCH_MCP_DIR.parents[1]
ENTRYPOINT = REPO_ROOT / "scripts" / "pathiel-research-mcp.py"
LAUNCHER = REPO_ROOT / "scripts" / "pathiel-research-mcp.sh"

# Modules that place, close, size, sign or otherwise act on a real order or
# account. Matched as an exact module or any of its submodules (a/b.py
# importing "a" or "a.b" or "a.b.c" all count).
FORBIDDEN_MODULES = (
    "pathiel.agents.bracket",       # place_bracket/close_position/set_stop/set_target/sweep
    "pathiel.agents.executor",      # the retired autonomous scan/research/execute engine
    "pathiel.client.exchange",      # Hyperliquid order signing + submission
    "services.strategies.next_trade",  # imports pathiel.agents.bracket at module level
    "scripts.pathiel-mcp-server",   # the trading MCP server itself (not a valid module name,
                                    # listed for completeness; see the filename check below)
    "eth_account",                 # wallet signing
    "hyperliquid.exchange",        # the Hyperliquid SDK's own order-submission module
)

# Every .py file this server ships, including its own tests and evals (an
# eval that imported order code would be just as bad as the server doing it).
PY_FILES = sorted(RESEARCH_MCP_DIR.rglob("*.py")) + [ENTRYPOINT]


def _imported_modules(path: Path) -> set:
    tree = ast.parse(path.read_text(), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _is_forbidden(module_name: str) -> bool:
    return any(module_name == f or module_name.startswith(f + ".") for f in FORBIDDEN_MODULES)


def test_every_shipped_file_exists_and_is_parseable():
    assert ENTRYPOINT.is_file(), "scripts/pathiel-research-mcp.py must exist"
    assert LAUNCHER.is_file(), "scripts/pathiel-research-mcp.sh must exist"
    assert len(PY_FILES) > 5, "expected more than the entrypoint alone under services/research_mcp/"


def test_static_no_file_imports_order_or_signing_code():
    offenders = []
    for path in PY_FILES:
        for name in _imported_modules(path):
            if _is_forbidden(name):
                offenders.append(f"{path.relative_to(REPO_ROOT)} imports {name!r}")
    assert not offenders, "forbidden import(s):\n  " + "\n  ".join(offenders)


def test_launcher_execs_the_research_entrypoint_not_the_trading_one():
    """The launcher must exec scripts/pathiel-research-mcp.py, never the
    trading server's scripts/pathiel-mcp-server.py."""
    exec_lines = [ln for ln in LAUNCHER.read_text().splitlines() if ln.strip().startswith("exec ")]
    assert exec_lines, "pathiel-research-mcp.sh has no exec line"
    assert all("pathiel-research-mcp.py" in ln for ln in exec_lines)
    assert all("pathiel-mcp-server.py" not in ln for ln in exec_lines)


def _code_lines(path: Path) -> str:
    """A shell script's lines with full-line '#' comments removed, so a
    CODE check isn't fooled by this file's own comments explaining what it
    deliberately does NOT do (which necessarily name the forbidden things
    by name)."""
    return "\n".join(ln for ln in path.read_text().splitlines() if not ln.strip().startswith("#"))


def test_launcher_shell_script_has_no_operational_env_local_or_pathiel_home_reference():
    code = _code_lines(LAUNCHER)
    assert ".env.local" not in code
    assert "PATHIEL_HOME" not in code
    assert "source" not in code.lower()  # never sources another env file


def test_dynamic_fresh_process_never_loads_a_forbidden_module():
    """Import ONLY services.research_mcp.server in a brand-new interpreter
    and list which of sys.modules' keys are forbidden -- isolated from
    whatever else this pytest session has already imported."""
    code = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from services.research_mcp import server\n"
        f"forbidden = {FORBIDDEN_MODULES!r}\n"
        "hits = [m for m in sys.modules if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
        "print(json.dumps(hits))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                          cwd=str(REPO_ROOT))
    assert proc.returncode == 0, f"import failed:\n{proc.stderr}"
    hits = ast_literal(proc.stdout.strip())
    assert hits == [], f"forbidden module(s) loaded by a fresh import: {hits}"


def ast_literal(s: str):
    import json
    return json.loads(s)


def test_dynamic_fresh_process_never_opens_env_local_or_pathiel_home():
    """A fresh import of services.research_mcp.server must not create or
    read anything under ~/.pathiel, and must not read the repo's own
    .env.local. Proven by monkeypatching open() in the subprocess to raise
    on any path containing either string, then importing the server."""
    code = (
        "import builtins, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "real_open = builtins.open\n"
        "def guarded_open(path, *a, **kw):\n"
        "    p = str(path)\n"
        "    if '.env.local' in p or '.pathiel/' in p or p.endswith('/.pathiel'):\n"
        "        raise AssertionError('research MCP import touched ' + p)\n"
        "    return real_open(path, *a, **kw)\n"
        "builtins.open = guarded_open\n"
        "from services.research_mcp import server\n"
        "print('ok')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                          cwd=str(REPO_ROOT))
    assert proc.returncode == 0 and "ok" in proc.stdout, (
        f"import touched a forbidden path:\nstdout={proc.stdout}\nstderr={proc.stderr}")
