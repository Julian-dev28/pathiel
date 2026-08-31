"""Every first-party import hidden behind `except Exception` must actually work.

A local import inside a broad try/except is a normal pattern here — metrics,
health checks and optional lanes all use it so one broken source degrades one
value instead of blanking a whole endpoint. The cost is that an ImportError
inside the guard is indistinguishable from the failure the guard is FOR.

That is not hypothetical. hermes_trader/metrics.py imported
`hermes_trader.agents.paths`, a module that has never existed. The guard caught
the ImportError and set the "never ran" sentinel, so hermes_supervisor_age_seconds
and hermes_alert_eval_age_seconds read 1e6 on a live box whose supervisor had
run 74 seconds earlier — and the test covering them asserted the sentinel, so it
passed. The bug was found by reading the live scrape.

97 guarded imports today. This resolves every one.
"""
from __future__ import annotations

import ast
import importlib
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIRST_PARTY = ("hermes_trader", "services")


def _guarded_imports():
    """(file, line, module, name) for each first-party `from X import Y` that
    sits inside a try block with a bare or broad `except`."""
    out = []
    for base in ("hermes_trader", "scripts", "services"):
        for path in (ROOT / base).rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            try:
                tree = ast.parse(path.read_text())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Try):
                    continue
                broad = any(
                    h.type is None or (isinstance(h.type, ast.Name)
                                       and h.type.id in ("Exception", "BaseException"))
                    for h in node.handlers)
                if not broad:
                    continue
                for stmt in node.body:
                    for n in ast.walk(stmt):
                        if isinstance(n, ast.ImportFrom) and \
                                (n.module or "").startswith(FIRST_PARTY):
                            for alias in n.names:
                                out.append((path.relative_to(ROOT), n.lineno,
                                            n.module, alias.name))
    return out


def test_every_guarded_first_party_import_resolves():
    broken = []
    for rel, line, module, name in _guarded_imports():
        try:
            mod = importlib.import_module(module)
            if hasattr(mod, name):
                continue
            importlib.import_module(f"{module}.{name}")   # a submodule, not an attribute
        except Exception as exc:                          # noqa: BLE001
            broken.append(f"{rel}:{line}: from {module} import {name} — "
                          f"{type(exc).__name__}: {exc}")
    assert not broken, (
        "these imports raise inside a broad `except`, so the failure is "
        "indistinguishable from the condition being guarded:\n" + "\n".join(broken))


def test_the_scan_finds_the_guarded_imports_it_should():
    """A scanner that silently matches nothing would pass forever."""
    found = _guarded_imports()
    assert len(found) > 50, f"only {len(found)} guarded imports found — scan is broken"
    assert any("metrics.py" in str(rel) for rel, _, _, _ in found)
