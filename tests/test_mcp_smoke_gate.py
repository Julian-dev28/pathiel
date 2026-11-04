"""Offline gate for scripts/mcp_smoke.py (the live smoke is the online eval
in tests/test_mcp_smoke_online.py).

Both bugs came out of the 2026-11-04 server deploy test, where the smoke ran
twice in a row on a fresh box:
  - set_target shipped as a live-money tool with no smoke case and no skip
    reason, so every smoke run failed "uncovered tools".
  - the parallel check failed on a warm cache: desk_brief answered in 0.04s,
    so get_server_time had nothing to overtake.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _smoke():
    spec = importlib.util.spec_from_file_location("mcp_smoke", ROOT / "scripts" / "mcp_smoke.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _server_set(name: str) -> set:
    tree = ast.parse((ROOT / "scripts" / "pathiel-mcp-server.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return set(ast.literal_eval(node.value.args[0]))
    raise AssertionError(f"{name} not found in pathiel-mcp-server.py")


def test_every_live_money_and_journal_write_tool_is_skipped_with_a_reason():
    smoke = _smoke()
    must_skip = _server_set("LIVE_MONEY_TOOLS") | _server_set("LOCAL_WRITE_TOOLS")
    assert "set_target" in must_skip
    missing = must_skip - set(smoke.SKIPPED)
    assert not missing, missing
    assert all(smoke.SKIPPED[t].strip() for t in must_skip)
    assert not must_skip & {c.tool for c in smoke.cases()}


def test_parallel_verdict():
    v = _smoke().parallel_verdict
    assert v(["get_server_time", "desk_brief"], 13.7) is True
    assert v(["desk_brief", "get_server_time"], 13.7) is False
    # the warm-cache case that failed the deploy test: inconclusive, not a failure
    assert v(["desk_brief", "get_server_time"], 0.04) is None
    assert v([], 0.0) is None
